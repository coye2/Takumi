"""Deterministic, bounded goal verification through the existing task harness."""
import time
from pathlib import Path

from .harness import completion_contract


def verify_completion(harness, project_id, contract, cancel, deadline, emit=lambda _: None):
    checks = contract.get('tasks', [])
    if not checks:
        return {'status': 'unverified', 'checks': [], 'message': 'No project completion checks were declared when this goal started.'}
    results = []
    root = Path(harness.store.get_project(project_id)['path'])
    for task in checks:
        if cancel.is_set() or time.time() >= deadline:
            return {'status': 'stopped', 'checks': results}
        if task.get('gpu_exclusive'):
            return {'status': 'blocked', 'checks': results, 'message': 'This completion check needs an exclusive GPU reservation. Run it separately after releasing the model.'}
        try:
            run = harness.launch(project_id, task['id'], expected_contract=contract)
        except (ValueError, OSError) as exc:
            return {'status': 'blocked', 'checks': results, 'message': str(exc)}
        emit({'type': 'progress', 'stage': 'verification', 'message': 'Running declared completion check: ' + task['name'], 'harness_run_id': run['id']})
        check_deadline = min(deadline, time.time() + 120)
        interrupted = None
        while True:
            result = next(r for r in harness.inspect(project_id) if r['id'] == run['id'])
            if result['status'] != 'running':
                break
            if cancel.is_set() or time.time() >= check_deadline:
                interrupted = 'stopped' if cancel.is_set() or time.time() >= deadline else 'timeout'
                harness.stop(run['id'])
                break
            cancel.wait(.05)
        results.append({**result, 'status': interrupted or result['status']})
        if interrupted:
            return {'status': 'stopped' if interrupted == 'stopped' else 'blocked', 'checks': results,
                    'message': 'The declared check was cancelled or exceeded its bounded time allowance.'}
        try:
            if completion_contract(root) != contract:
                raise ValueError('The completion checks or protected test files changed; success cannot be verified against the original contract.')
        except (ValueError, OSError) as exc:
            return {'status': 'blocked', 'checks': results, 'message': str(exc)}
        if result['exit_code'] != 0:
            return {'status': 'failed', 'checks': results, 'message': 'A declared completion check failed. Inspect its output and revise the implementation.'}
    return {'status': 'passed', 'checks': results,
            'message': 'All declared checks passed against the original protected test hashes. This verifies only their covered behavior.'}
