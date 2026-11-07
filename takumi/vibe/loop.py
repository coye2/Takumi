"""The verified loop: edit, run the project's real checks, repair from evidence.

States are deliberately narrow. `verified` means the project's own build or
tests ran and passed. Anything less says exactly what it is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import threading
import time
from typing import Callable

from . import coherence, context, diagnostics as diag, edits, oracle, platforms
from .memory import digest as tree_digest
from .memory import tree_hash

# verified            build/tests ran and passed, nothing blocked
# partially_verified  what could run passed, but a required check is blocked
# unverified          the edit applied and no check can prove it
# failed              checks still fail after the budget; changes rolled back
# stuck               the same failure repeated despite a changed approach
# no_change           the model never produced an applicable edit
# model_unavailable   the local model could not answer
# cancelled           stopped by the owner; changes rolled back
TERMINAL = {'verified', 'partially_verified', 'unverified', 'failed', 'stuck', 'no_change', 'model_unavailable', 'cancelled'}
TEST_PATH = ('test', 'spec', '__tests__')


@dataclass
class Options:
    max_attempts: int = 4
    allow_config: bool = False
    keep_failed: bool = False
    baseline: bool = True
    deadline_seconds: float = 1800
    budget: context.Budget = field(default_factory=context.Budget)
    edge_tests: bool = False
    edge_attempts: int = 3
    edge_think: bool = False
    main_think: bool = False
    think_num_predict: int = 8192
    edge_review: bool = False
    edge_review_predict: int = 4096
    recall: str = ''


# Fails to parse in every language the loop runs tests for, so a test command
# that still passes with this in place never looked at the file.
DISCOVERY_PROBE = b'@@@ takumi test discovery probe ((( \n'
TEST_FILE_LIMIT = 200_000


_DECLARED_NAME = re.compile(r'\b(?:[A-Za-z][A-Za-z0-9_-]*\.){2,}[A-Za-z][A-Za-z0-9_-]*\b')
_ASSERT_LINE = re.compile(r'^\s*(?:assert(?:[_!\s(])|t\.(?:Fatal|Error)|XCTAssert|self\.assert)')
_NAME_SUFFIXES = {'.plist', '.xml', '.csproj', '.json', '.js', '.mjs', '.cjs', '.py', '.cs', '.h', '.cpp', '.java', '.swift', '.html', '.ts'}


def declared_name_diagnostics(task: str, project: Path) -> list[diag.Diagnostic]:
    """The errors the loop adds when a reverse-DNS name never landed in the project."""
    return [diag.Diagnostic('task names', 'error', f'The task names "{name}" but no project file contains it.', source='static')
            for name in _missing_declared_names(task, project)]


def _missing_declared_names(task: str, project: Path) -> list[str]:
    """Reverse-DNS names the task asks for must land in a project file.

    The held-out iOS URL task was marked verified after the scheme was added
    and the requested name was left out. Visible tests only parsed the plist.
    """
    names = []
    for match in _DECLARED_NAME.finditer(task or ''):
        token = match.group(0)
        if token not in names:
            names.append(token)
    if not names:
        return []
    blob = []
    for item in platforms._walk(project):
        if item.suffix.lower() not in _NAME_SUFFIXES or is_test_path(item.as_posix()):
            continue
        path = project / item
        try:
            if path.stat().st_size > 200_000:
                continue
            blob.append(path.read_text('utf-8', 'replace'))
        except OSError:
            continue
    text = '\n'.join(blob)
    return [name for name in names if name not in text]


def _verification(state: str, changed: list[str], checks: dict | None) -> dict:
    """Keep source, build, test, and end-to-end as separate facts."""
    passed = bool(checks and checks.get('state') == 'passed' and state in ('verified', 'partially_verified'))
    return {'source_changed': bool(changed),
            'source_validated': bool(passed and checks.get('static')),
            'checks_executed': bool(checks and checks.get('steps')),
            'build_passed': bool(passed and checks.get('built')),
            'tests_passed': bool(passed and checks.get('tested')),
            'runtime_passed': bool(passed and checks.get('ran')),
            'end_to_end': state == 'verified'}


def is_test_path(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in TEST_PATH)


def _assertion_lines(text: str) -> list[str]:
    """Assertion lines, including a call that continues onto the following lines."""
    lines, kept, index = text.replace('\r\n', '\n').split('\n'), [], 0
    while index < len(lines):
        if not _ASSERT_LINE.match(lines[index]):
            index += 1
            continue
        depth = 0
        while index < len(lines):
            kept.append(lines[index])
            depth += lines[index].count('(') - lines[index].count(')')
            index += 1
            if depth <= 0:
                break
    return kept


def _lines_kept(old: str, new: str) -> bool:
    """Every non-blank line of the old text survives, in order. Tests may be added around them."""
    remaining = iter(new.replace('\r\n', '\n').split('\n'))
    return all(any(line == other for other in remaining) for line in old.replace('\r\n', '\n').split('\n') if line.strip())


class VibeRun:
    def __init__(self, project: Path, task: str, client, *, options: Options | None = None, cancel: threading.Event | None = None,
                 on_event: Callable[[dict], None] | None = None, plan: dict | None = None, snapshot: dict | None = None,
                 source_index=None):
        self.project, self.task, self.client = Path(project).resolve(), task, client
        self.options = options or Options()
        self.cancel = cancel or threading.Event()
        self.on_event = on_event
        self.plan = plan
        self.snapshot = snapshot
        self.source_index = source_index
        self.journal = edits.Journal(self.project)
        self.events: list[dict] = []
        self.attempts: list[dict] = []
        self.edge: dict = {'state': 'off'}
        self.edge_notes: dict = {}
        self.reviewed: dict[str, list[str] | None] = {}
        self._passed: dict[str, dict] = {}

    def _refresh_index(self) -> None:
        """Re-read changed files. Units whose bytes still match the saved index are kept."""
        if self.source_index is None:
            return
        try:
            self.snapshot = self.source_index.refresh(self.project, cancel=self.cancel)
        except (OSError, ValueError):
            return

    def _emit(self, kind: str, title: str, name: str | None = None, **data):
        event = {'time': time.time(), 'kind': kind, 'title': title, **data}
        if name:
            event['name'] = name
        self.events.append(event)
        if self.on_event:
            self.on_event(event)

    def _ask(self, messages: list[dict], think: bool) -> dict:
        extra = {'think': True, 'num_predict': self.options.think_num_predict} if think else {}
        reply = self.client.complete(messages, cancel=self.cancel, **extra)
        reasoning = str(reply.get('thinking') or '').strip()
        if reasoning:
            self._emit('reasoning', 'Model reasoning', name='reasoning', detail=reasoning)
        return reply

    def _on_step(self, step):
        name = {'build': 'build_started', 'test': 'test_started', 'run': 'runtime_started'}.get(step.kind)
        self._emit('step', step.name, name=name, step=step.public())

    def _verify(self, label: str) -> dict:
        self._emit('verify', label)
        fingerprint = tree_digest(tree_hash(self.project))
        cached = self._passed.get(fingerprint)
        if cached:
            name = 'test_completed' if cached.get('tested') else 'runtime_completed' if cached.get('ran') else 'build_completed' if cached.get('built') else None
            reused = {**cached, 'steps': list(cached.get('steps', [])), 'diagnostics': list(cached.get('diagnostics', []))}
            self._emit('checks', 'Checks reused because these files already passed.', name=name, reused=True, state=reused['state'])
            return reused
        result = platforms.run_steps(self.project, self.plan['steps'], cancel=self.cancel, on_step=self._on_step)
        for step in result['steps']:
            if step['kind'] == 'build':
                self._emit('build', f'{step["name"]} {step["status"]}', name='build_completed', status=step['status'])
            elif step['kind'] == 'test':
                self._emit('test', f'{step["name"]} {step["status"]}', name='test_completed', status=step['status'])
            elif step['kind'] == 'run':
                self._emit('run', f'{step["name"]} {step["status"]}', name='runtime_completed', status=step['status'])
        errors = diag.errors_first(result['diagnostics'])
        if errors:
            self._emit('diagnostic', errors[0].message[:180], name='diagnostic_found', file=errors[0].file, line=errors[0].line)
        self._emit('checks', f'Checks {result["state"]}', state=result['state'],
                   steps=[{k: v for k, v in s.items() if k != 'output_tail'} for s in result['steps']],
                   diagnostics=[d.public() for d in errors[:12]])
        if result['state'] == 'passed':
            self._passed[fingerprint] = result
        return result

    def _report(self, state: str, detail: str, checks: dict | None, started: float, baseline: dict | None = None) -> dict:
        if state not in ('verified', 'partially_verified', 'unverified') and not self.options.keep_failed:
            conflicts = self.journal.rollback()
            rolled_back = not conflicts
        else:
            conflicts, rolled_back = [], False
        changed = self.journal.changed()
        output = sum(a.get('output_tokens', 0) for a in self.attempts)
        report = {'state': state, 'detail': detail, 'attempts': len(self.attempts), 'attempt_log': self.attempts,
                  'changed_files': changed, 'diff': self.journal.diff(), 'rolled_back': rolled_back, 'rollback_conflicts': conflicts,
                  'platforms': self.plan['platforms'] if self.plan else [],
                  'checks': [{k: v for k, v in s.items() if k != 'output_tail'} for s in (checks or {}).get('steps', [])],
                  'blocked': (checks or {}).get('blocked', []),
                  'diagnostics': [d.public() for d in diag.errors_first((checks or {}).get('diagnostics', []))[:20]],
                  'baseline_failing': bool(baseline and baseline['state'] == 'failed'),
                  'tests_ran': bool(checks and checks.get('tested')), 'tests_edited': any(is_test_path(n) for n in changed), 'edge_tests': self.edge,
                  'output_tokens': output, 'seconds': round(time.monotonic() - started, 1), 'model': getattr(self.client, 'model', ''),
                  'verification': _verification(state, changed, checks),
                  'ledger': [{'attempt': item.get('attempt'), 'edited': item.get('touched', []),
                              'errors': item.get('errors'), 'edit_error': str(item.get('edit_error', ''))[:240]}
                             for item in self.attempts]}
        if changed and state in ('verified', 'partially_verified', 'unverified'):
            self._emit('checkpoint', 'Kept the edits from this run.', name='checkpoint_saved', files=changed)
        finished = 'task_blocked' if state in ('partially_verified', 'model_unavailable') else None if state == 'cancelled' else 'task_completed'
        self._emit('done', detail, name=finished, state=state)
        return report

    def run(self) -> dict:
        started = time.monotonic()
        if not self.project.is_dir():
            raise ValueError('The project folder does not exist')
        self.plan = self.plan or platforms.detect(self.project)
        self._original_tests = self._test_files()
        self._emit('task', 'Checking the project and its checks.', name='task_started')
        self._emit('plan', 'Detected ' + (', '.join(self.plan['platforms']) or 'no known project type'),
                   name='project_inspection', platforms=self.plan['platforms'], steps=[s.public() for s in self.plan['steps']])
        self._emit('plan', 'Checks for this project: ' + (', '.join(step.name for step in self.plan['steps']) or 'none'),
                   name='plan_updated', platforms=self.plan['platforms'])
        baseline = None
        known: set[str] = set()
        if self.options.baseline and self.plan['runnable']:
            # An already-broken project must not be mistaken for a regression,
            # and it is the failure the model has to reason about first.
            baseline = self._verify('Checking the project before any edits')
            if baseline['state'] == 'cancelled':
                return self._report('cancelled', 'Stopped before any edits.', baseline, started)
            known = {diag.fingerprint(baseline['diagnostics'])} - {''}
        current = list(baseline['diagnostics']) if baseline else []
        last_reply, feedback, stuck = '', '', False
        seen: dict[str, int] = {}
        best_errors, best_snapshot, best_diagnostics = None, {}, []
        checks = baseline
        for attempt in range(1, self.options.max_attempts + 1):
            if self.cancel.is_set():
                return self._report('cancelled', 'Stopped by you. Changes were rolled back.', checks, started, baseline)
            if time.monotonic() - started > self.options.deadline_seconds:
                return self._report('failed', 'Ran out of time before the checks passed.', checks, started, baseline)
            self._refresh_index()
            messages, info = context.build_messages(self.project, self.task, platforms=self.plan['platforms'], diagnostics=current,
                                                    touched=self.journal.changed(), last_reply=last_reply, stuck=stuck, feedback=feedback,
                                                    budget=self.options.budget, recall=self.options.recall, checks=self.plan['steps'],
                                                    snapshot=self.snapshot, disk_wins=True)
            index_counts = {}
            if isinstance(self.snapshot, dict) and 'parsed' in self.snapshot:
                index_counts = {'parsed': self.snapshot['parsed'], 'reused': self.snapshot.get('reused', 0)}
            self._emit('context', f'Reading {len(info["files"])} file(s).', name='context_retrieval', files=info['files'], **index_counts)
            self._emit('generate', f'Attempt {attempt}: writing edits', name='repair_started' if attempt > 1 else None,
                       attempt=attempt, context=info)
            try:
                reply = self._ask(messages, self.options.main_think)
            except RuntimeError as exc:
                return self._report('model_unavailable', str(exc), checks, started, baseline)
            record = {'attempt': attempt, **{k: v for k, v in reply.items() if k != 'text'}, 'context': info}
            self.attempts.append(record)
            if self.cancel.is_set():
                return self._report('cancelled', 'Stopped by you. Changes were rolled back.', checks, started, baseline)
            last_reply, feedback, stuck = reply['text'], '', False
            record['truncated'] = reply.get('done_reason') == 'length'
            try:
                parsed = edits.parse(reply['text'])
                if not parsed:
                    raise edits.EditError('No SEARCH/REPLACE blocks were found. Reply with edits only.')
                before = self.journal.snapshot()
                touched = edits.apply(self.project, parsed, self.journal, allow_config=self.options.allow_config)
                problem = self._weakened_tests()
                if problem:
                    self.journal.restore(before)
                    raise edits.EditError(problem)
            except edits.EditError as exc:
                feedback = str(exc)
                if record['truncated']:
                    feedback += ' Your reply was cut off: make smaller edits.'
                record['edit_error'] = feedback
                self._emit('edit_rejected', 'Edits could not be applied', detail=feedback[:600])
                continue
            record['touched'] = touched
            self._emit('edit', f'Edited {len(touched)} file(s)', name='file_changed', files=touched)
            if not self.plan['runnable']:
                return self._report('unverified', 'Edits applied, but this project has nothing that can check them.', checks, started, baseline)
            checks = self._verify(f'Attempt {attempt}: running checks')
            if checks['state'] == 'cancelled':
                return self._report('cancelled', 'Stopped by you. Changes were rolled back.', checks, started, baseline)
            if checks['state'] == 'passed':
                extra = declared_name_diagnostics(self.task, self.project) + coherence.interface_diagnostics(self.project)
                if extra:
                    checks = {**checks, 'state': 'failed', 'diagnostics': [*checks.get('diagnostics', []), *extra]}
            proved = checks.get('built') or checks.get('tested') or checks.get('ran')
            if checks['state'] == 'passed' and not any(s.blocked for s in self.plan['steps']) and proved:
                return self._finish(checks, last_reply, started, baseline)
            if checks['state'] == 'passed':
                proven = 'Static checks' if checks.get('static') else 'Available checks'
                blocked = '; '.join(b['detail'] for b in checks['blocked']) or 'No build, test, or runtime step exists for this project.'
                return self._report('partially_verified', f'{proven} passed. Not proven: {blocked}', checks, started, baseline)
            current = list(checks['diagnostics'])
            errors = diag.error_count(current)
            mark = diag.fingerprint(current)
            seen[mark] = seen.get(mark, 0) + 1
            record['errors'], record['fingerprint'] = errors, mark
            if best_errors is None or errors <= best_errors:
                best_errors, best_snapshot, best_diagnostics = errors, self.journal.snapshot(), current
                self._emit('checkpoint', f'Saved this attempt. It has {errors} error(s).', name='checkpoint_saved', errors=errors)
            elif errors > best_errors * 2:
                # A repair that makes things much worse is not a base to build on.
                self.journal.restore(best_snapshot)
                self._emit('revert', f'Attempt {attempt} made things worse: went from {best_errors} to {errors} errors. Reverted it.')
                last_reply, current = '', best_diagnostics
            if seen[mark] >= 3:
                return self._report('stuck', 'The same failure kept coming back after a changed approach.', checks, started, baseline)
            stuck = seen[mark] == 2 or mark in known
        if not any(a.get('touched') for a in self.attempts):
            return self._report('no_change', 'The model never produced an edit that could be applied.', checks, started, baseline)
        return self._report('failed', f'Checks still fail after {self.options.max_attempts} attempts.', checks, started, baseline)

    def _success_detail(self, checks: dict) -> str:
        names = ', '.join(s['name'] for s in checks['steps'] if s['status'] == 'passed')
        return f'Passed: {names}.'

    def _finish(self, checks: dict, last_reply: str, started: float, baseline: dict | None) -> dict:
        """The project's checks passed. With edge tests on, that earns `verified`
        only once Sumi's own attempt to break the change has also passed."""
        if not self.options.edge_tests:
            return self._report('verified', self._success_detail(checks), checks, started, baseline)
        if not checks.get('tested'):
            self.edge = {'state': 'skipped', 'detail': 'This project has no test step that could run new tests.'}
            return self._report('verified', self._success_detail(checks), checks, started, baseline)
        self._emit('edge_start', 'Trying to break the change with new tests', state='verified')
        result, final = self._edge_round(checks, last_reply, started)
        self.edge = result
        if result['state'] == 'cancelled':
            return self._report('cancelled', 'Stopped by you. Changes were rolled back.', final, started, baseline)
        if result['state'] == 'passed':
            names = ', '.join(result['files'])
            return self._report('verified', f'{self._success_detail(final)} Edge-case tests added and passing in {names}.', final, started, baseline)
        return self._report('partially_verified', f'The project checks passed, but the edge-case step did not finish: {result["detail"]} '
                            'Its edits were undone.', final, started, baseline)

    def _weakened_tests(self) -> str:
        """A repair may add tests. It may not delete or rewrite one that was already there.

        A task that names the file is allowed to change it.
        """
        task = self.task or ''
        for name, old in self._original_tests.items():
            if name in task or Path(name).name in task:
                continue
            path = self.project / name
            try:
                current = path.read_text('utf-8', 'replace')
            except OSError:
                return f'{name}: you removed a test file. Fix the code instead of deleting the test.'
            if not _lines_kept(old, current):
                return f'{name}: you removed or changed a test line that was already there. Fix the code instead.'
        # Rust and others keep the assertion in the same file as the function.
        # Changing that function is allowed. Changing the assertion is not.
        for name, old_bytes in self.journal.before.items():
            if not isinstance(old_bytes, (bytes, bytearray)) or name in task or Path(name).name in task:
                continue
            if b'\0' in old_bytes[:8192]:
                continue
            asserts = _assertion_lines(old_bytes.decode('utf-8', 'replace'))
            if not asserts:
                continue
            path = self.project / name
            try:
                current = path.read_text('utf-8', 'replace')
            except OSError:
                return f'{name}: you removed a file that contains an assertion. Fix the code instead.'
            if not _lines_kept('\n'.join(asserts), current):
                return f'{name}: you removed or changed an assertion that was already there. Fix the code instead.'
        return ''

    def _test_files(self) -> dict[str, str]:
        """Text test files on disk now. Build output with a test-like name is not one."""
        found = {}
        for item in platforms._walk(self.project):
            name = item.as_posix()
            if not is_test_path(name):
                continue
            try:
                if (self.project / item).stat().st_size > TEST_FILE_LIMIT:
                    continue
                raw = (self.project / item).read_bytes()
            except OSError:
                continue
            if b'\0' not in raw[:8192]:
                found[name] = raw.decode('utf-8', 'replace')
        return found

    def _written_tests(self) -> dict[str, str]:
        """Test files this run wrote. Every model edit goes through the journal."""
        found = {}
        for name in self.journal.written:
            if is_test_path(name) and (self.project / name).is_file():
                found[name] = (self.project / name).read_text('utf-8', 'replace')
        return found

    def _edge_problem(self, existing: dict[str, str]) -> str:
        """Why this reply cannot count as adding tests, if anything."""
        now = self._written_tests()
        for name, body in now.items():
            if name in existing and not _lines_kept(existing[name], body):
                return f'{name}: you changed or removed lines of a test that existed before this step. Only add to it.'
        if not any(body != existing.get(name) for name, body in now.items()):
            return 'This reply added no tests. Add tests for what the existing ones miss.'
        return ''

    def _not_run(self, existing: dict[str, str]) -> list[str]:
        """New test files the project's own test command never reads. A passing
        run says nothing about them, so each is replaced by unparsable text in
        turn: the checks must then fail."""
        ignored = []
        for name in sorted(set(self._written_tests()) - set(existing)):
            target = self.project / name
            original = target.read_bytes()
            try:
                target.write_bytes(DISCOVERY_PROBE)
                outcome = platforms.run_steps(self.project, self.plan['steps'], cancel=self.cancel)
            finally:
                target.write_bytes(original)
            if outcome['state'] == 'passed':
                ignored.append(name)
        return ignored

    def _review_tests(self, existing: dict[str, str]) -> tuple[list[str] | None, list[str]]:
        """Ask a reviewer that cannot see the code whether the new tests are right.
        Returns the wrong lines, an empty list for none, or None without a verdict."""
        lines = [line for name, body in sorted(self._written_tests().items()) for line in oracle.added_lines(existing.get(name, ''), body)]
        if not lines:
            return [], lines
        key = '\n'.join(lines)
        if key in self.reviewed:
            return self.reviewed[key], lines
        self._emit('generate', 'Reviewing the new tests against the task, without the code')
        try:
            reply = self.client.complete(oracle.review_messages(self.task, lines), cancel=self.cancel, think=True, num_predict=self.options.edge_review_predict)
        except RuntimeError:
            return None, lines
        self.attempts.append({'attempt': len(self.attempts) + 1, 'round': 'edge-review', **{k: v for k, v in reply.items() if k != 'text'}})
        shown = lines[:oracle.REVIEW_LINE_LIMIT]
        numbers = oracle.parse_verdict(reply.get('text', ''), len(shown))
        wrong = None if numbers is None else [shown[n - 1] for n in numbers]
        self.reviewed[key] = wrong
        self.edge_notes['reviews'] = self.edge_notes.get('reviews', 0) + 1
        self.edge_notes.setdefault('rejected', []).extend(line.strip()[:200] for line in wrong or [])
        if wrong is None:
            self.edge_notes['no_verdict'] = self.edge_notes.get('no_verdict', 0) + 1
        return wrong, lines

    def _code_changed_since(self, start: dict) -> list[str]:
        now = self.journal.snapshot()
        return [name for name, body in now.items() if not is_test_path(name) and body != start.get(name, self.journal.before.get(name))]

    def _restore_code(self, start: dict):
        snapshot = self.journal.snapshot()
        for name in self._code_changed_since(start):
            if name in start:
                snapshot[name] = start[name]
            else:
                snapshot.pop(name)
        self.journal.restore(snapshot)

    def _surviving_mutants(self) -> list[dict]:
        """Break the changed code a little at a time. Tests that still pass on most
        breaks would pass on wrong code too. Files always go back, even on Stop."""
        survivors, tried = [], 0
        for name in self.journal.changed():
            if is_test_path(name) or not oracle.is_code_file(name):
                continue
            target = self.project / name
            original = target.read_bytes()
            old = (self.journal.before.get(name) or b'').decode('utf-8', 'replace')
            for mutant in oracle.mutants(old, original.decode('utf-8', 'replace')):
                if self.cancel.is_set():
                    break
                tried += 1
                try:
                    target.write_bytes(oracle.apply_mutant(original.decode('utf-8', 'replace'), mutant).encode('utf-8'))
                    outcome = platforms.run_steps(self.project, self.plan['steps'], cancel=self.cancel)
                finally:
                    target.write_bytes(original)
                if outcome['state'] == 'passed':
                    survivors.append({**mutant, 'file': name})
        self.edge_notes['mutants'] = tried
        self.edge_notes['mutants_survived'] = len(survivors)
        # One survivor can be an equivalent change. Most surviving is a weak test.
        return survivors if tried >= 2 and len(survivors) * 2 > tried else []

    def _edge_round(self, passed: dict, last_reply: str, started: float) -> tuple[dict, dict]:
        start = self.journal.snapshot()
        existing = self._test_files()
        current, feedback, detail, used = [], '', 'No usable tests were added.', 0
        for attempt in range(1, self.options.edge_attempts + 1):
            if self.cancel.is_set():
                return {'state': 'cancelled'}, passed
            if time.monotonic() - started > self.options.deadline_seconds:
                detail = 'Ran out of time.'
                break
            changed = self.journal.changed()
            touched = changed + [n for n in existing if n not in changed][:4]
            self._refresh_index()
            messages, info = context.build_messages(self.project, self.task, platforms=self.plan['platforms'], diagnostics=current, touched=touched,
                                                    last_reply=last_reply, feedback=feedback, budget=self.options.budget,
                                                    rule=context.EDGE_REPAIR_RULE, instruction='' if current else context.EDGE_RULE,
                                                    recall=self.options.recall, checks=self.plan['steps'], snapshot=self.snapshot,
                                                    disk_wins=True)
            self._emit('generate', f'Edge cases {attempt}: writing tests', attempt=attempt, context=info)
            try:
                reply = self._ask(messages, self.options.edge_think)
            except RuntimeError as exc:
                detail = str(exc)
                break
            record = {'attempt': len(self.attempts) + 1, 'round': 'edge', **{k: v for k, v in reply.items() if k != 'text'}, 'context': info}
            self.attempts.append(record)
            used += 1
            if self.cancel.is_set():
                return {'state': 'cancelled'}, passed
            if reply.get('done_reason') == 'length':
                # The measured runaway spent 4,000 tokens per try and never closed a block.
                detail = 'The reply ran past the length limit while writing tests.'
                break
            last_reply, feedback = reply['text'], ''
            before_attempt = self.journal.snapshot()
            try:
                parsed = edits.parse(reply['text'])
                if not parsed:
                    raise edits.EditError('No SEARCH/REPLACE blocks were found. Reply with edits only.')
                record['touched'] = edits.apply(self.project, parsed, self.journal, allow_config=self.options.allow_config)
                problem = self._edge_problem(existing)
                if current and not problem and all(self.journal.snapshot().get(n) == before_attempt.get(n) for n in record['touched']):
                    problem = 'Your last edit changed nothing, so the same check still fails. Change the code or the test you added.'
                if problem:
                    self.journal.restore(before_attempt)
                    raise edits.EditError(problem)
            except edits.EditError as exc:
                feedback = detail = str(exc)
                record['edit_error'] = feedback
                self._emit('edit_rejected', 'Edits could not be used', detail=feedback[:600])
                continue
            self._emit('edit', f'Edited {len(record["touched"])} file(s)', files=record['touched'])
            checks = self._verify(f'Edge cases {attempt}: running checks')
            if checks['state'] == 'cancelled':
                return {'state': 'cancelled'}, checks
            names = (declared_name_diagnostics(self.task, self.project) + coherence.interface_diagnostics(self.project)
                     if checks['state'] == 'passed' else [])
            if names:
                checks = {**checks, 'state': 'failed', 'diagnostics': [*checks.get('diagnostics', []), *names]}
            review = self.options.edge_review and (checks['state'] != 'passed' or bool(self._code_changed_since(start)))
            wrong, _ = self._review_tests(existing) if review else ([], [])
            if self.cancel.is_set():
                return {'state': 'cancelled'}, checks
            if wrong:
                # A wrong expected value must not get to bend the code toward it.
                self._restore_code(start)
                listed = '\n'.join(line.strip()[:200] for line in wrong[:6])
                current = [diag.Diagnostic('test review', 'error', 'A reviewer working from the task alone says these test lines expect the wrong result. '
                                           f'Work the answer out by hand from the task, then correct or remove them. Do not change the code for them.\n{listed}', source='review')]
                detail = 'The added tests were reviewed and still expected the wrong result.'
                record['test_review'] = 'wrong'
                self._emit('edit_rejected', 'A new test expects the wrong result', detail=listed[:600])
                continue
            if checks['state'] != 'passed':
                current = list(checks['diagnostics'])
                detail = (diag.errors_first(current)[0].message if current else 'A check failed.')[:400]
                record['errors'] = diag.error_count(current)
                continue
            ignored = self._not_run(existing)
            if self.cancel.is_set():
                return {'state': 'cancelled'}, checks
            if ignored:
                self.journal.restore(before_attempt)
                current, feedback = [], f'The test command does not run {", ".join(ignored)}. Add the tests to an existing test file instead.'
                detail = feedback
                record['edit_error'] = feedback
                self._emit('edit_rejected', 'New tests were never run', detail=feedback)
                continue
            survivors = self._surviving_mutants() if self.options.edge_review else []
            if self.cancel.is_set():
                return {'state': 'cancelled'}, checks
            if survivors:
                sample = survivors[0]
                current = [diag.Diagnostic('test strength', 'error', 'The tests still pass when the code is deliberately broken, so they would pass on wrong code too. '
                                           f'In {sample["file"]} line {sample["line"]}, they pass with:\n{sample["mutated"].strip()[:200]}\ninstead of:\n{sample["original"].strip()[:200]}\n'
                                           'Add a test from the task that fails on that change. Do not change the code.', sample['file'], sample['line'], source='mutation')]
                detail = 'The added tests still passed when the code was deliberately broken.'
                record['test_strength'] = 'weak'
                self._emit('edit_rejected', 'The new tests are too weak', detail=detail)
                continue
            now = self._written_tests()
            return {**self.edge_notes, 'state': 'passed', 'attempts': used, 'files': sorted(n for n, body in now.items() if body != existing.get(n)),
                    'new_files': sorted(set(now) - set(existing))}, checks
        conflicts = self.journal.restore(start)
        return {**self.edge_notes, 'state': 'failed', 'attempts': used, 'detail': detail, 'rollback_conflicts': conflicts}, passed
