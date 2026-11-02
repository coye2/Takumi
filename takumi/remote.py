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
from pathlib import Path


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
