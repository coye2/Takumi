"""Desktop-only orchestration of the exact-base experimental trainer."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid

BASE_SHA = '4f99aed01b8a877e153f9aa6569a4440fe17c59701cc0953e36d7f460549e70e'
TERMINAL = {'finished', 'failed', 'stopped'}


class Trainer:
    def __init__(self, base, root, scheduler):
        self.base, self.root, self.scheduler = Path(base), Path(root), scheduler
        self.directory = self.base / 'training-runs'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.current = None
        self.process = None
        self.state_file = self.directory / 'current.json'
        try:
            self.current = json.loads(self.state_file.read_text())
            if self.current['status'] not in TERMINAL:
                # The wrapper owns cancellation and verifies its WSL child's identity.
                (self.directory / (self.current['id'] + '.cancel')).touch()
                self.current['status'] = 'recovering'
                with self.scheduler.lock:
                    self.scheduler.active = 'training-' + self.current['id']
                    self.scheduler.since = time.time()
                self._save()
        except (OSError, ValueError, KeyError):
            self.current = None

    def _save(self):
        tmp = self.state_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.current, indent=2), encoding='utf-8')
        tmp.replace(self.state_file)

    def configuration(self):
        try:
            config = json.loads((self.base / 'training-config.json').read_text())
        except (OSError, ValueError):
            return {}
        return config if isinstance(config, dict) else {}

    def configure(self, config):
        with self.lock:
            if self.current and self.current['status'] not in TERMINAL:
                raise ValueError('Wait for the current training check to stop')
            if not all(isinstance(config.get(key), str) and config[key].strip() for key in ('python', 'gguf', 'gguf_python', 'tokenizer')):
                raise ValueError('Choose all four training paths')
            # Linux venv Python is a WSL symlink that Windows cannot dereference.
            clean = {key: os.path.abspath(config[key]) for key in ('python', 'gguf', 'gguf_python', 'tokenizer')}
            for key, value in clean.items():
                if not os.path.lexists(value):
                    raise ValueError('Missing training path: ' + key)
            if Path(clean['gguf']).name != 'sha256-' + BASE_SHA:
                raise ValueError('Choose the verified exact Sumi 27B blob')
            (self.base / 'training-config.json').write_text(json.dumps(clean, indent=2))
            return self.status()

    def status(self):
        with self.lock:
            job = dict(self.current) if self.current else None
            if job:
                output = self.directory / job['id']
                for name, key in [('result.json', 'result'), ('smoke-progress.json', 'progress')]:
                    try:
                        data = json.loads((output / name).read_text())
                        job[key] = {k: v for k, v in data.items() if 'hashes' not in k}
                    except (OSError, ValueError):
                        pass
                job['optimizer_steps'] = job.get('progress', {}).get('optimizer_steps',
                    job.get('result', {}).get('optimizer_steps', 0))
                host = output.with_suffix('.host.json')
                if job['status'] == 'recovering' and host.exists():
                    self.current['status'] = job['status'] = 'stopped'
                    self.scheduler.release('training-' + job['id'])
                    self._save()
                log = self.directory / (job['id'] + '.log')
                if log.exists():
                    with log.open('rb') as f:
                        f.seek(max(0, log.stat().st_size - 12000))
                        job['log'] = f.read().decode('utf-8', errors='replace')
            checkpoints = []
            finished = list(self.directory.glob('*/smoke-result.json'))
            parents = {path.parent for path in finished}
            reports = finished + [path for path in self.directory.glob('*/smoke-progress.json') if path.parent not in parents]
            for result in sorted(reports, key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
                try:
                    report = json.loads(result.read_text())
                    if report.get('optimizer_steps', 0) > 0 and (result.parent / 'adapter.safetensors').is_file():
                        checkpoints.append({'id': result.parent.name, 'steps': report['optimizer_steps']})
                except (OSError, ValueError):
                    continue
            return {'configured': bool(self.configuration()), 'config': self.configuration(), 'job': job, 'checkpoints': checkpoints,
                    'experimental': True, 'base': 'Exact Sumi Bonsai 2 27B',
                    'qualification': 'Experimental diagnostic training. Engineering improvement and deployment remain unqualified.'}

    def start(self, mode, steps=2, checkpoint_id=None):
        if mode not in ('forward', 'smoke', 'reload', 'pilot', 'specialist', 'vibe', 'continue'):
            raise ValueError('Unknown trainer action')
        if type(steps) is not int or not 1 <= steps <= 128:
            raise ValueError('Choose 1–128 bounded training steps')
        with self.lock:
            if self.current and self.current['status'] not in TERMINAL:
                raise ValueError('A training check is already active')
            config = self.configuration()
            if not config:
                raise ValueError('Configure the local training environment first')
            checkpoint = None
            if mode in ('reload', 'continue'):
                if not isinstance(checkpoint_id, str) or len(checkpoint_id) != 32 or any(c not in '0123456789abcdef' for c in checkpoint_id):
                    raise ValueError('Choose a saved training run')
                checkpoint = self.directory / checkpoint_id
                progress = (checkpoint / 'smoke-result.json').is_file() or (checkpoint / 'smoke-progress.json').is_file()
                if not (checkpoint / 'adapter.safetensors').is_file() or not progress:
                    raise ValueError('That run has no saved adapter')
                if mode == 'reload' and not (checkpoint / 'smoke-result.json').is_file():
                    raise ValueError('That run has no completed diagnostic checkpoint')
            job_id = uuid.uuid4().hex
            self.scheduler.acquire('training-' + job_id)
            self.current = {'id': job_id, 'mode': mode, 'status': 'checking', 'started': time.time(), 'optimizer_steps': 0}
            self._save()
            threading.Thread(target=self._run, args=(job_id, config, mode, steps, checkpoint), daemon=True).start()
            return dict(self.current)

    def _run(self, job_id, config, mode, steps, checkpoint):
        cancel = self.directory / (job_id + '.cancel')
        try:
            digest = hashlib.sha256()
            with open(config['gguf'], 'rb') as source:
                while chunk := source.read(8 * 1024**2):
                    if cancel.exists():
                        return
                    digest.update(chunk)
            if digest.hexdigest() != BASE_SHA:
                raise ValueError('Installed model bytes do not match the verified exact base')
            cmd = [sys.executable, str(self.root / 'scripts/run_bonsai_wsl_probe.py')]
            for key in ('python', 'gguf', 'gguf_python', 'tokenizer'):
                cmd += ['--' + key.replace('_', '-'), config[key]]
            cmd += ['--out', str(self.directory / job_id), '--cancel-file', str(cancel)]
            if mode in ('smoke', 'pilot', 'specialist', 'vibe', 'continue'):
                cmd += ['--smoke-steps', str(steps)]
            if mode == 'pilot':
                cmd += ['--dataset',str(self.root/'training/pilot/verified-v1.json'),'--max-seconds','1800']
            if mode == 'specialist':
                cmd += ['--dataset',str(self.root/'training/pilot/specialist-v1.json'),'--max-seconds','1800']
            if mode in ('vibe', 'continue'):
                # Replies are scored afterward with real toolchains: training.vibe.mlx_eval.
                cmd += ['--dataset',str(self.root/'training/vibe/vibe-coding-v1.json'),'--max-seconds','3600']
            if mode == 'continue' and checkpoint:
                cmd += ['--continue-from', str(checkpoint)]
            elif checkpoint:
                cmd += ['--reload-from', str(checkpoint)]
            with (self.directory / (job_id + '.log')).open('w', encoding='utf-8') as log:
                with self.lock:
                    if cancel.exists():
                        return
                    self.process = subprocess.Popen(cmd, cwd=self.root, stdout=log, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    self.current['status'] = 'running'
                    self._save()
                code = self.process.wait()
            with self.lock:
                self.current.update(status='finished' if code == 0 else 'failed', exit_code=code)
        except Exception as exc:
            with self.lock:
                self.current.update(status='failed', error=str(exc))
        finally:
            with self.lock:
                if cancel.exists():
                    self.current['status'] = 'stopped'
                self.current['ended'] = time.time()
                self.process = None
                self._save()
                self.scheduler.release('training-' + job_id)

    def stop(self):
        with self.lock:
            if self.current and self.current['status'] not in TERMINAL:
                (self.directory / (self.current['id'] + '.cancel')).touch()
                self.current['status'] = 'stopping'
                self._save()
            return self.status()

