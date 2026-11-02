"""Opt-in iPhone pairing for private Tailscale Serve access.

The HTTP service remains bound to loopback. Remote traffic must arrive through
Tailscale Serve's HTTPS reverse proxy and an authenticated tailnet, and must
present an additional paired-device token for every stateful API request.
Never use public Tailscale Funnel for workstation control.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import subprocess
import shutil
import ctypes
from contextlib import contextmanager
from pathlib import Path

_awake_active = False


@contextmanager
def keep_phone_service_awake():
    """Hold only automatic system sleep while private access is running."""
    global _awake_active
    if os.name == 'nt' and remote_mode_enabled():
        _awake_active = bool(ctypes.windll.kernel32.SetThreadExecutionState(0x80000001))
    try:
        yield
    finally:
        if _awake_active:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        _awake_active = False


class Pairing:
    def __init__(self, base: Path):
        self.path = base / "trusted-devices.json"
        self.lock = threading.RLock()
        self.devices: dict[str, dict] = {}
        self.code: str | None = None
        self.expires: int = 0
        self.failed_attempts: list[float] = []
        try:
            source = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(source, dict):
                self.devices = {key: value for key, value in source.items() if isinstance(value, dict)}
        except (OSError, ValueError):
            pass

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _save(self):
        p = self.path.with_suffix(".tmp")
        p.write_text(json.dumps(self.devices, indent=2), encoding="utf-8")
        p.replace(self.path)
        if os.name != "nt":
            self.path.chmod(0o600)

    def new_code(self) -> dict:
        """Only callable from a loopback browser, never a remote request."""
        with self.lock:
            # Eight digits; rate-limited with a five-minute lifetime and Tailscale ACL.
            self.code = f"{secrets.randbelow(100_000_000):08d}"
            self.expires = int(time.time()) + 300
            self.failed_attempts.clear()
            return {"code": self.code[:4] + " " + self.code[4:], "valid_seconds": 300}

    def pair(self, code: str, label: str = "iPhone") -> dict:
        with self.lock:
            now = time.time()
            self.failed_attempts = [x for x in self.failed_attempts if now-x < 900]
            if len(self.failed_attempts) >= 6:
                raise ValueError("Too many attempts. Generate a new code on the desktop.")
            canonical = "".join(ch for ch in str(code) if ch.isdigit())
            if not self.code or now > self.expires or not hmac.compare_digest(canonical, self.code):
                self.failed_attempts.append(now)
                raise ValueError("Invalid or expired pairing code.")
            self.code = None
            self.expires = 0
            if len(self.devices) >= 8:
                raise ValueError("Device limit reached; revoke an old device first.")
            token = secrets.token_urlsafe(36)
            key = self._digest(token)
            self.devices[key] = {"label": str(label)[:45], "paired": int(now),
                                 "last_used": int(now)}
            self._save()
            return {"token": token, "label": str(label)[:45]}

    def authorized(self, bearer: str) -> bool:
        if len(bearer) < 40 or len(bearer) > 200:
            return False
        digest = self._digest(bearer)
        with self.lock:
            for key, device in self.devices.items():
                if hmac.compare_digest(key, digest):
                    # Do not write to disk on every HTTP request.
                    device["last_used"] = int(time.time())
                    return True
        return False

    def list_devices(self) -> list[dict]:
        with self.lock:
            return [{"id": key[:16], **value} for key, value in self.devices.items()]

    def revoke(self, id_prefix: str) -> bool:
        if len(id_prefix) != 16:
            return False
        with self.lock:
            matches = [k for k in self.devices if k.startswith(id_prefix)]
            if len(matches) != 1:
                return False
            del self.devices[matches[0]]
            self._save()
            return True


def remote_mode_enabled() -> bool:
    return os.environ.get("TAKUMI_TAILSCALE_SERVE", "").strip() == "1"


def connection_status():
    """Read-only desktop setup diagnostics; never returns tailnet peer details."""
    result = {'installed': False, 'connected': False, 'serving': False,
              'remote_enabled': remote_mode_enabled(), 'url': None, 'keeping_pc_awake': _awake_active}
    executable = shutil.which('tailscale')
    if not executable:
        result['message'] = 'Install Tailscale on this PC and your iPhone, then sign in to the same account.'
        return result
    result['installed'] = True
    try:
        def read(*args):
            proc = subprocess.run([executable, *args], capture_output=True, text=True, timeout=5,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if proc.returncode:
                raise ValueError('Tailscale is not ready')
            return json.loads(proc.stdout)
        status = read('status', '--json')
        result['connected'] = status.get('BackendState') == 'Running'
        dns = status.get('Self', {}).get('DNSName', '').rstrip('.')
        serve = read('serve', 'status', '--json')
        if dns and dns.endswith('.ts.net'):
            web = serve.get('Web', {}).get(dns + ':443', {})
            proxy = web.get('Handlers', {}).get('/', {}).get('Proxy')
            result['serving'] = proxy in ('http://127.0.0.1:8765', 'http://localhost:8765')
            if result['serving']:
                result['url'] = 'https://' + dns
        result['message'] = ('Ready to pair your iPhone.' if result['serving'] and result['remote_enabled'] else
                             'Run private phone setup on this PC to finish the connection.')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        result['message'] = 'Open Tailscale on this PC and finish signing in, then check again.'
    return result
