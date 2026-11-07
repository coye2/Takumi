"""SEARCH/REPLACE edits with a recoverable journal.

The format is the one aider popularised (Apache-2.0); this is an independent
implementation. A narrow ladder of match relaxations absorbs the whitespace
drift typical of local models, and anything ambiguous goes back to the model
as feedback instead of being guessed at.
"""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import os
from pathlib import Path
import re
import tempfile

HEAD = re.compile(r'^\s*<{5,9}\s*SEARCH\s*$')
DIVIDER = re.compile(r'^\s*={5,9}\s*$')
TAIL = re.compile(r'^\s*>{5,9}\s*REPLACE\s*$')
FENCE = re.compile(r'^\s*(?:```|~~~)')
THINK = re.compile(r'<think>.*?</think>', re.S)
PROTECTED_PARTS = {'.git', '.ssh', '.gnupg'}
PROTECTED_NAMES = re.compile(r'(?i)^(?:\.env(?:\..+)?|.*\.(?:pem|key|pfx|p12|jks|keystore)|id_rsa.*|\.npmrc|\.netrc|credentials(?:\.json)?)$')
# These can make the verifier itself run something different, so a model edit
# to them is held for the owner unless the run explicitly allows it.
CONFIG_NAMES = re.compile(r'(?i)^(?:package\.json|.*\.csproj|.*\.sln|.*\.slnx|directory\.build\.(?:props|targets)|cmakelists\.txt|.*\.cmake|makefile|build\.gradle(?:\.kts)?|settings\.gradle(?:\.kts)?|gradlew(?:\.bat)?|pyproject\.toml|setup\.py|conftest\.py|package\.swift|.*\.pbxproj)$')
FUZZY_RATIO = 0.92
_ELISION = re.compile(r'^\s*\.\.\. (?:lines \d+-\d+ of \d+|omitted middle|rest of file omitted) \.\.\.\s*$', re.M)


class EditError(ValueError):
    """A problem the model can fix; the message is written for it."""


@dataclass(frozen=True)
class Edit:
    path: str
    search: str
    replace: str


def parse(text: str) -> list[Edit]:
    lines = THINK.sub('', text or '').replace('\r\n', '\n').split('\n')
    edits, i, last = [], 0, ''
    while i < len(lines):
        if not HEAD.match(lines[i]):
            i += 1
            continue
        path, back = '', i - 1
        while back >= 0 and back >= i - 3:
            candidate = lines[back].strip()
            if candidate and not FENCE.match(candidate):
                path = _path_from(candidate)
                break
            back -= 1
        path = path or last
        if not path:
            raise EditError('Each SEARCH block needs the file path on the line directly above it.')
        i += 1
        search = []
        while i < len(lines) and not DIVIDER.match(lines[i]):
            search.append(lines[i])
            i += 1
        i += 1
        replace = []
        while i < len(lines) and not TAIL.match(lines[i]):
            replace.append(lines[i])
            i += 1
        if i >= len(lines):
            raise EditError(f'The block for {path} is missing its >>>>>>> REPLACE line.')
        edits.append(Edit(path, '\n'.join(search), '\n'.join(replace)))
        last = path
        i += 1
    return edits


def _path_from(line: str) -> str:
    line = line.strip().strip('`*').strip()
    line = re.sub(r'(?i)^(?:file(?:name|path)?|path)\s*:\s*', '', line).strip('`*: ').strip()
    return line.replace('\\', '/') if ' ' not in line and re.search(r'[\\/.]|^[A-Z]\w+$', line) else ''


def safe_target(project: Path, relative: str, *, allow_config: bool = False) -> Path:
    name = relative.replace('\\', '/').strip()
    if not name or name.startswith('/') or re.match(r'^[A-Za-z]:', name) or '\0' in name:
        raise EditError(f'{relative}: use a path relative to the project root.')
    parts = [p for p in name.split('/') if p not in ('', '.')]
    if '..' in parts or PROTECTED_PARTS & {p.lower() for p in parts} or PROTECTED_NAMES.match(parts[-1]):
        raise EditError(f'{relative}: this path is off limits.')
    if not allow_config and CONFIG_NAMES.match(parts[-1]):
        raise EditError(f'{relative}: build configuration needs owner approval. Solve this in the source files.')
    root = Path(project).resolve()
    target = root.joinpath(*parts).resolve()
    if target != root and root not in target.parents:
        raise EditError(f'{relative}: resolves outside the project.')
    return target


def _indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]


def _segments(text: str) -> list[str]:
    """Pieces of a window with the elision banners taken out."""
    parts, buf = [], []
    for line in text.split('\n'):
        if _ELISION.match(line):
            chunk = '\n'.join(buf).strip('\n')
            if chunk.strip():
                parts.append(chunk)
            buf = []
        else:
            buf.append(line)
    chunk = '\n'.join(buf).strip('\n')
    if chunk.strip():
        parts.append(chunk)
    return parts


