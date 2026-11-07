"""Build and validate vibe-coding-v1 and check tasks with real toolchains.

A reference edit reaches the training set only if the unedited fixture fails its
hidden tests and the edited one passes them, using the actual compiler or runtime.
A toolchain this machine lacks excludes the task and says so.
Repair rows are the loop's second turn. The wrong edit stays in the history,
and the completion a trainer learns is the fix.

    py -3 -m training.vibe.dataset build training/vibe/vibe-coding-v1.json
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

from takumi.vibe import context, edits, platforms
from takumi.vibe.loop import declared_name_diagnostics
from takumi.vibe.platforms import Step

from .tasks import TASKS, Task, edit

VERSION = 'vibe-coding-v1'
ROOT = Path(__file__).resolve().parents[2]
MAX_CHARS = 2600  # keeps rows under the 640-token training limit with room to spare
KEPT_LOGIT_POSITIONS = 16  # per row, for adapter parity checks. A run holds three copies under a 6 GiB host guard


@dataclass(frozen=True)
class RepairCase:
    """A second training turn: a plausible wrong edit, then the fix.

    `kind` is the visible check the loop actually stops on. `jsin` is the static
    `in` check, which runs only after the task's own tests have passed. `name`
    is the reverse-DNS check, which runs only after every project check passed.
    The supervised completion is `fix`, not the wrong edit.
    """
    task_id: str
    kind: str
    wrong: str
    fix: str


# These are the two held-out failure shapes, taught on train-only families.
REPAIRS = (
    RepairCase('js-has', 'jsin',
               edit('app.mjs', '  return key in value;', '  if (value == null) return false;\n  return key in value;'),
               edit('app.mjs', '  return key in value;', '  return key in Object(value);')),
    RepairCase('py-channel', 'name',
               edit('app.py', 'CHANNEL = ""', 'CHANNEL = "inbox"'),
               edit('app.py', 'CHANNEL = "inbox"', 'CHANNEL = "app.takumi.inbox"')),
    RepairCase('ios-app-group', 'name',
               edit('Info.plist', '  <key>CFBundleName</key>', '  <key>AppGroupId</key>\n  <string>group.notes</string>\n  <key>CFBundleName</key>'),
               edit('Info.plist', '<string>group.notes</string>', '<string>group.dev.takumi.notes</string>')),
)


def validator_sha256() -> str:
    """Covers the fixtures and the prompts they render to, not budget tuning."""
    digest = hashlib.sha256((ROOT / 'training/vibe/tasks.py').read_bytes().replace(b'\r\n', b'\n'))
    digest.update(json.dumps([prompt_for(task) for task in TASKS], sort_keys=True).encode('utf-8'))
    return digest.hexdigest()


def materialize(task: Task, folder: Path, *, hidden: bool = False) -> None:
    for group in (task.support, task.files, task.visible, task.hidden if hidden else {}):
        for name, body in group.items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding='utf-8', newline='\n')


def _java_tool(name: str) -> str:
    home = platforms.java_home()
    return str(Path(home, 'bin', name)) if home else name


def steps_for(task: Task, folder: Path, *, hidden: bool = False) -> list[Step]:
    """The checks for a task. Running hidden tests is the only difference between
    what the loop sees and what scores it."""
    runner, platform = task.runner, task.platform
    if runner == 'python':
        python = shutil.which('py')
        prefix = [python, '-3'] if python else [sys.executable]
        return [Step('tests', 'test', prefix + ['-m', 'unittest', 'discover', '-s', '.', '-p', 'test*.py'], timeout=60, platform=platform)]
    if runner == 'node':
        node = shutil.which('node')
        return [Step('tests', 'test', [node or 'node', '--test'], timeout=60, blocked='' if node else 'Node.js is missing.', platform=platform),
                Step('javascript in-operator', 'static', builtin='jsin', platform=platform)]
    if runner == 'dotnet':
        dotnet = shutil.which('dotnet')
        return [Step('dotnet run', 'test', [dotnet or 'dotnet', 'run', '--project', '.', '--nologo', '-v', 'q'], timeout=240,
                     blocked='' if dotnet else 'The .NET SDK is missing.', platform=platform)]
    if runner == 'cpp':
        command = 'g++ -std=c++20 -Wall -Wno-unused-parameter *.cpp -o t && ./t'
        if shutil.which('g++'):
            return [Step('g++ tests', 'test', ['sh', '-c', command], timeout=120, platform=platform)]
        tools = platforms._wsl_tools(('g++',))
        return [Step('g++ tests', 'test', ['wsl', '--cd', platforms.wsl_path(folder), '-e', 'sh', '-c', command], timeout=120,
                     blocked='' if tools.get('g++') else 'No C++ compiler found.', platform=platform)]
    if runner == 'java':
        blocked = '' if platforms.java_home() else 'No JDK found.'
        sources = sorted({*task.files, *task.visible, *(task.hidden if hidden else {})})
        steps = [Step('javac', 'build', [_java_tool('javac'), '-d', 'out', *sources], timeout=90, blocked=blocked, platform=platform),
                 Step('java tests', 'test', [_java_tool('java'), '-ea', '-cp', 'out', 'Tests'], timeout=30, blocked=blocked, platform=platform)]
        if hidden:
            steps.append(Step('java hidden', 'test', [_java_tool('java'), '-ea', '-cp', 'out', 'HiddenTests'], timeout=30, blocked=blocked, platform=platform))
        return steps
    raise ValueError('Unknown runner: ' + runner)


def score_reply(task: Task, reply: str) -> dict:
    """Apply a model's reply to a clean fixture and run visible and hidden tests."""
    with tempfile.TemporaryDirectory(prefix='vibe-', ignore_cleanup_errors=True) as raw:
        folder = Path(raw)
        materialize(task, folder, hidden=True)
        try:
            parsed = edits.parse(reply)
            if not parsed:
                return {'passed': False, 'reason': 'no edits'}
            edits.apply(folder, parsed, edits.Journal(folder))
        except edits.EditError as exc:
            return {'passed': False, 'reason': str(exc)[:300]}
        return _run(task, folder)


