"""Declarative Takumi integrations. Existing external CLIs are never redistributed.

Potentially publishing actions require an explicit, expiring, one-shot approval.
Neither models nor agent prompts may execute prepared deployments automatically.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time


CATALOG = [
    {"id": "browser", "name": "Browser", "kind": "local MCP connector", "description": "Let coding tasks navigate, click, inspect pages and capture screenshots in a separate browser.", "capabilities": ["navigate", "interact", "screenshot"]},
    {"id": "research", "name": "Web Research", "kind": "built-in", "description": "Look up current technical references and collect cited sources.", "capabilities": ["search", "references"]},
    {"id": "github", "name": "GitHub", "kind": "local connector", "description": "Inspect repositories, find source code, review pull requests and push approved changes.", "capabilities": ["code search", "repository", "push"]},
    {"id": "figma", "name": "Figma", "kind": "MCP connector", "description": "Bring design context into coding sessions and edit Figma through an authorized MCP connection.", "capabilities": ["read design", "write design"]},
    {"id": "cloudflare", "name": "Cloudflare Pages", "kind": "CLI connector", "description": "Deploy prebuilt web projects through your separately installed Wrangler CLI.", "capabilities": ["deploy", "status"]},
    {"id": "harness", "name": "Test Harness", "kind": "built-in", "description": "Run and monitor project-defined build, test and emulator tasks.", "capabilities": ["build", "test", "launch", "logs"]},
    {"id": "discord", "name": "Discord Bot", "kind": "built-in", "description": "Supervise an existing locally hosted bot while the PC is running.", "capabilities": ["start", "stop", "logs"]},
]


def _run(argv: list[str], cwd: Path | None = None, timeout: int = 12) -> dict:
    try:
        cp = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=False,
                            encoding="utf-8", errors="replace")
        return {"ok": cp.returncode == 0, "exit_code": cp.returncode,
                "output": (cp.stdout or cp.stderr)[-12000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)[:220], "output": ""}


def status(settings: dict | None = None) -> list[dict]:
    settings = settings or {}
    out = []
    for item in CATALOG:
        entry = dict(item)
        exe = {"github": "gh", "cloudflare": "wrangler"}.get(item["id"])
        if exe:
            entry["installed"] = bool(shutil.which(exe))
            entry["connected"] = False
            if entry["installed"]:
                cmd = [exe, "auth", "status"] if exe == "gh" else [exe, "whoami"]
                entry["connected"] = _run(cmd, timeout=6)["ok"]
        elif item["id"] == "browser":
            from .browser_tools import command
            entry['installed'] = bool(command())
            entry['connected'] = False
            entry['enabled'] = settings.get('browser_enabled') is True
            entry['note'] = 'Enabled for new coding runs.' if entry['enabled'] else 'Enable after installing browser tools.'
        elif item["id"] == "figma":
            from .core import open_code_path
            entry["installed"] = bool(open_code_path())
            entry["connected"] = False
            if entry["installed"] and settings.get("figma_enabled") is True:
                from .companion import connections_status
                entry["connected"] = connections_status().get("figma", {}).get("connected") is True
            entry["note"] = "Authorize with Figma and verify connection before editing designs."
        elif item['id'] == 'discord':
            entry.update(installed=True, connected=False, status='Local supervisor available',
                         note='A local bot process does not establish a verified Discord account or channel connection.')
        else:
            entry["installed"] = True
            entry["connected"] = True
        out.append(entry)
    return out


def in_project(project: Path, relative: str, *, must_be_dir: bool = False) -> Path:
    """Stay within a registered project; reject symlink escape and absolute paths."""
    if not isinstance(relative, str) or not relative or len(relative) > 500:
        raise ValueError("Choose a project-relative path")
    if Path(relative).is_absolute() or re.match(r"^[a-zA-Z]:", relative):
        raise ValueError("Absolute paths are not permitted")
    root = project.resolve(strict=True)
    candidate = (root / relative).resolve(strict=True)
    if not candidate.is_relative_to(root):
        raise ValueError("Path escapes the selected project")
    if must_be_dir and not candidate.is_dir():
        raise ValueError("Choose an existing build output directory")
    return candidate


class Approvals:
    """Memory-only, one-shot approvals. No persistent deployment secrets."""
    def __init__(self):
        self.lock = threading.RLock()
        self.pending: dict[str, dict] = {}

    def prepare(self, provider: str, action: str, project: Path, arguments: dict) -> dict:
        project = project.resolve(strict=True)
        if not project.is_dir():
            raise ValueError("Registered project folder is unavailable")
        if provider == "cloudflare" and action == "pages_deploy":
            exe = shutil.which("wrangler")
            if not exe:
                raise ValueError("Install Wrangler separately and sign in before deploying")
            name = str(arguments.get("name", "")).strip()
            branch = str(arguments.get("branch", "preview")).strip()
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,56}[a-z0-9]", name):
                raise ValueError("Choose a valid Cloudflare Pages project name")
            if not re.fullmatch(r"[A-Za-z0-9][\w./-]{0,100}", branch) or ".." in branch:
                raise ValueError("Invalid deployment branch")
            folder = in_project(project, str(arguments.get("directory", "dist")), must_be_dir=True)
            argv = [exe, "pages", "deploy", str(folder), "--project-name", name, "--branch", branch]
            title = f"Deploy {folder.name} to Cloudflare Pages / {name} ({branch})"
        elif provider == "github" and action == "push":
            exe = shutil.which("git")
            if not exe or not (project / ".git").exists():
                raise ValueError("Open a checked-out Git repository before pushing")
            branch = _run([exe, "branch", "--show-current"], cwd=project, timeout=8)
            name = branch.get("output", "").strip()
            if not branch["ok"] or not name or name in ("main", "master"):
                raise ValueError("Push a named feature branch, not detached HEAD or a protected default branch")
            argv = [exe, "push", "-u", "origin", name]
            title = f"Push feature branch {name} to origin"
        else:
            raise ValueError("Unsupported publishing action")
        ticket = secrets.token_urlsafe(26)
        record = {"provider": provider, "action": action, "project": str(project),
                  "argv": argv, "title": title, "expires": time.monotonic()+180}
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if v["expires"] > time.monotonic()}
            self.pending[ticket] = record
        return {"approval_id": ticket, "title": title,
                "command_preview": " ".join(repr(a) if " " in a else a for a in argv),
                "expires_seconds": 180, "requires_confirmation": True}

    def execute(self, ticket: str, *, local_only: bool) -> dict:
        if not local_only:
            raise ValueError("Publishing and pushing require confirmation on the Windows PC")
        with self.lock:
            record = self.pending.pop(ticket, None)
        if not record or time.monotonic() >= record["expires"]:
            raise ValueError("Approval expired or was already used")
        result = _run(record["argv"], cwd=Path(record["project"]), timeout=240)
        return {"title": record["title"], **result}