def _splice(body: str, search: str, replace: str, path: str) -> str:
    """Return body with one occurrence replaced. Relaxations only ever apply
    when they select exactly one location."""
    if _ELISION.search(search) or _ELISION.search(replace):
        # The prompt hides the middle of a long function. A copied banner is not a line of the file.
        left, right = _segments(search), _segments(replace)
        if len(left) != len(right):
            raise EditError(f'{path}: the SEARCH and REPLACE sides do not line up once the omitted-line banners are removed. Quote one contiguous excerpt.')
        changed = [(old, new) for old, new in zip(left, right) if old != new]
        if not changed:
            return body
        for old, new in changed:
            body = _splice(body, old, new, path)
        return body
    count = body.count(search) if search else 0
    if count == 1:
        return body.replace(search, replace, 1)
    if count > 1:
        raise EditError(f'{path}: the SEARCH text appears {count} times. Include more surrounding lines.')
    have, want = body.split('\n'), search.split('\n')
    while want and not want[-1].strip():
        want.pop()
    while want and not want[0].strip():
        want.pop(0)
    if not want:
        raise EditError(f'{path}: SEARCH is empty but the file exists. Quote the lines to change.')
    size = len(want)
    for normal, reindent in ((str.rstrip, False), (str.strip, True)):
        target = [normal(x) for x in want]
        hits = [i for i in range(len(have) - size + 1) if [normal(x) for x in have[i:i + size]] == target]
        if len(hits) > 1:
            raise EditError(f'{path}: the SEARCH text matches {len(hits)} places. Include more surrounding lines.')
        if hits:
            return _replace_lines(have, hits[0], size, want, replace.split('\n'), reindent)
    wanted = '\n'.join(want)
    starts = range(max(1, len(have) - size + 1))
    near = sorted(starts, key=lambda i: difflib.SequenceMatcher(None, '\n'.join(have[i:i + size]), wanted).quick_ratio(), reverse=True)[:6]
    scored = sorted(((difflib.SequenceMatcher(None, '\n'.join(have[i:i + size]), wanted).ratio(), i) for i in near), reverse=True)
    if scored[0][0] >= FUZZY_RATIO and (len(scored) < 2 or scored[1][0] < FUZZY_RATIO):
        return _replace_lines(have, scored[0][1], size, want, replace.split('\n'), True)
    hint = '\n'.join(have[scored[0][1]:scored[0][1] + size][:12])
    raise EditError(f'{path}: SEARCH text not found. Copy lines exactly from the file. Closest lines:\n{hint}')


def _replace_lines(have, start, size, want, replacement, reindent):
    if reindent and replacement:
        old, new = _indent(want[0]), _indent(have[start])
        if old != new:
            replacement = [new + x[len(old):] if x.startswith(old) else x for x in replacement]
    return '\n'.join(have[:start] + replacement + have[start + size:])


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


class Journal:
    """Pre-images of everything a run touches, so a failed or stopped run can
    leave the project exactly as it found it."""

    def __init__(self, project: Path):
        self.project = Path(project).resolve()
        self.before: dict[str, bytes | None] = {}
        self.written: dict[str, str] = {}

    def snapshot(self) -> dict[str, bytes | None]:
        return {name: _read(self.project / name) for name in self.written}

    def write(self, name: str, target: Path, content: bytes):
        if name not in self.before:
            self.before[name] = _read(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=target.parent, prefix='.takumi-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(content)
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        self.written[name] = hashlib.sha256(content).hexdigest()

    def restore(self, snapshot: dict[str, bytes | None]) -> list[str]:
        """Return to a snapshot. Files someone else edited meanwhile are kept
        and reported instead of being overwritten."""
        conflicts = []
        for name in list(self.written):
            target = self.project / name
            current = _read(target)
            if current is not None and hashlib.sha256(current).hexdigest() != self.written[name]:
                conflicts.append(name)
                continue
            wanted = snapshot[name] if name in snapshot else self.before.get(name)
            if wanted is None:
                if target.exists():
                    target.unlink()
                self.written.pop(name)
            else:
                target.write_bytes(wanted)
                self.written[name] = hashlib.sha256(wanted).hexdigest()
        return conflicts

    def rollback(self) -> list[str]:
        return self.restore(dict(self.before))

    def changed(self) -> list[str]:
        return sorted(n for n in self.written if _read(self.project / n) != self.before.get(n))

    def diff(self, limit: int = 60000) -> str:
        chunks = []
        for name in self.changed():
            old = (self.before.get(name) or b'').decode('utf-8', 'replace').splitlines()
            new = (_read(self.project / name) or b'').decode('utf-8', 'replace').splitlines()
            chunks.extend(difflib.unified_diff(old, new, 'a/' + name, 'b/' + name, lineterm=''))
        return '\n'.join(chunks)[:limit]


def apply(project: Path, edits: list[Edit], journal: Journal, *, allow_config: bool = False) -> list[str]:
    """All-or-nothing: every edit is resolved in memory before one byte is written."""
    root = Path(project).resolve()
    pending: dict[str, tuple[Path, str, bool]] = {}
    for edit in edits:
        target = safe_target(root, edit.path, allow_config=allow_config)
        name = target.relative_to(root).as_posix()
        if target.is_dir():
            raise EditError(f'{edit.path}: this is a directory.')
        if name in pending:
            body, crlf = pending[name][1], pending[name][2]
        elif target.exists():
            raw = target.read_bytes()
            if b'\0' in raw[:8192]:
                raise EditError(f'{edit.path}: binary files cannot be edited here.')
            decoded = raw.decode('utf-8-sig', 'replace')
            crlf, body = '\r\n' in decoded, decoded.replace('\r\n', '\n')
        else:
            body, crlf = None, False
        if body is None:
            if edit.search.strip():
                raise EditError(f'{edit.path}: this file does not exist. Use an empty SEARCH to create it.')
            body = edit.replace.rstrip('\n') + '\n'
        elif not edit.search.strip() and not body.strip():
            body = edit.replace.rstrip('\n') + '\n'
        else:
            body = _splice(body, edit.search, edit.replace, edit.path)
        pending[name] = (target, body, crlf)
    for name, (target, body, crlf) in pending.items():
        journal.write(name, target, (body.replace('\n', '\r\n') if crlf else body).encode('utf-8'))
    return sorted(pending)
