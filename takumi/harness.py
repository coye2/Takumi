"""Bounded, inspectable development/test harness. No shell or bundled CLI binaries.

Only explicitly declared project tasks run. A task is untrusted code with the
Windows user's permissions; use a non-admin Windows account for development.
"""
from __future__ import annotations

from collections import deque
import json
import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time

from .plugins import in_project

VALID_ID = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")


def task_definitions(project: Path) -> list[dict]:
    cfg = project / "takumi.tasks.json"
    if not cfg.is_file():
        return []
    cfg = in_project(project, "takumi.tasks.json")
    if cfg.stat().st_size > 64_000:
        raise ValueError("Task configuration is too large")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("tasks"), list):
        raise ValueError("Expected takumi.tasks.json with version=1 and a tasks array")
    result, ids = [], set()
    for entry in data["tasks"][:32]:
        if not isinstance(entry, dict):
            raise ValueError("Each task must be an object")
        tid = entry.get("id")
        argv = entry.get("argv")
        if not isinstance(tid, str) or not VALID_ID.fullmatch(tid) or tid in ids:
            raise ValueError("Invalid or duplicate task ID")
        if not isinstance(argv, list) or not 1 <= len(argv) <= 40 or not all(isinstance(a, str) and 0 < len(a) <= 600 and "\x00" not in a for a in argv):
            raise ValueError("Each task needs an argument list, not a shell command string")
        if argv[0].lower() in ("cmd", "cmd.exe", "sh", "bash", "powershell", "powershell.exe", "pwsh") and len(argv) < 3:
            raise ValueError("Interactive shells aren't supported; use a script with -File")
        cwd = str(entry.get("cwd", "."))
        in_project(project, cwd, must_be_dir=True)
        protected = entry.get('protected_files', [])
        if not isinstance(protected, list) or len(protected) > 100 or not all(isinstance(p, str) and 0 < len(p) < 500 for p in protected):
            raise ValueError('protected_files must be a list of at most 100 project files')
        for path in protected:
            if not in_project(project, path).is_file():
                raise ValueError('A protected test file is missing: ' + path)
        ids.add(tid)
        result.append({"id": tid, "name": str(entry.get("name") or tid)[:100], "argv": argv,
                       "cwd": cwd, "description": str(entry.get("description") or "")[:280],
                       "gpu_exclusive": entry.get("gpu_exclusive") is True,
                       "completion_check": entry.get('completion_check') is True,
                       "protected_files": protected})
    return result


def completion_contract(project: Path) -> dict:
    """Freeze declared checks and independent test hashes before the first edit.

    This is a regression guard, not isolation from hostile project code.
    """
    tasks = [t for t in task_definitions(project) if t['completion_check']]
    if len(tasks) > 8:
        raise ValueError('At most eight automatic completion checks are supported')
    paths = {'takumi.tasks.json'} | {p for t in tasks for p in t['protected_files']} if tasks else set()
    hashes = {}
    for path in sorted(paths):
        target = in_project(project, path)
        if not target.is_file() or target.stat().st_size > 8_000_000:
            raise ValueError('Protected check files must exist and be under 8 MB: ' + path)
        hashes[path] = hashlib.sha256(target.read_bytes()).hexdigest()
    return {'version': 1, 'tasks': tasks, 'sha256': hashes}


class Harness:
    def __init__(self, store, gpu_scheduler=None):
        self.store = store
        self.gpu_scheduler = gpu_scheduler
        self.lock = threading.RLock()
        self.runs: dict[str, dict] = {}

    def list_tasks(self, project_id: str) -> list[dict]:
        project = self.store.get_project(project_id)
        if not project:
            raise ValueError("Select a registered project")
        return task_definitions(Path(project["path"]))

    def launch(self, project_id: str, task_id: str, *, expected_contract=None) -> dict:
        project = self.store.get_project(project_id)
        if not project:
            raise ValueError("Select a registered project")
        root = Path(project["path"]).resolve(strict=True)
        if expected_contract is not None and completion_contract(root) != expected_contract:
            raise ValueError('The completion checks or protected test files changed after the goal started')
        task = next((t for t in task_definitions(root) if t["id"] == task_id), None)
        if task is None:
            raise ValueError("Unknown project-defined task")
        with self.lock:
            if any(r["status"] == "running" for r in self.runs.values()):
                raise ValueError("A test task is already running")
            argv = list(task["argv"])
            first = argv[0]
            if "/" in first or "\\" in first:
                resolved = in_project(root, first)
                if not resolved.is_file():
                    raise ValueError("Task executable must be a file inside the project")
                argv[0] = str(resolved)
            else:
                exe = shutil.which(first)
                if not exe:
                    raise ValueError("Task executable not found: " + first)
                argv[0] = exe
            cwd = in_project(root, task["cwd"], must_be_dir=True)
            if task.get("gpu_exclusive") and self.gpu_scheduler:
                self.gpu_scheduler.acquire(task_id)
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            try:
                p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                     bufsize=1, env=env, shell=False, creationflags=flags,
                                     start_new_session=os.name != "nt")
            except Exception:
                if task.get("gpu_exclusive") and self.gpu_scheduler:
                    self.gpu_scheduler.release(task_id)
                raise
            rid = secrets.token_hex(12)
            record = {"id": rid, "task_id": task_id, "name": task["name"], "project_id": project_id,
                      "pid": p.pid, "status": "running", "exit_code": None, "started": time.time(),
                      "finished": None, "gpu_exclusive": task.get("gpu_exclusive", False),
                      "log": deque(maxlen=350), "process": p}
            self.runs[rid] = record
            threading.Thread(target=self._drain, args=(record,), name="Takumi-Test-"+rid[:6], daemon=True).start()
            return self._public(record)

    def _drain(self, record):
        p = record["process"]
        try:
            assert p.stdout is not None
            for line in p.stdout:
                with self.lock:
                    record["log"].append(line.rstrip("\r\n")[:1500])
            code = p.wait()
        except Exception as exc:
            code = -1
            with self.lock:
                record["log"].append("Harness error: " + str(exc)[:300])
        finally:
            if p.stdout:
                p.stdout.close()
            with self.lock:
                record["exit_code"] = code
                record["status"] = "passed" if code == 0 else "failed"
                record["finished"] = time.time()
            if record.get("gpu_exclusive") and self.gpu_scheduler:
                self.gpu_scheduler.release(record["task_id"])

    def _public(self, record: dict) -> dict:
        return {k: v for k, v in record.items() if k not in ("log", "process")}

    def inspect(self, project_id: str | None = None) -> list[dict]:
        with self.lock:
            return [{**self._public(r), "tail": "\n".join(list(r["log"])[-80:])}
                    for r in list(self.runs.values())[-20:]
                    if project_id is None or project_id == r["project_id"]]

    def stop(self, run_id: str) -> bool:
        with self.lock:
            rec = self.runs.get(run_id)
            if not rec or rec["status"] != "running":
                return False
            p = rec["process"]
        try:
            if os.name == "nt":
                subprocess.run(["taskkill.exe", "/PID", str(p.pid), "/T", "/F"],
                               capture_output=True, timeout=8, shell=False)
            else:
                os.killpg(p.pid, signal.SIGTERM)
            with self.lock:
                rec["log"].append("Stopped by user")
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False
