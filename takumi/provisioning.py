"""Local, on-demand Windows first-run model provisioning.

Takumi's distributable contains source and its own Windows setup program only.
The official external model runtime, model weights and coding CLI are fetched
on the user's computer by a fixed PowerShell script, never by arbitrary chat.
"""
from __future__ import annotations

import os
import json
import urllib.error
import urllib.request
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

_SECRET = re.compile(r"(?i)(?:(?:api|access|auth|discord|bot)[_ -]?(?:token|key)|token|api[_ -]?key|password|secret)\s*[:=]\s*\S+")


class Provisioner:
    """Supervise only the local install process Takumi itself launched."""

    def __init__(self, root: Path, *, platform: str | None = None, executable: str | None = None):
        self.root = root.resolve()
        self.platform = platform or os.name
        self.executable = executable
        self.lock = threading.RLock()
        self.process: subprocess.Popen | None = None
        self.lines: deque[str] = deque(maxlen=55)
        self.phase = "idle"
        self.exit_code: int | None = None
        self.started_at: float | None = None

    def status(self) -> dict:
        with self.lock:
            return {"state": self.phase, "running": self.phase == "running",
                    "exit_code": self.exit_code, "started_at": self.started_at,
                    "messages": list(self.lines)[-22:]}

    def start(self, *, lightweight: bool = False, skip_coding_agent: bool = False) -> dict:
        if self.platform != "nt":
            raise RuntimeError("Guided model installation is available from the Windows desktop application.")
        script = self.root / "scripts" / "Ensure-Sumi-1.0.ps1"
        if not script.is_file():
            raise RuntimeError("Sumi setup script is missing from the installation. Repair Takumi first.")
        exe = self.executable or shutil.which("powershell.exe")
        if not exe:
            raise RuntimeError("Windows PowerShell is not available. Repair your Windows installation.")
        with self.lock:
            if self.phase == "running":
                raise RuntimeError("Sumi installation is already running. Check progress below.")
            args = [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
            if lightweight:
                args.append("-PreferSmallModel")
            if skip_coding_agent:
                args.append("-SkipCodingAgent")
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if self.platform == "nt" and os.name == "nt" else 0
            self.process = subprocess.Popen(
                args, cwd=str(self.root), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=flags,
            )
            self.phase = "running"
            self.exit_code = None
            self.started_at = time.time()
            self.lines.clear()
            self.lines.append("Preparing Rei 1.0 (lightweight) or Sumi 1.0 on this PC. Downloads use official sources.")
            threading.Thread(target=self._collect, daemon=True, name="takumi-sumi-setup").start()
            return self.status()

    def _collect(self) -> None:
        p = self.process
        if p is None:
            return
        try:
            assert p.stdout
            for raw in p.stdout:
                trimmed = _SECRET.sub("[redacted]", raw.strip())[:420]
                if trimmed:
                    with self.lock:
                        self.lines.append(trimmed)
            code = p.wait()
            with self.lock:
                self.exit_code = code
                self.phase = "ready" if code == 0 else "failed"
                self.lines.append("Model setup complete." if code == 0 else
                                  "Sumi setup did not complete. See the full log under Takumi\\Logs.")
        except Exception:
            with self.lock:
                self.phase = "failed"
                self.exit_code = -1
                self.lines.append("Setup process ended unexpectedly; inspect the installation log.")
        finally:
            if p.stdout:
                p.stdout.close()


def start_installed_engine_if_needed() -> bool:
    """Best-effort launch of an *already installed* local engine on Windows.

    Does not download software, open ports to LAN, or run a shell command.
    Call asynchronously at local backend startup so Chat UI stays responsive.
    """
    if os.name != "nt":
        return False
    def ready() -> bool:
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2) as response:
                return isinstance(json.load(response).get("version"), str)
        except (OSError, ValueError, urllib.error.URLError):
            return False
    if ready():
        return True
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    binary = shutil.which("ollama") or next((str(p) for p in (
        local / "Programs" / "Ollama" / "ollama.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Ollama" / "ollama.exe",
    ) if p.is_file()), None)
    if not binary:
        return False
    env = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_NO_CLOUD": "1"}
    try:
        subprocess.Popen([binary, "serve"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError:
        return False
    for _ in range(16):
        if ready():
            return True
        time.sleep(.5)
    return False
