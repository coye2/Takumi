"""Warm locally installed syntax checkers for bounded first-call CI tests.

A compiler's first invocation can exceed the four-second per-snippet deadline
on cold Windows runners. This optional preflight runs known fixed code only;
it does not execute any user-supplied source or affect test expectations.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from takumi.compiler_checks import find_tool


def main() -> int:
    cases = (
        ('clang', ['--no-default-config', '-x', 'c', '-std=c17',
                   '-fsyntax-only', '-fno-color-diagnostics', '-'],
         'int takumi_compiler_warmup(void) { return 1; }\n'),
        ('glslangValidator', ['--stdin', '-S', 'frag'],
         '#version 450\nlayout(location=0) out vec4 color;\n'
         'void main() { color = vec4(1.0); }\n'),
    )
    for name, args, source in cases:
        exe = find_tool(name)
        if not exe:
            print(name + ': not installed, corresponding tests will report skipped')
            continue
        print('Preflighting ' + name + ' with fixed source...', flush=True)
        try:
            result = subprocess.run([exe, *args], input=source, capture_output=True,
                                    encoding='utf-8', errors='replace', timeout=60,
                                    shell=False, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(name + ': preflight error: ' + str(exc), file=sys.stderr)
            return 1
        if result.returncode:
            print(name + ': preflight failed: ' + (result.stderr or result.stdout)[-2500:],
                  file=sys.stderr)
            return 1
        print(name + ': preflight passed', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
