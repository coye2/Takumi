"""Read-only reproducibility evidence. Prompt/template contents and secrets are omitted."""
import hashlib
import platform
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone

from .core import json_request, open_code_path, reasoning_profile
from .intelligence import profile_for, safe_temperature


def inventory(model, speed='standard', effort='balanced'):
    def probe(path, data=None):
        try:
            return {'available': True, 'data': json_request(path, data, timeout=4)}
        except (OSError, ValueError) as exc:
            return {'available': False, 'error': type(exc).__name__}
    version, tags, show, resident = (probe('/api/version'), probe('/api/tags'),
                                    probe('/api/show', {'model': model}), probe('/api/ps'))
    record = next((m for m in tags.get('data', {}).get('models', []) if m.get('name') == model), {})
    details = show.get('data', {})
    engine = open_code_path()
    engine_version = None
    if engine:
        try:
            result = subprocess.run([engine, '--version'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                engine_version = result.stdout.strip()[:100]
        except (OSError, subprocess.TimeoutExpired):
            pass
    gpu = safe_temperature()
    return {
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'platform': platform.platform(), 'python': sys.version.split()[0],
        'ollama': version, 'open_code': {'path': engine, 'version': engine_version,
            'adapter': 'installed OpenCode JSONL + Ollama OpenAI-compatible API'},
        'model': {'tag': model, 'digest': record.get('digest'), 'disk_bytes': record.get('size'),
            'details': details.get('details'), 'capabilities': details.get('capabilities'),
            'parameters': details.get('parameters'), 'native_thinking': reasoning_profile(model),
            'template_sha256': hashlib.sha256(details['template'].encode()).hexdigest() if 'template' in details else None,
            'system_sha256': hashlib.sha256(details['system'].encode()).hexdigest() if 'system' in details else None,
            'available': show['available'], 'error': show.get('error')},
        'requested_profile': asdict(profile_for(speed, effort, model, gpu.get('memory_total_mb'))),
        'resident_models': resident, 'gpu': gpu,
        'limits': 'Requested budgets are not observed allocation. Runtime resident records describe effective loaded models. '
                  'A local alias is not new model training. Upstream license/revision is not verified by this inventory.'}