def score_project(task: Task, source: Path, *, drop: tuple[str, ...] = ()) -> dict:
    """Score a finished project copy. Hidden tests are added only now.

    The score is about the code: the visible tests return to their original text,
    and test files the run itself added (`drop`) cannot pass or fail anything.
    """
    with tempfile.TemporaryDirectory(prefix='vibe-', ignore_cleanup_errors=True) as raw:
        folder = Path(raw) / 'p'
        shutil.copytree(source, folder, ignore=shutil.ignore_patterns('bin', 'obj', 'out', '__pycache__'))
        for name in drop:
            (folder / name).unlink(missing_ok=True)
        for name, body in {**task.visible, **task.hidden}.items():
            (folder / name).write_text(body, encoding='utf-8', newline='\n')
        return _run(task, folder)


def _run(task: Task, folder: Path) -> dict:
    steps = steps_for(task, folder, hidden=True)
    if any(s.blocked for s in steps):
        return {'passed': None, 'reason': next(s.blocked for s in steps if s.blocked)}
    result = platforms.run_steps(folder, steps)
    return {'passed': result['state'] == 'passed', 'reason': '; '.join(d.render() for d in result['diagnostics'][:2])[:400],
            'seconds': round(sum(s['seconds'] for s in result['steps']), 2)}


def check_task(task: Task) -> dict:
    """Prove the task means something: the fixture fails, the reference passes."""
    with tempfile.TemporaryDirectory(prefix='vibe-', ignore_cleanup_errors=True) as raw:
        folder = Path(raw)
        materialize(task, folder, hidden=True)
        steps = steps_for(task, folder, hidden=True)
        if any(s.blocked for s in steps):
            return {'id': task.id, 'status': 'excluded', 'reason': next(s.blocked for s in steps if s.blocked)}
        before = platforms.run_steps(folder, steps)
    after = score_reply(task, task.solution)
    if before['state'] != 'failed':
        return {'id': task.id, 'status': 'invalid', 'reason': 'The unedited fixture already passes its hidden tests.'}
    if after['passed'] is not True:
        return {'id': task.id, 'status': 'invalid', 'reason': 'The reference fails: ' + after['reason']}
    return {'id': task.id, 'status': 'verified', 'split': task.split, 'platform': task.platform, 'seconds': after.get('seconds')}


