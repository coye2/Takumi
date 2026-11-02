"""Explicit local app launches inside approved project trees.

This is not generalized remote desktop access and is deliberately not an
arbitrary shell or command endpoint. Only child processes launched here are
manageable through the API.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path


class ProcessSupervisor:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.processes: dict[str, tuple[subprocess.Popen, dict]] = {}

    def launch(self, project_id: str, relative_path: str) -> dict:
        project = self.store.get_project(project_id)
        if not project:
            raise ValueError("Choose a registered project first")
        root = Path(project["path"]).resolve(strict=True)
        if not isinstance(relative_path, str) or not relative_path or len(relative_path) > 240:
            raise ValueError("Select an application inside your project")
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts or relative_path.startswith("\\\\"):
            raise ValueError("Choose a path relative to your project folder")
        target = (root / relative_path).resolve(strict=True)
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError("Application must be a file inside the selected project")
        if os.name == "nt":
            if target.suffix.lower() != ".exe":
                raise ValueError("Only Windows .exe applications can be launched")
        elif not os.access(target, os.X_OK):
            raise ValueError("This application is not executable")
        with self.lock:
            self._clean()
            if len(self.processes) >= 3:
                raise ValueError("Maximum three project applications running")
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            proc = subprocess.Popen([str(target)], cwd=str(target.parent), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=flags, start_new_session=os.name != "nt")
            uid = uuid.uuid4().hex
            meta = {"id": uid, "pid": proc.pid, "name": target.name,
                    "project_id": project_id, "started": int(time.time())}
            self.processes[uid] = proc, meta
            return dict(meta)

    def _clean(self):
        stale = [uid for uid, (proc, _) in self.processes.items() if proc.poll() is not None]
        for uid in stale:
            del self.processes[uid]

    def list(self) -> list[dict]:
        with self.lock:
            self._clean()
            return [dict(metadata) for proc, metadata in self.processes.values()]

    def stop(self, uid: str) -> bool:
        with self.lock:
            entry = self.processes.pop(uid, None)
        if not entry:
            return False
        p, _ = entry
        if p.poll() is not None:
            return True
        if os.name == "nt":
            try:
                subprocess.run(["taskkill.exe", "/PID", str(p.pid), "/T", "/F"],
                               shell=False, capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                p.terminate()
        else:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                p.kill()
        return True
