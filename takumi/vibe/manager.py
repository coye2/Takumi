"""One verified run at a time, in the background, with saved reports."""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time
import uuid

from . import platforms
from .client import DEFAULT_MODEL, ModelUnavailable, OllamaClient
from .context import REI_BUDGET, budget_for
from .loop import Options, VibeRun
from .memory import EngineeringMemory

ALLOWED_MODELS = {'sumi-1.0:latest': 'Sumi 1.1', 'rei-1.0:latest': 'Rei 1.1'}
EVENT_LIMIT = 400
MAX_TASK = 12000


class VibeManager:
    def __init__(self, base: Path, scheduler, client_factory=OllamaClient, source_index=None):
        self.directory = Path(base) / 'vibe-runs'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.memory = EngineeringMemory(Path(base) / 'engineering-memory.json')
        self.scheduler, self.client_factory = scheduler, client_factory
        self.source_index = source_index
        self.lock = threading.RLock()
        self.current: dict | None = None
        self.cancel: threading.Event | None = None
        self.thread: threading.Thread | None = None
        self._listen = None
        self._on_finished = None

    def is_busy(self) -> bool:
        with self.lock:
            return bool(self.current and self.current['state'] == 'running')

    def plan(self, project: Path) -> dict:
        plan = platforms.detect(Path(project))
        return {**plan, 'steps': [s.public() for s in plan['steps']]}

    def start(self, project: Path, task: str, *, model: str = DEFAULT_MODEL, allow_config: bool = False, max_attempts: int = 4,
              listen=None, on_finished=None) -> dict:
        task = (task or '').strip()
        if not task:
            raise ValueError('Describe what to build or fix')
        if len(task) > MAX_TASK:
            raise ValueError('That task is too long; put the details in a project file')
        if model not in ALLOWED_MODELS:
            raise ValueError('Choose Sumi 1.1 or Rei 1.1')
        if type(max_attempts) is not int or not 1 <= max_attempts <= 8:
            raise ValueError('Choose 1 to 8 attempts')
        with self.lock:
            if self.is_busy():
                raise ValueError('A verified run is already active')
            # Training and GPU-exclusive tests keep the GPU; never compete with them.
            if self.scheduler.is_reserved():
                raise ValueError('The GPU is reserved by training or a performance test. Try again when it finishes.')
            run_id = uuid.uuid4().hex
            self.cancel = threading.Event()
            self._listen, self._on_finished = listen, on_finished
            self.current = {'id': run_id, 'state': 'running', 'task': task, 'project': str(project), 'model': model,
                            'started': time.time(), 'events': []}
            self.thread = threading.Thread(target=self._run, args=(run_id, Path(project), task, model, allow_config, max_attempts), daemon=True, name='takumi-vibe')
            self.thread.start()
            return self.status()

    def _on_event(self, event: dict):
        listener = None
        with self.lock:
            if self.current:
                self.current['events'].append(event)
                del self.current['events'][:-EVENT_LIMIT]
            listener = self._listen
        if listener and event.get('name'):
            try:
                listener(event)
            except Exception:
                pass

    def _run(self, run_id, project, task, model, allow_config, max_attempts):
        budget = budget_for(model)
        try:
            try:
                recalled = self.memory.recall(project)
            except (OSError, ValueError):
                recalled = {'fresh': False}
            client = self.client_factory(model, budget=budget)
            snapshot = None
            if self.source_index is not None:
                try:
                    snapshot = self.source_index.load(project)
                except (OSError, ValueError):
                    snapshot = None
            report = VibeRun(project, task, client, cancel=self.cancel, on_event=self._on_event, snapshot=snapshot,
                             source_index=self.source_index,
                             options=Options(max_attempts=max_attempts, allow_config=allow_config, budget=budget,
                                             recall=recalled['summary'] if recalled.get('fresh') else '',
                                             # Measured on Sumi only. Edge-case step: edge-holdout3.json.
                                             # Reasoning in that step: reasoning-holdout4-b-edge.json. Rei was not measured.
                                             edge_tests=budget != REI_BUDGET,
                                             edge_think=budget != REI_BUDGET)).run()
            try:
                self.memory.record(project, report)
            except OSError:
                pass
        except ModelUnavailable as exc:
            report = {'state': 'model_unavailable', 'detail': str(exc)}
        except Exception as exc:  # a run must end in a truthful state, whatever happened
            report = {'state': 'failed', 'detail': 'The run stopped on an internal error: ' + str(exc)[:400]}
        finished = None
        with self.lock:
            finished = self._on_finished
        if finished:
            try:
                finished(report)
            except Exception:
                pass
        with self.lock:
            self.current.update(state=report['state'], report=report, ended=time.time())
            try:
                target = self.directory / (run_id + '.json')
                target.write_text(json.dumps(self.current, indent=1), encoding='utf-8')
            except OSError:
                pass

    def stop(self) -> dict:
        with self.lock:
            if self.is_busy() and self.cancel:
                self.cancel.set()
            return self.status()

    def status(self) -> dict:
        with self.lock:
            return {'current': json.loads(json.dumps(self.current)) if self.current else None, 'gpu': self.scheduler.status(),
                    'models': ALLOWED_MODELS, 'history': self._history()}

    def _history(self) -> list[dict]:
        rows = []
        for path in sorted(self.directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            try:
                data = json.loads(path.read_text('utf-8'))
                rows.append({'id': data['id'], 'state': data['state'], 'task': data['task'][:160], 'ended': data.get('ended')})
            except (OSError, ValueError, KeyError):
                continue
        return rows
