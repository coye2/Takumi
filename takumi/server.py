"""Takumi local HTTP interface and control plane (Python stdlib only)."""
from __future__ import annotations

import argparse
import json
import hashlib
import os
import re
import secrets
import sys
import shutil
import subprocess
import threading
import time
import urllib.error
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs

from .core import (APP_VERSION, Agent, Storage, installed_models, instruction_prompt, json_request,
                   open_code_path, polish_explanation, reasoning_profile, ui_event_stream,
                   invalidate_installed_models, explicit_model_refusal, repair_generated_html)
from .intelligence import LocalMemory, profile_for, safe_temperature, guard_new_run, repo_snapshot, review_prompt
from .remote import Pairing, remote_mode_enabled
from .remote import connection_status, keep_phone_service_awake
from .trainer import Trainer
from .companion import Companion, BotSupervisor, connections_status
from .kage import KageManager, context as kage_context
from .desktop_controls import ProcessSupervisor
from .oauth import initiate_sign_in
from .research import research as conduct_research, should_research, context as research_context, public_query, EvidenceCache
from .github_reader import requested_repository
from .core import ANSWER_STYLE_GUIDANCE
from .repository_review import collect_repository_review
from .plugins import Approvals, status as plugin_status
from .harness import Harness, completion_contract
from .completion import verify_completion
from .source_miner import map_repository, inspect_file
from .expertise import catalogue as expertise_catalogue, retrieve as retrieve_expertise, ReviewedLessons, VERSION as EXPERTISE_VERSION
from .models import catalog as model_catalog, state as sumi_state, register_sumi, register_rei, default_model
from .provisioning import Provisioner, start_installed_engine_if_needed
from .profiles import ProfileManager, DiscordOAuth
from .search_connectors import key_status, set_key
from .profile_archive import export_profile, import_profile
from .grounding import project_facts, research_correction
from .routing import choose_mode
from .code_quality import validate_generated_code, should_repair_html, engineering_repair_prompt, accept_engineering_repair
from .source_index import SourceIndex
from .format_memory import FormatMemory
from .evidence import response_contract, audit_answer, structure_report
from .language import resolve_language, model_instruction
from .engineering_tools import inventory as engineering_inventory, inspect_binary, symbol_search, validate_spirv, disassemble_spirv, analyze_ghidra
from .gpu_scheduler import GpuScheduler
from .updater import UpdateManager
from .runs import RunJournal, TERMINAL, data_directory_lease
from .runtime_inventory import inventory as runtime_inventory
from .goals import Goals

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web"

_STAMP_DIRS = ('takumi', 'web', 'models', 'expertise', 'assets')

def build_stamp(root: Path = ROOT) -> str:
    """Exact source identity, shared with the WinForms desktop host."""
    digest = hashlib.sha256()
    files = sorted(path for folder in _STAMP_DIRS for path in (Path(root) / folder).rglob('*')
                   if path.is_file() and '__pycache__' not in path.parts
                   and path.suffix not in ('.pyc', '.pyo'))
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
        digest.update(path.read_bytes() + b'\0')
    return digest.hexdigest()

# Python retains imported code after files change. Never advertise freshly
# edited disk files as the identity of an already-running backend.
PROCESS_BUILD_STAMP = build_stamp()
PROCESS_FRONTEND_SHA256 = hashlib.sha256((STATIC / 'app.js').read_bytes()).hexdigest()

MIMES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json",
         ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
         ".webmanifest": "application/manifest+json", ".ico": "image/x-icon"}


class ChatCancellation:
    def __init__(self):
        self.cancel = threading.Event()

    def stop(self):
        self.cancel.set()


