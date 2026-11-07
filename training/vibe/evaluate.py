"""Measure Sumi through the verified loop on tasks it did not train on.

    py -3 -m training.vibe.evaluate --split test --out report.json

Scores come from hidden tests the loop never saw, run with the real toolchain
after the loop has finished. Nothing is rewarded for the loop's own opinion.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import platform as host
import shutil
import subprocess
import tempfile
import time
import urllib.request

from takumi.vibe.client import BASE_URL, DEFAULT_MODEL, OllamaClient, require_batch_memory
from takumi.vibe.context import budget_for
from takumi.vibe.loop import Options, VibeRun

from .dataset import VERSION, materialize, score_project, score_reply, steps_for, validator_sha256
from .holdout3 import HOLDOUT3, holdout3_sha256
from .holdout4 import HOLDOUT4, holdout4_sha256
from .tasks import TASKS


def inventory(model: str) -> dict:
    info = {'model': model, 'host': host.platform()}
    try:
        with urllib.request.urlopen(BASE_URL + '/api/tags', timeout=5) as response:
            for item in json.load(response).get('models', []):
                if item.get('name') == model:
                    info.update(digest=item.get('digest'), quantization=(item.get('details') or {}).get('quantization_level'))
        with urllib.request.urlopen(BASE_URL + '/api/version', timeout=5) as response:
            info['ollama'] = json.load(response).get('version')
    except OSError as exc:
        info['inventory_error'] = str(exc)
    try:
        info['gpu'] = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader'],
                                     capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def run_task(task, client, *, max_attempts: int, edge_tests: bool = False, edge_think: bool = False, main_think: bool = False, edge_review: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix='vibe-', ignore_cleanup_errors=True) as raw:
        folder = Path(raw) / 'p'
        folder.mkdir()
        materialize(task, folder)
        steps = steps_for(task, folder)
        if any(s.blocked for s in steps):
            return {'id': task.id, 'platform': task.platform, 'runner': task.runner, 'state': 'excluded', 'hidden_passed': None,
                    'reason': next(s.blocked for s in steps if s.blocked)}
        plan = {'platforms': [task.platform], 'steps': steps, 'runnable': len(steps), 'has_tests': True, 'can_prove': True, 'blocked': []}
        first_reply: list[str] = []

        class Recording:
            model = client.model

            def complete(self, messages, **kwargs):
                reply = client.complete(messages, **kwargs)
                first_reply.append(reply['text'])
                return reply

        # The same run gives both arms: the score just before the edge-case step
        # is what this run would have delivered without it.
        before_edge: dict = {}

        def watch(event):
            if event['kind'] == 'edge_start':
                before_edge.update(score_project(task, folder), state=event['state'])

        run = VibeRun(folder, task.prompt, Recording(), on_event=watch, plan=plan,
                      options=Options(max_attempts=max_attempts, keep_failed=True, budget=client.budget, edge_tests=edge_tests, edge_think=edge_think, main_think=main_think, edge_review=edge_review))
        report = run.run()
        hidden = score_project(task, folder, drop=tuple(report['edge_tests'].get('new_files', ())))
        # The one-shot baseline: the first reply alone, with no repair loop.
        one_shot = score_reply(task, first_reply[0]) if first_reply else {'passed': False, 'reason': 'no reply'}
    return {'id': task.id, 'split': task.split, 'platform': task.platform, 'runner': task.runner, 'state': report['state'],
            'replies': [text[:3000] for text in first_reply], 'edge_tests': report['edge_tests'], 'state_before_edge': before_edge.get('state', report['state']),
            'hidden_before_edge': before_edge.get('passed', hidden['passed']),
            'attempts': report['attempts'], 'hidden_passed': hidden['passed'], 'hidden_reason': hidden.get('reason', ''),
            'one_shot_passed': one_shot['passed'], 'seconds': report['seconds'], 'output_tokens': report['output_tokens'],
            'prompt_tokens': [a.get('prompt_tokens') for a in report['attempt_log']], 'prompt_ms': [a.get('prompt_ms') for a in report['attempt_log']],
            'tokens_per_second': [a.get('tokens_per_second') for a in report['attempt_log']],
            'edit_errors': [a['edit_error'][:200] for a in report['attempt_log'] if a.get('edit_error')],
            'diagnostics': [d['message'][:200] for d in report['diagnostics'][:3]], 'tests_edited': report['tests_edited'], 'diff': report['diff'][:2500]}


def summarize(rows: list[dict]) -> dict:
    scored = [r for r in rows if r['hidden_passed'] is not None]
    grouped = defaultdict(list)
    for row in scored:
        grouped[row['platform']].append(row)
    return {'tasks': len(rows), 'scored': len(scored), 'excluded': [r['id'] for r in rows if r['hidden_passed'] is None],
            'hidden_passed': sum(r['hidden_passed'] is True for r in scored), 'one_shot_passed': sum(r['one_shot_passed'] is True for r in scored),
            'visible_verified': sum(r['state'] == 'verified' for r in scored), 'false_verified': [r['id'] for r in scored if r['state'] == 'verified' and not r['hidden_passed']],
            'before_edge': {'hidden_passed': sum(r.get('hidden_before_edge', r['hidden_passed']) is True for r in scored),
                            'false_verified': [r['id'] for r in scored if r.get('state_before_edge', r['state']) == 'verified' and not r.get('hidden_before_edge', r['hidden_passed'])]},
            'edge_gained': [r['id'] for r in scored if r['hidden_passed'] and not r.get('hidden_before_edge', r['hidden_passed'])],
            'edge_lost': [r['id'] for r in scored if r.get('hidden_before_edge', r['hidden_passed']) and not r['hidden_passed']],
            # Correct work the step refused to call verified, against wrong work it stopped calling verified.
            'edge_false_alarms': [r['id'] for r in scored if r.get('state_before_edge') == 'verified' and r['state'] != 'verified' and r['hidden_passed']],
            'edge_caught': [r['id'] for r in scored if r.get('state_before_edge') == 'verified' and not r.get('hidden_before_edge') and r['state'] != 'verified'],
            'by_platform': {k: {'hidden_passed': sum(r['hidden_passed'] is True for r in v), 'one_shot_passed': sum(r['one_shot_passed'] is True for r in v), 'total': len(v)} for k, v in sorted(grouped.items())},
            'median_seconds': sorted(r['seconds'] for r in scored)[len(scored) // 2] if scored else None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=('train', 'dev', 'test', 'holdout2', 'holdout3', 'holdout4', 'all'), default='test')
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--max-attempts', type=int, default=4)
    parser.add_argument('--edge-tests', action='store_true', help='add the edge-case step and record both arms')
    parser.add_argument('--edge-think', action='store_true', help='let the edge-case step reason before replying')
    parser.add_argument('--main-think', action='store_true', help='let the first reply and its repairs reason too')
    parser.add_argument('--edge-review', action='store_true', help='review new tests without the code, and break the code to test them')
    parser.add_argument('--only', default='', help='comma-separated task ids')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    require_batch_memory()
    client = OllamaClient(args.model, budget=budget_for(args.model))
    chosen = [t for t in [*TASKS, *(HOLDOUT3 if args.split == 'holdout3' else ()), *(HOLDOUT4 if args.split == 'holdout4' else ())] if (args.split == 'all' or t.split == args.split) and (not args.only or t.id in args.only.split(','))]
    report = {'suite': VERSION, 'split': args.split, 'validator_sha256': validator_sha256(), 'holdout3_sha256': holdout3_sha256(), 'holdout4_sha256': holdout4_sha256(), 'edge_tests': args.edge_tests, 'edge_think': args.edge_think, 'main_think': args.main_think, 'edge_review': args.edge_review, 'started': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
              'inventory': inventory(args.model), 'context_budget': vars(client.budget) if hasattr(client.budget, '__dict__') else {},
              'max_attempts': args.max_attempts, 'qualification': 'Small curated fixtures. Measures this loop on these tasks, not whole-project skill or parity with any other product.',
              'tasks': []}
    for task in chosen:
        row = run_task(task, client, max_attempts=args.max_attempts, edge_tests=args.edge_tests, edge_think=args.edge_think, main_think=args.main_think, edge_review=args.edge_review)
        report['tasks'].append(row)
        report['summary'] = summarize(report['tasks'])
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1) + '\n', encoding='utf-8')
        print(row['id'], row['state'], 'hidden=' + str(row['hidden_passed']), 'one_shot=' + str(row.get('one_shot_passed')), 'attempts=' + str(row.get('attempts')), str(row.get('seconds')) + 's', flush=True)
    print(json.dumps(report.get('summary', {}), indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
