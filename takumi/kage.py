"""Kage: opt-in, auditable Sumi-powered companions and scheduled project checks.

Tasks have deliberately narrow actions. They never gain a shell, arbitrary URL
fetching, deployment, repository-writing, or access to unregistered directories.
When Takumi is shut down, no background promises are made. All timestamps and
runs are persisted locally; the owner can delete a task or stop monitoring.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from .intelligence import repo_snapshot

MEMBERS = (
    {"id": "ren", "name": "Ren", "role": "Product lead", "description": "Priorities, technical decisions, release risk and daily progress", "avatar": "/kage/ren-256.webp"},
    {"id": "kiri", "name": "Kiri", "role": "Research scout", "description": "Current technical references, GitHub implementations and source evidence", "avatar": "/kage/kiri-256.webp"},
    {"id": "hana", "name": "Hana", "role": "Graphics specialist", "description": "Rendering, synchronization, GPU captures and shader correctness", "avatar": "/kage/hana-256.webp"},
    {"id": "tetsu", "name": "Tetsu", "role": "Validation engineer", "description": "Reproduction, diagnostics, build and regression checks", "avatar": "/kage/tetsu-256.webp"},
    {"id": "akane", "name": "Akane", "role": "Implementation engineer", "description": "Focused fixes, careful refactors and regression checks", "avatar": "/kage/akane.png"},
)
BY_ID = {member["id"]: member for member in MEMBERS}
CADENCES = {"manual": None, "30m": 1800, "hourly": 3600, "2h": 7200, "4h": 14400, "daily": None}
KINDS = {"brief", "repository", "research", "test"}
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def context(member_id: str) -> str:
    """Behavioral focus, never independent model/permissions or unverified expertise."""
    member = BY_ID.get(member_id)
    if member is None:
        raise ValueError("Unknown Kage companion")
    special = {
        "ren": "Set clear milestones, record dependencies and release blockers, and distinguish completed work from proposals.",
        "kiri": "Find primary implementation sources, cite verified URLs and commits, and distinguish leads from validated facts.",
        "hana": "Trace render passes, images, barriers, resources, shaders and synchronization through observable captures.",
        "tetsu": "Find the earliest failing condition, construct a minimal reproducer, run real tests, and preserve failure evidence.",
        "akane": "Make focused, reviewable changes, trace affected dependencies, preserve existing behavior, and verify fixes with actual tests.",
    }
    return (f"KAGE COMPANION: You are assisting as {member['name']}, {member['role']}, using the selected local model profile. "
            f"{special[member_id]} Do not invent research, executed commands, reminders or outcomes. "
            "Explain what was observed, what remains uncertain, and the next concrete step. "
            "Only tools actually granted through Takumi are available.")


class KageManager:
    """Durable local task scheduler with explicit, non-destructive operations."""

    def __init__(self, store, *, research: Callable | None = None,
                 summarize: Callable | None = None, harness=None, poll_seconds: int = 25):
        self.store, self.research, self.summarize, self.harness = store, research, summarize, harness
        self.path = self.store.base / "kage.json"
        self.lock = threading.RLock()
        self.tasks: list[dict] = []
        self.feed: list[dict] = []
        self.selected = "ren"
        self.jobs: list[dict] = []
        self.coordinator = None
        self._active = False
        self.stop_event = threading.Event()
        self.thread = None
        self.poll_seconds = max(5, poll_seconds)
        try:
            content = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(content, dict):
                self.tasks = [x for x in content.get("tasks", [])[:40] if isinstance(x, dict)]
                self.feed = [x for x in content.get("feed", [])[-120:] if isinstance(x, dict)]
                self.jobs = [x for x in content.get('jobs', [])[-40:] if isinstance(x, dict)]
                if content.get("selected") in BY_ID:
                    self.selected = content["selected"]
        except (OSError, ValueError):
            pass

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "selected": self.selected,
                                   "tasks": self.tasks, "feed": self.feed[-120:], 'jobs': self.jobs[-40:]},
                                  indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def _emit(self, kind: str, message: str, *, member: str = "ren", task_id: str = "", detail: str = ""):
        event = {"id": uuid.uuid4().hex, "at": int(time.time()), "kind": kind,
                 "member": member, "task_id": task_id, "message": str(message)[:300], "detail": str(detail)[:3200]}
        self.feed.append(event)
        self.feed = self.feed[-120:]
        return event

    def status(self) -> dict:
        with self.lock:
            return {"name": "Kage", "model": "Sumi 1.1", "members": list(MEMBERS),
                    "selected": self.selected, "monitoring": bool(self.thread and self.thread.is_alive()),
                    "active": self._active, "tasks": [dict(x) for x in self.tasks],
                    "feed": [dict(x) for x in self.feed[-45:]][::-1],
                    'jobs': [{k: v for k, v in x.items() if k != 'payload'} for x in self.jobs],
                    'unread': [dict(x) for x in self.feed if x.get('notification') and not x.get('read')]}

    def notify_run(self, run: dict, *, project_id=None, member='ren'):
        with self.lock:
            if any(x.get('run_id') == run['id'] and x.get('notification') for x in self.feed): return
            state = run['state']
            label = 'Work reported complete' if state == 'completed' else 'Work ' + state.replace('_', ' ')
            ev = self._emit('run_' + state, label, member=member,
                            detail='Open the conversation for the actual changes, checks and remaining uncertainty.')
            ev.update(notification=True, read=False, run_id=run['id'], session_id=run['session_id'], project_id=project_id)
            self._save()

    def acknowledge(self, ids: list) -> dict:
        if not isinstance(ids, list) or len(ids) > 120 or any(not isinstance(x, str) for x in ids):
            raise ValueError('Use notification IDs from the current feed')
        with self.lock:
            for event in self.feed:
                if event['id'] in ids: event['read'] = True
            self._save()
        return self.status()

    def forget_session(self, session_id: str):
        """Called under the runtime claim lock after active-run rejection."""
        with self.lock:
            self.jobs = [j for j in self.jobs if j.get('session_id') != session_id]
            self.feed = [e for e in self.feed if e.get('session_id') != session_id]
            self._save()

    def queue_goal(self, payload: dict, *, title: str, project_id: str, seconds: float) -> dict:
        if not self.store.get_project(project_id): raise ValueError('Choose a registered project')
        if type(seconds) not in (int, float) or not 1 <= seconds <= 43200: raise ValueError('Choose a goal budget up to 12 hours')
        with self.lock:
            if len([j for j in self.jobs if j['state'] in ('queued', 'starting', 'running')]) >= 12:
                raise ValueError('At most 12 outstanding Kage goals')
            job_id = uuid.uuid4().hex
            now = time.time()
            payload = {**payload, 'request_id': 'kage-' + job_id, 'goal_seconds': seconds, 'goal_deadline': now + seconds}
            job = {'id': job_id, 'title': title[:120], 'project_id': project_id,
                   'session_id': payload['session_id'], 'member': payload['kage_member'], 'state': 'queued',
                   'created': now, 'deadline': now + seconds, 'payload': payload, 'run_id': None}
            self.jobs.append(job); self.jobs = self.jobs[-40:]; self._save()
            return {k: v for k, v in job.items() if k != 'payload'}

    def cancel_job(self, job_id: str) -> dict:
        with self.lock:
            job = next((j for j in self.jobs if j['id'] == job_id), None)
            if not job: raise ValueError('Unknown Kage goal')
            if job['state'] in ('queued', 'starting'): job['state'] = 'stopped'
            self._save()
            return dict(job)

    def coordinate(self):
        # The callback uses the existing durable run engine. Never hold the Kage
        # file lock while acquiring its run lock or launching a worker.
        if not self.coordinator: return
        with self.lock:
            active = [dict(j) for j in self.jobs if j['state'] in ('queued', 'starting', 'running')]
        for snapshot in active:
            update = self.coordinator(snapshot)
            if update:
                with self.lock:
                    job = next((j for j in self.jobs if j['id'] == snapshot['id']), None)
                    if job and job['state'] != 'stopped':
                        job.update(update)
                        if update.get('state') in ('blocked', 'budget_exhausted') and not job.get('run_id'):
                            event = self._emit(update['state'], job['title'], member=job['member'], detail=update.get('reason', 'Original goal deadline reached while queued.'))
                            event.update(notification=True, read=False, session_id=job['session_id'], project_id=job['project_id'])
                        self._save()
            if update and update.get('state') == 'running': break

    def select(self, member_id: str):
        if member_id not in BY_ID:
            raise ValueError("Unknown Kage companion")
        with self.lock:
            self.selected = member_id
            self._save()
        return self.status()

    def avatar(self, member_id: str) -> dict:
        if member_id not in BY_ID:
            raise ValueError("Unknown companion")
        root = self.store.base / "kage-art"
        for ext, mime in (("png", "image/png"), ("webp", "image/webp"), ("jpg", "image/jpeg")):
            source = root / f"{member_id}.{ext}"
            if source.is_file() and 0 < source.stat().st_size <= 2_000_000:
                encoded = base64.b64encode(source.read_bytes()).decode("ascii")
                return {"member": member_id, "custom": True,
                        "src": f"data:{mime};base64,{encoded}"}
        return {"member": member_id, "custom": False, "src": BY_ID[member_id]["avatar"]}

    def import_avatar(self, member_id: str, data: str, source: str,
                      attribution: str, *, rights_confirmed: bool = False) -> dict:
        """Personal import only: the user must possess usable rights for their own app.

        The original files live exclusively in this PC's profile, not GitHub,
        source ZIPs, or the public distributable. Never permit SVG/scripts.
        """
        if member_id not in BY_ID or not rights_confirmed:
            raise ValueError("Choose a companion and confirm your artwork license/permission")
        if not isinstance(data, str) or len(data) > 2_700_000 or not isinstance(source, str) or len(source) > 500:
            raise ValueError("Use a PNG, JPEG or WebP avatar under 2 MB")
        if not isinstance(attribution, str) or not attribution.strip() or len(attribution) > 500:
            raise ValueError("Record the creator/source attribution or license details")
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Invalid image encoding") from None
        if not raw or len(raw) > 2_000_000:
            raise ValueError("Image size exceeds 2 MB")
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            ext = "png"
        elif raw.startswith(b"\xff\xd8\xff"):
            ext = "jpg"
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            ext = "webp"
        else:
            raise ValueError("Only PNG, JPEG and WebP images are supported")
        dest = self.store.base / "kage-art"
        dest.mkdir(parents=True, exist_ok=True)
        with self.lock:
            tmp = dest / f"{member_id}.{ext}.tmp"
            tmp.write_bytes(raw)
            tmp.replace(dest / f"{member_id}.{ext}")
            for old_ext in ("png", "jpg", "webp"):
                if old_ext != ext:
                    (dest / f"{member_id}.{old_ext}").unlink(missing_ok=True)
            metadata = dest / "credits.json"
            try:
                previous = json.loads(metadata.read_text("utf-8"))
                if not isinstance(previous, dict):
                    previous = {}
            except (OSError, ValueError):
                previous = {}
            previous[member_id] = {"source": source, "attribution": attribution,
                                   "imported": int(time.time()), "file": member_id + "." + ext}
            metadata.write_text(json.dumps(previous, ensure_ascii=False, indent=2), "utf-8")
            self._emit("avatar", f"Imported licensed avatar for {BY_ID[member_id]['name']}", member=member_id)
            self._save()
        return {"imported": True, "member": member_id, "attribution": attribution}

    def add(self, *, kind: str, cadence: str, member: str, title: str,
            project_id: str | None = None, query: str = "", clock: str = "09:00",
            test_id: str = "", allow_network: bool = False, use_sumi: bool = False) -> dict:
        if kind not in KINDS or cadence not in CADENCES or member not in BY_ID:
            raise ValueError("Choose a supported companion, task and schedule")
        if not isinstance(title, str) or not 2 <= len(title.strip()) <= 95:
            raise ValueError("Enter a task title under 96 characters")
        if not isinstance(query, str) or len(query) > 360:
            raise ValueError("Task research question is too long")
        if kind == "research" and (not query.strip() or not allow_network):
            raise ValueError("Research tasks require a question and explicit network permission")
        if kind == "test" and (not project_id or not test_id or not self.harness):
            raise ValueError("Automated tests require a registered project and a declared test task")
        if kind in ("repository", "test") and not project_id:
            raise ValueError("Choose a registered project for this task")
        if project_id and not self.store.get_project(project_id):
            raise ValueError("Unknown project")
        if kind == "test":
            if test_id not in {t["id"] for t in self.harness.list_tasks(project_id)}:
                raise ValueError("The task must be declared in takumi.tasks.json before scheduling")
        if cadence == "daily" and (not isinstance(clock, str) or not TIME_RE.fullmatch(clock)):
            raise ValueError("Use a local daily time in HH:MM format")
        if not isinstance(allow_network, bool) or not isinstance(use_sumi, bool):
            raise ValueError("Invalid task permissions")
        with self.lock:
            if len(self.tasks) >= 30:
                raise ValueError("At most 30 scheduled tasks can be registered")
            task = {"id": uuid.uuid4().hex, "kind": kind, "cadence": cadence,
                    "member": member, "title": title.strip(), "project_id": project_id,
                    "query": query.strip(), "clock": clock if cadence == "daily" else "",
                    "test_id": test_id if kind == "test" else "",
                    "allow_network": allow_network if kind == "research" else False,
                    "use_sumi": use_sumi and kind != "test", "enabled": cadence != "manual",
                    "created": time.time(), "last_started": None, "last_finished": None,
                    "last_slot": None, "status": "scheduled" if cadence != "manual" else "ready",
                    "last_summary": ""}
            self.tasks.append(task)
            self._emit("scheduled", f"Scheduled {task['title']}", member=member, task_id=task["id"])
            self._save()
            return dict(task)

    def remove(self, task_id: str) -> bool:
        with self.lock:
            before = len(self.tasks)
            self.tasks = [x for x in self.tasks if x.get("id") != task_id]
            self._save()
            return len(self.tasks) != before

    def set_enabled(self, task_id: str, enabled: bool) -> dict:
        if not isinstance(enabled, bool):
            raise ValueError("Invalid enabled setting")
        with self.lock:
            item = next((x for x in self.tasks if x["id"] == task_id), None)
            if not item:
                raise ValueError("Unknown task")
            item["enabled"] = enabled
            item["status"] = "scheduled" if enabled else "paused"
            self._save()
            return dict(item)

    def due(self, task: dict, now: dt.datetime) -> bool:
        if not task.get("enabled"):
            return False
        cadence = task.get("cadence")
        if cadence == "manual":
            return False
        if cadence == "daily":
            current = now.strftime("%H:%M")
            return current >= task.get("clock", "09:00") and task.get("last_slot") != now.strftime("%Y-%m-%d")
        seconds = CADENCES.get(cadence)
        if not seconds:
            return False
        past = task.get("last_started") or task.get("created", 0)
        return now.timestamp() >= float(past) + seconds

    def tick(self, now: dt.datetime | None = None) -> dict | None:
        """Try one due task per scheduler tick. No busy-loop or parallel GPU workers."""
        now = now or dt.datetime.now().astimezone()
        with self.lock:
            if self._active:
                return None
            task = next((t for t in self.tasks if self.due(t, now)), None)
            if not task:
                return None
            return self._launch(task, now)

    def run_now(self, task_id: str) -> dict:
        with self.lock:
            item = next((x for x in self.tasks if x["id"] == task_id), None)
            if not item:
                raise ValueError("Unknown Kage task")
            if self._active:
                raise ValueError("Another Kage task is running")
            return self._launch(item, dt.datetime.now().astimezone())

    def _launch(self, item: dict, now: dt.datetime) -> dict:
        self._active = True
        item["last_started"] = now.timestamp()
        item["last_slot"] = now.strftime("%Y-%m-%d") if item.get("cadence") == "daily" else None
        item["status"] = "running"
        self._emit("started", item["title"], member=item["member"], task_id=item["id"])
        self._save()
        snapshot = dict(item)
        threading.Thread(target=self._execute, args=(snapshot,), daemon=True, name="Takumi-Kage-"+item["id"][:7]).start()
        return dict(item)

    def _execute(self, task: dict):
        status, message = "completed", ""
        try:
            project = self.store.get_project(task["project_id"]) if task.get("project_id") else None
            if task.get("project_id") and not project:
                raise ValueError("Scheduled project no longer exists")
            if task["kind"] == "repository":
                result = repo_snapshot(Path(project["path"]))
                message = (f"{project['name']} · branch {result.get('branch', '?')} · "
                           f"HEAD {result.get('head', '?')} · {result.get('modified_count', '?')} modified files. "
                           "A repository snapshot is not proof of build or gameplay readiness.")
            elif task["kind"] == "research":
                if not self.research or not task["allow_network"]:
                    raise ValueError("Research is not available or network permission was removed")
                packet = self.research(task["query"], code=True,
                                       project=Path(project["path"]) if project else None)
                leads = packet.get("results", [])[:4]
                message = f"Research: {packet.get('freshness','unknown')} · {len(leads)} lead(s). "
                message += ' '.join(f"[{str(x.get('title','source'))[:70]}] {str(x.get('url',''))[:200]}" for x in leads)
                if not leads:
                    status = "limited"
                    message += " No new citations verified; don't treat this as completed research."
            elif task["kind"] == "test":
                run = self.harness.launch(task["project_id"], task["test_id"])
                message = f"Test harness started: {run['name']} · run ID {run['id']}. Inspect recorded exit status before claiming success."
            elif task["kind"] == "brief":
                if project:
                    snap = repo_snapshot(Path(project["path"]))
                    message = f"{project['name']}: branch {snap.get('branch','?')}, commit {snap.get('head','?')}, {snap.get('modified_count','?')} changed files."
                else:
                    message = "No specific project selected. Choose one for a grounded engineering status report."
            if task.get("use_sumi") and task["kind"] != "test" and self.summarize:
                # A scheduled model call is explicitly opted in and never edits files.
                summary = self.summarize(task["member"], task["title"], message)
                if summary:
                    message += "\n\nSumi briefing:\n" + str(summary)[:1800]
                else:
                    message += "\nSumi unavailable or working on another task; factual status above is retained."
        except Exception as exc:
            status, message = "failed", f"{type(exc).__name__}: {str(exc)[:350]}"
        finally:
            with self.lock:
                found = next((x for x in self.tasks if x["id"] == task["id"]), None)
                if found:
                    found["status"] = status
                    found["last_finished"] = time.time()
                    found["last_summary"] = message[:2300]
                self._emit(status, task["title"], member=task["member"],
                           task_id=task["id"], detail=message)
                self._active = False
                self._save()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="Takumi-Kage-Scheduler")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.coordinate()
                self.tick()
            except Exception as exc:
                with self.lock:
                    self._emit("error", "Kage schedule check failed", detail=str(exc)[:250])
                    self._save()
            self.stop_event.wait(min(self.poll_seconds, 3))
