"""Spend a small context window on what a repair actually needs.

Sections are ordered from most stable to least stable. Ollama reuses its cache
for a shared prompt prefix, so keeping files and the map ahead of the changing
diagnostics avoids re-evaluating them on every repair attempt.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from takumi.source_index import digest

from . import repomap
from .diagnostics import Diagnostic, errors_first
from .repomap import CHARS_PER_TOKEN

# The training dataset renders tasks through this same prompt, so what Sumi is
# trained on is what it sees when serving. Changing the rendered prompt invalidates
# vibe-coding datasets, whose validator hash covers it.
SYSTEM = """You are Sumi, a careful software engineer working in a real project.
Change only what the task needs. Follow the style of the surrounding code.
Reply with edits only, each in exactly this form:

path/to/file.ext
<<<<<<< SEARCH
exact existing lines
=======
replacement lines
>>>>>>> REPLACE

To create a file, leave SEARCH empty. Copy SEARCH lines exactly from the file."""
REPAIR_RULE = 'The checks failed. Fix the first error, which is the cause. Do not weaken or delete tests.'
REANCHOR_RULE = 'Your last repair did not change the failure. Your assumption is wrong: reread the code around the error and take a different approach.'
# Sent only after the project's checks pass, so neither text is part of the
# prompt the training dataset renders.
EDGE_RULE = """The project's checks pass, but they may not cover what the task asks for. Before this is finished, try to break your change.
Reread the task sentence by sentence, then add tests for what the existing tests miss: every behaviour the task states, empty input, a single item, boundaries, repeated values, and the state left behind after an error.
Test only behaviour the task states or the existing code clearly implies. Do not invent requirements.
Add the tests to an existing test file when you can. Otherwise create a file beside it, in the same framework and style, named so the same test command finds it. Do not change or remove existing tests.
If you already see a bug, fix it in the same reply. Reply with edits only."""
EDGE_REPAIR_RULE = """A check failed after you added tests. Reread the task and decide which side is wrong.
If the error is a compile or syntax error in your test, fix the test. If the task requires the behaviour your test expects, change the code so that exact input gives the expected result, and keep what already works. If the task does not say that, correct or remove the test you added. Do not change tests that existed before this step."""


@dataclass(frozen=True)
class Budget:
    """num_ctx must hold the prompt plus the reply. Measured on the 4060 Ti 16 GB:
    32K stays fully on the GPU at 24.6 tok/s against 25.9 at 16K, using 10.9 GiB.
    See training/evidence/2026-11-04/context-sweep.json."""
    num_ctx: int = 32768
    num_predict: int = 4096
    map_tokens: int = 2400
    file_tokens: int = 16000
    diagnostics_tokens: int = 1500

    @property
    def prompt_tokens(self) -> int:
        return self.num_ctx - self.num_predict


REI_BUDGET = Budget(num_ctx=8192, num_predict=1200, map_tokens=600, file_tokens=3600, diagnostics_tokens=500)


def budget_for(model: str) -> Budget:
    return REI_BUDGET if 'rei' in (model or '').lower() or ':9b' in (model or '').lower() else Budget()


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def fence(path: str, body: str) -> str:
    return f'--- {path}\n{body.rstrip()}\n'


def function_span(text: str, line: int) -> tuple[int, int] | None:
    """0-based half-open span of the function or type that starts at the 1-based line."""
    source = text.split('\n')
    start = line - 1
    if not 0 <= start < len(source):
        return None
    row = source[start]
    stripped = row.lstrip()
    if re.match(r'(?:async\s+)?(?:def|class)\s+\w', stripped):
        indent = len(row) - len(stripped)
        end = start + 1
        while end < len(source) and end - start < 400:
            nxt = source[end]
            if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                break
            end += 1
        return (start, end) if end > start else None
    return _brace_span(source, start)


def _brace_span(source: list[str], start: int) -> tuple[int, int] | None:
    depth, seen, quote, escape, comment = 0, False, '', False, False
    limit = min(len(source), start + 400)
    for index in range(start, limit):
        line, column = source[index], 0
        while column < len(line):
            ch = line[column]
            if comment:
                if ch == '*' and column + 1 < len(line) and line[column + 1] == '/':
                    comment = False
                    column += 2
                    continue
                column += 1
                continue
            if quote:
                if escape:
                    escape = False
                elif ch == '\\' and quote != '`':
                    escape = True
                elif ch == quote:
                    quote = ''
                column += 1
                continue
            if ch in ('"', "'", '`'):
                quote = ch
                column += 1
                continue
            if ch == '/' and column + 1 < len(line) and line[column + 1] == '/':
                break
            if ch == '/' and column + 1 < len(line) and line[column + 1] == '*':
                comment = True
                column += 2
                continue
            if ch == '{':
                depth += 1
                seen = True
            elif ch == '}' and seen:
                depth -= 1
                if depth == 0:
                    return (start, index + 1)
            column += 1
        if not seen and index > start + 12:
            return None
    return None


def _capped_span(span: tuple[int, int]) -> list[tuple[int, int]]:
    low, high = span
    if high - low <= 140:
        return [(low, high)]
    return [(low, low + 70), (high - 50, high)]


def _window(text: str, lines: list[int], limit: int, regions: list[tuple[int, int]] | None = None) -> str:
    """Whole file when it fits, otherwise the failure's neighborhood and the
    whole function a definition starts. Elisions are explicit so SEARCH is not misled."""
    if len(text) <= limit:
        return text
    source = text.split('\n')
    anchors = sorted(set(lines)) or ([] if regions else [1])
    radius = max(8, int(limit / (max(1, len(anchors)) * 2 * 40))) if anchors else 0
    points = [(max(0, line - 1 - radius), min(len(source), line + radius)) for line in anchors]
    for low, high in regions or []:
        points.append((max(0, low), min(len(source), high)))
    spans: list[list[int]] = []
    for low, high in sorted(points):
        if high <= low:
            continue
        if spans and low <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], high)
        else:
            spans.append([low, high])
    if not spans:
        spans = [[0, min(len(source), 8)]]
    parts, used = [], 0
    for low, high in spans:
        block = _fit('\n'.join(source[low:high]), max(0, limit - used))
        if not block:
            break
        used += len(block)
        parts.append(f'... lines {low + 1}-{high} of {len(source)} ...\n{block}')
    return '\n'.join(parts) + '\n... rest of file omitted ...'


def _fit(block: str, room: int) -> str:
    """Keep the start and the return when a function does not fit the window."""
    if len(block) <= room:
        return block
    if room < 160:
        return block[:room]
    marker = '\n... omitted middle ...\n'
    head_end = block.rfind('\n', 0, int(room * 0.55))
    if head_end < 40:
        return block[:room]
    tail_at = len(block) - (room - head_end - len(marker))
    tail_start = block.find('\n', tail_at)
    if tail_start == -1 or tail_start <= head_end:
        return block[:room]
    return block[:head_end] + marker + block[tail_start + 1:]


_NAMED_FILE = re.compile(r'(?:[\w.+-]+/)*[\w.+-]+\.[A-Za-z0-9]{1,8}')


def _named_files(project: Path, diagnostics: list[Diagnostic]) -> list[str]:
    """Source files a failure message names, besides the file the diagnostic is attached to."""
    found, root = [], project.resolve()
    for item in diagnostics:
        for match in _NAMED_FILE.findall(item.message or ''):
            name = match[2:] if match.startswith('./') else match
            if name in found or len(found) >= 4:
                continue
            path = project / name
            try:
                if not path.is_file() or not path.resolve().is_relative_to(root):
                    continue
            except OSError:
                continue
            if path.suffix.lower() in repomap.LANGUAGE:
                found.append(name)
    return found


def _file_tokens(budget: Budget, diagnostics: list[Diagnostic]) -> int:
    """Diagnostic room that this turn does not use is spent on source instead."""
    rendered = '\n'.join(item.render() for item in diagnostics)
    spare = max(0, budget.diagnostics_tokens - estimate_tokens(rendered))
    return budget.file_tokens + spare


_QUOTED_NAME = re.compile(r'''['"`]([A-Za-z_][A-Za-z0-9_]{2,})['"`]''')
_DOTTED_NAME = re.compile(r'\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b')
_CODE_NAME = re.compile(r'\b(?:[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+|[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+)\b')
_FILE_SUFFIX = {'py', 'js', 'mjs', 'cjs', 'ts', 'tsx', 'jsx', 'cs', 'cpp', 'cc', 'h', 'hpp', 'java', 'kt', 'swift', 'json', 'md', 'xml'}


def _mentioned_symbols(*texts: str) -> set[str]:
    """Names a failure uses as code: quotes, dotted names, snake_case, and CamelCase.

    A sentence such as "value must be 2" names nothing. A filename such as a.py names nothing.
    """
    found = set()
    for text in texts:
        if not text:
            continue
        found.update(_QUOTED_NAME.findall(text))
        for dotted in _DOTTED_NAME.findall(text):
            parts = dotted.split('.')
            if parts[-1] in _FILE_SUFFIX:
                continue
            found.update(part for part in parts if len(part) >= 3)
        found.update(_CODE_NAME.findall(text))
    return {name for name in found if not name.endswith(('Error', 'Exception', 'Warning'))}


def _text_files(project: Path, text: str) -> set[str]:
    """Source paths named in text that exist in the project."""
    found, root = set(), project.resolve()
    for match in _NAMED_FILE.findall(text or ''):
        name = match[2:] if match.startswith('./') else match
        path = project / name
        try:
            if path.is_file() and path.resolve().is_relative_to(root) and path.suffix.lower() in repomap.LANGUAGE:
                found.add(name)
        except OSError:
            continue
    return found


def symbol_anchors(snapshot: dict | None, project: Path, task: str, mention: str = '') -> dict[str, list[int]]:
    """Lines of a symbol the task or a failure names, from an index whose file hash still matches disk."""
    anchors, _rejected, _resolved = _symbol_focus(snapshot, project, task, mention)
    return anchors


def _symbol_focus(snapshot: dict | None, project: Path, task: str, mention: str = '') -> tuple[dict[str, list[int]], set[str], set[str]]:
    """Anchors, and definition files a shared name lost to a more specific source.

    One fresh definition file is used as-is. Several files that define the same name
    are not all read: the file the task names wins, otherwise the one definition that
    other files actually call. A call inside the defining file does not count. When
    those disagree or no outside call exists, the name is left to the text map.
    A stale hash is skipped. At most four other caller files are added.
    """
    if not snapshot or not isinstance(snapshot.get('units'), dict):
        return {}, set(), set()
    root = Path(project).resolve()
    if snapshot.get('root') != str(root):
        return {}, set(), set()
    tokens = set(re.findall(r'\b[A-Za-z_][A-Za-z0-9_]{2,}\b', task or ''))
    tokens.update(_mentioned_symbols(mention))
    if not tokens:
        return {}, set(), set()
    owners: dict[str, list[tuple[str, int, str]]] = {}
    hashes = snapshot.get('hashes') or {}
    fresh: dict[str, bool] = {}

    def current(name: str) -> bool:
        if name in fresh:
            return fresh[name]
        path = root / name
        try:
            if not path.is_file() or not path.resolve().is_relative_to(root):
                fresh[name] = False
                return False
            fresh[name] = digest(path.read_bytes()) == hashes.get(name)
        except OSError:
            fresh[name] = False
        return fresh[name]

    for name, unit in snapshot['units'].items():
        if not isinstance(unit, dict) or not current(name):
            continue
        for symbol in unit.get('symbols') or []:
            if symbol.get('definition') and symbol.get('name') in tokens and symbol.get('line'):
                owners.setdefault(symbol['name'], []).append((name, int(symbol['line']), symbol.get('id') or ''))
    anchors: dict[str, list[int]] = {}
    defined: set[str] = set()
    rejected: set[str] = set()
    resolved: set[str] = set()
    named = _text_files(Path(project), task)

    def take(places: list[tuple[str, int, str]], chosen: set[str]) -> None:
        for name, line, symbol_id in places:
            if symbol_id and symbol_id not in chosen:
                rejected.add(name)
                continue
            anchors.setdefault(name, []).append(line)
            if symbol_id:
                defined.add(symbol_id)

    for symbol_name, places in owners.items():
        files = {name for name, _, _ in places}
        if len(files) == 1:
            take(places, {symbol_id for _, _, symbol_id in places if symbol_id} or {''})
            resolved.add(symbol_name)
            continue
        by_file = {name for name, _, _ in places if name in named}
        if len(by_file) == 1:
            take(places, {symbol_id for name, _, symbol_id in places if name in by_file and symbol_id})
            resolved.add(symbol_name)
            continue
        id_file = {symbol_id: name for name, _, symbol_id in places if symbol_id}
        targets: set[str] = set()
        for name, unit in snapshot['units'].items():
            if not isinstance(unit, dict) or not current(name):
                continue
            for call in unit.get('calls') or []:
                target = call.get('target') or ''
                if target in id_file and name != id_file[target]:
                    targets.add(target)
        if len(targets) == 1:
            take(places, targets)
            resolved.add(symbol_name)
    added = 0
    for name in sorted(snapshot['units']):
        unit = snapshot['units'][name]
        if not isinstance(unit, dict) or not current(name):
            continue
        lines = [int(call['line']) for call in unit.get('calls') or []
                 if call.get('target') in defined and call.get('line')]
        if not lines:
            continue
        rejected.discard(name)
        if name not in anchors:
            if added >= 4:
                continue
            added += 1
        anchors.setdefault(name, []).extend(lines)
    rejected.difference_update(anchors)
    return anchors, rejected, resolved


def select_files(project: Path, task: str, diagnostics: list[Diagnostic], touched: list[str], budget: Budget,
                 anchors: dict[str, list[int]] | None = None, reject: set[str] | None = None,
                 resolved: set[str] | None = None, mention: str = '') -> dict:
    """Failing and edited files first, then whatever the task's map ranks highest."""
    project = Path(project)
    failing: dict[str, list[int]] = {}
    for item in errors_first(diagnostics):
        if item.file and not Path(item.file).is_absolute() and (project / item.file).is_file():
            failing.setdefault(item.file, [])
            if item.line:
                failing[item.file].append(item.line)
    named = [name for name in _named_files(project, diagnostics) if name not in failing]
    anchors = {name: lines for name, lines in (anchors or {}).items() if (project / name).is_file()}
    indexed = [name for name in anchors if name not in failing and name not in named]
    focus = list(failing) + named + indexed + [t for t in touched if t not in failing and t not in named and t not in indexed]
    # A handful of uncommitted files is the working copy. A tree full of them is not a task.
    dirty = [name for name in repomap.changed_files(project) if name not in focus][:4]
    focus += dirty
    mapped = repomap.build(project, task=task, focus=focus, budget_tokens=budget.map_tokens,
                           resolved=resolved or set(), resolved_files=set(anchors or {}), mention=mention)
    order = focus + [n for n in mapped['files'] if n not in focus]
    ranks = mapped.get('ranks') or {}
    top = max(ranks.values(), default=0)
    remaining, chosen = _file_tokens(budget, diagnostics) * CHARS_PER_TOKEN, []
    for name in order:
        # A shared name resolved to one definition does not also pull the other definition in.
        if name in (reject or set()) and name not in focus:
            continue
        # In a larger tree, a file the task does not name and nothing relevant refers to stays out.
        if name not in focus and mapped.get('indexed', 0) >= 6 and top and ranks.get(name, 0) < top * 0.15:
            continue
        if remaining < 200:
            break
        try:
            text = (project / name).read_text('utf-8', 'replace').replace('\r\n', '\n')
        except OSError:
            continue
        # One huge file must not crowd out the rest of the picture.
        limit = int(min(remaining, max(4000, remaining * (0.6 if name in failing else 0.4))))
        # A failure line and the indexed definition are both read. The failure does not hide the definition.
        definition_lines = [*anchors.get(name, []), *(mapped.get('hits') or {}).get(name, [])]
        regions = []
        for line in definition_lines:
            span = function_span(text, line)
            if span:
                regions.extend(_capped_span(span))
        shown = _window(text, [*failing.get(name, []), *definition_lines], limit, regions)
        chosen.append((name, shown))
        remaining -= len(shown)
        if name not in focus and len(chosen) >= 8:
            break
    full = [name for name, shown in chosen if not shown.startswith('... lines ') and 'rest of file omitted' not in shown]
    mapped = {**mapped, 'text': _without_full_files(mapped.get('text', ''), full)}
    return {'files': chosen, 'map': mapped}


