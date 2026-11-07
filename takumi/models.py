"""Public Takumi model identities with accurate upstream attribution.

Sumi 1.1 and Rei 1.1 are independently configurable inference profiles,
not separately trained foundation models. Base model blobs are installed by the
user's local runtime and are never included in Takumi distributions.
"""
from __future__ import annotations

import json
from pathlib import Path

MODEL_TAG = 'sumi-1.0:latest'
NAME = 'Sumi 1.1'
UPSTREAM = 'bonsai-2:27b'
REI_TAG = 'rei-1.0:latest'
REI_NAME = 'Rei 1.1'
REI_UPSTREAM = 'bonsai-2:9b'
# Older Takumi installs used this tag. Leave it usable; don't delete user models.
LEGACY_LITE_TAG = 'sumi-1.0-lite:latest'
MODEL_DIR = Path(__file__).resolve().parents[1] / 'models'
MANIFEST = MODEL_DIR / 'sumi-1.0.json'
REI_MANIFEST = MODEL_DIR / 'rei-1.0.json'


def canonical_tag(model_id: str) -> str:
    raw = str(model_id or '').strip().lower()
    return raw if ':' in raw else raw + ':latest'


def is_sumi(model_id: str) -> bool:
    return canonical_tag(model_id) == MODEL_TAG


def is_rei(model_id: str) -> bool:
    return canonical_tag(model_id) == REI_TAG


def is_sumi_lite(model_id: str) -> bool:
    """Legacy model accepted for existing installations; not a new product name."""
    return canonical_tag(model_id) == LEGACY_LITE_TAG


# Names for weights that are installed beside Sumi and Rei. These are not Sumi or Rei.
_KNOWN_LABELS = {
    'bonsai-2:27b': 'Bonsai 27B',
    'bonsai-2:9b': 'Bonsai 9B',
    'bonsai2-local:latest': 'Bonsai local',
}


def display_name(model_id: str) -> str:
    if is_sumi(model_id):
        return NAME
    if is_rei(model_id):
        return REI_NAME
    if is_sumi_lite(model_id):
        return 'Rei 1.1 (legacy)'
    tag = canonical_tag(model_id)
    if tag in _KNOWN_LABELS:
        return _KNOWN_LABELS[tag]
    base = tag[:-7] if tag.endswith(':latest') else tag
    base = ' '.join(base.replace(':', ' ').replace('_', ' ').replace('-', ' ').split())
    return (base[:1].upper() + base[1:]) if base else 'Installed model'


def model_note(model_id: str) -> str:
    """One line for the model menu. Base weights are not described as Sumi or Rei."""
    tag = canonical_tag(model_id)
    if is_sumi(model_id):
        return 'Full profile · recommended'
    if is_rei(model_id):
        return 'Lightweight profile'
    if is_sumi_lite(model_id):
        return 'Older lightweight profile'
    if tag == canonical_tag(UPSTREAM):
        return 'Base weights · not the Sumi profile'
    if tag == canonical_tag(REI_UPSTREAM):
        return 'Base weights · not the Rei profile'
    return 'Installed on this PC'


def catalog(installed: list[str]) -> list[dict]:
    rows = []
    for raw in installed:
        if not isinstance(raw, str) or not raw:
            continue
        branded = is_sumi(raw) or is_rei(raw) or is_sumi_lite(raw)
        rows.append({'id': raw, 'label': display_name(raw), 'note': model_note(raw),
                     'sumi': is_sumi(raw), 'rei': is_rei(raw),
                     'model_class': 'Takumi engineering profile' if branded else 'Installed local model'})
    rows.sort(key=lambda item: (
        0 if item['sumi'] else 1 if item['rei'] else 2 if is_sumi_lite(item['id']) else 3,
        item['label'].casefold()))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row['label']] = counts.get(row['label'], 0) + 1
    for row in rows:
        if counts[row['label']] > 1:
            row['label'] = row['label'] + ' · ' + row['id']
    return rows


def default_model(installed: list[str]) -> str:
    names = [row['id'] for row in catalog(installed)]
    return next((m for m in names if is_sumi(m)),
                next((m for m in names if is_rei(m)),
                     next((m for m in names if is_sumi_lite(m)),
                          next((m for m in names if m == UPSTREAM), names[0] if names else ''))))


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding='utf-8'))


def rei_manifest() -> dict:
    return json.loads(REI_MANIFEST.read_text(encoding='utf-8'))


def state(installed: list[str]) -> dict:
    return {'name': NAME, 'version': '1.1',
            'installed': any(is_sumi(x) for x in installed),
            'base_installed': UPSTREAM in installed,
            'installed_rei': any(is_rei(x) for x in installed),
            'rei_base_installed': REI_UPSTREAM in installed,
            'installed_lite': any(is_sumi_lite(x) for x in installed),
            'rei': rei_manifest(), 'registration_script': 'scripts/Register-Sumi-1.0.ps1',
            'profile': manifest()}


def _register(tag: str, upstream: str, filename: str, label: str, installed: list[str], *, runner=None, engine=None) -> dict:
    import shutil
    import subprocess
    if any(canonical_tag(x) == canonical_tag(tag) for x in installed):
        return {'registered': True, 'already_installed': True, 'model': label}
    if upstream not in installed:
        raise ValueError(f'{label} base weights are missing. Use the guided model setup in Takumi.')
    exe = engine or shutil.which('ollama')
    if not exe:
        raise ValueError('The local inference engine is not installed')
    modelfile = MODEL_DIR / filename
    if not modelfile.is_file():
        raise ValueError(f'{label} profile is missing from this installation')
    result = (runner or subprocess.run)([exe, 'create', tag.split(':')[0], '-f', str(modelfile)],
                                        capture_output=True, text=True, timeout=120, shell=False)
    if result.returncode:
        raise RuntimeError(f'{label} setup failed. Original models were not modified.')
    return {'registered': True, 'already_installed': False, 'model': label}


def register_sumi(installed: list[str], *, runner=None, engine=None) -> dict:
    return _register(MODEL_TAG, UPSTREAM, 'Sumi-1.0.Modelfile', NAME, installed, runner=runner, engine=engine)


def register_rei(installed: list[str], *, runner=None, engine=None) -> dict:
    return _register(REI_TAG, REI_UPSTREAM, 'Rei-1.0.Modelfile', REI_NAME, installed, runner=runner, engine=engine)
