"""Decisions the training guard can make without loading Sumi."""
from pathlib import Path
import json

YIELD_NAME = 'yield'


def gpu_streak(free_mib: float, streak: int, *, floor: float = 1024.0, required: int = 3) -> tuple[int, bool]:
    """A single backward spike is not a reason to throw away the run.

    The display guard trips only after several consecutive samples stay under
    the floor. One sample recovers the streak to zero.
    """
    streak = streak + 1 if free_mib < floor else 0
    return streak, streak >= required


def yield_path(directory) -> Path:
    return Path(directory) / YIELD_NAME


def request_yield(directory) -> Path:
    path = yield_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('1', encoding='utf-8')
    return path


def yield_requested(directory) -> bool:
    return yield_path(directory).is_file()


def prior_report(folder) -> dict:
    folder = Path(folder)
    for name in ('smoke-result.json', 'smoke-progress.json'):
        path = folder / name
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict) and 'optimizer_steps' in data:
            return data
    raise RuntimeError('That run has no saved training progress')


def continue_steps(prior: dict, current_version: str) -> int:
    """Same suite can continue after the set gains repair rows. A different suite cannot."""
    if prior.get('dataset_version') != current_version:
        raise RuntimeError('That adapter belongs to a different training set. Start that set from the base model.')
    steps = int(prior.get('optimizer_steps') or 0)
    if steps < 1:
        raise RuntimeError('That run did not finish an optimizer step')
    return steps
