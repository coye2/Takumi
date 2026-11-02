"""Opt-in, local-only Sumi diagnostic benchmark. No paid APIs or hidden downloads.

Reports observed timings, token throughput, and a shallow rubric-coverage heuristic.
Rubric coverage is NOT correctness; tasks are not run against real games here.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .core import OLLAMA_URL

CASES = Path(__file__).resolve().parents[1] / 'models' / 'emulator-benchmark-v1.json'


def suite() -> dict:
    data = json.loads(CASES.read_text(encoding='utf-8'))
    cases = data.get('cases')
    if data.get('version') != 1 or not isinstance(cases, list) or not 1 <= len(cases) <= 30:
        raise ValueError('Invalid benchmark suite')
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get('prompt'), str) or not isinstance(case.get('checks'), list):
            raise ValueError('Invalid benchmark case')
        if len(case['prompt']) > 2000 or not (2 <= len(case['checks']) <= 8):
            raise ValueError('Benchmark out of limits')
        if case.get('id') in ids:
            raise ValueError('Duplicate benchmark case')
        ids.add(case['id'])
        if not all(isinstance(words, list) and 1 <= len(words) <= 6 and all(isinstance(x, str) and 1 <= len(x) <= 35 for x in words) for words in case['checks']):
            raise ValueError('Invalid keyword alternatives')
    return data


def coverage(text: str, checks: list[list[str]]) -> dict:
    """Token/phrase presence only. Never label this an accuracy or quality score."""
    normalized = re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()
    matched = []
    for alternatives in checks:
        found = any(re.search(r'(?<!\w)' + re.escape(re.sub(r'[^a-z0-9]+', ' ', word.lower()).strip()) + r'(?=\W|$)', normalized) for word in alternatives)
        matched.append(bool(found))
    return {'matched': sum(matched), 'total': len(matched), 'fraction': round(sum(matched)/len(matched), 3)}


def run_one(model: str, prompt: str, *, timeout: float = 150.0, post=None, clock=time.perf_counter) -> dict:
    payload = {'model': model, 'stream': True, 'messages': [
        {'role': 'system', 'content': 'Give a concise evidence-driven emulator engineering answer. Avoid invented measurements, builds, or claims that you ran a test. Explain how each claim would be verified.'},
        {'role': 'user', 'content': prompt}],
        'options': {'num_ctx': 8192, 'num_predict': 900, 'temperature': 0.1}, 'keep_alive': '15m'}
    start = clock()
    first = None
    texts = []
    metrics = {}
    req = urllib.request.Request(OLLAMA_URL + '/api/chat', data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'}, method='POST')
    opener = post or (lambda request, timeout: urllib.request.urlopen(request, timeout=timeout))
    with opener(req, timeout=timeout) as response:
        for line in response:
            packet = json.loads(line)
            chunk = packet.get('message', {}).get('content') or ''
            if chunk:
                if first is None:
                    first = clock()
                texts.append(chunk)
            if packet.get('done'):
                metrics = packet
                break
    end = clock()
    count, duration = metrics.get('eval_count'), metrics.get('eval_duration')
    return {'model': model, 'answer': ''.join(texts),
            'time_to_first_token_ms': round((first-start)*1000) if first is not None else None,
            'wall_ms': round((end-start)*1000),
            'tokens_per_second': round(count/(duration/1e9),1) if isinstance(count,int) and isinstance(duration,int) and duration>0 else None,
            'eval_count': count, 'prompt_eval_count': metrics.get('prompt_eval_count'),
            'done_reason': metrics.get('done_reason'), 'finished': bool(metrics.get('done'))}


def evaluate(models: list[str], count: int, output: Path, *, runner=run_one) -> dict:
    cases = suite()['cases'][:count]
    results = []
    for model in models:
        for case in cases:
            print(f'  {model}: {case["id"]}', flush=True)
            try:
                result = runner(model, case['prompt'])
                result['coverage'] = coverage(result['answer'], case['checks'])
                result['case'] = case['id']
                results.append(result)
            except (OSError, ValueError, urllib.error.URLError) as exc:
                results.append({'model': model, 'case': case['id'], 'status': 'unavailable', 'error_type': type(exc).__name__})
    summaries = []
    for model in models:
        values = [r for r in results if r['model'] == model and r.get('finished')]
        def med(key):
            nums = [x[key] for x in values if isinstance(x.get(key), (int, float))]
            return round(statistics.median(nums), 1) if nums else None
        summaries.append({'model': model, 'completed': len(values), 'requested': len(cases),
                          'median_first_token_ms': med('time_to_first_token_ms'),
                          'median_tokens_per_second': med('tokens_per_second'),
                          'median_wall_ms': med('wall_ms'),
                          'mean_rubric_fraction_not_accuracy': round(statistics.mean([x['coverage']['fraction'] for x in values]), 3) if values else None})
    report = {'suite': 'emulator-benchmark-v1', 'date_utc': datetime.now(timezone.utc).isoformat(),
              'disclaimer': 'Real generated outputs and timings only. Rubric keyword coverage is not correctness or benchmark equivalence. Review answers and test implementations manually.',
              'summaries': summaries, 'results': results}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', action='append', dest='models', default=None, help='Already installed local model; repeat for comparisons')
    parser.add_argument('--limit', type=int, default=4, help='Number of diagnostic cases to run (default 4, max 10)')
    parser.add_argument('--output', type=Path, default=Path.home()/'.takumi'/'benchmark-sumi-1.0.json')
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= min(10, len(suite()['cases'])):
        parser.error('Choose 1 through 10 cases')
    models = args.models or ['sumi-1.0:latest']
    if any(len(m) > 150 or not re.fullmatch(r'[a-zA-Z0-9_./:-]+', m) for m in models):
        parser.error('Invalid local model name')
    print('Sumi diagnostic: local model, no online API, no file edits. Real GPU timing may vary.', flush=True)
    report = evaluate(models, args.limit, args.output)
    for row in report['summaries']:
        print(f'  {row["model"]}: {row["completed"]}/{row["requested"]} completed | median {row["median_tokens_per_second"]} tok/sec | first {row["median_first_token_ms"]} ms | rubric presence {row["mean_rubric_fraction_not_accuracy"]}', flush=True)
    print('Wrote', args.output)
    return 0 if all(x['completed'] == x['requested'] for x in report['summaries']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