def _without_full_files(text: str, names: list[str]) -> str:
    """Drop map entries whose full source is already in the prompt."""
    if not text or not names:
        return text
    skip, kept, block = set(names), [], []

    def flush():
        if block and block[0].split(':', 1)[0] not in skip:
            kept.append('\n'.join(block))

    for line in text.split('\n'):
        if line and not line.startswith(' '):
            flush()
            block = [line]
        elif block:
            block.append(line)
    flush()
    return '\n'.join(kept)


def format_checks(steps) -> str:
    """The checks this run will execute. Names and blocked reasons only, never a command line."""
    lines = []
    for step in steps or []:
        name, kind, blocked = getattr(step, 'name', ''), getattr(step, 'kind', ''), getattr(step, 'blocked', '')
        if not name:
            continue
        if blocked:
            lines.append(f'- {name} ({kind}): blocked. {blocked}')
        else:
            lines.append(f'- {name} ({kind}): will run')
    if not lines:
        return ''
    return 'Checks this run will execute:\n' + '\n'.join(lines)


def task_prompt(task: str, files: list[tuple[str, str]], *, repo_map: str = '', platforms: list[str] | None = None,
                checks: str = '') -> str:
    """The stable prefix of the user message; shared with the training dataset."""
    parts = []
    if platforms:
        parts.append('Project type: ' + ', '.join(platforms))
    if repo_map:
        parts.append('Repository map:\n' + repo_map)
    if files:
        parts.append('Files:\n' + '\n'.join(fence(path, body) for path, body in files))
    if checks:
        parts.append(checks)
    parts.append('Task: ' + task.strip())
    return '\n\n'.join(parts)


