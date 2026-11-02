"""Takumi Companion: lightweight project watch, integrations, and Discord bot supervisor.

No third-party service is bundled. Launch commands come only from registered
project entry points, never from arbitrary text submitted to the web interface.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .intelligence import repo_snapshot


def _run(args: list[str], cwd: Path | None = None, seconds: int = 8) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, cwd=str(cwd) if cwd else None,
                              text=True, capture_output=True, timeout=seconds,
                              shell=False, errors="replace",
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"})
    except (OSError, subprocess.TimeoutExpired):
        return None


def connections_status(project: Path | None = None) -> dict:
    gh = shutil.which("gh")
    github = {"installed": bool(gh), "connected": False, "repository": None, "pull_requests": []}
    if gh:
        auth = _run([gh, "auth", "status", "--hostname", "github.com"], seconds=6)
        github["connected"] = bool(auth and auth.returncode == 0)
        if github["connected"] and project and (project / ".git").exists():
            r = _run([gh, "repo", "view", "--json", "nameWithOwner,url", "--jq", ".nameWithOwner"], cwd=project)
            if r and r.returncode == 0:
                github["repository"] = r.stdout.strip()[:150]
            r = _run([gh, "pr", "list", "--limit", "5", "--json", "number,title,url,state"], cwd=project)
            if r and r.returncode == 0:
                try:
                    github["pull_requests"] = json.loads(r.stdout)[:5]
                except (ValueError, TypeError):
                    pass
    # Server authentication is performed by the user's existing agent client.
    # We do not inspect or copy OAuth tokens into the Takumi project.
    from .core import open_code_path
    executable = open_code_path()
    figma = {"available": bool(executable), "endpoint": "https://mcp.figma.com/mcp",
             "connected": False, "status": "Connect with your Figma account"}
    if executable:
        response = _run([executable, "mcp", "list"], seconds=8)
        if response:
            raw = response.stdout + response.stderr
            for line in raw.splitlines():
                clean = re.sub(r'\x1b\[[0-9;]*m', '', line).lower()
                if (response.returncode == 0 and 'figma' in clean and re.search(r'\b(connected|online)\b', clean)
                        and not re.search(r'\b(disconnected|offline|failed|error|disabled|unauthorized|not\s+connected)\b', clean)):
                    figma["connected"] = True
                    figma["status"] = "Ready"
                    break
    return {"github": github, "figma": figma}


class Companion:
    """Background read-only change detector. No GPU inference when idle."""
    def __init__(self, store, interval: int = 120):
        self.store = store
        self.interval = max(30, interval)
        self.path = self.store.base / "companion.json"
        self.lock = threading.RLock()
        self.events: list[dict] = []
        self.versions: dict[str, str] = {}
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.events = data.get("events", [])[-100:]
            self.versions = data.get("versions", {})
        except (ValueError, OSError, AttributeError):
            pass

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, name="Takumi-Companion", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def _save(self):
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"events": self.events[-100:], "versions": self.versions},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def note(self, kind: str, text: str, project_id: str | None = None):
        with self.lock:
            ev = {"id": str(time.time_ns()), "kind": kind, "text": str(text)[:280],
                  "project_id": project_id, "time": int(time.time())}
            self.events.append(ev)
            self.events = self.events[-100:]
            self._save()
            return ev

    def status(self):
        with self.lock:
            return {"monitoring": bool(self.thread and self.thread.is_alive()),
                    "interval_seconds": self.interval, "events": self.events[-40:][::-1]}

    def scan_once(self):
        with self.store.lock:
            projects = list(self.store.data.get("projects", []))
        for proj in projects[:15]:
            path = Path(proj["path"])
            if not path.is_dir():
                continue
            snap = repo_snapshot(path)
            if not snap.get("repository"):
                continue
            signature = f"{snap['head']}|{snap['modified_count']}|{snap['branch']}"
            with self.lock:
                before = self.versions.get(proj["id"])
                if before != signature:
                    self.versions[proj["id"]] = signature
                    if before is not None:
                        self.events.append({"id": str(time.time_ns()), "kind": "repository",
                                            "project_id": proj["id"], "time": int(time.time()),
                                            "text": f"{proj['name']}: branch {snap['branch']}, commit {snap['head']}; {snap['modified_count']} modified files."})
                        self.events = self.events[-100:]
                    self._save()

    def _loop(self):
        # First scan establishes a baseline without spamming the activity feed.
        while not self.stop_event.is_set():
            try:
                self.scan_once()
            except Exception:
                pass
            self.stop_event.wait(self.interval)


class BotSupervisor:
    """Supervise one configured Discord bot per project; never accept a shell string."""
    def __init__(self, store, companion: Companion | None = None):
        self.store, self.companion = store, companion
        self.path = store.base / "bots.json"
        self.lock = threading.RLock()
        self.entries: dict[str, dict] = {}
        self.processes: dict[str, subprocess.Popen] = {}
        self.logs: dict[str, object] = {}
        self.stop_event = threading.Event()
        self.monitor: threading.Thread | None = None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.entries = {k: v for k, v in raw.items() if isinstance(v, dict)}
        except (ValueError, OSError):
            pass

    def _save(self):
        dest = self.path.with_suffix(".tmp")
        dest.write_text(json.dumps(self.entries, indent=2), encoding="utf-8")
        dest.replace(self.path)

    def _project(self, project_id: str) -> Path:
        project = self.store.get_project(project_id)
        if not project:
            raise ValueError("Choose an existing registered project")
        directory = Path(project["path"]).resolve()
        if not directory.is_dir():
            raise ValueError("The project folder is not available")
        return directory

    def configure(self, project_id: str, relative_script: str, *, autostart: bool = False,
                  autorestart: bool = True) -> dict:
        root = self._project(project_id)
        if not relative_script or len(relative_script) > 250:
            raise ValueError("Choose a Python or JavaScript bot entry point inside the project")
        candidate = (root / relative_script).resolve()
        if (not candidate.is_relative_to(root) or not candidate.is_file() or
                candidate.suffix.lower() not in (".py", ".js", ".mjs")):
            raise ValueError("Bot entry must be a .py/.js/.mjs file inside its registered project")
        with self.lock:
            existing = self.entries.get(project_id, {})
            bot = {"project_id": project_id, "script": candidate.relative_to(root).as_posix(),
                   "autostart": bool(autostart), "autorestart": bool(autorestart),
                   "crashes": existing.get("crashes", 0), "last_exit": existing.get("last_exit")}
            self.entries[project_id] = bot
            self._save()
        return dict(bot)

    def start(self, project_id: str) -> dict:
        with self.lock:
            bot = self.entries.get(project_id)
            if bot is None:
                raise ValueError("Configure a bot first")
            p = self.processes.get(project_id)
            if p and p.poll() is None:
                return self._summary(project_id)
            root = self._project(project_id)
            target = (root / bot["script"]).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                raise ValueError("Bot script was moved or is outside the selected project")
            if target.suffix.lower() == ".py":
                executable = sys.executable
            else:
                executable = shutil.which("node")
            if not executable:
                raise RuntimeError("The bot's Python/Node runtime is not installed")
            log_path = self.store.base / f"bot-{project_id}.log"
            if log_path.is_file() and log_path.stat().st_size > 4_000_000:
                log_path.rename(log_path.with_suffix(".log.old"))
            log = log_path.open("a", encoding="utf-8")
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            try:
                child = subprocess.Popen([str(executable), str(target)], cwd=str(root),
                                         shell=False, stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=subprocess.STDOUT,
                                         creationflags=flags, start_new_session=os.name != "nt")
            except BaseException:
                log.close()
                raise
            self.logs[project_id] = log
            self.processes[project_id] = child
            bot["started"] = int(time.time())
            bot["crashes"] = 0
            self._save()
            if self.companion:
                self.companion.note("discord", "Discord bot started under the selected project", project_id)
            return self._summary(project_id)

    def stop(self, project_id: str) -> dict:
        with self.lock:
            p = self.processes.pop(project_id, None)
            if p and p.poll() is None:
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                                       capture_output=True, timeout=8, shell=False)
                    else:
                        os.killpg(p.pid, signal.SIGTERM)
                except (OSError, subprocess.TimeoutExpired):
                    p.kill()
            log = self.logs.pop(project_id, None)
            if log:
                log.close()
            bot = self.entries.get(project_id)
            if bot:
                bot["autostart"] = False
                self._save()
            if self.companion:
                self.companion.note("discord", "Discord bot stopped", project_id)
            return self._summary(project_id)

    def _summary(self, project_id):
        bot = self.entries.get(project_id, {})
        p = self.processes.get(project_id)
        return {**bot, "running": bool(p and p.poll() is None),
                "pid": p.pid if p and p.poll() is None else None}

    def status(self):
        with self.lock:
            return [self._summary(k) for k in self.entries]

    def tail(self, project_id: str, max_lines: int = 70) -> str:
        if project_id not in self.entries:
            raise ValueError("Bot is not configured")
        path = self.store.base / f"bot-{project_id}.log"
        if not path.is_file():
            return "No output yet."
        with path.open("rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - 10000))
            lines = f.read().decode("utf-8", "replace").splitlines()
        # Bot logs may contain secrets; the web UI never needs complete logs.
        from .core import SECRETS
        def redact(line: str) -> str:
            line = SECRETS.sub(r"\1=[REDACTED]", line)
            return re.sub(r"(?i)(authorization\s*[:=]\s*(?:bot|bearer)\s+)[^\s,;]+",
                          r"\1[REDACTED]", line)
        return "\n".join(redact(line) for line in lines[-max_lines:])[-7000:]

    def start_background(self):
        if self.monitor and self.monitor.is_alive():
            return
        for project_id, bot in list(self.entries.items()):
            if bot.get("autostart"):
                try:
                    self.start(project_id)
                except Exception as exc:
                    if self.companion:
                        self.companion.note("discord", "Bot autostart failed: " + str(exc)[:160], project_id)
        self.monitor = threading.Thread(target=self._loop, name="Takumi-Discord-Supervisor", daemon=True)
        self.monitor.start()

    def _loop(self):
        while not self.stop_event.wait(15):
            with self.lock:
                for project_id, process in list(self.processes.items()):
                    code = process.poll()
                    if code is None:
                        continue
                    self.processes.pop(project_id, None)
                    log = self.logs.pop(project_id, None)
                    if log:
                        log.close()
                    bot = self.entries[project_id]
                    bot["last_exit"] = code
                    bot["crashes"] = int(bot.get("crashes", 0)) + 1
                    self._save()
                    if self.companion:
                        self.companion.note("discord", f"Bot exited ({code}); restart count {bot['crashes']}", project_id)
                    if bot.get("autorestart") and bot.get("autostart") and bot["crashes"] <= 3:
                        # Restart on next check, never a tight crash-loop.
                        timer = threading.Timer(20 * bot["crashes"], self._restart, args=[project_id])
                        timer.daemon = True
                        timer.start()

    def _restart(self, project_id: str):
        if self.stop_event.is_set():
            return
        try:
            # start() resets crashes for manual launches only; preserve here.
            with self.lock:
                crashes = self.entries[project_id].get("crashes", 0)
            self.start(project_id)
            with self.lock:
                self.entries[project_id]["crashes"] = crashes
                self._save()
        except Exception:
            pass

    def close(self, stop_bots: bool = False):
        self.stop_event.set()
        if self.monitor:
            self.monitor.join(timeout=2)
        if stop_bots:
            for bot in list(self.processes):
                self.stop(bot)
