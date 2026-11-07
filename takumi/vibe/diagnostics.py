"""Turn raw build and test output into a short, ranked list of causal failures.

Local models repair better from the first real error than from a pasted log,
and a stable fingerprint lets the loop notice when it is going in circles.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re

ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
_PATH = r"(?P<file>(?:[A-Za-z]:)?[^\s:()'\"<>|][^:()'\"<>|\n]*?\.[A-Za-z0-9_+]{1,12})"
PATTERNS = [
    # MSBuild, csc, MSVC, tsc: file(12,5): error CS0103: message
    ('msbuild', re.compile(_PATH + r"\((?P<line>\d+)(?:,(?P<col>\d+))?\)\s*:\s*(?P<severity>fatal error|error|warning)\s+(?P<code>[A-Z]{1,6}\d+)\s*:\s*(?P<message>.+)")),
    # kotlinc / Gradle: e: file:///C:/x/A.kt:12:5 message
    ('kotlin', re.compile(r"^(?P<severity>e|w):\s*(?:file:/+)?" + _PATH + r":(?P<line>\d+):(?P<col>\d+)\s+(?P<message>.+)")),
    # gcc, clang, swiftc: file:12:5: error: message
    ('gnu', re.compile(_PATH + r":(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<severity>fatal error|error|warning)\s*:\s*(?P<message>.+)")),
    # Go: file:12:5: message, with no error: word
    ('go', re.compile(_PATH + r":(?P<line>\d+):(?P<col>\d+):\s+(?P<message>\S.+)")),
    # Go test: file:12: message. Pytest's "file:12: in func" and a bare exception name are not this.
    ('gotest', re.compile(_PATH + r":(?P<line>\d+):\s+(?P<message>(?!(?:in|In)\s)(?![A-Za-z_][\w.]*(?:Error|Exception|Failure)\s*$)\S.+)")),
    # Android resource and manifest merger
    ('aapt', re.compile(r"(?P<severity>ERROR):\s*" + _PATH + r":(?P<line>\d+):\s*(?P<message>.+)")),
    # Linker
    ('linker', re.compile(r"(?P<severity>)(?P<message>undefined reference to .+|unresolved external symbol .+|LNK\d+: .+)")),
    # CMake configuration
    ('cmake', re.compile(r"CMake Error at " + _PATH + r":(?P<line>\d+)(?:\s*\((?P<message>.*)\))?(?P<severity>)")),
]
PY_FRAME = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+)')
PYTEST_FRAME = re.compile(r'^(?P<file>(?:[A-Za-z]:)?[^\s:][^:\n]*?\.[A-Za-z0-9_+]{1,12}):(?P<line>\d+):(?:\d+:)? in \S+')
_JS_FILE = r'(?P<file>(?:[A-Za-z]:)?[^\s:()]+\.(?:[cm]?js|[jt]sx?)):(?P<line>\d+)(?::(?P<col>\d+))?'
JS_FRAME = re.compile(r'(?:at .*?\(?|^\s*)(?:file:/+)?' + _JS_FILE + r'\)?\s*$')
JS_CALL = re.compile(r'\((?:file:/+)?' + _JS_FILE + r'\)')
NODE_LOCATION = re.compile(r"location:\s*'?(?:file:/+)?" + _JS_FILE + r"'?")
# Node 24 lists each failing test as `test at file:line:col`: where the test starts, not the failure.
NODE_TEST_AT = re.compile(r"^\s*test at (?:file:/+)?" + _JS_FILE + r"\s*$")
RUST_PANIC = re.compile(r"panicked at (?:'(?P<quoted>[^']*)', )?(?P<file>[^:\s]+):(?P<line>\d+)(?::(?P<col>\d+))?")
RUSTC_ERROR = re.compile(r'^(?P<severity>error|warning)(?:\[(?P<code>E\d+)\])?: (?P<message>.+)$')
RUSTC_LOC = re.compile(r'^\s*-->\s+(?P<file>[^:\s]+):(?P<line>\d+):(?P<col>\d+)')
ERROR_LINE = re.compile(r"^\s*(?:(?:[A-Za-z_][\w.]*(?:Error|Exception|Failure))\b.*|Error:.*|error:.*|E\s{3}.+)$")
FAILED_TEST = re.compile(r"^(?:FAILED|ERROR)\s+(?P<test>\S+)|^\s*(?:not ok \d+ - |✖ |× |✕ |Failed\s+)(?P<other>.+)$")
NOISE = ('node_modules', 'site-packages', 'dist-packages', 'node:internal', '/usr/lib/', '/usr/include/', 'Microsoft.Common', '/lib/python', 'Python3')


@dataclass(frozen=True)
class Diagnostic:
    step: str
    severity: str
    message: str
    file: str = ''
    line: int = 0
    column: int = 0
    code: str = ''
    source: str = ''

    def public(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        where = self.file + (f':{self.line}' if self.line else '') + (f':{self.column}' if self.column else '')
        return ' '.join(x for x in (where + ':' if where else '', self.code, self.message) if x)


def _clean_file(name: str, project: str = '') -> str:
    name = (name or '').strip().strip('"\'').replace('\\', '/')
    root = project.replace('\\', '/').rstrip('/')
    if root and name.lower().startswith(root.lower() + '/'):
        name = name[len(root) + 1:]
    return name[2:] if name.startswith('./') else name


def _severity(raw: str) -> str:
    return 'warning' if (raw or '').lower() in ('w', 'warning') else 'error'


def parse(output: str, *, step: str = '', project: str = '', limit: int = 40) -> list[Diagnostic]:
    """Parse any supported toolchain's output. Order follows the tool's own, so
    the first entry is the first thing that went wrong rather than the last."""
    found: list[Diagnostic] = []
    seen: set[tuple] = set()
    lines = ANSI.sub('', output or '').replace('\r\n', '\n').split('\n')
    pending_rust = None

    def add(diagnostic: Diagnostic):
        key = (diagnostic.file, diagnostic.line, diagnostic.code, diagnostic.message[:160])
        if key not in seen and len(found) < limit:
            seen.add(key)
            found.append(diagnostic)

    for raw in lines:
        line = raw.strip()
        if not line or len(line) > 2000:
            continue
        located = RUSTC_LOC.match(line)
        if located and pending_rust is not None:
            add(Diagnostic(step, pending_rust['severity'], pending_rust['message'][:600], _clean_file(located['file'], project),
                           int(located['line']), int(located['col']), pending_rust['code'], 'rustc'))
            pending_rust = None
            continue
        rustc = RUSTC_ERROR.match(line)
        if rustc and not rustc['message'].startswith(('could not compile', 'aborting due to')):
            pending_rust = {'severity': rustc['severity'], 'message': rustc['message'].strip(), 'code': rustc['code'] or ''}
            continue
        for source, pattern in PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            values = match.groupdict()
            # MSBuild repeats each error with a trailing [project] suffix.
            message = re.sub(r'\s*\[[^\]]+\.(?:cs|vb|fs|vcx)proj[^\]]*\]\s*$', '', values.get('message') or line).strip()
            add(Diagnostic(step, _severity(values.get('severity', '')), message[:600], _clean_file(values.get('file', ''), project),
                           int(values.get('line') or 0), int(values.get('col') or 0), values.get('code') or '', source))
            break
    # Runtime and test failures have no compiler format. Keep the raise and the
    # project callers above it. A library frame is left out.
    if not any(d.severity == 'error' for d in found):
        message, frames, failures, kinds = '', [], [], set()
        detail: list[str] = []
        panic_lines: list[str] = []
        locations: list[tuple[str, int]] = []
        collect_error = False
        collect_panic = False
        # Node 24 prints an assertion's expected/actual lines after the message, not in a YAML block.
        collect_assert = False
        assert_lines: list[str] = []
        seen_frames: set[tuple[str, int]] = set()
        for raw in lines:
            line = raw.rstrip()
            if collect_panic:
                stripped = line.strip()
                if stripped and not stripped.startswith(('note:', 'failures:')):
                    panic_lines.append(stripped)
                    continue
                collect_panic = False
            if collect_assert:
                stripped = line.strip()
                if not stripped.startswith('at ') and len(assert_lines) < 8:
                    if stripped and not stripped.startswith('^'):
                        assert_lines.append(stripped)
                    continue
                collect_assert = False
            if collect_error:
                if not line.strip() or line.startswith('    ') or line.startswith('\t'):
                    if line.strip():
                        detail.append(line.strip())
                    continue
                collect_error = False
            if line.strip() in {'error: |-', 'stack: |-'}:
                collect_error = line.strip() == 'error: |-'
                continue
            panic = RUST_PANIC.search(line)
            if panic and not any(n in panic['file'].replace('\\', '/') for n in NOISE):
                key = (_clean_file(panic['file'], project), int(panic['line']))
                if key[0] and key not in seen_frames:
                    seen_frames.add(key)
                    frames.append(key)
                    kinds.add('py')
                if panic['quoted']:
                    panic_lines.append(panic['quoted'])
                else:
                    collect_panic = True
                continue
            test_at = NODE_TEST_AT.match(line)
            if test_at and not any(n in test_at['file'].replace('\\', '/') for n in NOISE):
                key = (_clean_file(test_at['file'], project), int(test_at['line']))
                if key[0] and key not in seen_frames and key not in locations:
                    locations.append(key)
                continue
            matched = False
            for kind, pattern in (('py', PY_FRAME), ('py', PYTEST_FRAME), ('js', JS_FRAME), ('js', JS_CALL)):
                match = pattern.search(line)
                if not match or any(n in match['file'].replace('\\', '/') for n in NOISE):
                    continue
                key = (_clean_file(match['file'], project), int(match['line']))
                if key[0] and key not in seen_frames:
                    seen_frames.add(key)
                    frames.append(key)
                    kinds.add(kind)
                matched = True
                break
            if matched:
                continue
            located = NODE_LOCATION.search(line)
            if located and not any(n in located['file'].replace('\\', '/') for n in NOISE):
                key = (_clean_file(located['file'], project), int(located['line']))
                if key[0] and key not in seen_frames:
                    locations.append(key)
                continue
            if ERROR_LINE.match(line) and not message:
                message = line.strip()
                collect_assert = 'ERR_ASSERTION' in message and message.endswith(':')
                if message.startswith('E ') or message.startswith('E\t'):
                    message = message[1:].strip()
            test = FAILED_TEST.match(line.strip())
            if test:
                failures.append((test['test'] or test['other']).strip())
        if panic_lines:
            message = '\n'.join(panic_lines)
        elif detail:
            message = '\n'.join(detail)
        elif assert_lines and message:
            message = message + '\n' + '\n'.join(assert_lines)
        if not frames:
            for key in locations:
                if key not in seen_frames:
                    seen_frames.add(key)
                    frames.append(key)
                    kinds.add('js')
        if message or failures:
            text = (message or 'Test failed: ' + failures[0])[:600]
            # Python lists the caller first. JavaScript lists the raise first.
            ordered = frames[:4] if kinds == {'js'} else list(reversed(frames))[:4]
            for path, line_no in ordered or [('', 0)]:
                add(Diagnostic(step, 'error', text, path, line_no, source='runtime'))
            for test in failures[:8]:
                add(Diagnostic(step, 'error', 'Failed test: ' + test[:300], source='test'))
    return found


def errors_first(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    """Keep tool order within each group; errors in project files lead."""
    def rank(d: Diagnostic) -> int:
        external = any(n in d.file for n in NOISE)
        return (0 if d.severity == 'error' else 2) + (1 if external or not d.file else 0)
    return sorted(diagnostics, key=rank)


def fingerprint(diagnostics: list[Diagnostic]) -> str:
    """Line numbers drift between edits, so they do not take part in identity."""
    parts = sorted({f'{d.file}|{d.code}|{re.sub(r"[0-9]+", "#", d.message)[:200]}' for d in diagnostics if d.severity == 'error'})
    return hashlib.sha256('\n'.join(parts).encode('utf-8', 'replace')).hexdigest()[:16] if parts else ''


def error_count(diagnostics: list[Diagnostic]) -> int:
    return sum(d.severity == 'error' for d in diagnostics)