def prompt_for(task: Task) -> list[dict]:
    files = sorted({**task.files, **task.visible}.items())
    return [{'role': 'system', 'content': context.SYSTEM},
            {'role': 'user', 'content': context.task_prompt(task.prompt, files, platforms=[task.platform])}]


def _apply(folder: Path, reply: str) -> None:
    parsed = edits.parse(reply)
    if not parsed:
        raise edits.EditError('No SEARCH/REPLACE blocks were found.')
    edits.apply(folder, parsed, edits.Journal(folder))


def _task(task_id: str) -> Task:
    found = next((task for task in TASKS if task.id == task_id), None)
    if found is None or found.split != 'train':
        raise ValueError('Repair row is not a training task: ' + task_id)
    return found


def repair_diagnostics(task: Task, folder: Path, kind: str):
    """The failure the loop would show, without replaying toolchain text."""
    if kind == 'jsin':
        found = platforms.check_js_in(folder)
    elif kind == 'name':
        found = declared_name_diagnostics(task.prompt, folder)
    else:
        raise ValueError('Unknown repair kind: ' + kind)
    if not found:
        raise ValueError(task.id + ' repair no longer fails its visible check')
    return found


def render_repair(case: RepairCase) -> list[dict]:
    """The messages the loop sends on the second turn, plus the fix to learn."""
    task = _task(case.task_id)
    with tempfile.TemporaryDirectory(prefix='vibe-repair-', ignore_cleanup_errors=True) as raw:
        folder = Path(raw)
        materialize(task, folder, hidden=False)
        _apply(folder, case.wrong)
        messages, _info = context.build_messages(
            folder, task.prompt, platforms=[task.platform],
            diagnostics=repair_diagnostics(task, folder, case.kind),
            touched=[item.path for item in edits.parse(case.wrong)], last_reply=case.wrong)
    messages.append({'role': 'assistant', 'content': case.fix})
    return messages


def prove_repair(case: RepairCase) -> None:
    """The wrong edit must fail only the check the loop can see, and the fix must pass hidden tests."""
    task = _task(case.task_id)
    with tempfile.TemporaryDirectory(prefix='vibe-prove-', ignore_cleanup_errors=True) as raw:
        folder = Path(raw)
        materialize(task, folder, hidden=False)
        _apply(folder, case.wrong)
        steps = steps_for(task, folder, hidden=False)
        blocked = next((step.blocked for step in steps if step.blocked), '')
        if blocked:
            raise ValueError(task.id + ' repair cannot be proved here: ' + blocked)
        visible = platforms.run_steps(folder, steps)
        if case.kind == 'jsin':
            if visible['state'] != 'failed' or not any(step['status'] == 'passed' and step['kind'] == 'test' for step in visible['steps']):
                raise ValueError(task.id + ' repair did not pass its tests before the static check')
        elif visible['state'] != 'passed':
            raise ValueError(task.id + ' name repair must pass the visible tests so the loop reaches the name check')
        repair_diagnostics(task, folder, case.kind)
        _apply(folder, case.fix)
        for name, body in task.hidden.items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding='utf-8', newline='\n')
        hidden = platforms.run_steps(folder, steps_for(task, folder, hidden=True))
        if hidden['state'] != 'passed':
            raise ValueError(task.id + ' repair fix failed hidden tests: ' + '; '.join(d.render() for d in hidden['diagnostics'][:2])[:300])
        if case.kind == 'name' and declared_name_diagnostics(task.prompt, folder):
            raise ValueError(task.id + ' repair fix still omits a name the task asked for')
        if case.kind == 'jsin' and platforms.check_js_in(folder):
            raise ValueError(task.id + ' repair fix still uses `in` on a nullable value')


def audit_splits() -> None:
    by_split: dict[str, set[str]] = {}
    ids = set()
    for task in TASKS:
        if task.id in ids:
            raise ValueError('Duplicate task: ' + task.id)
        ids.add(task.id)
        by_split.setdefault(task.split, set()).add(task.family)
    for a, b in combinations(sorted(by_split), 2):
        shared = by_split[a] & by_split[b]
        if shared:
            raise ValueError(f'{a}/{b} family overlap: {sorted(shared)}')


