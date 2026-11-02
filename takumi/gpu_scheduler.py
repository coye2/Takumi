"""Cooperative GPU work reservation; never changes clocks, voltage or fan limits.

A registered GPU-exclusive harness task reserves the GPU for its lifetime so
new Takumi inference is not started accidentally during performance capture.
"""
from __future__ import annotations

import threading
import time


class GpuScheduler:
    def __init__(self, is_model_busy=lambda: False, unload=None):
        self.lock = threading.RLock()
        self.active: str | None = None
        self.since = 0.0
        self.is_model_busy = is_model_busy
        self.unload = unload

    def acquire(self, task_id: str) -> None:
        if not task_id:
            raise ValueError("A GPU task ID is required")
        with self.lock:
            if self.active is not None:
                raise ValueError("A GPU-exclusive task is already running")
            if self.is_model_busy():
                raise ValueError("Stop active Sumi/Rei inference before a GPU-exclusive performance test")
            self.active = task_id
            self.since = time.time()
        if self.unload:
            try:
                self.unload()
            except Exception as exc:
                self.release(task_id)
                raise ValueError("The local model could not release GPU memory: " + str(exc)[:180]) from exc

    def release(self, task_id: str) -> bool:
        with self.lock:
            if self.active != task_id:
                return False
            self.active = None
            self.since = 0
            return True

    def is_reserved(self) -> bool:
        with self.lock:
            return self.active is not None

    def status(self) -> dict:
        with self.lock:
            return {"gpu_reserved": self.active is not None,
                    "task_id": self.active, "since": self.since or None,
                    "policy": "Cooperative model/test isolation, no GPU overclocking"}
