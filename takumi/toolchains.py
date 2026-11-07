"""Give a project the exact toolchain it pins.

A global.json with rollForward disabled makes every dotnet command fail inside
that project unless the exact SDK is found. NX Infinity pins one, so without this
nothing started from Takumi could build or test it, from the PC or from a phone.
Nothing is downloaded here: only SDKs already in Takumi's tools folder are used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess


def tool_roots() -> list[Path]:
    local = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    extra = os.environ.get('TAKUMI_TOOLS_DIR')
    return [*([Path(extra)] if extra else []), local / 'Takumi' / 'Tools']


def pinned_dotnet_sdk(project: Path, cwd: Path | None = None) -> str:
    """The SDK version a global.json insists on, searching from cwd up to the project,
    then one level down from it. Empty when nothing is pinned."""
    project = Path(project)
    candidates = []
    if cwd:
        here = Path(cwd)
        while True:
            candidates.append(here / 'global.json')
            if here == project or here.parent == here or project not in here.parents:
                break
            here = here.parent
    candidates.append(project / 'global.json')
    try:
        candidates.extend(sorted(item / 'global.json' for item in project.iterdir() if item.is_dir() and not item.name.startswith('.')))
    except OSError:
        pass
    for path in candidates:
        try:
            sdk = json.loads(path.read_text('utf-8-sig')).get('sdk') or {}
        except (OSError, ValueError, AttributeError):
            continue
        version = sdk.get('version')
        if isinstance(version, str) and re.fullmatch(r'[\d.]+(?:-[\w.]+)?', version) and sdk.get('rollForward') in ('disable', 'patch', 'latestPatch'):
            return version + ('' if sdk.get('rollForward') == 'disable' else '+')
    return ''


def installed_sdks() -> set[str]:
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        done = subprocess.run(['dotnet', '--list-sdks'], capture_output=True, text=True, timeout=20, cwd=Path.home(), creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return set()
    return {line.split(' ', 1)[0] for line in done.stdout.splitlines() if line.strip()}


def _band(version: str) -> tuple:
    major, minor, patch = (version.split('-')[0].split('.') + ['0', '0'])[:3]
    return major, minor, int(patch) // 100


def _satisfied(pin: str, version: str) -> bool:
    """A trailing + means any newer patch in the same feature band, such as 10.0.1xx."""
    if not pin.endswith('+'):
        return version == pin
    wanted = pin[:-1]
    return _band(version) == _band(wanted) and int(version.split('-')[0].split('.')[2]) >= int(wanted.split('-')[0].split('.')[2])


def _sdk_home(version: str) -> Path | None:
    name = 'dotnet.exe' if os.name == 'nt' else 'dotnet'
    version = version.rstrip('+')
    for root in tool_roots():
        home = root / f'dotnet-{version}'
        if (home / name).is_file() and (home / 'sdk' / version).is_dir():
            return home
    return None


def apply_project_toolchain(env: dict, project: Path, cwd: Path | None = None) -> dict:
    """Point dotnet at the pinned SDK when the normal install lacks it. Returns env."""
    version = pinned_dotnet_sdk(project, cwd)
    if not version or any(_satisfied(version, sdk) for sdk in installed_sdks()):
        return env
    home = _sdk_home(version)
    if home:
        env['PATH'] = str(home) + os.pathsep + env.get('PATH', '')
        env['DOTNET_ROOT'] = str(home)
        env['DOTNET_MULTILEVEL_LOOKUP'] = '0'
    return env