def _example(task: Task, messages: list[dict], *, repair: RepairCase | None = None) -> dict:
    size = sum(len(m['content']) for m in messages)
    if size > MAX_CHARS:
        raise ValueError(f'{task.id} is {size} characters; the row limit is {MAX_CHARS}')
    row = {'id': task.id + ('#repair' if repair else ''), 'project': task.platform, 'task_family': task.family,
           'license': 'MIT', 'messages': messages}
    if repair:
        row['turn'] = 'repair'
    return row


def build(path: Path) -> dict:
    audit_splits()
    previous = {}
    if Path(path).is_file():
        try:
            previous = json.loads(Path(path).read_text(encoding='utf-8'))
        except ValueError:
            previous = {}
    prior = {row['id']: row for row in previous.get('audit', []) if row.get('status') == 'verified'}
    audit = []
    for task in TASKS:
        result = check_task(task)
        # A machine without .NET or a JDK must not drop rows the owner's PC already proved.
        if result['status'] == 'excluded' and task.id in prior:
            audit.append(prior[task.id])
        else:
            audit.append(result)
    bad = [a for a in audit if a['status'] == 'invalid']
    if bad:
        raise ValueError('Invalid tasks: ' + json.dumps(bad, indent=1))
    verified = {a['id'] for a in audit if a['status'] == 'verified'}
    examples = []
    for task in TASKS:
        if task.split != 'train' or task.id not in verified:
            continue
        examples.append(_example(task, prompt_for(task) + [{'role': 'assistant', 'content': task.solution}]))
    for case in REPAIRS:
        if case.task_id not in verified:
            raise ValueError(case.task_id + ' repair has no verified task')
        prove_repair(case)
        examples.append(_example(_task(case.task_id), render_repair(case), repair=case))
    report = {'version': VERSION, 'purpose': 'Teach the edit format and the repair turn used by the verified loop',
              'license': 'MIT', 'source': 'Original fixtures in training/vibe/tasks.py; no private source or game data',
              'validator_sha256': validator_sha256(), 'audit': audit, 'examples': examples}
    validate_dataset(report)
    Path(path).write_text(json.dumps(report, indent=1) + '\n', encoding='utf-8', newline='\n')
    return report


def validate_dataset(dataset: dict) -> None:
    """Fail closed before training. Runs inside WSL, which may lack the toolchains,
    so it trusts the recorded audit only if the hash of tasks and prompt matches."""
    if dataset.get('version') != VERSION or dataset.get('validator_sha256') != validator_sha256():
        raise ValueError('The vibe dataset does not match the current tasks or prompt; rebuild it')
    audit_splits()
    known = {t.id: t for t in TASKS}
    verified = {a['id'] for a in dataset.get('audit', []) if a.get('status') == 'verified'}
    records = dataset.get('examples', [])
    if not records:
        raise ValueError('The vibe dataset has no examples')
    repairs = {case.task_id: case for case in REPAIRS}
    seen_repairs = set()
    for record in records:
        raw_id = str(record.get('id'))
        repair_id = raw_id.endswith('#repair')
        task = known.get(raw_id[:-7] if repair_id else raw_id)
        if not task or task.split != 'train' or task.id not in verified:
            raise ValueError('Not a verified training task: ' + raw_id)
        if sum(len(m['content']) for m in record.get('messages', [])) > MAX_CHARS:
            raise ValueError(task.id + ' exceeds the training row limit')
        if repair_id:
            case = repairs.get(task.id)
            if case is None or record.get('turn') != 'repair' or record.get('messages') != render_repair(case):
                raise ValueError('Repair example differs from the loop: ' + task.id)
            seen_repairs.add(task.id)
        elif record.get('messages') != prompt_for(task) + [{'role': 'assistant', 'content': task.solution}]:
            raise ValueError('Example differs from its task: ' + task.id)
    if seen_repairs != set(repairs):
        raise ValueError('Missing repair rows: ' + ', '.join(sorted(set(repairs) - seen_repairs)))


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != 'build':
        raise SystemExit('usage: python -m training.vibe.dataset build OUTPUT.json')
    result = build(Path(sys.argv[2]))
    for row in result['audit']:
        print(row['id'], row['status'], row.get('seconds', row.get('reason', '')))
    print(len(result['examples']), 'training examples;', result['validator_sha256'])
