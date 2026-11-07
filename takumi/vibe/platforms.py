"""Find out what a project is and how to prove a change to it works.

Commands come from the project's own files and this table, never from model
output. A missing toolchain is reported as blocked with what to install: it is
not skipped, and it can never count as a pass.
"""
from __future__ import annotations

from ..toolchains import apply_project_toolchain
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time
from xml.etree import ElementTree

from .diagnostics import Diagnostic, parse
from .repomap import SKIP_DIRS

BUILD_DIR = '.takumi-build'
WINDOWS = os.name == 'nt'
SECRET_ENV = re.compile(r'(?i)(token|secret|password|passwd|api_?key|credential|private_key)')
OUTPUT_LIMIT = 200_000


@dataclass
class Step:
    name: str
    kind: str  # build | test | lint | static | run
    argv: list[str] = field(default_factory=list)
    cwd: str = '.'
    timeout: int = 300
    builtin: str = ''
    blocked: str = ''
    platform: str = ''

    def public(self) -> dict:
        return asdict(self)


def _which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _walk(project: Path, depth: int = 3, limit: int = 3000):
    seen = 0
    for current, dirs, files in os.walk(project):
        relative = Path(current).relative_to(project)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d != BUILD_DIR and len(relative.parts) < depth]
        for name in files:
            seen += 1
            if seen > limit:
                return
            yield relative / name


