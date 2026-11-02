"""Launch explicit first-party interactive browser sign-ins on the Windows PC.

Takumi never reads, stores, forwards, or exposes credentials from these flows.
These commands are invoked only by a user clicking a local-only UI button.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .core import Agent, open_code_path


def initiate_sign_in(provider: str, *, model: str = "bonsai-2:27b") -> dict:
    if provider not in ("figma", "github"):
        raise ValueError("Unsupported account provider")
    if os.name != "nt":
        raise RuntimeError("Interactive account authorization requires the Takumi Windows desktop app")
    if provider == "github":
        binary = shutil.which("gh")
        if not binary:
            raise RuntimeError("GitHub CLI is not installed. Install it from its official source first")
        argv = [str(binary), "auth", "login", "--hostname", "github.com", "--web", "--git-protocol", "https"]
        env = os.environ.copy()
    else:
        binary = open_code_path()
        if not binary:
            raise RuntimeError("The coding engine is not installed. Install it separately first")
        agent = Agent()
        env = os.environ.copy()
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(agent._config(model, figma_enabled=True))
        argv = [str(binary), "mcp", "auth", "figma"]
    # The executable comes from PATH / the existing installed client. The only
    # variable argument is an allowlisted provider; no user-supplied shell code.
    # PowerShell displays the authentication flow and opens the provider's
    # official browser sign-in. Credentials never enter Takumi chat or logs.
    escaped = ["'" + arg.replace("'", "''") + "'" for arg in argv]
    command = "& " + " ".join(escaped)
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NoExit", "-Command", command],
                     creationflags=subprocess.CREATE_NEW_CONSOLE, env=env,
                     stdin=subprocess.DEVNULL)
    return {"provider": provider, "started": True,
            "message": "Finish the official sign-in in the new window."}
