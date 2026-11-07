"""Facts from a finished verified run, kept only while the source still matches.

A changed file drops the record. The summary is built from the saved report,
not from a model.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading

from . import platforms


def tree_hash(project: Path) -> dict[str, str]:
    hashes = {}
    project = Path(project)
    for item in platforms._walk(project):
        path = project / item
        try:
            if not path.is_file() or path.stat().st_size > 200_000:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b'\0' in raw:
            continue
        hashes[item.as_posix()] = hashlib.sha256(raw).hexdigest()
    return hashes


def digest(hashes: dict[str, str]) -> str:
    material = hashlib.sha256()
    for name in sorted(hashes):
        material.update(name.encode())
        material.update(hashes[name].encode())
    return material.hexdigest()


def summary(report: dict) -> str:
    files = ', '.join(report.get('changed_files') or []) or 'none'
    remaining = '; '.join(str(item.get('message', ''))[:160] for item in (report.get('diagnostics') or [])[:3]) or 'none'
    verified = bool((report.get('verification') or {}).get('end_to_end'))
    return (f'Previous run on these exact files ended {report.get("state")}. '
            f'Files changed: {files}. Remaining failures: {remaining}. '
            f'End-to-end verified: {verified}.')


class EngineeringMemory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text('utf-8'))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def recall(self, project: Path) -> dict:
        project = Path(project).resolve()
        with self.lock:
            record = self._load().get(str(project))
        if not isinstance(record, dict) or not record.get('summary'):
            return {'fresh': False, 'reason': 'none'}
        if tree_hash(project) != record.get('hashes'):
            return {'fresh': False, 'reason': 'files changed'}
        return {'fresh': True, 'summary': record['summary'], 'state': record.get('state'),
                'verification': record.get('verification') or {}}

    def record(self, project: Path, report: dict) -> None:
        project = Path(project).resolve()
        entry = {'hashes': tree_hash(project), 'state': report.get('state'), 'summary': summary(report),
                 'verification': report.get('verification') or {}}
        with self.lock:
            data = self._load()
            data[str(project)] = entry
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data), encoding='utf-8')
            temporary.replace(self.path)