def _wsl_tools(needed: tuple[str, ...]) -> dict[str, bool]:
    if not WINDOWS or not shutil.which('wsl'):
        return {}
    probe = ' ; '.join(f'command -v {tool} >/dev/null && echo {tool}' for tool in needed)
    try:
        result = subprocess.run(['wsl', '-e', 'sh', '-c', probe], capture_output=True, timeout=45,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        lines = result.stdout.decode('utf-8', 'replace').replace('\0', '').split()
        return {tool: tool in lines for tool in needed}
    except (OSError, subprocess.SubprocessError):
        return {}


def wsl_path(path: Path) -> str:
    resolved = Path(path).resolve().as_posix()
    match = re.match(r'^([A-Za-z]):/(.*)$', resolved)
    return f'/mnt/{match[1].lower()}/{match[2]}' if match else resolved


def android_sdk() -> str | None:
    candidates = [os.environ.get('ANDROID_HOME'), os.environ.get('ANDROID_SDK_ROOT'),
                  os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Android', 'Sdk') if WINDOWS else os.path.expanduser('~/Android/Sdk')]
    return next((c for c in candidates if c and Path(c, 'platform-tools').is_dir()), None)


def java_home() -> str | None:
    candidates = [os.environ.get('JAVA_HOME'), r'C:\Program Files\Android\Android Studio\jbr' if WINDOWS else None]
    exe = 'java.exe' if WINDOWS else 'java'
    return next((c for c in candidates if c and Path(c, 'bin', exe).is_file()), None)


def _web(project: Path, files: list[Path]) -> tuple[list[str], list[Step]]:
    platforms, steps = [], []
    package = project / 'package.json'
    if package.is_file():
        platforms.append('web')
        try:
            data = json.loads(package.read_text('utf-8'))
        except ValueError as exc:
            return platforms, [Step('package.json', 'static', blocked=f'package.json is not valid JSON: {exc}', platform='web')]
        scripts = data.get('scripts') or {}
        npm = _which('npm.cmd', 'npm')
        has_deps = bool(data.get('dependencies') or data.get('devDependencies'))
        blocked = '' if npm else 'Node.js is not installed. Install it with: winget install OpenJS.NodeJS.LTS'
        if not blocked and has_deps and not (project / 'node_modules').is_dir():
            blocked = 'Dependencies are not installed. Run npm ci in this project once, then try again.'
        for script, kind in (('typecheck', 'build'), ('lint', 'lint'), ('build', 'build'), ('test', 'test')):
            command = str(scripts.get(script, ''))
            if command and 'no test specified' not in command:
                steps.append(Step('npm ' + script, kind, [npm or 'npm', 'run', '--silent', script], blocked=blocked, platform='web'))
    sources = [f for f in files if f.suffix in ('.html', '.js', '.mjs')]
    if sources and not any(s.platform == 'web' and s.kind in ('build', 'test') for s in steps):
        if not package.is_file():
            platforms.append('web')
        steps.append(Step('syntax check', 'static', builtin='web', platform='web'))
    manifests = [f for f in files if f.name in ('manifest.webmanifest', 'manifest.json', 'site.webmanifest') and 'android' not in f.as_posix().lower()]
    if manifests:
        platforms.append('pwa')
        steps.append(Step('PWA contract', 'static', builtin='pwa:' + manifests[0].as_posix(), platform='pwa'))
    return platforms, steps


def _dotnet(project: Path, files: list[Path]) -> tuple[list[str], list[Step]]:
    solutions = [f for f in files if f.suffix in ('.sln', '.slnx')]
    projects = [f for f in files if f.suffix in ('.csproj', '.fsproj', '.vbproj')]
    if not solutions and not projects:
        return [], []
    texts = {}
    for item in projects:
        try:
            texts[item] = (project / item).read_text('utf-8', 'replace')
        except OSError:
            texts[item] = ''
    sdk_tests = sorted(item for item, text in texts.items() if 'Microsoft.NET.Test.Sdk' in text)
    plain_tests = sorted(item for item in projects if item.name.endswith('.Tests.csproj') and item not in sdk_tests)
    # Building the test project also builds the program it references. Building only the
    # program would leave the later test command with nothing to run.
    if solutions:
        target = solutions[0].as_posix()
    elif sdk_tests:
        target = sdk_tests[0].as_posix()
    elif plain_tests:
        target = plain_tests[0].as_posix()
    else:
        target = sorted(projects, key=lambda p: len(p.parts))[0].as_posix()
    dotnet = _which('dotnet')
    blocked = '' if dotnet else 'The .NET SDK is not installed. Install it with: winget install Microsoft.DotNet.SDK.10'
    steps = [Step('dotnet build', 'build', [dotnet or 'dotnet', 'build', target, '--nologo', '-v', 'q', '-clp:NoSummary'], blocked=blocked, platform='dotnet')]
    if sdk_tests:
        steps.append(Step('dotnet test', 'test', [dotnet or 'dotnet', 'test', target, '--nologo', '--no-build', '-v', 'q'], blocked=blocked, platform='dotnet'))
    elif plain_tests:
        steps.append(Step('dotnet run tests', 'test', [dotnet or 'dotnet', 'run', '--project', plain_tests[0].as_posix(), '--no-build', '--nologo', '-v', 'q'], blocked=blocked, platform='dotnet'))
    return ['dotnet'], steps


def _cpp(project: Path, files: list[Path]) -> tuple[list[str], list[Step]]:
    if not (project / 'CMakeLists.txt').is_file():
        if (project / 'Makefile').is_file() and any(f.suffix in ('.c', '.cc', '.cpp', '.cxx') for f in files):
            make = _which('make')
            return ['cpp'], [Step('make', 'build', [make or 'make'], blocked='' if make else 'make is not on PATH.', platform='cpp')]
        return [], []
    cmake, ctest = _which('cmake'), _which('ctest')
    compiler = _which('cl', 'g++', 'clang++', 'c++')
    if cmake and ctest and compiler:
        # Pin the compiler we found. CMake's default c++ can be a different, unlinkable binary.
        steps = [Step('cmake configure', 'build', [cmake, '-S', '.', '-B', BUILD_DIR, '-DCMAKE_BUILD_TYPE=Debug', f'-DCMAKE_CXX_COMPILER={compiler}'], platform='cpp'),
                 Step('cmake build', 'build', [cmake, '--build', BUILD_DIR, '--parallel'], platform='cpp'),
                 Step('ctest', 'test', [ctest, '--test-dir', BUILD_DIR, '--output-on-failure', '--no-tests=ignore'], platform='cpp')]
        return ['cpp'], steps
    tools = _wsl_tools(('cmake', 'ctest', 'g++'))
    if tools and all(tools.values()):
        # The Windows PATH has no compiler, but the owner's WSL does.
        where = wsl_path(project)
        wsl = lambda *argv: ['wsl', '--cd', where, '-e', *argv]
        return ['cpp'], [Step('cmake configure (WSL)', 'build', wsl('cmake', '-S', '.', '-B', BUILD_DIR, '-DCMAKE_BUILD_TYPE=Debug'), platform='cpp'),
                         Step('cmake build (WSL)', 'build', wsl('cmake', '--build', BUILD_DIR, '--parallel', '8'), platform='cpp'),
                         Step('ctest (WSL)', 'test', wsl('ctest', '--test-dir', BUILD_DIR, '--output-on-failure', '--no-tests=ignore'), platform='cpp')]
    return ['cpp'], [Step('cmake', 'build', blocked='No CMake and C++ compiler found. Install them with: winget install Kitware.CMake LLVM.LLVM', platform='cpp')]


def _android(project: Path, files: list[Path]) -> tuple[list[str], list[Step]]:
    manifest = next((f for f in files if f.name == 'AndroidManifest.xml'), None)
    settings = any((project / n).is_file() for n in ('settings.gradle', 'settings.gradle.kts'))
    if not manifest and not settings:
        return [], []
    steps = [Step('Android manifest', 'static', builtin='android:' + manifest.as_posix(), platform='android')] if manifest else []
    wrapper = project / ('gradlew.bat' if WINDOWS else 'gradlew')
    gradle = str(wrapper) if wrapper.is_file() else _which('gradle.bat', 'gradle')
    blocked = ''
    if not gradle:
        blocked = 'No Gradle found. Add the project wrapper (gradle wrapper) or install Gradle.'
    elif not java_home():
        blocked = 'No JDK found. Android Studio includes one; set JAVA_HOME to its jbr folder.'
    elif not android_sdk():
        blocked = 'No Android SDK found. Install it from Android Studio, then set ANDROID_HOME.'
    steps += [Step('Gradle unit tests', 'test', [gradle or 'gradlew', 'testDebugUnitTest', '--console=plain'], timeout=900, blocked=blocked, platform='android'),
              Step('Gradle assemble', 'build', [gradle or 'gradlew', 'assembleDebug', '--console=plain'], timeout=900, blocked=blocked, platform='android')]
    return ['android'], steps


def _ios(project: Path, files: list[Path]) -> tuple[list[str], list[Step]]:
    apple = [d for d in project.iterdir() if d.suffix in ('.xcodeproj', '.xcworkspace')] if project.is_dir() else []
    package = (project / 'Package.swift').is_file()
    swift_sources = any(f.suffix == '.swift' for f in files)
    if not apple and not package and not swift_sources:
        return [], []
    steps = [Step('Info.plist', 'static', builtin='plist:' + f.as_posix(), platform='ios') for f in files if f.name == 'Info.plist'][:4]
    swift, xcodebuild = _which('swift'), _which('xcodebuild')
    if package and swift:
        steps += [Step('swift build', 'build', [swift, 'build'], platform='ios'), Step('swift test', 'test', [swift, 'test'], platform='ios')]
    elif apple and xcodebuild:
        kind = '-workspace' if apple[0].suffix == '.xcworkspace' else '-project'
        steps.append(Step('xcodebuild', 'build', [xcodebuild, kind, apple[0].name, 'build', 'CODE_SIGNING_ALLOWED=NO'], timeout=1200, platform='ios'))
    else:
        steps.append(Step('iOS compile', 'build', platform='ios',
                          blocked='Swift and iOS builds need macOS with Xcode. This PC can only check Info.plist. Connect a Mac host to compile.'))
    return ['ios'], steps


def _go(project: Path, files: list[Path]) -> tuple[list[str], list[Step]]:
    if not (project / 'go.mod').is_file():
        return [], []
    go = _which('go')
    blocked = '' if go else 'The Go toolchain is not installed.'
    tool = go or 'go'
    steps = [Step('go build', 'build', [tool, 'build', '-o', f'{BUILD_DIR}/app', '.'], blocked=blocked, platform='go'),
             Step('go test', 'test', [tool, 'test', '-v', './...'], blocked=blocked, platform='go')]
    return ['go'], steps


def _rust(project: Path, files: list[Path]) -> tuple[list[str], list[Step]]:
    if not (project / 'Cargo.toml').is_file():
        return [], []
    cargo = _which('cargo')
    blocked = '' if cargo else 'Rust and Cargo are not installed.'
    tool = cargo or 'cargo'
    steps = [Step('cargo build', 'build', [tool, 'build', '--quiet'], timeout=600, blocked=blocked, platform='rust'),
             Step('cargo test', 'test', [tool, 'test', '--quiet'], timeout=600, blocked=blocked, platform='rust')]
    return ['rust'], steps


def _python(project: Path, files: list[Path]) -> tuple[list[str], list[Step]]:
    sources = [f for f in files if f.suffix == '.py']
    if not sources:
        return [], []
    python = _which('py', 'python3', 'python')
    prefix = [python, '-3'] if python and Path(python).stem == 'py' else [python or 'python']
    blocked = '' if python else 'Python is not installed.'
    steps = [Step('python compile', 'static', prefix + ['-m', 'compileall', '-q', '.'], blocked=blocked, platform='python')]
    tests = [f for f in sources if f.name.startswith('test_') or f.name.endswith('_test.py')]
    if tests:
        folder = tests[0].parts[0] if len(tests[0].parts) > 1 else '.'
        steps.append(Step('python tests', 'test', prefix + ['-m', 'unittest', 'discover', '-s', folder, '-t', '.', '-p', 'test*.py'], blocked=blocked, platform='python'))
    return ['python'], steps


def _project_tasks(project: Path) -> list[Step]:
    from ..harness import task_definitions
    kinds = {'build': 'build', 'test': 'test', 'tests': 'test', 'lint': 'lint', 'verify': 'test', 'run': 'run', 'runtime': 'run'}
    try:
        return [Step(t['id'], kinds[t['id']], list(t['argv']), timeout=min(int(t.get('timeout', 600)), 1800), platform='project')
                for t in task_definitions(project) if t['id'] in kinds]
    except (ValueError, OSError, KeyError):
        return []


def detect(project: Path) -> dict:
    project = Path(project)
    files = list(_walk(project))
    platforms: list[str] = []
    steps = _project_tasks(project)
    for probe in (_dotnet, _cpp, _android, _ios, _web, _go, _rust, _python):
        found, found_steps = probe(project, files)
        platforms += [p for p in found if p not in platforms]
        if not steps or any(s.platform != 'project' for s in steps):
            steps += found_steps
    if any(s.platform == 'project' for s in steps):
        # takumi.tasks.json is the owner's statement of how this project is verified.
        steps = [s for s in steps if s.platform == 'project' or s.kind == 'static']
    runnable = [s for s in steps if not s.blocked]
    proves = [s for s in runnable if s.kind in ('build', 'test', 'run')]
    return {'platforms': platforms, 'steps': steps, 'runnable': len(runnable), 'blocked': [s.public() for s in steps if s.blocked],
            'has_tests': any(s.kind == 'test' for s in runnable), 'can_prove': bool(proves)}


class _Html(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__()
        self.open, self.problems = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.open.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.open or self.open[-1][0] != tag:
            if tag in [t for t, _ in self.open]:
                while self.open and self.open[-1][0] != tag:
                    missing, line = self.open.pop()
                    if missing not in ('li', 'p', 'td', 'tr', 'option', 'dt', 'dd', 'th'):
                        self.problems.append((line, f'<{missing}> is never closed'))
                self.open.pop()
            else:
                self.problems.append((self.getpos()[0], f'</{tag}> has no matching opening tag'))
        else:
            self.open.pop()


def _error(step: str, message: str, file: str = '', line: int = 0) -> Diagnostic:
    return Diagnostic(step, 'error', message, file, line, source='static')


def check_web(project: Path, step: str = 'syntax check') -> list[Diagnostic]:
    found = []
    node = _which('node')
    for item in _walk(project):
        if item.suffix == '.html':
            checker = _Html()
            checker.feed((project / item).read_text('utf-8', 'replace'))
            found += [_error(step, message, item.as_posix(), line) for line, message in checker.problems]
        elif item.suffix in ('.js', '.mjs', '.cjs') and node:
            result = _run_quiet([node, '--check', str(project / item)], project, 30)
            if result[0] != 0:
                parsed = parse(result[1], step=step, project=str(project))
                found += parsed or [_error(step, result[1].strip()[:400], item.as_posix())]
    found += check_js_in(project, step)
    return found


def check_js_in(project: Path, step: str = 'javascript in-operator') -> list[Diagnostic]:
    """`in` throws on numbers and strings. A null check does not make it safe.

    `current == null || key in current` is the held-out js-deep-get failure:
    the visible test walked objects, and the hidden one walked a number.
    `key in Object(current)` is the form that does not throw.
    """
    pattern = re.compile(
        r'\b(?P<name>[A-Za-z_$][\w$]*)\s*(?:!==|===|!=|==)\s*null\b[\s\S]{0,160}?\bin\s+(?!Object\s*\()(?P=name)\b')
    found = []
    for item in _walk(project):
        if item.suffix not in ('.js', '.mjs', '.cjs'):
            continue
        try:
            text = (project / item).read_text('utf-8', 'replace')
        except OSError:
            continue
        for match in pattern.finditer(text):
            line = text.count('\n', 0, match.start()) + 1
            name = match.group('name')
            found.append(_error(
                step,
                f'The `in` operator throws on numbers and strings. Comparing `{name}` with null does not exclude them. Use `key in Object({name})`.',
                item.as_posix(), line))
    return found


def check_pwa(project: Path, manifest: str, step: str = 'PWA contract') -> list[Diagnostic]:
    found, path = [], project / manifest
    try:
        data = json.loads(path.read_text('utf-8-sig'))
    except (OSError, ValueError) as exc:
        return [_error(step, f'The manifest is not valid JSON: {exc}', manifest)]
    for key in ('name', 'start_url', 'display', 'icons'):
        if not data.get(key):
            found.append(_error(step, f'The manifest needs "{key}".', manifest))
    sizes = set()
    base = PurePosixPath(manifest).parent
    for icon in data.get('icons') or []:
        sizes.update(str(icon.get('sizes', '')).split())
        source = str(icon.get('src', '')).lstrip('/')
        if source and not re.match(r'^[a-z]+:', source) and not ((project / base / source).is_file() or (project / source).is_file()):
            found.append(_error(step, f'Icon file not found: {source}', manifest))
    for size in ('192x192', '512x512'):
        if data.get('icons') and size not in sizes:
            found.append(_error(step, f'The manifest needs a {size} icon to be installable.', manifest))
    pages = [f for f in _walk(project) if f.suffix == '.html']
    text = '\n'.join((project / f).read_text('utf-8', 'replace') for f in pages)
    if pages and not re.search(r'<link[^>]+rel=["\']manifest["\']', text):
        found.append(_error(step, 'No page links the manifest with <link rel="manifest">.', pages[0].as_posix()))
    scripts = '\n'.join((project / f).read_text('utf-8', 'replace') for f in _walk(project) if f.suffix in ('.js', '.mjs', '.ts'))
    registered = re.search(r'serviceWorker\s*\.\s*register\(\s*["\'`]([^"\'`]+)', text + scripts)
    if not registered:
        found.append(_error(step, 'Nothing registers a service worker, so the app cannot work offline.', pages[0].as_posix() if pages else manifest))
    elif not (project / registered[1].lstrip('./')).is_file() and not (project / base / registered[1].lstrip('./')).is_file():
        found.append(_error(step, f'The registered service worker file does not exist: {registered[1]}', pages[0].as_posix() if pages else manifest))
    return found


def check_android_manifest(project: Path, manifest: str, step: str = 'Android manifest') -> list[Diagnostic]:
    android = '{http://schemas.android.com/apk/res/android}'
    try:
        root = ElementTree.parse(project / manifest).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        return [_error(step, f'The manifest is not valid XML: {exc}', manifest)]
    found = []
    if root.tag != 'manifest':
        found.append(_error(step, 'The root element must be <manifest>.', manifest))
    for tag in ('activity', 'service', 'receiver', 'provider'):
        for item in root.iter(tag):
            if item.find('intent-filter') is not None and item.get(android + 'exported') is None:
                found.append(_error(step, f'<{tag}> {item.get(android + "name")} has an intent-filter and needs android:exported (required since Android 12).', manifest))
    return found


def check_plist(project: Path, name: str, step: str = 'Info.plist') -> list[Diagnostic]:
    try:
        data = plistlib.loads((project / name).read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError, ElementTree.ParseError) as exc:
        return [_error(step, f'Not a valid property list: {exc}', name)]
    return [_error(step, f'Info.plist needs {key}.', name) for key in ('CFBundleIdentifier', 'CFBundleName') if not data.get(key)]


def _kill_tree(process: subprocess.Popen):
    try:
        if WINDOWS:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        process.kill()


def _short_path(value: str, limit: int = 7000) -> str:
    """cmd.exe ignores a PATH over 8191 characters, which leaves npm scripts unable
    to find node. Drop duplicates and keep the toolchain folders first."""
    if len(value) <= limit:
        return value
    entries = list(dict.fromkeys(x for x in value.split(os.pathsep) if x))
    tools = [str(Path(found).parent) for found in map(shutil.which, ('node', 'dotnet', 'git', 'py')) if found]
    out, used = [], 0
    for entry in list(dict.fromkeys(tools)) + entries:
        if entry not in out and used + len(entry) + 1 <= limit:
            out.append(entry)
            used += len(entry) + 1
    return os.pathsep.join(out)


def _env(cwd=None) -> dict:
    # Project tests have no business reading the owner's tokens.
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
    if 'PATH' in env:
        env['PATH'] = _short_path(env['PATH'])
    # A baseline check and the repair check can land in the same second. Python
    # would then trust the bytecode from the broken source and report a fix as
    # still failing. Always read the source the edit just wrote.
    env.update(CI='1', DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1', NO_COLOR='1',
               GIT_TERMINAL_PROMPT='0', PYTHONDONTWRITEBYTECODE='1',
               # Do not leave build servers behind, holding the project folder open.
               MSBUILDDISABLENODEREUSE='1', UseSharedCompilation='false')
    java, sdk = java_home(), android_sdk()
    if java:
        env.setdefault('JAVA_HOME', java)
    if sdk:
        env.setdefault('ANDROID_HOME', sdk)
    return apply_project_toolchain(env, Path(cwd), Path(cwd)) if cwd else env


def _run_quiet(argv: list[str], cwd: Path, timeout: int, cancel=None) -> tuple[int | None, str, bool]:
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if WINDOWS else {'start_new_session': True}
    env = _env(cwd)
    # A plan may hold the machine's dotnet, which would refuse a project's pinned SDK.
    name = 'dotnet' if Path(argv[0]).stem.lower() == 'dotnet' else argv[0]
    argv = [sys.executable if name.lower() in ('python', 'python3') else shutil.which(name, path=env.get('PATH')) or argv[0], *argv[1:]]
    process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, **options)
    started = time.monotonic()
    timed_out = False
    import threading
    chunks: list[bytes] = []
    stdout = process.stdout
    reader = threading.Thread(target=lambda: chunks.append(stdout.read() if stdout else b''), daemon=True)
    reader.start()
    try:
        while process.poll() is None:
            if (cancel and cancel.is_set()) or time.monotonic() - started > timeout:
                timed_out = not (cancel and cancel.is_set())
                _kill_tree(process)
                break
            time.sleep(0.05)
        process.wait(timeout=15)
        reader.join(timeout=5)
    finally:
        if stdout and not stdout.closed:
            stdout.close()
    output = b''.join(chunks).decode('utf-8', 'replace')
    return process.returncode, output[-OUTPUT_LIMIT:], timed_out


def _no_tests_ran(output: str) -> bool:
    """True when a test harness started and no test passed or failed.

    An ignored test is counted by Cargo and still does not run. A doc-test suite
    at zero beside a suite that passed is not an empty run.
    """
    results = re.findall(r'(\d+) passed; (\d+) failed', output)
    if results:
        return all(int(passed) == 0 and int(failed) == 0 for passed, failed in results)
    node_pass, node_fail = re.search(r'(?m)^# pass (\d+)$', output), re.search(r'(?m)^# fail (\d+)$', output)
    if node_pass and node_fail:
        return int(node_pass.group(1)) == 0 and int(node_fail.group(1)) == 0
    ran, skipped = re.search(r'(?m)^Ran (\d+) tests?\b', output), re.search(r'OK \(skipped=(\d+)\)', output)
    if ran and skipped and int(skipped.group(1)) >= int(ran.group(1)) > 0:
        return True
    # `go test -v` prints PASS lines for tests that ran. A run of only SKIP did not.
    if re.search(r'(?m)^--- SKIP:', output) and not re.search(r'(?m)^--- (?:PASS|FAIL):', output):
        return True
    counts = [int(item) for item in re.findall(r'running (\d+) tests?\b', output)]
    if counts:
        return sum(counts) == 0
    return bool(re.search(r'\[no tests to run\]|\[no test files\]|^# tests 0$', output, re.M))


def _drop_bytecode(project: Path) -> None:
    """A .pyc written in the same second as the edit still describes the old source."""
    for current, dirs, _files in os.walk(project):
        relative = Path(current).relative_to(project)
        if any(part in SKIP_DIRS or part == BUILD_DIR for part in relative.parts):
            dirs.clear()
            continue
        for name in list(dirs):
            if name == '__pycache__':
                shutil.rmtree(Path(current) / name, ignore_errors=True)
            if name == '__pycache__' or name in SKIP_DIRS or name == BUILD_DIR:
                dirs.remove(name)


def run_steps(project: Path, steps: list[Step], *, cancel=None, on_step=None) -> dict:
    """Run steps in order and stop at the first failure: later steps depend on
    earlier ones, and only the first failure is causal."""
    project = Path(project)
    _drop_bytecode(project)
    results, diagnostics, state = [], [], 'passed'
    for step in steps:
        started = time.monotonic()
        if step.blocked:
            results.append({'name': step.name, 'kind': step.kind, 'platform': step.platform, 'status': 'blocked', 'detail': step.blocked, 'seconds': 0})
            continue
        if cancel and cancel.is_set():
            state = 'cancelled'
            break
        if on_step:
            on_step(step)
        try:
            if step.builtin:
                checker, _, argument = step.builtin.partition(':')
                found = {'web': lambda: check_web(project, step.name), 'pwa': lambda: check_pwa(project, argument, step.name),
                         'android': lambda: check_android_manifest(project, argument, step.name),
                         'plist': lambda: check_plist(project, argument, step.name),
                         'jsin': lambda: check_js_in(project, step.name)}[checker]()
                code, output, timed_out = (1 if found else 0), '\n'.join(d.render() for d in found), False
            else:
                code, output, timed_out = _run_quiet(step.argv, project / step.cwd, step.timeout, cancel)
                found = parse(output, step=step.name, project=str(project))
        except (OSError, subprocess.SubprocessError) as exc:
            code, output, timed_out, found = 1, str(exc), False, [_error(step.name, f'Could not start {step.name}: {exc}')]
        if cancel and cancel.is_set():
            state = 'cancelled'
            break
        passed = code == 0 and not timed_out
        if passed and step.kind == 'test' and _no_tests_ran(output):
            passed = False
            found = [_error(step.name, 'The test command finished without running a test. Fix the code instead of removing the test.')]
        if not passed and not any(d.severity == 'error' for d in found):
            # Exit code is the authority; an unparsed failure keeps its tail.
            tail = '\n'.join(output.strip().split('\n')[-14:])
            found = [_error(step.name, f'Timed out after {step.timeout}s.' if timed_out else f'Exited with code {code}.\n{tail}'[:1400])]
        results.append({'name': step.name, 'kind': step.kind, 'platform': step.platform, 'status': 'passed' if passed else 'failed',
                        'exit_code': code, 'seconds': round(time.monotonic() - started, 2), 'output_tail': output[-3000:]})
        diagnostics += found
        if not passed:
            state = 'failed'
            break
    ran = [r for r in results if r['status'] in ('passed', 'failed')]
    if state == 'passed' and not ran:
        state = 'unverified'
    return {'state': state, 'steps': results, 'diagnostics': diagnostics,
            'blocked': [r for r in results if r['status'] == 'blocked'],
            'static': any(r['kind'] == 'static' and r['status'] == 'passed' for r in results),
            'tested': any(r['kind'] == 'test' and r['status'] == 'passed' for r in results),
            'built': any(r['kind'] == 'build' and r['status'] == 'passed' for r in results),
            'ran': any(r['kind'] == 'run' and r['status'] == 'passed' for r in results)}
