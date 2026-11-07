"""Measure what Sumi actually does at each context size on this GPU.

    py -3 -m takumi.vibe.tuning --out evidence.json

Only request options change; no profile or weights are modified. Each size loads
the model again, so run this while nothing else is using Sumi.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.request

from .client import BASE_URL, DEFAULT_MODEL, require_batch_memory

# Stops at the largest size proven on the owner's PC. An unclean reboot followed
# the first 49152 attempt on 2026-11-04 under low RAM, cause not established, so
# larger sizes stay out of the default until a guarded run clears them.
SIZES = (8192, 16384, 24576, 32768)
FILLER = '\n'.join(f'def helper_{i}(value):\n    """Return value adjusted by {i}."""\n    return value + {i}\n' for i in range(900))
QUESTION = '\n\nName the helper that adds 7. Reply with its name only, then write a short Python function that adds the first ten helpers.'


def _post(path: str, body: dict, timeout: int = 900) -> dict:
    request = urllib.request.Request(BASE_URL + path, json.dumps(body).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _loaded(model: str) -> dict:
    with urllib.request.urlopen(BASE_URL + '/api/ps', timeout=10) as response:
        for item in json.load(response).get('models', []):
            if item.get('name') == model:
                return item
    return {}


def _gpu_used_mib() -> int | None:
    try:
        output = subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10).stdout
        return int(output.strip().split('\n')[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def _rates(reply: dict) -> dict:
    prompt_ns, eval_ns = reply.get('prompt_eval_duration') or 0, reply.get('eval_duration') or 0
    return {'prompt_tokens': reply.get('prompt_eval_count', 0), 'prompt_seconds': round(prompt_ns / 1e9, 2),
            'prompt_tps': round(reply.get('prompt_eval_count', 0) / (prompt_ns / 1e9), 1) if prompt_ns else None,
            'output_tokens': reply.get('eval_count', 0), 'output_tps': round(reply.get('eval_count', 0) / (eval_ns / 1e9), 1) if eval_ns else None,
            'load_seconds': round((reply.get('load_duration') or 0) / 1e9, 2), 'done_reason': reply.get('done_reason')}


def measure(model: str, size: int, *, prompt_chars: int, num_predict: int = 160) -> dict:
    _post('/api/generate', {'model': model, 'keep_alive': 0})
    prompt = FILLER[:prompt_chars] + QUESTION
    options = {'num_ctx': size, 'num_predict': num_predict, 'temperature': 0, 'seed': 27}
    cold = _post('/api/generate', {'model': model, 'prompt': prompt, 'stream': False, 'think': False, 'keep_alive': '3m', 'options': options})
    loaded = _loaded(model)
    # Same prefix, new ending: what a repair attempt pays with a stable prefix.
    repeat = _post('/api/generate', {'model': model, 'prompt': prompt + '\nAlso name the helper that adds 8.', 'stream': False, 'think': False, 'keep_alive': '3m', 'options': options})
    total, vram = loaded.get('size') or 0, loaded.get('size_vram') or 0
    return {'num_ctx': size, 'cold': _rates(cold), 'repeat_prefix': _rates(repeat), 'gpu_used_mib': _gpu_used_mib(),
            'model_size_bytes': total, 'size_vram_bytes': vram, 'gpu_fraction': round(vram / total, 3) if total else None, 'context_reported': loaded.get('context_length')}


def recommend(rows: list[dict]) -> dict:
    """The largest context that stays fully on the GPU without losing more than
    15% of the best output speed. Spilling to RAM is what ruins interactivity."""
    usable = [r for r in rows if r.get('gpu_fraction') == 1.0 and r['cold'].get('output_tps')]
    if not usable:
        return {'num_ctx': None, 'reason': 'No measured size stayed fully on the GPU.'}
    best = max(r['cold']['output_tps'] for r in usable)
    chosen = max((r for r in usable if r['cold']['output_tps'] >= best * 0.85), key=lambda r: r['num_ctx'])
    return {'num_ctx': chosen['num_ctx'], 'output_tps': chosen['cold']['output_tps'], 'reason': 'Largest size fully on GPU within 15% of the best measured speed.'}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--sizes', default=','.join(map(str, SIZES)))
    args = parser.parse_args(argv)
    require_batch_memory()
    report = {'model': args.model, 'started': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'rows': []}
    for size in map(int, args.sizes.split(',')):
        # Fill about half the window so eviction and prompt speed both show up.
        row = measure(args.model, size, prompt_chars=int(size * 0.5 * 3.2))
        report['rows'].append(row)
        report['recommendation'] = recommend(report['rows'])
        args.out.write_text(json.dumps(report, indent=1) + '\n', encoding='utf-8')
        print(size, 'gpu', row['gpu_fraction'], 'out', row['cold']['output_tps'], 'prompt', row['cold']['prompt_tps'], 'repeat_prompt_s', row['repeat_prefix']['prompt_seconds'], flush=True)
    _post('/api/generate', {'model': args.model, 'keep_alive': 0})
    print(json.dumps(report['recommendation']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
