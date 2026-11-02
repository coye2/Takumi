"""Bounded syntax checks. Never execute generated programs or project build files."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

LANGUAGES = {'c', 'cpp', 'c++', 'cxx', 'python', 'py', 'glsl', 'vert', 'frag', 'comp', 'geom', 'tesc', 'tese'}


def preprocessing_view(source: str) -> str:
    # Translation phases splice lines before replacing comments. This view is
    # only a conservative access guard; Clang remains the syntax authority.
    source = re.sub(r'\\\r?\n', '', source)
    return re.sub(r'/\*[\s\S]*?\*/', ' ', source)


def find_tool(name: str) -> str | None:
    found = shutil.which(name) or (shutil.which('glslang') if name == 'glslangValidator' else None)
    if found:
        return found
    candidates = []
    if os.name == 'nt' and name in ('clang', 'clangd'):
        candidates.append(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / ('LLVM/bin/' + name + '.exe'))
    if name == 'glslangValidator':
        if os.name == 'nt' and os.environ.get('LOCALAPPDATA'):
            candidates.append(Path(os.environ['LOCALAPPDATA']) / 'Takumi/Tools/glslang/bin/glslang.exe')
        sdk = os.environ.get('VULKAN_SDK')
        if sdk:
            candidates.extend([Path(sdk) / 'Bin/glslangValidator.exe', Path(sdk) / 'bin/glslangValidator'])
    return next((str(p) for p in candidates if p.is_file()), None)


def check_block(language: str, source: str, *, timeout: float = 4.0, cancel=None) -> dict:
    result = {'language': language, 'status': 'unverified', 'tool': None,
              'scope': 'Syntax only; dependencies, target ABI, linking and runtime behavior are not verified.'}
    if cancel is not None and cancel.is_set():
        return {**result, 'reason': 'Cancelled before syntax check.'}
    if len(source) > 80000:
        return {**result, 'reason': 'Block exceeds the 80,000-character syntax-check limit.'}
    if language in ('python', 'py'):
        result['tool'] = 'Python ' + sys.version.split()[0] + ' ast.parse'
        try:
            ast.parse(source, filename='<response>', mode='exec')
            return {**result, 'status': 'passed'}
        except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
            return {**result, 'status': 'failed', 'diagnostic': str(exc)[:1500]}
    if language not in LANGUAGES:
        return {**result, 'reason': 'No syntax checker configured for this language.'}
    # A response is not permission to read arbitrary local files via a compiler.
    # Standalone snippets accept ordinary system header names, never paths,
    # macro-expanded includes, #embed, pragmas or module imports. Project-aware
    # builds use the separately authorized harness instead.
    directives = re.findall(r'^\s*#\s*([^\n]*)', preprocessing_view(source), re.M)
    for directive in directives:
        if re.match(r'(include|include_next|import|embed)\b', directive):
            if not re.fullmatch(r'include\s*<[A-Za-z0-9_.]+>\s*(?://[^\n]*)?', directive):
                return {**result, 'reason': 'Local, computed or nonstandard include needs a project build.'}
        if re.match(r'pragma\b', directive):
            return {**result, 'reason': 'Compiler pragmas require a project build.'}
    if re.search(r'\b(?:import|__has_include|_Pragma|__pragma)\b|\?\?[/=]|%:|##', source):
        return {**result, 'reason': 'Module/file-dependent directives require a project build.'}
    if language in ('c', 'cpp', 'c++', 'cxx'):
        exe = find_tool('clang')
        kind = 'c' if language == 'c' else 'c++'
        args = ['--no-default-config', '-x', kind, '-std=' + ('c17' if kind == 'c' else 'c++20'), '-fsyntax-only',
                '-fno-color-diagnostics', '-ferror-limit=5', '-']
    else:
        exe = find_tool('glslangValidator')
        if language == 'glsl':
            return {**result, 'reason': 'Specify a shader stage: vert, frag, comp, geom, tesc or tese.'}
        args = ['--stdin', '-S', language]
    if not exe:
        return {**result, 'reason': ('Clang' if language in ('c', 'cpp', 'c++', 'cxx') else 'glslangValidator') + ' is not installed or discoverable.'}
    result.update(tool=exe, command=[Path(exe).name, *args])
    # Do not inherit project include search paths from environment variables.
    env = {k: v for k, v in os.environ.items() if k.upper() not in
           {'CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH', 'OBJC_INCLUDE_PATH', 'INCLUDE',
            'CL', '_CL_', 'CCC_OVERRIDE_OPTIONS', 'CLANG_CONFIG_FILE_USER_DIR', 'CLANG_CONFIG_FILE_SYSTEM_DIR'}}
    try:
        with tempfile.TemporaryDirectory(prefix='takumi-syntax-') as directory:
            with tempfile.TemporaryFile() as log:
                proc = subprocess.Popen([exe, *args], stdin=subprocess.PIPE, stdout=log, stderr=log,
                                        cwd=directory, env=env, creationflags=0x08000000 if os.name == 'nt' else 0)
                deadline = time.monotonic() + max(.1, min(timeout, 5.0))
                data = source.encode('utf-8')
                while True:
                    try:
                        proc.communicate(data, timeout=.05)
                        break
                    except subprocess.TimeoutExpired:
                        data = None
                        if time.monotonic() >= deadline or (cancel is not None and cancel.is_set()):
                            proc.kill()
                            proc.communicate()
                            return {**result, 'reason': 'Syntax check interrupted or timed out; no pass is claimed.'}
                log.seek(0)
                diagnostic = log.read(6000).decode('utf-8', errors='replace')
                return {**result, 'status': 'passed' if proc.returncode == 0 else 'failed',
                        'exit_code': proc.returncode, 'diagnostic': diagnostic.strip()}
    except OSError as exc:
        return {**result, 'reason': 'Compiler could not run: ' + str(exc)[:200]}