def _recoverable_local_error(exc: BaseException) -> bool:
    """Retry transient transport and local-inference availability failures only."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (404, 408, 429, 500, 502, 503, 504, 507)
    return (isinstance(exc, (OSError, urllib.error.URLError, TimeoutError)) or
            isinstance(exc, RuntimeError) and str(exc) in ("Local model returned no events", "Local model returned no answer"))


def resilient_chat_events(streamer, url: str, messages: list[dict], model: str,
                          level: str, cancel: threading.Event, kwargs: dict,
                          available: list[str], *, allow_alternative: bool = True,
                          pause=lambda _: None):
    """One recovery retry, then a *labelled* installed-model alternative.

    Never restart after visible answer text: that would duplicate a partial reply.
    Never download or substitute a hosted model. Do not retry invalid settings.
    """
    candidates = [model]
    if allow_alternative:
        # Prefer a genuinely smaller resident model, never an unknown 70B
        # alternative that could make VRAM exhaustion worse.
        def size_b(name):
            lower = name.lower()
            if lower.startswith(('rei-1.0', 'sumi-1.0-lite')):
                return 9.0
            if lower.startswith('sumi-1.0'):
                return 27.0
            matches = re.findall(r"(\d+(?:\.\d+)?)b\b", lower)
            return float(matches[-1]) if matches else None
        primary_size = size_b(model)
        # Unknown-size aliases must not accidentally offload a 70B model onto
        # an 8 GB card. The fallback is only a known, no-larger resident model.
        extras = [m for m in available if m != model and
                  primary_size is not None and size_b(m) is not None and size_b(m) <= primary_size]
        extras.sort(key=lambda m: (size_b(m) if size_b(m) is not None else float('inf'), m))
        candidates.extend(extras[:1])
    previous_error = None
    for i, name in enumerate(candidates):
        attempts = 2 if i == 0 else 1
        effective_level = level if i == 0 else 'default'
        for attempt in range(attempts):
            if cancel.is_set():
                yield {"type": "cancelled"}
                return
            visible = False
            got_events = False
            last_metrics = {}
            try:
                for item in streamer(url, messages, name, effective_level, cancel, **kwargs):
                    got_events = True
                    if item.get("type") == "delta" and item.get("text"):
                        visible = True
                    if item.get('type') == 'metrics': last_metrics = item
                    yield item
                exhausted = last_metrics.get('done_reason') == 'length'
                if visible:
                    if exhausted:
                        yield {'type': 'notice', 'message': 'Response reached its output limit. Inspect code fences and unfinished sections before using this draft.'}
                    yield {"type": "model_used", "model": name, "fallback": name != model}
                    return
                if exhausted:
                    capability = reasoning_profile(name)
                    if (attempt + 1 < attempts and effective_level in (None, 'default')
                            and last_metrics.get('thinking_request') is not False
                            and capability.get('verified') and 'off' in capability.get('levels', [])):
                        effective_level = 'off'
                        yield {'type': 'notice', 'message': 'The model used its output allowance without an answer. Retrying once with its supported native thinking switch off.',
                               'recovery': 'thinking_budget', 'reasoning_override': 'off'}
                        continue
                    raise ValueError('The model reached its output limit without an answer. No identical retry was made. Use a smaller request or the supported Quick/native-thinking-off control.')
                raise RuntimeError("Local model returned no answer" if got_events else "Local model returned no events")
            except Exception as exc:
                if cancel.is_set():
                    yield {'type': 'cancelled'}
                    return
                # Partial generation could be useful, but restarting risks
                # silently combining two distinct and inconsistent answers.
                if visible or not _recoverable_local_error(exc):
                    raise
                previous_error = exc
                if attempt + 1 < attempts:
                    yield {"type": "notice", "message": "The local model connection was interrupted. Retrying once."}
                    pause(0.35)
                elif i + 1 < len(candidates):
                    yield {"type": "notice", "message": "Primary model unavailable. Switching to a smaller installed local model: " + candidates[i+1]}
    if previous_error is not None:
        raise previous_error
    raise RuntimeError("No local model returned a response")


class Runtime:
    def __init__(self, store: Storage | None = None, agent_cls=Agent):
        self.store = store or Storage()
        self.runs = RunJournal(self.store.base)
        self.runs.recover()
        self.goals = Goals(self.store.base)
        for session in self.store.data['sessions']:
            for goal in self.goals.for_session(session['id']):
                previous = self.runs.get(goal.get('run_id', ''))
                if goal['state'] == 'running' and previous and previous['state'] in TERMINAL:
                    goal['state'] = previous['state']
                    self.goals.save(goal)
        self.active_runs: dict[str, str] = {}
        self.token = secrets.token_urlsafe(36)
        self.busy: dict[str, tuple[threading.Event, Agent]] = {}
        self.pending_updates: dict[str, list[str]] = {}
        self.lock = threading.RLock()
        self.agent_cls = agent_cls
        self.memory = LocalMemory(self.store.base)
        self.source_index = SourceIndex(self.store.base / 'source-index.sqlite3')
        self.formats = FormatMemory(self.store.base / 'format-memory.sqlite3')
        self.lessons = ReviewedLessons(self.store.base)
        self.pairing = Pairing(self.store.base)
        self.companion = Companion(self.store)
        self.bots = BotSupervisor(self.store, self.companion)
        self.processes = ProcessSupervisor(self.store)
        self.gpu_scheduler = GpuScheduler(
            is_model_busy=lambda: bool(self.busy) or getattr(self, "_kage_inference", False),
            unload=self._unload_models_for_gpu_test)
        self.harness = Harness(self.store, gpu_scheduler=self.gpu_scheduler)
        self.trainer = Trainer(self.store.base, ROOT, self.gpu_scheduler)
        self.updater = UpdateManager(self.store.base, APP_VERSION)
        self.provisioner = Provisioner(ROOT)
        self.profiles = ProfileManager(self.store.base)
        self.discord_oauth = DiscordOAuth(self.profiles)
        if os.name == "nt":
            # Restart an already installed local model service after Windows
            # login; never make the UI wait for the service or download weights.
            threading.Thread(target=start_installed_engine_if_needed, daemon=True,
                             name="takumi-local-engine-start").start()
        self.approvals = Approvals()
        self.research_cache: dict[str, tuple[float, dict]] = {}
        self.evidence_cache = EvidenceCache(self.store.base / "research-cache.json")
        self.settings_path = self.store.base / "settings.json"
        self.settings = {"figma_enabled": False, "personality": "precise",
                         "theme": "porcelain", "speed": "standard", "effort": "balanced",
                         "refine_reports": False, "autonomy_passes": 8,
                         "research_mode": "auto", "github_research": True,
                         "model_failover": True,
                         "language": "auto", "show_progress": True, "show_citations": True,
                         "verify_generated_code": True, "repair_generated_html": True, "repair_generated_code": True,
                         "send_on_enter": True, "voice_output": False}
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.settings.update({k: v for k, v in data.items() if k in self.settings})
        except (ValueError, OSError):
            pass
        self._kage_inference = False
        self.kage = KageManager(self.store, research=self.get_research, summarize=self._summarize_kage, harness=self.harness)
        self.companion.start()
        self.kage.start()
        self.bots.start_background()

    def _unload_models_for_gpu_test(self) -> None:
        """Unload only models already resident on the GPU; never download or alter drivers."""
        try:
            loaded = json_request("/api/ps", timeout=2).get("models", [])
        except (OSError, ValueError, urllib.error.URLError):
            return  # No local runtime/model is resident.
        for record in loaded[:4]:
            name = record.get("name") if isinstance(record, dict) else None
            if name and isinstance(name, str):
                json_request("/api/generate", {"model": name, "keep_alive": 0}, timeout=20)

    def _summarize_kage(self, member: str, title: str, evidence: str) -> str | None:
        """Short, explicitly opt-in Sumi briefing; never modifies files or invents work."""
        with self.lock:
            if self.busy or self._kage_inference or self.gpu_scheduler.is_reserved():
                return None
            self._kage_inference = True
        try:
            model = default_model(installed_models())
            if not model:
                return None
            prompt = ("Summarize the following observed task evidence concisely. Include factual next steps. "
                      "If evidence is missing, say so. Do not claim tests, changes or online checks occurred "
                      "unless the evidence proves they did. No tool use is available in this call.")
            payload = {"model": model, "stream": False,
                       "messages": [{"role": "system", "content": kage_context(member) + "\n" + prompt + ANSWER_STYLE_GUIDANCE},
                                    {"role": "user", "content": "Task: " + title[:100] + "\nEvidence:\n" + evidence[:1800]}],
                       "options": {"num_ctx": 4096, "num_predict": 400, "temperature": 0.15}}
            result = json_request("/api/chat", payload, timeout=65)
            text = result.get("message", {}).get("content", "").strip()
            return text[:1800] or None
        except (OSError, RuntimeError, ValueError, urllib.error.URLError):
            return None
        finally:
            with self.lock:
                self._kage_inference = False

    def update_settings(self, body: dict):
        allowed = {"figma_enabled": [False, True], "browser_enabled": [False, True], "refine_reports": [False, True],
                   "autonomy_passes": [4, 8, 12],
                   "personality": ["precise", "friendly", "technical", "concise", "craftsman"],
                   "theme": ["porcelain", "sumi", "sakura", "matcha", "indigo", "midnight",
                             "washi", "kintsugi", "ume", "yoru", "kyoto", "moss", "sora", "akane", "zen",
                             "beni", "shinku", "shu", "botan", "tsuki", "ruri", "sage"],
                   "speed": ["standard", "boost"],
                   "effort": ["quick", "balanced", "high", "maximum"],
                   "research_mode": ["auto", "always", "off"],
                   "github_research": [False, True], "model_failover": [False, True],
                   "language": ["auto", "en", "it"],
                   "show_progress": [False, True], "show_citations": [False, True],
                   "verify_generated_code": [False, True], "repair_generated_html": [False, True], "repair_generated_code": [False, True],
                   "send_on_enter": [False, True], "voice_output": [False, True]}
        with self.lock:
            for key, value in body.items():
                if key not in allowed or value not in allowed[key]:
                    raise ValueError("Invalid preference: " + str(key))
            self.settings.update(body)
            temp = self.settings_path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
            temp.replace(self.settings_path)
            return dict(self.settings)

    def get_research(self, query: str, *, code: bool = False, project: Path | None = None) -> dict:
        repo_request = requested_repository(query)
        if repo_request:
            return collect_repository_review(repo_request)
        clean = public_query(query)
        # A local project's search results never enter the shared public cache.
        key = ("code:" if code else "web:") + clean + ("|project:" + str(project) if project else "")
        cached = self.research_cache.get(key)
        if cached:
            age = time.monotonic() - cached[0]
            ttl = 360 if cached[1].get("freshness") == "live_leads" else 25
            if age < ttl:
                return {**cached[1], "cached": True, "cache_kind": "memory",
                        "note": cached[1].get("note", "") + " This is a recent in-session reuse, not a new lookup."}
        packet = conduct_research(clean, github=self.settings.get("github_research") is True,
                                  code=code, project=project, cache=self.evidence_cache, limit=5,
                                  credentials_dir=self.store.base)
        self.research_cache[key] = (time.monotonic(), packet)
        if len(self.research_cache) > 50:
            old = sorted(self.research_cache, key=lambda k: self.research_cache[k][0])[:15]
            for item in old:
                self.research_cache.pop(item, None)
        return packet

    def claim(self, session_id: str, agent: Agent) -> threading.Event:
        with self.lock:
            if self.busy or self._kage_inference or self.gpu_scheduler.is_reserved():
                raise ValueError("A local run is already active, or Sumi briefing / GPU-exclusive test is active. Retry after it finishes.")
            cancel = threading.Event()
            agent.cancel = cancel
            self.busy[session_id] = (cancel, agent)
            return cancel

    def queue_update(self, session_id: str, instruction: str, run_id=None) -> dict:
        """Append a user instruction to the active conversation; execute at next safe agent step.

        We never claim to inject into a model call already generating tokens.
        Queue is retained in session memory if a run finishes before consuming it.
        """
        instruction = str(instruction or '').strip()
        if not instruction or len(instruction) > 6000:
            raise ValueError('Follow-up must contain 1–6,000 characters.')
        with self.lock:
            if session_id not in self.busy:
                raise ValueError('This conversation has no running task to update.')
            rid = self.active_runs.get(session_id)
            if rid and self.runs.get(rid)['state'] in TERMINAL:
                raise ValueError('This run has finished. Send a new turn for this instruction.')
            if run_id and run_id != rid:
                raise ValueError('The active run changed. Review the new run before sending instructions.')
            if self.busy[session_id][0].is_set():
                raise ValueError('This run is stopping. Send a new turn after it stops.')
            item = self.runs.queue_input(session_id, rid or '', instruction)
            self.store.add_message(session_id, 'user', instruction, mode='live_update', queued=True, input_id=item['id'])
            if rid:
                self.runs.append(rid, {'type': 'input_queued', 'input_id': item['id'], 'message': 'Instruction queued for the next step.'})
            count = sum(x['state'] == 'queued' for x in self.runs.inputs(session_id))
            return {'accepted': True, 'input_id': item['id'], 'state': 'queued', 'queued': count,
                    'message': 'Instruction queued for the next agent/model step; active work was not restarted.'}

    def take_updates(self, session_id: str) -> list[str]:
        with self.lock:
            if session_id in self.busy and self.busy[session_id][0].is_set():
                return []
            items = self.runs.consume_inputs(session_id)
            rid = self.active_runs.get(session_id)
            if items and rid:
                self.runs.append(rid, {'type': 'input_accepted', 'input_ids': [x['id'] for x in items],
                    'message': 'Queued instructions accepted for the next provider step; application is not yet verified.'})
            return [x['text'] for x in items]

    def release(self, session_id: str):
        with self.lock:
            self.busy.pop(session_id, None)
            self.active_runs.pop(session_id, None)

    def stop(self, session_id: str, run_id: str | None = None) -> bool:
        with self.lock:
            if run_id and self.active_runs.get(session_id) != run_id:
                return False
            pair = self.busy.get(session_id)
            if not pair:
                return False
            rid = self.active_runs.get(session_id)
            if pair[0].is_set():
                return True
            pair[0].set()
            if rid and self.runs.get(rid)['state'] not in TERMINAL:
                self.runs.append(rid, {'type': 'stop_requested'}, state='stopping')
        # Acknowledgment is immediate. Actual termination is published by the worker.
        threading.Thread(target=pair[1].stop, daemon=True, name='takumi-stop').start()
        return True


def handler_factory(runtime: Runtime):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Takumi/" + APP_VERSION
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if os.environ.get("TAKUMI_DEBUG") == "1":
                super().log_message(fmt, *args)

        def _host(self):
            host = self.headers.get("Host", "").strip().lower()
            if host.startswith("["):
                return host.split("]", 1)[0] + "]"
            return host.split(":")[0]

        def _remote(self):
            # Tailscale Serve may forward a request with Host=127.0.0.1 even
            # when the client originated on an iPhone. The forwarded headers
            # MUST take precedence over the host's loopback appearance.
            return (self._host() not in ("127.0.0.1", "localhost", "[::1]") or
                    "X-Forwarded-Proto" in self.headers or
                    "X-Forwarded-For" in self.headers or
                    "X-Forwarded-Host" in self.headers)

        def _proxy_host(self):
            return self.headers.get("X-Forwarded-Host", "").strip().lower().split(":")[0] or self._host()

        def _trusted(self):
            if not self._remote():
                return True
            # Deny all non-Tailscale requests. Users must not expose this app
            # through a public reverse proxy or a generic LAN interface.
            host = self._proxy_host()
            return (remote_mode_enabled() and host.endswith(".ts.net") and
                    self.headers.get("X-Forwarded-Proto", "").lower() == "https")

        def _origin_ok(self):
            origin = self.headers.get("Origin")
            if not origin:
                return True
            try:
                url = urlparse(origin)
                if self._remote():
                    return (url.scheme == "https" and
                            url.hostname == self._proxy_host() and url.port in (None, 443))
                return url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1") and url.port == self.server.server_port
            except ValueError:
                return False

        def _api_token_ok(self):
            token = self.headers.get("X-Takumi-Token", "")
            if self._remote():
                return runtime.pairing.authorized(token)
            return secrets.compare_digest(token, runtime.token)

        def _send(self, status, body: bytes, mime="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Service-Worker-Allowed", "/")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, item: dict | list):
            self._send(status, json.dumps(item, ensure_ascii=False).encode("utf-8"))

        def _fail(self, status, error):
            # These are application/environment preflight failures, not model refusals.
            self._json(status, {"error": str(error), "source": "application"})

        def _read(self, max_bytes=40000):
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > max_bytes:
                raise ValueError("Invalid request size.")
            parsed = json.loads(self.rfile.read(length))
            if not isinstance(parsed, dict):
                raise ValueError("Request must be a JSON object.")
            return parsed

        def do_GET(self):
            if not self._trusted() or not self._origin_ok():
                return self._fail(403, "Private workspace access only.")
            route = unquote(urlparse(self.path).path)
            if route == "/api/runtime-identity":
                if self._remote():
                    return self._fail(403, "Local identity probe only")
                disk_stamp = build_stamp()
                return self._json(200, {"version": APP_VERSION, "build_stamp": PROCESS_BUILD_STAMP,
                                        'disk_build_stamp': disk_stamp,
                                        'source_changed_since_start': disk_stamp != PROCESS_BUILD_STAMP,
                                        "process_id": os.getpid(), 'source_root': str(ROOT),
                                        'data_root': str(runtime.store.base),
                                        'startup_frontend_sha256': PROCESS_FRONTEND_SHA256,
                                        'frontend_sha256': hashlib.sha256((STATIC / 'app.js').read_bytes()).hexdigest(),
                                        'run_schema_version': 1})
            if route == "/api/remote-info":
                return self._json(200, {"remote": self._remote(), "pair_required": self._remote(),
                                        "remote_enabled": remote_mode_enabled()})
            if route == '/api/runtime-inventory':
                if self._remote() or not self._api_token_ok():
                    return self._fail(403, 'Inspect runtime diagnostics from your Windows PC.')
                model = parse_qs(urlparse(self.path).query).get('model', [''])[0]
                if model not in installed_models():
                    return self._fail(400, 'Select an installed model')
                return self._json(200, runtime_inventory(model, runtime.settings['speed'], runtime.settings['effort']))
            if route.startswith("/api/") and self._remote() and not self._api_token_ok():
                return self._fail(401, "Pair this device with Takumi on your PC first.")
            if route.startswith('/api/runs/'):
                if not self._api_token_ok():
                    return self._fail(403, 'Run access requires the workspace token')
                parts = route.split('/')
                run = runtime.runs.get(parts[3])
                if not run or not runtime.store.get_session(run['session_id']):
                    return self._fail(404, 'Run not found')
                if len(parts) == 5 and parts[4] == 'activity':
                    return self._json(200, {'state': run['state'], 'events': runtime.runs.activity(run['id'])})
                if len(parts) == 5 and parts[4] == 'events':
                    try:
                        cursor = self.headers.get('Last-Event-ID') or parse_qs(urlparse(self.path).query).get('after', ['0'])[0]
                        if ':' in cursor:
                            cursor_run, cursor = cursor.rsplit(':', 1)
                            if cursor_run != run['id']:
                                raise ValueError('Cursor belongs to another run')
                        after = int(cursor)
                        if after < 0 or after > run['seq']:
                            raise ValueError('Invalid replay cursor')
                    except ValueError as exc:
                        return self._fail(400, str(exc))
                    return self._stream_run(run['id'], after, canonical=True)
                return self._json(200, run)
            if route == "/auth/discord/callback":
                if self._remote():
                    return self._fail(403, "Sign in from your Takumi desktop PC")
                args = parse_qs(urlparse(self.path).query)
                try:
                    if args.get("error"):
                        raise ValueError("Discord authorization was canceled")
                    profile = runtime.discord_oauth.finish(args.get("state", [""])[0], args.get("code", [""])[0])
                    message = "Discord profile connected. Return to Takumi and reopen Profile."
                except (ValueError, OSError, KeyError) as exc:
                    message = "Discord sign-in did not complete. Reopen Profile in Takumi and retry."
                page = '<!doctype html><meta name="viewport" content="width=device-width"><title>Takumi profile</title><body style="font:16px system-ui;margin:12vh auto;max-width:520px;padding:20px;background:#faf8f4;color:#273b42"><h1>Takumi</h1><p>'+message+'</p></body>'
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            if route == "/api/profile/export":
                if self._remote():
                    return self._fail(403, "Export your profile from the Windows PC")
                return self._json(200, export_profile(runtime))
            if route == "/api/profile":
                return self._json(200, {"profile": runtime.profiles.current(),
                                        "discord_available": runtime.discord_oauth.available(),
                                        "redirect_uri": f"http://127.0.0.1:{self.server.server_port}/auth/discord/callback"})
            if route == "/api/research/providers":
                return self._json(200, key_status(runtime.store.base))
            if route == "/api/bootstrap":
                found_models = installed_models()
                return self._json(200, {
                    "token": runtime.token if not self._remote() else self.headers.get("X-Takumi-Token"),
                    "version": APP_VERSION,
                    "projects": runtime.store.data["projects"],
                    "active_project": runtime.store.data["active_project"],
                    "sessions": [{"id": s["id"], "title": s["title"], "project_id": s.get("project_id"), "updated": s.get("updated", 0)} for s in runtime.store.data["sessions"]],
                    "models": found_models, "model_catalog": model_catalog(found_models),
                    "sumi": sumi_state(found_models), "agentInstalled": bool(open_code_path()),
                    "busy": list(runtime.busy.keys()), "settings": runtime.settings,
                    "remote": self._remote(), "profile": runtime.profiles.current(),
                    "research_connectors": key_status(runtime.store.base),
                })
            if route == "/api/setup/status":
                if self._remote():
                    return self._fail(403, "Model setup status is available only on your Windows PC.")
                return self._json(200, runtime.provisioner.status())
            if route == "/api/models":
                found = installed_models()
                return self._json(200, {"models": found, "model_catalog": model_catalog(found), "sumi": sumi_state(found)})
            if route == "/api/expertise":
                kb = expertise_catalogue()
                return self._json(200, {"version": EXPERTISE_VERSION,
                                        "counts": {name: len(rows) for name, rows in kb.items()},
                                        "platforms": [{"id": row["id"], "title": row["title"]} for row in kb["platforms"]],
                                        "playbooks": [{"id": row["id"], "title": row["title"]} for row in kb["playbooks"]]})
            if route == "/api/expertise/lessons":
                return self._json(200, {"lessons": runtime.lessons.list()})
            if route == "/api/status":
                models = installed_models()
                return self._json(200, {"ollama": bool(models), "models": models, "openCode": bool(open_code_path()),
                                        "busy": list(runtime.busy.keys()), "gpu": safe_temperature(),
                                        "gpu_scheduler": runtime.gpu_scheduler.status()})
            if route == "/api/update/check":
                if self._remote():
                    return self._fail(403, "Install updates from the Windows PC")
                return self._json(200, runtime.updater.check())
            if route == "/api/engineering/tools":
                return self._json(200, {"tools": engineering_inventory(),
                                        "gpu_scheduler": runtime.gpu_scheduler.status()})
            if route == "/api/companion":
                return self._json(200, runtime.companion.status())
            if route == "/api/kage":
                return self._json(200, {**runtime.kage.status(), 'busy_sessions': list(runtime.busy)})
            if route.startswith("/api/kage/avatar/"):
                return self._json(200, runtime.kage.avatar(route.rsplit("/", 1)[-1]))
            if route == "/api/memory":
                return self._json(200, {"items": runtime.memory.list()})
            if route == "/api/processes":
                return self._json(200, {"processes": runtime.processes.list()})
            if route == "/api/bots":
                return self._json(200, {"bots": runtime.bots.status()})
            if route.startswith("/api/bots/log/"):
                return self._json(200, {"text": runtime.bots.tail(route.rsplit("/", 1)[-1])})
            if route == "/api/plugins":
                return self._json(200, {"plugins": plugin_status(runtime.settings)})
            if route == "/api/harness":
                return self._json(200, {"runs": runtime.harness.inspect()})
            if route.startswith("/api/harness/tasks/"):
                return self._json(200, {"tasks": runtime.harness.list_tasks(route.rsplit("/", 1)[-1])})
            if route == "/api/connections":
                proj = runtime.store.get_project(runtime.store.data.get("active_project"))
                return self._json(200, connections_status(Path(proj["path"]) if proj else None))
            if route in ("/api/training", "/api/remote/status"):
                if self._remote() or not self._api_token_ok():
                    return self._fail(403, "Manage training and phone setup on the Windows PC")
                return self._json(200, runtime.trainer.status() if route == "/api/training" else connection_status())
            if route == "/api/pairing/devices":
                if self._remote():
                    return self._fail(403, "Manage trusted devices from the PC.")
                return self._json(200, {"devices": runtime.pairing.list_devices(),
                                        "remote_enabled": remote_mode_enabled()})
            if route.startswith("/api/project/status/"):
                project = runtime.store.get_project(route.rsplit("/", 1)[-1])
                if not project:
                    return self._fail(404, "Project not found")
                return self._json(200, repo_snapshot(Path(project["path"])))
            if route.startswith("/api/reasoning/"):
                model = route[len("/api/reasoning/"):]
                if not model or len(model) > 200:
                    return self._fail(400, "Invalid model.")
                return self._json(200, reasoning_profile(model))
            if route.startswith("/api/session/"):
                sid = route[len("/api/session/"):]
                sess = runtime.store.get_session(sid)
                if not sess:
                    return self._fail(404, "Conversation not found.")
                return self._json(200, {**sess, 'runs': runtime.runs.for_session(sid),
                    'inputs': runtime.runs.inputs(sid), 'goals': runtime.goals.for_session(sid)})
            target = route.lstrip("/") or "index.html"
            if target.startswith("web/"):
                target = target[4:]
            candidate = (STATIC / target).resolve()
            if not candidate.is_relative_to(STATIC.resolve()) or not candidate.is_file():
                return self._fail(404, "Not found.")
            if candidate.suffix not in MIMES:
                return self._fail(404, "File type not available.")
            return self._send(200, candidate.read_bytes(), MIMES[candidate.suffix])

        def do_POST(self):
            if not self._trusted() or not self._origin_ok():
                return self._fail(403, "Private workspace access only.")
            route = urlparse(self.path).path
            if route == "/api/pair":
                if not self._remote():
                    return self._fail(403, "Pairing is for the private iPhone connection.")
                try:
                    body = self._read()
                    return self._json(200, runtime.pairing.pair(str(body.get("code", "")), str(body.get("label", "iPhone"))))
                except ValueError as exc:
                    return self._fail(403, str(exc))
            if not self._api_token_ok():
                return self._fail(403, "Workspace authorization required.")
            try:
                body = self._read(4_000_000 if route == "/api/kage/avatar/import" else 1_000_000 if route == "/api/profile/import" else 40000)
                if route.startswith('/api/training/'):
                    if self._remote():
                        return self._fail(403, 'Control training from the Windows PC')
                    if route == '/api/training/configure':
                        return self._json(200, runtime.trainer.configure(body))
                    if route == '/api/training/start':
                        return self._json(202, runtime.trainer.start(body.get('mode'), body.get('steps', 2), body.get('checkpoint_id')))
                    if route == '/api/training/stop':
                        return self._json(200, runtime.trainer.stop())
                    return self._fail(404, 'Unknown training action')
                if route == "/api/profile/select":
                    if self._remote():
                        return self._fail(403, "Choose the profile from your Windows PC")
                    kind = str(body.get("kind", ""))
                    if kind not in ("guest", "windows"):
                        raise ValueError("Discord requires a verified OAuth sign-in")
                    return self._json(200, {"profile": runtime.profiles.select(kind)})
                if route == "/api/profile/import":
                    if self._remote():
                        return self._fail(403, "Import profiles from the Windows PC")
                    if runtime.busy:
                        raise ValueError("Stop active work before importing a profile")
                    return self._json(200, {"imported": import_profile(runtime, body)})
                if route == "/api/profile/discord/start":
                    if self._remote():
                        return self._fail(403, "Start Discord sign-in on your Windows PC")
                    return self._json(200, runtime.discord_oauth.start(self.server.server_port))
                if route == "/api/research/providers":
                    if self._remote():
                        return self._fail(403, "Configure research credentials on the desktop")
                    provider = str(body.get("provider", ""))
                    return self._json(200, set_key(runtime.store.base, provider, str(body.get("key", "")),
                                                    google_enabled=body.get("google_enabled") if provider=="google" else None))
                if route == "/api/projects":
                    project = runtime.store.register_project(str(body.get("path", "")))
                    return self._json(200, {"project": project})
                if route == "/api/projects/pick":
                    if self._remote():
                        return self._fail(403, "Choose folders from the PC. Existing projects can be opened remotely.")
                    try:
                        import tkinter as tk
                        from tkinter import filedialog
                        root = tk.Tk()
                        root.withdraw()
                        root.attributes("-topmost", True)
                        path = filedialog.askdirectory(title="Choose a project for Takumi")
                        root.destroy()
                    except Exception as exc:
                        return self._fail(501, "Folder picker unavailable; paste the path instead: " + str(exc))
                    if not path:
                        return self._fail(400, "Folder selection canceled.")
                    return self._json(200, {"project": runtime.store.register_project(path)})
                if route == "/api/kage/select":
                    return self._json(200, runtime.kage.select(str(body.get("member", ""))))
                if route == '/api/kage/notifications/read':
                    return self._json(200, runtime.kage.acknowledge(body.get('ids', [])))
                if route == '/api/kage/goals':
                    if self._remote(): return self._fail(403, 'Start independent Kage goals on the Windows PC')
                    pid = str(body.get('project_id', ''))
                    if not runtime.store.get_project(pid): raise ValueError('Choose a registered project')
                    prompt = str(body.get('prompt', '')).strip()
                    if not 3 <= len(prompt) <= 24000: raise ValueError('Describe the project goal in 3–24,000 characters')
                    seconds = body.get('seconds', 43200)
                    if type(seconds) not in (int, float) or not 1 <= seconds <= 43200: raise ValueError('Choose a budget up to 12 hours')
                    session = runtime.store.new_session(pid)
                    with runtime.store.lock:
                        session.update(purpose='kage', title='Kage · ' + prompt[:65]); runtime.store.save()
                    payload = {'session_id': session['id'], 'prompt': '/goal ' + prompt, 'mode': 'agent',
                               'model': str(body.get('model', '')), 'kage_member': runtime.kage.selected,
                               'effort': 'high', 'speed': 'standard', 'polish': False}
                    job = runtime.kage.queue_goal(payload, title=prompt, project_id=pid, seconds=seconds)
                    return self._json(201, {'job': job})
                if route == '/api/kage/goals/stop':
                    with runtime.lock:
                        job = runtime.kage.cancel_job(str(body.get('job_id', '')))
                        if job.get('run_id'): runtime.stop(job['session_id'], job['run_id'])
                    return self._json(200, {'stopped': True})
                if route == "/api/kage/tasks":
                    return self._json(201, {"task": runtime.kage.add(
                        kind=str(body.get("kind", "")), cadence=str(body.get("cadence", "manual")),
                        member=str(body.get("member", "ren")), title=str(body.get("title", "")),
                        project_id=str(body.get("project_id") or "") or None,
                        query=str(body.get("query", "")), clock=str(body.get("clock", "09:00")),
                        test_id=str(body.get("test_id", "")),
                        allow_network=body.get("allow_network") is True,
                        use_sumi=body.get("use_sumi") is True)})
                if route == "/api/kage/tasks/delete":
                    return self._json(200, {"deleted": runtime.kage.remove(str(body.get("task_id", "")))})
                if route == "/api/kage/tasks/enable":
                    return self._json(200, {"task": runtime.kage.set_enabled(
                        str(body.get("task_id", "")), body.get("enabled"))})
                if route == "/api/kage/tasks/run":
                    return self._json(202, {"task": runtime.kage.run_now(str(body.get("task_id", "")))})
                if route == "/api/kage/avatar/import":
                    if self._remote():
                        return self._fail(403, "Import your licensed companion artwork on your Windows PC")
                    return self._json(200, runtime.kage.import_avatar(
                        str(body.get("member", "")), str(body.get("data", "")),
                        str(body.get("source", "")), str(body.get("attribution", "")),
                        rights_confirmed=body.get("rights_confirmed") is True))
                if route == "/api/sessions":
                    pid = body.get("project_id")
                    if pid and not runtime.store.get_project(str(pid)):
                        raise ValueError("Select a registered project.")
                    return self._json(201, runtime.store.new_session(pid))
                if route == '/api/kage/chat':
                    pid = body.get('project_id') or None
                    if pid and not runtime.store.get_project(pid):
                        raise ValueError('Select a registered project.')
                    with runtime.store.lock:
                        session = next((s for s in runtime.store.data['sessions']
                            if s.get('purpose') == 'kage' and s.get('project_id') == pid), None)
                        if session is None:
                            session = runtime.store.new_session(pid)
                            session.update(purpose='kage', title='Kage coordination')
                            runtime.store.save()
                    return self._json(200, session)
                if route == "/api/sessions/delete":
                    sid = str(body.get("session_id", ""))
                    with runtime.lock:
                        if sid in runtime.busy:
                            raise ValueError("Stop the active run before deleting its conversation.")
                        runtime.kage.forget_session(sid)
                        runtime.runs.delete_session(sid)
                        runtime.goals.delete_session(sid)
                        return self._json(200, {"deleted": runtime.store.delete_session(sid)})
                if route == "/api/run/update":
                    return self._json(202, runtime.queue_update(
                        str(body.get('session_id', '')), body.get('prompt', ''), body.get('run_id')))
                if route == '/api/run/input/edit':
                    text = body.get('text')
                    if text is not None and (not isinstance(text, str) or not text.strip() or len(text) > 6000):
                        raise ValueError('Instruction must contain 1–6,000 characters.')
                    sid = str(body.get('session_id', ''))
                    with runtime.lock:
                        runtime.runs.edit_input(sid, str(body.get('input_id', '')), text)
                        with runtime.store.lock:
                            session = runtime.store.get_session(sid)
                            for message in (session or {}).get('messages', []):
                                if message.get('input_id') == body.get('input_id'):
                                    message['cancelled'] = text is None
                                    if text is not None:
                                        message['content'] = text
                            runtime.store.save()
                    return self._json(200, {'updated': True})
                if route == "/api/stop":
                    accepted = runtime.stop(str(body.get('session_id', '')), body.get('run_id'))
                    return self._json(200, {'stop_requested': accepted,
                                           'message': 'Stopping; waiting for owned work to finish.' if accepted else 'This run is no longer active.'})
                if route == "/api/settings":
                    return self._json(200, {"settings": runtime.update_settings(body)})
                if route == "/api/research/cache/clear":
                    if self._remote():
                        return self._fail(403, "Clear research history on your PC")
                    runtime.evidence_cache.clear()
                    runtime.research_cache.clear()
                    return self._json(200, {"cleared": True})
                if route == "/api/research":
                    query = str(body.get("query", ""))
                    if len(query) > 800 or not query.strip():
                        raise ValueError("Enter a concise research query")
                    pid = str(body.get("project_id", ""))
                    selected = runtime.store.get_project(pid) if pid else None
                    if pid and not selected:
                        raise ValueError("Unknown project for local source research")
                    project_path = Path(selected["path"]) if selected else None
                    return self._json(200, runtime.get_research(query, code=body.get("code") is True,
                                                                 project=project_path))
                if route == "/api/code/validate":
                    code = body.get("code", "")
                    language = str(body.get("language", "html"))
                    if (not isinstance(code, str) or len(code) > 36000
                            or language not in ("html", "htm", "js", "javascript", "mjs", "css", "json", "ts", "tsx", "python", "py", "cs", "c", "cpp", "c++", "cxx", "glsl", "vert", "frag", "comp", "geom", "tesc", "tese", "rs", "go", "txt")):
                        raise ValueError("Select a supported code block under 36,000 characters")
                    fenced = "```" + language + "\n" + code + "\n```"
                    return self._json(200, validate_generated_code(fenced))
                if route == "/api/source/inspect":
                    action = str(body.get("action", "map"))
                    repo = str(body.get("repository", ""))
                    if action == "map":
                        return self._json(200, map_repository(repo, str(body.get("query", ""))[:140], ref=body.get('ref')))
                    if action == "file":
                        return self._json(200, inspect_file(repo, str(body.get("path", "")), str(body.get("ref", "HEAD"))))
                    raise ValueError("Unsupported source-inspection action")
                if route == "/api/update/prepare":
                    if self._remote():
                        return self._fail(403, "Install updates on the Windows PC")
                    if runtime.busy:
                        raise ValueError("Finish the active task before staging a software update")
                    return self._json(200, runtime.updater.prepare())
                if route == "/api/update/apply":
                    if self._remote():
                        return self._fail(403, "Install updates on the Windows PC")
                    if body.get("confirmed") is not True or runtime.busy:
                        raise ValueError("Confirm the update and finish the active task before installation")
                    return self._json(202, runtime.updater.install_on_close(ROOT))
                if route.startswith("/api/engineering/"):
                    if self._remote():
                        return self._fail(403, "Inspect local binaries only from the Windows PC")
                    proj = runtime.store.get_project(str(body.get("project_id", "")))
                    if not proj:
                        raise ValueError("Choose a registered project first")
                    project_root = Path(proj["path"])
                    if route == '/api/engineering/index':
                        snapshot = runtime.source_index.refresh(project_root)
                        action = body.get('action', 'search')
                        if action == 'trace':
                            return self._json(200, SourceIndex.trace(snapshot, str(body.get('query', '')), str(body.get('end', ''))))
                        if action == 'refresh':
                            return self._json(200, {k: v for k, v in snapshot.items() if k != 'units'})
                        return self._json(200, SourceIndex.query(snapshot, str(body.get('query', '')), str(body.get('kind', 'all'))))
                    if route == '/api/engineering/formats':
                        action = body.get('action', 'list')
                        if action == 'save':
                            return self._json(201, {'schema': runtime.formats.save(project_root, body.get('schema'))})
                        if action == 'inspect':
                            return self._json(200, runtime.formats.inspect(project_root, str(body.get('path', '')), str(body.get('name', '')), body.get('revision')))
                        if action != 'list': raise ValueError('Unknown format action')
                        return self._json(200, {'formats': runtime.formats.list(project_root)})
                    if route == "/api/engineering/symbols":
                        return self._json(200, symbol_search(project_root, str(body.get("query", ""))))
                    if route == "/api/engineering/binary":
                        return self._json(200, inspect_binary(project_root, str(body.get("path", ""))))
                    if route == "/api/engineering/spirv":
                        return self._json(200, validate_spirv(project_root, str(body.get("path", ""))))
                    if route == "/api/engineering/disassemble":
                        return self._json(200, disassemble_spirv(project_root, str(body.get("path", ""))))
                    if route == "/api/engineering/ghidra":
                        if body.get("confirmed") is not True:
                            raise ValueError("Confirm opt-in local Ghidra analysis before launching")
                        if runtime.busy or runtime.gpu_scheduler.is_reserved():
                            raise ValueError("Finish the active model or GPU work before analyzing")
                        return self._json(200, analyze_ghidra(project_root, str(body.get("path", ""))))
                    raise ValueError("Unsupported engineering analysis operation")
                if route == "/api/harness/run":
                    return self._json(200, {"run": runtime.harness.launch(
                        str(body.get("project_id", "")), str(body.get("task_id", "")))})
                if route == "/api/harness/stop":
                    return self._json(200, {"stopped": runtime.harness.stop(str(body.get("run_id", "")))})
                if route == "/api/plugins/prepare":
                    if self._remote():
                        return self._fail(403, "Prepare deployments and pushes on the PC")
                    project = runtime.store.get_project(str(body.get("project_id", "")))
                    if not project:
                        raise ValueError("Select a registered project")
                    return self._json(200, runtime.approvals.prepare(
                        str(body.get("plugin", "")), str(body.get("action", "")),
                        Path(project["path"]), body.get("arguments") if isinstance(body.get("arguments"), dict) else {}))
                if route == "/api/plugins/execute":
                    return self._json(200, runtime.approvals.execute(
                        str(body.get("approval_id", "")), local_only=not self._remote()))
                if route == "/api/connections/sign-in":
                    if self._remote():
                        return self._fail(403, "Complete account sign-ins from your Windows PC")
                    return self._json(200, initiate_sign_in(str(body.get("provider", "")),
                                                           model=str(body.get("model", "bonsai-2:27b"))))
                if route == "/api/setup/start":
                    if self._remote():
                        return self._fail(403, "Install models from the Windows desktop, not from a remote device.")
                    if runtime.busy:
                        raise ValueError("Finish or stop the active coding task before installing a model")
                    if not isinstance(body.get("lightweight", False), bool) or not isinstance(body.get("skip_coding_agent", False), bool):
                        raise ValueError("Invalid setup options")
                    return self._json(202, runtime.provisioner.start(
                        lightweight=body.get("lightweight", False),
                        skip_coding_agent=body.get("skip_coding_agent", False)))
                if route == "/api/models/register":
                    if self._remote():
                        return self._fail(403, "Set up models from your Windows PC")
                    if runtime.busy:
                        raise ValueError("Finish or stop active work before configuring the model")
                    profile_name = body.get('profile', 'sumi')
                    if profile_name not in ('sumi', 'rei'):
                        raise ValueError('Choose Sumi or Rei')
                    result = (register_rei if profile_name == 'rei' else register_sumi)(installed_models())
                    invalidate_installed_models()
                    return self._json(200, result)
                if route == "/api/expertise/search":
                    return self._json(200, retrieve_expertise(str(body.get("query", "")), max_chars=5000))
                if route == "/api/expertise/lessons/add":
                    scope = body.get("project_id")
                    if scope and not runtime.store.get_project(scope):
                        raise ValueError("Unknown project scope")
                    return self._json(201, {"lesson": runtime.lessons.add(
                        str(body.get("text", "")), str(body.get("evidence", "")),
                        scope, reviewed=body.get("reviewed") is True)})
                if route == "/api/expertise/lessons/delete":
                    return self._json(200, {"deleted": runtime.lessons.remove(str(body.get("id", "")))})
                if route == "/api/memory":
                    scope = body.get("project_id")
                    if scope and not runtime.store.get_project(scope):
                        raise ValueError("Unknown memory project scope")
                    return self._json(201, {"memory": runtime.memory.add(str(body.get("text", "")), scope)})
                if route == "/api/memory/delete":
                    return self._json(200, {"deleted": runtime.memory.remove(str(body.get("id", "")))})
                if route == "/api/remote/setup":
                    if self._remote():
                        return self._fail(403, "Configure remote access from the Windows PC")
                    if os.name != "nt":
                        raise RuntimeError("The guided iPhone setup requires Windows")
                    script = ROOT / "scripts" / "Enable-iPhone-PWA.ps1"
                    if not script.is_file():
                        raise RuntimeError("The iPhone setup script was not packaged")
                    subprocess.Popen(["powershell.exe", "-NoProfile", "-NoExit", "-ExecutionPolicy", "Bypass",
                                      "-File", str(script)], cwd=str(ROOT),
                                     creationflags=subprocess.CREATE_NEW_CONSOLE, stdin=subprocess.DEVNULL)
                    return self._json(200, {"started": True, "message": "Finish the guided private iPhone setup in the Windows window."})
                if route == "/api/remote/pair-code":
                    if self._remote():
                        return self._fail(403, "Only the PC can issue pairing codes")
                    return self._json(200, runtime.pairing.new_code())
                if route == "/api/remote/revoke":
                    if self._remote():
                        return self._fail(403, "Manage trusted devices from the PC")
                    return self._json(200, {"revoked": runtime.pairing.revoke(str(body.get("id", "")))})
                if route == "/api/process/start":
                    return self._json(200, {"process": runtime.processes.launch(
                        str(body.get("project_id", "")), str(body.get("path", "")))})
                if route == "/api/process/stop":
                    return self._json(200, {"stopped": runtime.processes.stop(
                        str(body.get("process_id", "")))})
                if route == "/api/bots/configure":
                    return self._json(200, {"bot": runtime.bots.configure(
                        str(body.get("project_id", "")), str(body.get("script", "")),
                        autostart=body.get("autostart") is True,
                        autorestart=body.get("autorestart", True) is True)})
                if route == "/api/bots/start":
                    return self._json(200, {"bot": runtime.bots.start(str(body.get("project_id", "")))})
                if route == "/api/bots/stop":
                    return self._json(200, {"bot": runtime.bots.stop(str(body.get("project_id", "")))})
                if route == "/api/run":
                    return self._run(body)
                return self._fail(404, "Unknown action.")
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                return self._fail(400, str(exc))
            except (OSError, RuntimeError) as exc:
                return self._fail(500, "Local service error: " + str(exc))

        def _stream_headers(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

        def _emit(self, event: dict):
            message = "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            self.wfile.write(message.encode("utf-8"))
            self.wfile.flush()

        def _stream_run(self, run_id, after=0, canonical=False):
            self._stream_headers()
            self.close_connection = True
            heartbeat = time.monotonic()
            try:
                while True:
                    events = runtime.runs.events(run_id, after)
                    for event in events:
                        data = event if canonical else {**event['payload'],
                            'run_id': run_id, 'seq': event['seq'], 'event_id': event['event_id']}
                        frame = 'id: ' + event['event_id'] + '\ndata: ' + json.dumps(data, ensure_ascii=False) + '\n\n'
                        self.wfile.write(frame.encode('utf-8'))
                        self.wfile.flush()
                        after = event['seq']
                    run = runtime.runs.get(run_id)
                    if run['state'] in TERMINAL and after >= run['seq']:
                        return
                    if time.monotonic() - heartbeat >= 2:
                        self.wfile.write(b': keepalive\n\n')
                        self.wfile.flush()
                        heartbeat = time.monotonic()
                    time.sleep(.04)
            except OSError:
                # Subscription loss is not execution failure or cancellation.
                return

        def _run(self, payload: dict, *, subscribe=True):
            request_id = payload.get('request_id')
            if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 128):
                raise ValueError('Invalid request ID')
            if request_id:
                existing = runtime.runs.find_request(request_id, payload)
                if existing:
                    return self._stream_run(existing['id']) if subscribe else existing
            sid = str(payload.get("session_id", ""))
            prompt = str(payload.get("prompt", "")).strip()
            mode = payload.get("mode", "chat")
            model = str(payload.get("model", ""))
            level = payload.get("reasoning", "default")
            enabled_polish = payload.get("polish", runtime.settings.get("refine_reports", True)) is True
            autocontinue = payload.get("autocontinue", True) is True
            passes = max(1, min(12, int(payload.get("passes", 6))))
            speed = str(payload.get("speed", runtime.settings.get("speed", "standard")))
            effort = str(payload.get("effort", runtime.settings.get("effort", "balanced")))
            personality = runtime.settings.get("personality", "precise")
            kage_member = str(payload.get("kage_member") or runtime.kage.selected)
            persona_context = kage_context(kage_member)
            language_setting = runtime.settings.get('language', 'auto')
            reply_language = resolve_language(prompt, language_setting)
            hardware_snapshot = safe_temperature()
            profile = profile_for(speed, effort, model=model,
                                  vram_mb=hardware_snapshot.get("memory_total_mb"))
            session = runtime.store.get_session(sid)
            if not session or not prompt or len(prompt) > 26000:
                raise ValueError("Select a conversation and enter a prompt under 26,000 characters.")
            project = runtime.store.get_project(session.get("project_id"))
            goal = None
            contract = None
            goal_seconds = None
            goal_deadline = None
            goal_command = prompt.startswith('/goal ')
            resume_goal = prompt.strip() == '/goal resume'
            if goal_command:
                if not project:
                    raise ValueError('Choose a project before starting a bounded coding goal.')
                if resume_goal:
                    previous_goals = runtime.goals.for_session(sid)
                    if not previous_goals:
                        raise ValueError('This conversation has no saved goal to resume.')
                    goal = previous_goals[0]
                    prompt = goal['prompt']
                else:
                    prompt = prompt[len('/goal '):].strip()
                    if not prompt:
                        raise ValueError('Describe the goal after /goal.')
                    seconds = payload.get('goal_seconds', 43200)
                    deadline = payload.get('goal_deadline', time.time() + 43200)
                    if (not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 1 <= seconds <= 43200
                            or not isinstance(deadline, (int, float)) or isinstance(deadline, bool)
                            or not time.time() < deadline <= time.time() + 43201):
                        raise ValueError('Goal duration must be 1 second to 12 hours, with an unexpired absolute deadline.')
                    goal_seconds, goal_deadline = seconds, deadline
                    contract = completion_contract(Path(project['path']))
                mode = 'agent'
            mode = choose_mode(mode, prompt, bool(project))
            if level is not None and not isinstance(level, (str, bool)):
                raise ValueError("Invalid reasoning level.")
            if mode == "chat" and model not in installed_models():
                raise ValueError("Select an installed local model. Check model setup if none is available.")
            if mode == "agent" and not project:
                raise ValueError("Choose a project folder before running the coding agent.")
            if mode == "agent" and not open_code_path():
                raise ValueError("The local coding engine is not configured. Open Tools in Settings to set it up.")
            if speed not in ("standard", "boost") or effort not in ("quick", "balanced", "high", "maximum"):
                raise ValueError("Invalid speed or reasoning profile")
            guard_new_run(hardware_snapshot)
            # No overlapping active streams for one conversation.
            agent = runtime.agent_cls() if mode == "agent" else ChatCancellation()
            with runtime.lock:
                # Recheck under the claim lock: simultaneous retry tabs share one run.
                existing = runtime.runs.find_request(request_id, payload) if request_id else None
                if not existing:
                    cancel = runtime.claim(sid, agent)
                    try:
                        import uuid
                        run, _ = runtime.runs.create(sid, request_id or uuid.uuid4().hex, payload)
                        runtime.active_runs[sid] = run['id']
                        if goal_command:
                            goal = (runtime.goals.resume(goal['id'], sid, project['id']) if resume_goal else
                                    runtime.goals.create(sid, project['id'], prompt,
                                        goal_seconds, deadline=goal_deadline))
                            if not resume_goal:
                                goal['completion_contract'] = contract
                            runtime.goals.checkpoint(goal, state='running', run_id=run['id'],
                                                     native_session=session.get('open_code_session'))
                        runtime.store.add_message(sid, "user", prompt, mode=mode, run_id=run['id'])
                    except Exception:
                        if 'run' in locals():
                            runtime.runs.append(run['id'], {'type': 'error', 'message': 'Run preparation failed'},
                                                state='failed', event_type='run.failed')
                        runtime.release(sid)
                        raise
            if existing:
                return self._stream_run(existing['id']) if subscribe else existing

            deadline_hit = threading.Event()
            deadline_timer = None
            if goal:
                def expire_goal():
                    deadline_hit.set()
                    runtime.stop(sid, run['id'])
                deadline_timer = threading.Timer(runtime.goals.remaining(goal), expire_goal)
                deadline_timer.daemon = True
                deadline_timer.start()

            def emit(event):
                with runtime.lock:
                    return emit_locked(event)

            def emit_locked(event):
                if event['type'] == 'done':
                    outcome = ('stopped' if cancel.is_set() else 'blocked' if event.get('status') in ('BLOCKED', 'CONTINUE') or mode == 'agent' and event.get('status') != 'COMPLETE'
                               else 'completed')
                    event = {**event, 'run_state': outcome}
                    if goal:
                        goal_state = 'budget_exhausted' if deadline_hit.is_set() else outcome
                        runtime.goals.checkpoint(goal, state=goal_state, run_id=run['id'],
                            native_session=session.get('open_code_session'), report=event.get('text', ''))
                        event.update(goal_id=goal['id'], goal_state=goal_state, deadline=goal['deadline'])
                    runtime.runs.append(run['id'], event, state=outcome, event_type='run.' + outcome)
                else:
                    runtime.runs.append(run['id'], event,
                        state='running' if event['type'] == 'start' and not cancel.is_set() else None)

            def execute():
                execute_started = time.perf_counter()
                first_visible_ms = None
                total_generated = 0
                total_generation_ns = 0
                generation_attempts = 0
                answer = ""
                original = ""
                polished = False
                status = None
                status_origin = None
                metrics = None
                code_validation = None
                initial_code_validation = None
                code_repaired = False
                research_data = None
                source_snapshot = None
                source_context = ''
                evidence_report = None
                completion_verification = None
                def measure(item, visible=True):
                    nonlocal first_visible_ms, total_generated, total_generation_ns, generation_attempts, metrics
                    if visible and first_visible_ms is None and (item.get('type') == 'first_token' or item.get('type') == 'delta' and item.get('text')):
                        first_visible_ms = round((time.perf_counter() - execute_started) * 1000)
                    if item.get('type') == 'first_token':
                        return {**item, 'provider_milliseconds': item.get('milliseconds'), 'milliseconds': first_visible_ms}
                    if item.get('type') == 'metrics':
                        generation_attempts += 1
                        total_generated += item.get('eval_count') or 0
                        total_generation_ns += item.get('eval_duration') or 0
                        metrics = {'tokens_per_second': round(total_generated / (total_generation_ns / 1e9), 1) if total_generation_ns else item.get('tokens_per_second'),
                            'done_reason': item.get('done_reason'), 'output_limit': item.get('output_limit'),
                            'thinking_request': item.get('thinking_request'), 'generation_attempts': generation_attempts,
                            'eval_count': total_generated, 'eval_duration': total_generation_ns,
                            'time_to_first_token_ms': first_visible_ms, 'provider_first_token_ms': item.get('time_to_first_token_ms'),
                            'response_wall_ms': round((time.perf_counter() - execute_started) * 1000),
                            'prompt_eval_ms': item.get('prompt_eval_ms'), 'prompt_eval_tokens': item.get('prompt_eval_tokens')}
                        return {**item, **metrics}
                    return item
                try:
                    specialist_pack = retrieve_expertise(prompt, max_chars=3900 if speed == "boost" else 5500)
                    specialist_context = specialist_pack.get("context", "")
                    lesson_context = runtime.lessons.context(prompt, session.get("project_id"))
                    emit({"type": "start", "session_id": sid, "mode": mode})
                    specialist_context += '\n' + response_contract(prompt, acting=mode == 'agent')
                    if project and not cancel.is_set():
                        emit({'type': 'progress', 'stage': 'indexing', 'message': 'Refreshing project source evidence'})
                        try:
                            source_snapshot = runtime.source_index.refresh(Path(project['path']), cancel=cancel, budget=8)
                            recent_queries = ' '.join(m.get('content', '')[:1000] for m in session['messages'][-4:] if m.get('role') == 'user')
                            source_context = SourceIndex.context(source_snapshot, recent_queries + '\n' + prompt)
                            source_context += '\n' + runtime.formats.context(Path(project['path']), prompt)
                            emit({'type': 'source_index', 'files': len(source_snapshot['units']),
                                  'complete': source_snapshot['complete'], 'commit': source_snapshot['commit']})
                        except (OSError, ValueError) as exc:
                            emit({'type': 'notice', 'message': 'Source index unavailable: ' + str(exc)[:160]})
                    specialist_context += '\n' + source_context
                    if goal:
                        emit({'type': 'notice', 'message': 'Bounded goal active. The original wall-clock deadline includes waiting and restarts.',
                              'goal_id': goal['id'], 'deadline': goal['deadline']})
                        check_contract = goal.get('completion_contract', {'tasks': []})
                        if check_contract.get('tasks'):
                            specialist_context += ('\nCOMPLETION CHECKS: the application runs these declared tests before accepting completion. '
                                'Fix implementation failures; preserve the task manifest and protected test files.\n' +
                                json.dumps(check_contract['tasks'])[:6000])
                    if specialist_pack.get("relevant"):
                        emit({"type": "specialization", "version": specialist_pack["version"],
                                    "platforms": specialist_pack["platforms"],
                                    "playbooks": specialist_pack["playbooks"],
                                    "message": "Loaded task-specific emulator engineering references"})
                    if mode == "chat":
                        research_data = None
                        chosen_research = runtime.settings.get("research_mode", "auto")
                        repo_request = requested_repository(prompt, session['messages'])
                        if repo_request or should_research(prompt, chosen_research):
                            emit({"type": "progress", "stage": "research", "message": "Checking live references"})
                            try:
                                project_path = Path(project["path"]) if project else None
                                research_data = (collect_repository_review(repo_request, cancel) if repo_request else
                                                 runtime.get_research(prompt, project=project_path))
                                sources = research_data.get("results", [])
                                freshness = research_data.get("freshness", "unavailable")
                                emit({"type": "sources", "sources": sources,
                                            "cached": research_data.get("cached", False),
                                            "freshness": freshness, "providers": research_data.get("providers", [])})
                                descriptions = {"live_leads": "Live source leads",
                                                "live_source_files": "Repository source files read",
                                                "live_comments": "Reddit comment sample read", "live_post": "Reddit post read",
                                                "stale_cache": "Outdated cached references (live search unavailable)",
                                                "local_only": "Local project references only", "unavailable": "All research sources unavailable"}
                                runtime.store.add_event(sid, {"kind": "research", "title": "Research status",
                                                               "status": "done" if sources else "unavailable",
                                                               "detail": f"{descriptions.get(freshness, 'Research')} · {len(sources)} leads"})
                            except (ValueError, OSError) as exc:
                                emit({"type": "notice", "message": "Live research unavailable: " + str(exc)[:120]})
                        emit({"type": "progress", "stage": "analysis", "message": "Reviewing evidence and preparing answer"})
                        accepted_inputs = {x['id'] for x in runtime.runs.inputs(sid) if x['state'] == 'accepted'}
                        previous = [m for m in session["messages"] if m.get("role") in ("user", "assistant")
                                    and not m.get('cancelled') and
                                    (m.get('mode') != 'live_update' or m.get('input_id') in accepted_inputs)]
                        # History is deliberately compact under boost; auto-trimming
                        # a giant conversation improves time-to-first-token and VRAM.
                        history = [{"role": m["role"], "content": m['content'] if m.get('code_repaired') else m.get("original") or m["content"]} for m in previous]
                        history = history[-(8 if speed == "boost" else 22):]
                        max_history_chars = 18000 if speed == "boost" else 65000
                        while len(json.dumps(history)) > max_history_chars and len(history) > 2:
                            history.pop(0)
                        import inspect
                        stream_kwargs = {}
                        if "speed" in inspect.signature(ui_event_stream).parameters:
                            stream_kwargs = {"speed": speed, "effort": effort,
                                             "memory": runtime.memory.context(session.get("project_id")) + ("\n" + lesson_context if lesson_context else "") + ("\n" + project_facts(prompt) if project_facts(prompt) else "") + "\n" + persona_context,
                                             "personality": personality,
                                             "research_context": research_context(research_data) if research_data else "",
                                             "expertise_context": specialist_context,
                                             "language": language_setting,
                                             "vram_mb": hardware_snapshot.get("memory_total_mb")}
                        for optional in ('language', 'vram_mb'):
                            if optional not in inspect.signature(ui_event_stream).parameters:
                                stream_kwargs.pop(optional, None)
                        used_model = model
                        for item in resilient_chat_events(ui_event_stream, "http://127.0.0.1:11434",
                                                          history, model, level, cancel, stream_kwargs,
                                                          installed_models(), allow_alternative=runtime.settings.get("model_failover") is True,
                                                          pause=time.sleep):
                            item = measure(item)
                            if item.get("type") == "model_used":
                                used_model = item.get("model", model)
                            if item.get("type") == "delta":
                                answer += item.get("text", "")
                            emit(item)
                        # Users can add instructions without terminating a streaming reply.
                        # Each follow-up is a separate local-model call in the SAME
                        # conversation/HTTP stream; it is not injected mid-token.
                        for followup_step in range(3):
                            queued = runtime.take_updates(sid)
                            if not queued or cancel.is_set():
                                break
                            emit({'type': 'progress', 'stage': 'continuation',
                                        'message': 'Adding your new instructions to this conversation'})
                            history.append({'role': 'assistant', 'content': answer[-10000:]})
                            history.extend({'role':'user','content':x} for x in queued)
                            history = history[-12:]
                            supplement = ''
                            for item in resilient_chat_events(
                                ui_event_stream, 'http://127.0.0.1:11434', history,
                                used_model, level, cancel, stream_kwargs, installed_models(),
                                allow_alternative=runtime.settings.get('model_failover') is True,
                                pause=time.sleep):
                                item = measure(item)
                                if item.get('type') == 'delta':
                                    if not supplement:
                                        emit({'type':'delta','text':'\n\n'})
                                    supplement += item.get('text', '')
                                elif item.get('type') == 'model_used':
                                    used_model = item.get('model', used_model)
                                emit(item)
                            if supplement:
                                answer += '\n\n' + supplement
                        original = answer
                        if runtime.settings.get('verify_generated_code', True):
                            code_validation = validate_generated_code(answer, cancel=cancel, prompt=prompt)
                            initial_code_validation = code_validation
                            if code_validation['blocks']:
                                emit({'type': 'code_validation', 'report': code_validation})
                            correction_prompt = engineering_repair_prompt(prompt, answer, code_validation)
                            if (correction_prompt and runtime.settings.get('repair_generated_code', True)
                                    and speed != 'boost' and not cancel.is_set()):
                                emit({'type': 'progress', 'stage': 'verification', 'message': 'Correcting compiler-reported errors in the draft'})
                                corrected = ''
                                repair_level = level
                                capability = reasoning_profile(used_model)
                                if level in (None, 'default') and capability.get('verified') and 'off' in capability.get('levels', []):
                                    repair_level = 'off'
                                try:
                                    for repair_event in ui_event_stream('http://127.0.0.1:11434', [{'role':'user','content':correction_prompt}],
                                            used_model, repair_level, cancel, **stream_kwargs):
                                        repair_event = measure(repair_event, visible=False)
                                        if repair_event['type'] == 'delta': corrected += repair_event.get('text', '')
                                        elif repair_event['type'] in ('metrics', 'reasoning_activity', 'cancelled'):
                                            emit({**repair_event, 'purpose': 'syntax_repair'})
                                    checked = validate_generated_code(corrected, cancel=cancel, prompt=prompt)
                                    if not cancel.is_set() and accept_engineering_repair(answer, corrected, checked):
                                        answer, code_validation, code_repaired = corrected, checked, True
                                        emit({'type':'code_validation','report':checked,'repaired':True})
                                    else:
                                        emit({'type':'notice','message':'The correction did not pass all original language checks. The original draft and diagnostics are retained.'})
                                except Exception as exc:
                                    emit({'type':'notice','message':'Syntax correction could not finish; original draft retained: ' + str(exc)[:160]})
                            if (runtime.settings.get('repair_generated_html', True)
                                    and speed != 'boost' and not cancel.is_set()
                                    and should_repair_html(answer, code_validation)):
                                emit({'type': 'progress', 'stage': 'verification',
                                            'message': 'Checking and correcting generated HTML syntax'})
                                corrected = repair_generated_html(answer, used_model, code_validation['errors'])
                                if corrected:
                                    checked = validate_generated_code(corrected)
                                    # Never replace a visible reply with an equally broken rewrite.
                                    if not checked['errors'] and checked['blocks'] >= code_validation['blocks']:
                                        answer = corrected
                                        code_validation = checked
                                        code_repaired = True
                                        emit({'type': 'code_validation', 'report': checked,
                                                    'repaired': True})
                        if research_data:
                            emit({"type": "progress", "stage": "verification", "message": "Sources attached; unsupported claims still require validation"})
                    else:
                        project_path = Path(project["path"])
                        repo_request = requested_repository(prompt, session['messages'])
                        if repo_request or should_research(prompt, runtime.settings.get("research_mode", "auto")):
                            emit({"type": "progress", "stage": "research", "message": "Finding implementation references and local source matches"})
                            try:
                                research_data = (collect_repository_review(repo_request, cancel) if repo_request else
                                                 runtime.get_research(prompt, code=True, project=project_path))
                                emit({"type": "sources", "sources": research_data.get("results", []),
                                            "freshness": research_data.get("freshness", "unavailable"),
                                            "cached": research_data.get("cached", False),
                                            "providers": research_data.get("providers", [])})
                                runtime.store.add_event(sid, {"kind": "research", "title": "Implementation references",
                                                               "status": research_data.get("freshness", "unavailable"),
                                                               "detail": f"{len(research_data.get('results', []))} leads, not verified implementations"})
                            except (ValueError, OSError) as exc:
                                emit({"type": "notice", "message": "Research unavailable; proceeding with local project analysis: " + type(exc).__name__})
                        native_session = session.get("open_code_session")
                        # OpenCode variants are provider/model-specific, not an artificial prompt length knob.
                        # Ollama's native on/off thinking switch is not an
                        # OpenCode provider variant. Forward only explicit named
                        # variants; never send --variant on/off to the coding CLI.
                        variant = level if isinstance(level, str) and level in ("low", "medium", "high", "xhigh") else "default"
                        if variant != "default":
                            advertised = reasoning_profile(model)
                            if not advertised["verified"] or variant not in advertised["levels"]:
                                raise ValueError("This model did not advertise the selected reasoning level. Choose Default.")
                        turns = 1_000_000 if goal else passes if autocontinue else 1
                        task = prompt
                        if goal and resume_goal:
                            task = ('Resume the saved goal in this existing session. First inspect the current files, '
                                    'processes and tests; previous side effects may have completed before interruption. '
                                    'Do not blindly repeat commands.\nGOAL: ' + prompt)
                        previous_updates = runtime.take_updates(sid)
                        if previous_updates:
                            task += '\n\nPENDING INSTRUCTIONS FROM PREVIOUS RUN:\n' + '\n'.join(previous_updates)
                        # Declared independent tests are the completion review
                        # for goals that have them. Avoid another ungrounded model
                        # review delaying or overriding their observed results.
                        review_remaining = profile.review_passes if autocontinue and not (goal and goal.get('completion_contract', {}).get('tasks')) else 0
                        iteration = 0
                        previous_report = None
                        repeated_reports = 0
                        while iteration < turns:
                            if cancel.is_set():
                                break
                            emit({"type": "cycle", "index": iteration+1, "total": None if goal else turns,
                                  'goal': bool(goal)})
                            emit({"type": "progress", "stage": "implementation", "message":
                                        "Continuing existing coding session" if iteration else "Inspecting project and diagnosing task"})
                            cycle_answer = ""
                            failed = False
                            result = None
                            # Legacy test fixtures and older agents can still use
                            # the six-argument run signature; current agents support
                            # context sizing and opt-in Figma connectivity.
                            import inspect
                            agent_kwargs = {}
                            params = inspect.signature(agent.run).parameters
                            if "context_tokens" in params:
                                agent_kwargs["context_tokens"] = profile.context
                            if "figma_enabled" in params:
                                agent_kwargs["figma_enabled"] = runtime.settings.get("figma_enabled") is True
                            if "browser_enabled" in params:
                                agent_kwargs["browser_enabled"] = runtime.settings.get("browser_enabled") is True
                            if "web_enabled" in params:
                                agent_kwargs["web_enabled"] = runtime.settings.get("research_mode") != "off"
                            context = runtime.memory.context(session.get("project_id")) + "\n" + persona_context
                            user_task = task
                            if iteration == 0 and specialist_context:
                                user_task += "\n\nCURATED SPECIALIST PLAYBOOK (verify source and applicability):\n" + specialist_context
                            if iteration == 0 and lesson_context:
                                user_task += "\n\nUSER-REVIEWED PROJECT LESSONS (may be stale):\n" + lesson_context
                            if iteration == 0 and research_data:
                                user_task += "\n\nREFERENCE LEADS (untrusted; verify before using):\n" + research_context(research_data)[:5000]
                            updates = runtime.take_updates(sid)
                            if updates:
                                user_task += "\n\nNEW USER INSTRUCTIONS RECEIVED DURING THIS TASK (apply next; do not restart):\n" + "\n".join('- ' + x for x in updates)
                                emit({'type':'notice','message':f'Applying {len(updates)} new instruction(s) to this coding pass.'})
                            for item in agent.run(instruction_prompt(user_task, extra=iteration > 0,
                                                                     project=project_path, memory=context, effort=effort,
                                                                     personality=personality, language=language_setting, model=model),
                                                  project_path, model, native_session, variant, **agent_kwargs):
                                et = item.get("type")
                                if et == "delta":
                                    cycle_answer += item.get("text", "")
                                    emit(item)
                                elif et == "agent_session":
                                    native_session = item.get("id")
                                    with runtime.store.lock:
                                        session["open_code_session"] = native_session
                                        runtime.store.save()
                                elif et == "activity":
                                    activity = item["activity"]
                                    runtime.store.add_event(sid, activity)
                                    emit(item)
                                elif et == "agent_result":
                                    result = item
                                elif et == "cancelled":
                                    cancel.set()
                                    emit(item)
                                elif et == "agent_failure":
                                    answer = cycle_answer or item.get('text', '')
                                    raise RuntimeError(f"OpenCode returned exit code {item.get('code')}. See the activity log.")
                                else:
                                    emit(item)
                            if failed or cancel.is_set():
                                answer = cycle_answer or answer
                                status = "BLOCKED" if failed else "STOPPED"
                                status_origin = "application"
                                break
                            if result:
                                answer = result.get("text") or cycle_answer
                                status = result.get("status")
                                status_origin = result.get("status_origin") or ("model" if status is not None else "unreported")
                                native_session = result.get("native_session") or native_session
                                # Status intentionally follows real agent report, not speculative verification.
                            else:
                                status = "BLOCKED"
                                status_origin = "application"
                            if goal:
                                runtime.goals.checkpoint(goal, state='running', run_id=run['id'],
                                    native_session=native_session, report=answer)
                                repeated_reports = repeated_reports + 1 if answer == previous_report else 0
                                previous_report = answer
                                if status in ('CONTINUE', None) and repeated_reports >= 2:
                                    status, status_origin = 'BLOCKED', 'application'
                                    emit({'type': 'notice', 'message': 'Three unchanged reports; goal paused for inspection rather than repeating work.'})
                            # Local models sometimes omit a requested completion marker.
                            # Give one focused status/verification turn instead of silently
                            # pretending an unmarked answer is a finished coding task.
                            if status is None and (goal or autocontinue and iteration == 0) and iteration < turns-1:
                                status = "CONTINUE"
                                status_origin = "application-continuation"
                                task = ("Inspect the current implementation and the last run's actual results. "
                                        "Verify whether the user objective is complete. If not, make the next "
                                        "concrete change and test it. End with TAKUMI_STATUS: COMPLETE, CONTINUE, "
                                        "or BLOCKED. Do not invent results.")
                                emit({"type": "notice", "message": "Verifying progress before ending the coding session."})
                            if status == "COMPLETE" and review_remaining > 0 and iteration < turns-1:
                                review_remaining -= 1
                                task = review_prompt(answer)
                                status = "CONTINUE"
                                status_origin = "application-review"
                                emit({"type": "notice", "message": "Checking the proposed fix against regressions before finishing."})
                            # A follow-up arriving DURING a pass overrides apparent
                            # completion: apply it in the next pass of the same session.
                            additional = runtime.take_updates(sid)
                            if additional:
                                completion_verification = None
                                task = ("Apply these NEW USER INSTRUCTIONS in the same project and agent session. "
                                        "Preserve completed work and verify resulting changes:\n" +
                                        "\n".join('- ' + x for x in additional))
                                status = "CONTINUE"
                                status_origin = "user-update"
                                if iteration >= turns - 1 and turns < 24:
                                    turns = min(24, turns + 2)
                                emit({'type':'notice','message':'New instructions received. Continuing this task without resetting context.'})
                            if goal and status == 'COMPLETE':
                                completion_verification = verify_completion(runtime.harness, project['id'],
                                    goal.get('completion_contract', {'tasks': []}), cancel, goal['deadline'], emit)
                                goal['verification'] = completion_verification
                                runtime.goals.save(goal)
                                emit({'type': 'completion_verification', 'report': completion_verification})
                                checked = completion_verification['status']
                                emit({'type': 'notice', 'message': completion_verification.get('message', 'Completion check stopped.')})
                                if checked == 'passed':
                                    status_origin = 'declared-checks'
                                elif checked == 'failed':
                                    status, status_origin = 'CONTINUE', 'completion-check'
                                    task = ('The application ran the ORIGINAL declared completion checks and they failed. '
                                            'Inspect this observed output, revise the diagnosis and fix the implementation. '
                                            'Do not weaken tests or change their manifest.\n' + json.dumps(completion_verification)[-14000:])
                                elif checked in ('blocked', 'stopped'):
                                    status, status_origin = ('STOPPED' if checked == 'stopped' else 'BLOCKED'), 'completion-check'
                            if status != "CONTINUE":
                                break
                            if not native_session:
                                status = "BLOCKED"
                                status_origin = "application"
                                emit({"type": "notice", "message": "The agent requested continuation but returned no reusable session ID. Stopped to avoid starting a new, disconnected task."})
                                break
                            if iteration == turns-1:
                                emit({"type": "notice", "message": "Autonomous pass limit reached. Resume manually to continue."})
                            iteration += 1
                        if any(x['state'] == 'queued' for x in runtime.runs.inputs(sid)):
                            emit({'type':'notice','message':'A late instruction is saved in this conversation for the next task turn.'})
                        original = answer
                        if not original.strip():
                            original = "The agent stopped without a final report. Inspect the activity log and run results."
                        if (enabled_polish and speed != "boost" and not cancel.is_set()
                                and status != "BLOCKED" and not explicit_model_refusal(original)):
                            emit({"type": "polishing"})
                            answer, polished = polish_explanation(original, model)
                        else:
                            answer = original
                    if mode == 'agent' and runtime.settings.get('verify_generated_code', True):
                        code_validation = validate_generated_code(answer, cancel=cancel, prompt=prompt)
                        if code_validation['blocks']:
                            emit({'type': 'code_validation', 'report': code_validation})
                    if cancel.is_set():
                        status, status_origin = 'STOPPED', 'application'
                        emit({"type": "stopped"})
                    evidence_report = audit_answer(answer, Path(project['path']) if project else None, source_snapshot)
                    evidence_report['structure'] = structure_report(answer)
                    emit({'type': 'evidence', 'report': evidence_report})
                    # Preserve the complete original report; never present polishing as new test evidence.
                    refusal_origin = "model" if explicit_model_refusal(original) else None
                    correction = research_correction(original, research_data) if mode == "chat" else None
                    if metrics:
                        metrics['response_wall_ms'] = round((time.perf_counter() - execute_started) * 1000)
                    runtime.store.add_message(sid, "assistant", answer,
                                              run_id=run['id'], original=original, polished=polished, mode=mode, status=status,
                                              status_origin=status_origin, refusal_origin=refusal_origin,
                                              completion_verification=completion_verification,
                                              metrics=metrics, sources=(research_data or {}).get("results", []),
                                              research_freshness=(research_data or {}).get("freshness", "none"),
                                              research_note=correction,
                                              kage_member=kage_member, model_used=used_model if mode == "chat" else model,
                                              code_validation=code_validation, initial_code_validation=initial_code_validation,
                                              code_repaired=code_repaired, evidence=evidence_report)
                    emit({"type": "done", "text": answer, "original": original, "polished": polished,
                                "evidence": evidence_report,
                                "completion_verification": completion_verification,
                                "status": status, "status_origin": status_origin,
                                "refusal_origin": refusal_origin, "metrics": metrics,
                                "sources": (research_data or {}).get("results", []),
                                "research_freshness": (research_data or {}).get("freshness", "none"),
                                "research_note": correction,
                                "kage_member": kage_member, "model_used": used_model if mode == "chat" else model,
                                "code_validation": code_validation, "initial_code_validation": initial_code_validation, "code_repaired": code_repaired})
                except Exception as exc:
                    # Preserve visible text even on a provider/validation failure.
                    partial = answer or locals().get('cycle_answer', '')
                    message = str(exc)
                    stopped = cancel.is_set()
                    if goal:
                        runtime.goals.checkpoint(goal, state='budget_exhausted' if deadline_hit.is_set() else
                            'stopped' if stopped else 'failed', run_id=run['id'],
                            native_session=session.get('open_code_session'), report=partial)
                    runtime.store.add_message(sid, 'assistant', partial or
                        ('Stopped.' if stopped else 'The run could not complete: ' + message),
                        original=partial, run_id=run['id'], status='STOPPED' if stopped else 'BLOCKED',
                        status_origin='application', mode=mode, error=None if stopped else message)
                    runtime.runs.append(run['id'], {'type': 'done' if stopped else 'error',
                        'text': partial, 'message': message, 'source': 'application',
                        'run_state': 'stopped' if stopped else 'failed',
                        'status': 'STOPPED' if stopped else 'BLOCKED'},
                        state='stopped' if stopped else 'failed',
                        event_type='run.stopped' if stopped else 'run.failed')
                finally:
                    try:
                        finished = runtime.runs.get(run['id'])
                        if finished and finished['state'] in TERMINAL:
                            runtime.kage.notify_run(finished, project_id=session.get('project_id'), member=kage_member)
                    except (OSError, ValueError):
                        pass  # Notification storage must never strand the active-run lock.
                    if deadline_timer:
                        deadline_timer.cancel()
                    runtime.release(sid)

            threading.Thread(target=execute, daemon=True, name='takumi-run-' + run['id']).start()
            return self._stream_run(run['id']) if subscribe else runtime.runs.get(run['id'])
    def coordinate(job):
        existing = runtime.runs.find_request(job['payload']['request_id'], job['payload'])
        if existing:
            if existing['state'] in TERMINAL:
                runtime.kage.notify_run(existing, project_id=job['project_id'], member=job['member'])
            return {'run_id': existing['id'], 'state': existing['state'] if existing['state'] in TERMINAL else 'running'}
        if time.time() >= job['deadline']:
            return {'state': 'budget_exhausted'}
        with runtime.lock:
            # Re-read the queued job under the same lock used by the Stop route.
            with runtime.kage.lock:
                actual = next((j for j in runtime.kage.jobs if j['id'] == job['id']), None)
                if not actual or actual['state'] == 'stopped': return None
            if runtime.busy or runtime._kage_inference or runtime.gpu_scheduler.is_reserved(): return None
            try:
                started = Handler._run(None, job['payload'], subscribe=False)
                update = {'run_id': started['id'], 'state': started['state'] if started['state'] in TERMINAL else 'running'}
                with runtime.kage.lock:
                    actual.update(update)
                    runtime.kage._save()
                return update
            except (OSError, ValueError) as exc:
                return {'state': 'blocked', 'reason': str(exc)[:300]}
    runtime.kage.coordinator = coordinate
    return Handler


def run_server(port: int = 0, open_browser: bool = True, base: Path | None = None):
    directory = base or Path(os.environ.get('TAKUMI_DATA_DIR') or Path.home() / '.takumi')
    with data_directory_lease(directory):
        _serve(port, open_browser, directory)


def _serve(port: int, open_browser: bool, base: Path):
    runtime = Runtime(Storage(base))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_factory(runtime))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}/"
    print("\nTAKUMI  |  Local AI workspace")
    print("Local interface:", url)
    print("Ollama models:", ", ".join(installed_models()) or "not detected")
    print("OpenCode:", open_code_path() or "not detected (Chat mode still available)")
    print("Project and chat data:", runtime.store.base)
    print("Close this window to stop the workspace service.\n", flush=True)
    if open_browser:
        threading.Timer(0.55, lambda: webbrowser.open(url)).start()
    try:
        with keep_phone_service_awake():
            server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        for session in list(runtime.busy):
            runtime.stop(session)
        server.server_close()
        runtime.companion.stop()
        runtime.kage.stop()
        runtime.bots.close(stop_bots=False)


def main():
    p = argparse.ArgumentParser(description="Start Takumi, a source-only local AI workspace.")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--data-dir", type=Path)
    args = p.parse_args()
    run_server(args.port, not args.no_browser, args.data_dir)


if __name__ == "__main__":
    main()
