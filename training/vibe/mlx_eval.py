"""Greedy held-out generation inside the exact-base trainer, plus host scoring.

The MLX environment lives in WSL, which may not have every toolchain, so it only
records replies. The Windows side then scores those replies with real toolchains:

    py -3 -m training.vibe.mlx_eval RUN_FOLDER

No score exists until host scoring has run. Inside the trainer `passed` stays None.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time

MAX_GENERATED = 420
MAX_PROMPT = 1400
SPLIT = 'test'  # the trainer sets this from its training config


def score(model, tokenizer, out, label):
    import mlx.core as mx
    from training.bonsai.smoke import evaluated_layers
    from .dataset import VERSION, prompt_for, validator_sha256
    from .tasks import TASKS
    validator = validator_sha256()
    report = {'label': label, 'suite': VERSION, 'decoding': 'greedy', 'max_generated_tokens': MAX_GENERATED, 'validator_sha256': validator,
              'split': SPLIT, 'scored': False, 'tasks': [], 'qualification': 'Replies only; scored afterward on the host with real toolchains.'}
    path = Path(out) / f'heldout-{label}.json'
    for task in TASKS:
        if task.split != SPLIT:
            continue
        started = time.monotonic()
        rendered = tokenizer.apply_chat_template(prompt_for(task), tokenize=False, add_generation_prompt=True, enable_thinking=False)
        ids = tokenizer.encode(rendered, add_special_tokens=False)
        if len(ids) > MAX_PROMPT:
            raise ValueError(f'{task.id}: prompt has {len(ids)} tokens; the locked budget is {MAX_PROMPT}')
        cache = model.make_cache()
        generated, ended = [], False
        with evaluated_layers(model):
            logits = model(mx.array([ids]), cache=cache)
            mx.eval(logits, [c.state for c in cache])
        for _ in range(MAX_GENERATED):
            token = int(mx.argmax(logits[0, -1]).item())
            del logits
            if token == tokenizer.eos_token_id:
                ended = True
                break
            generated.append(token)
            logits = model(mx.array([[token]]), cache=cache)
            mx.eval(logits, [c.state for c in cache])
        report['tasks'].append({'id': task.id, 'project': task.platform, 'output': tokenizer.decode(generated, skip_special_tokens=True), 'ended': ended,
                                'prompt_tokens': len(ids), 'generated_tokens': len(generated), 'seconds': time.monotonic() - started, 'passed': None})
        print(f'Held-out {label}: {task.id} recorded', flush=True)
        del cache
        mx.clear_cache()
        path.write_text(json.dumps(report, indent=2))
    if validator_sha256() != validator:
        raise RuntimeError('Validator changed during evaluation')
    report['total'] = len(report['tasks'])
    path.write_text(json.dumps(report, indent=2))
    return {'passed': None, 'total': report['total'], 'validator_sha256': validator, 'scored_on_host': False}


def host_score(folder: Path) -> dict:
    """Run hidden tests against recorded replies; compare base with candidate."""
    from .dataset import score_reply, validator_sha256
    from .tasks import TASKS
    tasks = {t.id: t for t in TASKS}
    summary = {'folder_sha256': hashlib.sha256(str(folder.name).encode()).hexdigest()[:12], 'labels': {}}
    for path in sorted(folder.glob('heldout-*.json')):
        report = json.loads(path.read_text())
        if report.get('validator_sha256') != validator_sha256():
            raise ValueError(f'{path.name} used different tasks or a different prompt')
        for row in report['tasks']:
            result = score_reply(tasks[row['id']], row['output']) if row['ended'] else {'passed': False, 'reason': 'Generation budget exhausted'}
            row.update(passed=result['passed'], reason=result.get('reason', ''))
        report['scored'], report['passed'] = True, sum(r['passed'] is True for r in report['tasks'])
        path.write_text(json.dumps(report, indent=2))
        summary['labels'][report['label']] = {'passed': report['passed'], 'total': len(report['tasks']),
                                              'tasks': {r['id']: r['passed'] for r in report['tasks']}}
    (folder / 'heldout-host-score.json').write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == '__main__':
    print(json.dumps(host_score(Path(sys.argv[1])), indent=1))
