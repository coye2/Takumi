"""Optional, separately installed browser tools for the coding engine."""
import json
from pathlib import Path
import shutil

VERSION = '0.0.84'


def command():
    root = Path.home() / '.takumi' / 'tools' / 'browser' / 'node_modules' / '@playwright' / 'mcp'
    node = shutil.which('node')
    try:
        version = json.loads((root / 'package.json').read_text(encoding='utf-8'))['version']
    except (OSError, ValueError, KeyError):
        return None
    if not node or version != VERSION or not (root / 'cli.js').is_file():
        return None
    # A fresh browser profile per task; never attach to the owner's tabs/cookies.
    return [node, str(root / 'cli.js'), '--browser', 'msedge', '--isolated', '--headless']


def config():
    argv = command()
    if not argv:
        raise RuntimeError('Install browser tools with scripts/Install-Browser-Tools.ps1 first.')
    return {'type': 'local', 'command': argv, 'enabled': True, 'timeout': 20000}
