"""Read-only discovery of an installed Forge Universal, plus opt-in tool access.

Forge is a separate product with its own lifecycle. Takumi never installs,
starts, stops or upgrades it, and a project-writing agent only gets Forge's tools
when the owner turns that setting on: its tools run with the owner's permissions.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


def forge_home() -> Path | None:
    configured = os.environ.get('TAKUMI_FORGE_HOME') or os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ForgeUniversal')
    path = Path(configured)
    return path if configured and (path / 'installation.json').is_file() else None


def _installation(home: Path) -> dict:
    # Forge's PowerShell installer writes a UTF-8 BOM.
    data = json.loads((home / 'installation.json').read_text('utf-8-sig'))
    return data if isinstance(data, dict) else {}


def status(*, timeout: float = 12) -> dict:
    home = forge_home()
    if not home:
        return {'installed': False, 'detail': 'Forge Universal is not installed on this PC.'}
    try:
        install = _installation(home)
        result = subprocess.run([install['python'], install['launcher'], '--home', str(home), '--version'], capture_output=True, text=True, timeout=timeout,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        return {'installed': True, 'usable': False, 'detail': 'Forge is installed but could not be asked for its version: ' + str(exc)[:200]}
    return {'installed': True, 'usable': result.returncode == 0, 'version': result.stdout.strip()[:80], 'mcp_available': bool(mcp_entry())}


def mcp_entry() -> dict | None:
    """Forge's own generated tool configuration, untouched, or None when absent."""
    home = forge_home()
    if not home:
        return None
    try:
        entry = json.loads((home / 'opencode-forge.json').read_text('utf-8-sig'))['mcp']['forge']
        command = entry['command']
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if entry.get('type') != 'local' or not isinstance(command, list) or not all(isinstance(x, str) for x in command):
        return None
    return {'type': 'local', 'command': command, 'enabled': True, 'timeout': int(entry.get('timeout', 20000))}
