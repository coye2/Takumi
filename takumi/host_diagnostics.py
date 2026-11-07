"""Bounded read-only host diagnostics for the paired Takumi phone.

This is not a general Windows shell endpoint. No client-controlled command,
script path or environment variable can be passed to the subprocess runner.
The PC performs the checks; the browser receives only short status summaries.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


def _probe(argv: list[str], *, cwd: Path | None = None, timeout: int = 8,
           reveal_output: bool = True) -> dict:
    """Execute one fixed argument vector with no shell and bounded output."""
    try:
        result = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=timeout, shell=False,
            stdin=subprocess.DEVNULL, check=False,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'},
            creationflags=(getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                           if os.name == 'nt' else 0),
        )
    except subprocess.TimeoutExpired:
        return {'status': 'unverified', 'detail': 'Timed out; no successful execution is claimed'}
    except OSError as exc:
        return {'status': 'unavailable', 'detail': type(exc).__name__ + ': ' + str(exc)[:120]}
    detail = ((result.stdout or '') if reveal_output else '')
    return {'status': 'passed' if result.returncode == 0 else 'failed',
            'exit_code': result.returncode,
            'detail': detail.strip()[:350] if reveal_output else
                      ('Command completed successfully' if result.returncode == 0
                       else 'Command failed; credentials were not exposed')}


def host_readiness(project: Path | None = None) -> dict:
    """Report what this running host can actually do. Never mutate source or processes."""
    checks = {}
    windows = os.name == 'nt'

    if windows:
        whoami = shutil.which('whoami.exe')
        checks['whoami'] = _probe([whoami], timeout=5) if whoami else {
            'status': 'unavailable', 'detail': 'whoami.exe is not installed'}
        powershell = shutil.which('powershell.exe') or shutil.which('pwsh.exe')
        if powershell:
            checks['powershell'] = _probe([
                powershell, '-NoProfile', '-NonInteractive', '-Command',
                '$PSVersionTable.PSVersion.ToString()'], timeout=8)
            checks['get_location'] = _probe([
                powershell, '-NoProfile', '-NonInteractive', '-Command',
                '(Get-Location).Path'], timeout=8)
            checks['get_command_gh'] = _probe([
                powershell, '-NoProfile', '-NonInteractive', '-Command',
                "(Get-Command gh -ErrorAction SilentlyContinue).Name"], timeout=8)
        else:
            for name in ('powershell', 'get_location', 'get_command_gh'):
                checks[name] = {'status': 'unavailable',
                                'detail': 'No PowerShell executable was found on this host'}
    else:
        for name in ('whoami', 'powershell', 'get_location', 'get_command_gh'):
            checks[name] = {'status': 'unavailable',
                            'detail': 'Not a Windows host; Windows commands were not executed'}

    gh = shutil.which('gh')
    if gh:
        # Do not return the output of 'gh auth status': that diagnostic may
        # display token metadata or other sensitive authentication details.
        checks['github_auth'] = _probe([gh, 'auth', 'status', '-h', 'github.com'],
                                       timeout=8, reveal_output=False)
        checks['github_login'] = (_probe([gh, 'api', 'user', '--jq', '.login'], timeout=10)
                                  if checks['github_auth']['status'] == 'passed'
                                  else {'status': 'unverified',
                                        'detail': 'GitHub authentication was not verified'})
    else:
        for name in ('github_auth', 'github_login'):
            checks[name] = {'status': 'unavailable',
                            'detail': 'GitHub CLI is not installed on this PC'}

    dotnet = shutil.which('dotnet')
    checks['dotnet'] = (_probe([dotnet, '--version'], timeout=8) if dotnet else
                        {'status': 'unavailable', 'detail': '.NET SDK is not on PATH'})
    git = shutil.which('git')
    checks['git'] = (_probe([git, '--version'], timeout=5) if git else
                     {'status': 'unavailable', 'detail': 'Git is not on PATH'})

    if project is not None:
        root = Path(project).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('Selected registered project folder is unavailable')
        if git:
            checks['project_branch'] = _probe(
                [git, 'branch', '--show-current'], cwd=root, timeout=5)
            checks['project_status'] = _probe(
                [git, 'status', '--short'], cwd=root, timeout=8)
        else:
            checks['project_branch'] = checks['project_status'] = {
                'status': 'unavailable', 'detail': 'Git is not available'}
    return {'scope': 'fixed_read_only_host_checks', 'host': 'Windows' if windows else 'non-Windows',
            'executed_on': 'Takumi backend host, not the phone',
            'checks': checks, 'source_or_installed_build_modified': False,
            'github_release_published': False}
