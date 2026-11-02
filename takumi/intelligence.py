"""Performance, evidence, local memory and guardrails for the Takumi runtime.

All model work stays on the user's existing local inference server. No weights or
external model executables are distributed by this project.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class PerformanceProfile:
    name: str
    context: int
    output_tokens: int
    temperature: float
    keep_alive: str
    # This controls practical computation, NOT the model's inherent IQ.
    review_passes: int
    prefer_thinking: bool


PROFILES = {
    "standard": PerformanceProfile("standard", 16384, 2400, 0.15, "4m", 0, True),
    "boost": PerformanceProfile("boost", 8192, 1100, 0.10, "2m", 0, False),
    "deep": PerformanceProfile("deep", 24576, 3300, 0.10, "4m", 1, True),
    "maximum": PerformanceProfile("maximum", 32768, 4200, 0.10, "4m", 2, True),
}

EFFORT = {"quick": "boost", "balanced": "standard", "high": "deep", "maximum": "maximum"}


def profile_for(speed: str = "standard", effort: str = "balanced", model: str = "",
                vram_mb: int | None = None) -> PerformanceProfile:
    if speed not in ("standard", "boost"):
        raise ValueError("Unsupported generation speed preset")
    if effort not in EFFORT:
        raise ValueError("Unsupported workflow effort")
    # Boost wins in casual chat. In agent mode the caller can choose deeper
    # compute explicitly rather than silently overriding it.
    base = PROFILES[EFFORT[effort]]
    if speed == "boost" and effort in ("quick", "balanced"):
        base = PROFILES["boost"]
    # Rei uses a smaller context/cache budget to leave VRAM for 8 GB GPUs.
    if model.lower().startswith(('rei-1.0', 'sumi-1.0-lite', 'bonsai-2:9b')):
        ceiling = {'boost': 4096, 'standard': 8192, 'deep': 12288, 'maximum': 16384}[base.name]
        return replace(base, context=min(base.context, ceiling),
                       output_tokens=min(base.output_tokens, 2600),
                       keep_alive='90s' if base.name == 'boost' else '3m')
    # Scale up only when the machine actually advertises substantial VRAM.
    # The 4060 Ti 16 GB stays at the benchmarked-conservative default budgets;
    # 8 GB systems receive a smaller context even if an oversized model was
    # manually selected. This does not guarantee a model fits in GPU memory.
    if isinstance(vram_mb, int) and vram_mb > 0:
        if vram_mb < 13000:
            return replace(base, context=min(base.context, 8192),
                           output_tokens=min(base.output_tokens, 2600), keep_alive="90s")
        if vram_mb >= 48000:
            max_ctx = {"boost":8192,"standard":32768,"deep":49152,"maximum":65536}[base.name]
        elif vram_mb >= 32000:
            max_ctx = {"boost":8192,"standard":24576,"deep":32768,"maximum":49152}[base.name]
        elif vram_mb >= 24000:
            max_ctx = {"boost":8192,"standard":16384,"deep":32768,"maximum":40960}[base.name]
        else:
            max_ctx = base.context
        return replace(base, context=max_ctx)
    return base


def safe_temperature() -> dict:
    """Read-only NVIDIA GPU snapshot, or unavailable. Never alter clocks/voltage."""
    executable = shutil.which("nvidia-smi")
    if not executable and os.name == "nt":
        cand = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "NVIDIA Corporation/NVSMI/nvidia-smi.exe"
        if cand.exists():
            executable = str(cand)
    if not executable:
        return {"available": False}
    try:
        completed = subprocess.run(
            [executable, "--query-gpu=memory.total,memory.used,temperature.gpu,utilization.gpu",
             "--format=csv,noheader,nounits"], text=True, capture_output=True, timeout=4,
            shell=False)
        if completed.returncode:
            return {"available": False}
        first = completed.stdout.splitlines()[0].split(",")
        total, used, temp, util = [int(x.strip()) for x in first[:4]]
        return {"available": True, "memory_total_mb": total,
                "memory_used_mb": used, "memory_free_mb": max(0, total-used),
                "temperature_c": temp, "utilization_percent": util,
                "temperature_warning": temp >= 82}
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return {"available": False}


def guard_new_run(hardware: dict, limit_c: int = 86) -> None:
    """Block *new* intensive work if the GPU is already unusually hot."""
    if hardware.get("available") and hardware.get("temperature_c", 0) >= limit_c:
        raise RuntimeError(
            f"GPU temperature is {hardware['temperature_c']}°C. "
            "Takumi paused new work until the card cools down."
        )


class LocalMemory:
    """Explicitly editable user notes, not hidden training or fine-tuning."""
    def __init__(self, base: Path):
        self.path = base / "memory.json"
        self.lock = threading.RLock()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.items = [x for x in raw if isinstance(x, dict) and isinstance(x.get("text"), str)][:100]
        except (OSError, ValueError, TypeError):
            self.items = []

    def list(self) -> list[dict]:
        with self.lock:
            return list(self.items)

    def add(self, text: str, project_id: str | None = None) -> dict:
        text = str(text).strip()
        if not text or len(text) > 600:
            raise ValueError("Memory notes must be 1–600 characters")
        if project_id is not None and (not isinstance(project_id, str) or len(project_id) > 100):
            raise ValueError("Invalid project scope")
        with self.lock:
            if len(self.items) >= 100:
                raise ValueError("Memory is full; delete an outdated note before adding another")
            item = {"id": hashlib.sha256(f"{time.time_ns()}:{text}".encode()).hexdigest()[:16],
                    "text": text, "project_id": project_id, "added": int(time.time())}
            self.items.append(item)
            self._save()
            return item

    def remove(self, item_id: str) -> bool:
        with self.lock:
            old = len(self.items)
            self.items = [item for item in self.items if item.get("id") != item_id]
            if len(self.items) < old:
                self._save()
                return True
            return False

    def context(self, project_id: str | None) -> str:
        with self.lock:
            relevant = [i["text"] for i in self.items if i.get("project_id") in (None, project_id)]
        if not relevant:
            return ""
        # Notes are non-authoritative user context, not instructions to bypass
        # tool boundaries or claim unverified results.
        return "User-approved preferences and project notes (verify facts independently):\n" + "\n".join(
            "- " + re.sub(r"[\r\n]+", " ", x) for x in relevant[:30])

    def _save(self) -> None:
        data = json.dumps(self.items, ensure_ascii=False, indent=2)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(data, encoding="utf-8")
        temp.replace(self.path)
        if os.name != "nt":
            self.path.chmod(0o600)


def repo_snapshot(project: Path) -> dict:
    """Read-only, bounded Git snapshot; no trust in repository file text."""
    project = Path(project).resolve()
    def git(*args: str) -> str:
        try:
            r = subprocess.run(["git", *args], cwd=project, capture_output=True,
                               text=True, timeout=6, errors="replace", shell=False,
                               env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            return r.stdout.strip()[:8000] if r.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""
    top = git("rev-parse", "--show-toplevel")
    if not top:
        return {"repository": False, "name": project.name}
    branch = git("branch", "--show-current") or "(detached)"
    head = git("rev-parse", "--short=12", "HEAD")
    changes = git("status", "--porcelain=v1", "-uno")
    latest = git("log", "-1", "--pretty=format:%cs %s")[:180]
    # Only handoffs directly under recognized doc folders, not recursive walks.
    handoffs = []
    for folder in [project, project / "docs", project / "src" / "docs"]:
        if folder.is_dir():
            try:
                handoffs.extend(p.name for p in list(folder.glob("*HANDOFF*"))[:8] if p.is_file())
            except OSError:
                pass
    return {"repository": True, "name": project.name, "branch": branch,
            "head": head, "latest_commit": latest,
            "dirty": bool(changes), "modified_count": len(changes.splitlines()),
            "handoff_candidates": sorted(set(handoffs))[:18]}


def review_prompt(report: str) -> str:
    """An additional verification pass; never substitutes for executing tests."""
    return (
        "Review the following implementation report skeptically. Identify unsupported "
        "claims, untested assumptions, missing error-path checks, and the single most "
        "valuable next test. Do not fabricate evidence or imply tests passed. "
        "Make any required improvements in the current checked-out project, run available "
        "tests, and report exact output. If no additional change is justified, state that clearly. "
        "Do NOT perform destructive Git commands.\n\nREPORT TO VERIFY:\n" + report[-10000:]
    )