def lesson_for(diagnostics: list[Diagnostic]) -> str:
    """A worked repair for a failure class the served model has already missed.

    Ollama cannot load the MLX adapter, so the lesson rides the repair turn the
    loop already sends. Examples are from the training split, not held-out tasks.
    """
    text = '\n'.join(item.message for item in diagnostics)
    if 'in` operator' in text:
        return ('Worked example, a different function: `key in value` throws on a number. '
                'Comparing value with null does not make it safe. Write `key in Object(value)`.')
    if any(item.step == 'task names' for item in diagnostics):
        return ('Worked example: the task named app.takumi.inbox and the edit wrote a shorter id. '
                'Copy the full reverse-DNS id from the task into the file, unchanged.')
    return ''


def failure_prompt(diagnostics: list[Diagnostic], *, stuck: bool = False, feedback: str = '', budget: Budget = Budget(),
                   rule: str = '', instruction: str = '') -> str:
    ordered = errors_first(diagnostics)
    limit, lines, used = budget.diagnostics_tokens * CHARS_PER_TOKEN, [], 0
    for item in ordered:
        line = item.render()
        if used + len(line) > limit:
            break
        lines.append(line)
        used += len(line)
    parts = [instruction] if instruction else []
    if lines:
        parts.append(rule or (REANCHOR_RULE if stuck else REPAIR_RULE))
        parts.append('First failure first:\n' + '\n'.join(lines))
    lesson = lesson_for(ordered)
    if lesson:
        parts.append(lesson)
    if feedback:
        parts.append('Your edits could not be applied: ' + feedback)
    return '\n\n'.join(parts)


