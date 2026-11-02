"""Local profiles and optional, genuinely verified Discord OAuth identity.

Profiles are local display identities and never weaken workstation access
controls. Discord requires a developer-configured OAuth application; a 'login'
button without a verified authorization-code exchange is not authentication.
"""
from __future__ import annotations
import getpass
import json
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ProfileManager:
    def __init__(self, base: Path):
        self.path = Path(base) / "profile.json"
        self.lock = threading.RLock()
        self.value = {"kind": "guest", "label": "Guest workspace", "id": "guest"}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if self._valid(data):
                self.value = data
        except (OSError, ValueError):
            pass

    @staticmethod
    def _valid(data):
        return (isinstance(data, dict) and data.get("kind") in ("guest", "windows", "discord") and
                isinstance(data.get("label"), str) and 1 <= len(data["label"]) <= 100 and
                isinstance(data.get("id"), str) and 1 <= len(data["id"]) <= 100)

    def current(self):
        with self.lock:
            return dict(self.value)

    def select(self, kind: str, label: str = "", identity: str = ""):
        if kind == "guest":
            item = {"kind": "guest", "label": "Guest workspace", "id": "guest"}
        elif kind == "windows":
            user = getpass.getuser()
            item = {"kind": "windows", "label": (label or user)[:100], "id": "windows-local"}
        elif kind == "discord" and identity.isdigit() and len(identity) <= 30 and label.strip():
            item = {"kind": "discord", "label": label.strip()[:100], "id": identity}
        else:
            raise ValueError("Invalid profile selection. Discord must be verified through OAuth.")
        with self.lock:
            self.value = item
            self._save()
        return dict(item)

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.value), encoding="utf-8")
        os.replace(tmp, self.path)


class DiscordOAuth:
    def __init__(self, profiles: ProfileManager):
        self.profiles = profiles
        self.pending: dict[str, tuple[float, str]] = {}
        self.lock = threading.RLock()

    def available(self):
        return bool(os.getenv("TAKUMI_DISCORD_CLIENT_ID") and os.getenv("TAKUMI_DISCORD_CLIENT_SECRET"))

    def start(self, local_port: int) -> dict:
        if not self.available():
            return {"available": False, "message": "Discord sign-in requires a Discord application configured by the Takumi distributor.",
                    "setup_url": "https://discord.com/developers/applications",
                    "redirect_uri": f"http://127.0.0.1:{local_port}/auth/discord/callback"}
        if not 1 <= local_port <= 65535:
            raise ValueError("Invalid local callback port")
        state = secrets.token_urlsafe(24)
        redirect = f"http://127.0.0.1:{local_port}/auth/discord/callback"
        with self.lock:
            # Expire old states so a browser login cannot be replayed.
            self.pending = {k: v for k,v in self.pending.items() if v[0] > time.monotonic()}
            self.pending[state] = (time.monotonic() + 300, redirect)
        params = urlencode({"response_type": "code", "client_id": os.environ["TAKUMI_DISCORD_CLIENT_ID"],
                            "scope": "identify", "state": state, "redirect_uri": redirect,
                            "prompt": "consent"})
        return {"available": True, "url": "https://discord.com/oauth2/authorize?" + params,
                "redirect_uri": redirect}

    def finish(self, state: str, code: str, *, fetch=None) -> dict:
        with self.lock:
            item = self.pending.pop(state, None)
        if not item or item[0] <= time.monotonic() or not code or len(code) > 512:
            raise ValueError("Discord authorization expired or did not match this session")
        redirect = item[1]
        body = urlencode({"client_id": os.environ["TAKUMI_DISCORD_CLIENT_ID"],
                          "client_secret": os.environ["TAKUMI_DISCORD_CLIENT_SECRET"],
                          "grant_type": "authorization_code", "code": code,
                          "redirect_uri": redirect}).encode("utf-8")
        if fetch is None:
            def fetch(url, *, data=None, headers=None):
                req = Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
                with urlopen(req, timeout=9) as response:
                    raw = response.read(65_537)
                if len(raw) > 65_536:
                    raise ValueError("OAuth response too large")
                return json.loads(raw)
        token = fetch("https://discord.com/api/oauth2/token",data=body,
                      headers={"Content-Type":"application/x-www-form-urlencoded"})
        access = token.get("access_token", "") if isinstance(token, dict) else ""
        if not access or token.get("token_type", "").lower() != "bearer":
            raise ValueError("Discord did not return a valid OAuth access token")
        user = fetch("https://discord.com/api/users/@me",headers={"Authorization": "Bearer " + access})
        if not isinstance(user, dict) or not str(user.get("id", "")).isdigit():
            raise ValueError("Discord identity could not be verified")
        return self.profiles.select("discord",str(user.get("global_name") or user.get("username") or "Discord user"),str(user["id"]))