DISK_WINS = 'The files above are what is on disk. Where your previous reply disagrees with those files, the files are right.'


def build_messages(project: Path, task: str, *, platforms: list[str], diagnostics: list[Diagnostic], touched: list[str],
                   last_reply: str = '', stuck: bool = False, feedback: str = '', budget: Budget = Budget(),
                   rule: str = '', instruction: str = '', recall: str = '', checks=None, snapshot: dict | None = None,
                   disk_wins: bool = False) -> tuple[list[dict], dict]:
    mention = '\n'.join(item.message or '' for item in diagnostics)
    anchors, rejected, resolved = _symbol_focus(snapshot, project, task, mention)
    selected = select_files(project, task, diagnostics, touched, budget, anchors, rejected, resolved, mention)
    plan = format_checks(checks)
    user = task_prompt(task, selected['files'], repo_map=selected['map']['text'], platforms=platforms, checks=plan)
    messages = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}]
    follow_up = failure_prompt(diagnostics, stuck=stuck, feedback=feedback, budget=budget, rule=rule, instruction=instruction)
    note = (recall or '').strip()
    if note:
        follow_up = (follow_up + '\n\n' + note) if follow_up else note
    if follow_up:
        if last_reply and disk_wins:
            follow_up = follow_up + '\n\n' + DISK_WINS
        if last_reply:
            messages.append({'role': 'assistant', 'content': last_reply[:6000]})
        messages.append({'role': 'user', 'content': follow_up})
    estimate = sum(estimate_tokens(m['content']) for m in messages)
    while estimate > budget.prompt_tokens and len(selected['files']) > 1:
        # A last resort: the token estimate is approximate and Ollama truncates silently.
        selected['files'].pop()
        messages[1]['content'] = task_prompt(task, selected['files'], repo_map=selected['map']['text'], platforms=platforms, checks=plan)
        estimate = sum(estimate_tokens(m['content']) for m in messages)
    return messages, {'estimated_prompt_tokens': estimate, 'num_ctx': budget.num_ctx, 'files': [n for n, _ in selected['files']],
                      'map_files': selected['map']['indexed'], 'map_partial': selected['map']['partial']}
