"""Takumi console-emulation and decompilation expertise retrieval.

Versioned, original engineering playbooks are retrieved by task rather than
stuffing every console into the local model's limited context window. URLs are
reference leads, not freshly inspected source or permission to copy code.
No weights are modified, downloaded or represented as trained by this module.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

DATA = Path(__file__).resolve().parents[1] / "expertise"
VERSION = "2026-11-02.1"
_WORD = re.compile(r"[a-z0-9]+", re.I)
_EMULATION = re.compile(
    r"\b(emulat(?:or|ion|e|ing)|console|gpu|shader|graphics|render(?:ing|er)?|"
    r"reverse.engineer(?:ing)?|decomp(?:ile|ilation)?|disassembl(?:e|y)|"
    r"roms?|jit|dynarec|kernel|ipc|spu|rsp|rdp|vulkan|direct3d|"
    r"xenia|rpcs3|dolphin|pcsx2|ppsspp|nxi|pokopia|nintendo|playstation|"
    r"xbox|game ?boy|gamecube|wii|dreamcast|saturn|gba|mips|powerpc|"
    r"arm64|opcodes?|dma|fences?|framebuffers?|hadd2|depbar|pica200|gcn|rsx|"
    r"completion counter|serial number|row pitch|bit patterns?|binary layout|vertex fetch)\b", re.I)


@lru_cache(maxsize=1)
def catalogue() -> dict:
    """Load the shipped schema with bounded size and validate cross-references."""
    loaded = {}
    for name in ("platforms", "playbooks", "sources"):
        file = DATA / (name + ".json")
        if file.stat().st_size > 200_000:
            raise ValueError("Expertise catalog exceeds size bound")
        value = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(value, list) or len(value) > 100 or not all(isinstance(row, dict) for row in value):
            raise ValueError("Invalid expertise catalog")
        if len({row.get("id") for row in value}) != len(value):
            raise ValueError("Duplicate expertise identifiers")
        loaded[name] = value
    ids = {row["id"] for row in loaded["sources"]}
    for category in ("platforms", "playbooks"):
        for row in loaded[category]:
            if not set(row.get("source_ids", ())).issubset(ids):
                raise ValueError("Unknown expertise reference")
    return loaded


def _words(s: str) -> set[str]:
    return set(_WORD.findall(s.lower()))


def _matches(query: str, row: dict) -> float:
    q = " ".join(_WORD.findall(query.lower()))
    tokens = _words(q)
    aliases = row.get("aliases", ())
    score = 0.0
    for alias in aliases:
        phrase = " ".join(_WORD.findall(alias.lower()))
        if not phrase:
            continue
        # Only whole token sequences; 'nes' must not match 'windows'.
        if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", q):
            score += 9 if len(phrase) >= 5 else 7
        elif len(_words(phrase)) > 1 and len(_words(phrase) & tokens) >= 2:
            score += 1.5
    # General search text gives small nudges, but doesn't activate unrelated rows.
    return score + min(2.0, len(tokens & _words(row.get("title", ""))) * 0.25) if score else 0.0


def is_specialist_task(text: str) -> bool:
    return bool(_EMULATION.search(str(text)[:15000])) or any(
        _matches(str(text)[:500], row) >= 7 for row in catalogue()["platforms"] if row["id"] != "other"
    )


def retrieve(task: str, *, platforms: int = 2, playbooks: int = 3,
             max_chars: int = 6400) -> dict:
    """Deterministic retrieval; no network or inference needed.

    A 16 GB GPU benefits from a short, relevant context, not a 100K-token dump.
    """
    task = str(task)[:15000]
    pack = catalogue()
    if not is_specialist_task(task):
        return {"version": VERSION, "relevant": False, "platforms": [], "playbooks": [],
                "sources": [], "context": "", "truncated": False}
    ranking = lambda kind: sorted(((round(_matches(task, item), 2), item) for item in pack[kind]),
                                  key=lambda row: (-row[0], row[1]["id"]))
    ps = [item for score, item in ranking("platforms") if score > 0 and item["id"] != "other"]
    # Explicit platform names override incidental shared hardware terms (e.g.,
    # the SPU is not evidence that a PS3 task is about PS1). Keep multiple
    # systems when the user explicitly asks for a cross-platform comparison.
    named = [item for item in ps if _matches(task, {"aliases": item.get("console_aliases", [])}) >= 7]
    if named:
        specific = [item for item in named if item["id"] == "switch2"]
        if specific and not re.search(r"\b(compare|versus|vs|against|both)\b", task, re.I):
            named = [item for item in named if item["id"] != "switch1"]
        ps = named
    ps = ps[:max(1, min(3, platforms))]
    if not ps:
        ps = [next(x for x in pack["platforms"] if x["id"] == "other")]
    books = [item for score, item in ranking("playbooks") if score > 0][:max(1, min(4, playbooks))]
    if not books:
        books = [next(x for x in pack["playbooks"] if x["id"] == "harness")]
    refs = dict((x["id"], x) for x in pack["sources"])
    chosen = []
    seen = set()
    for row in [*ps, *books]:
        for ident in row["source_ids"]:
            if ident not in seen:
                chosen.append(refs[ident]);seen.add(ident)
    lines = [
        "TAKUMI ENGINEERING SPECIALIZATION — version " + VERSION,
        "This is a curated engineering reference, NOT additional training or a verified upstream implementation.",
        "Use these methods for the requested investigation, not as a checklist to print in the reply. Explain only what helps answer the user's question.",
        "Required workflow: inspect source branch and existing changes → reproduce → measure and isolate → "
        "consult exact primary-source implementations → implement a narrow fix → run real tests → inspect regression paths.",
        "Do not assume an emulator is correct because it compiles, submits frames, or exits with code 0.",
    ]
    for row in ps:
        lines.extend(["PLATFORM: " + row["title"], "Architecture: " + row["architecture"],
                      "Evidence to collect: " + row["diagnostic_probes"]])
    for row in books:
        lines.extend(["ENGINEERING METHOD: " + row["title"], row["method"],
                      "Validation: " + row["verification"]])
    lines.append("Reference leads only; inspect current code, commit, LICENSE and per-file notices before reuse:")
    for row in chosen[:7]:
        lines.append("- " + row["title"] + ": " + row["url"] + " — " + row["focus"] +
                     " [" + row["license_note"] + "]")
    lines.append("Keep proprietary games/firmware/keys local; never ship them or fabricate observations. "
                 "Respect upstream license and AI contribution policies.")
    result = "\n".join(lines)
    max_chars = max(800, min(12000, int(max_chars)))
    clipped = len(result) > max_chars
    if clipped:
        # Don't cut mid-source URL; a bounded shorter reference list is preferable.
        head = lines[:-1]
        while len("\n".join(head + [lines[-1]])) > max_chars and len(head) > 6:
            head.pop(-1)
        result = "\n".join(head + [lines[-1]])[:max_chars]
    return {"version": VERSION, "relevant": True,
            "platforms": [{"id": x["id"], "title": x["title"]} for x in ps],
            "playbooks": [{"id": x["id"], "title": x["title"]} for x in books],
            "sources": chosen[:7], "context": result, "truncated": clipped}


class ReviewedLessons:
    """Optional, user-reviewed local lessons; never self-label them verified facts.

    This is a learning-memory overlay. Fine-tuning model weights is an independent,
    hardware/data/licensing-intensive job, not something Takumi claims to do.
    """
    def __init__(self, base: Path):
        self.path = Path(base) / "expertise-lessons.json"
        self.lock = threading.RLock()

    def list(self, project_id: str | None = None) -> list[dict]:
        with self.lock:
            try:
                if self.path.stat().st_size > 250000:
                    return []
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    return []
                result = [r for r in data if isinstance(r, dict) and isinstance(r.get("text"), str)]
            except (OSError, ValueError):
                result = []
        return [r for r in result[:120] if project_id is None or r.get("project_id") in (None, project_id)]

    def add(self, text: str, evidence: str, project_id: str | None = None, *, reviewed: bool = False) -> dict:
        if reviewed is not True:
            raise ValueError("A human must approve a lesson before Takumi remembers it")
        text, evidence = str(text).strip(), str(evidence).strip()
        if not (12 <= len(text) <= 900 and 12 <= len(evidence) <= 1200):
            raise ValueError("Lesson and evidence must be concise and nonempty")
        if project_id is not None and (not isinstance(project_id, str) or len(project_id) > 100):
            raise ValueError("Invalid project scope")
        with self.lock:
            rows = self.list()
            if len(rows) >= 120:
                raise ValueError("Lesson storage full; remove an older lesson")
            digest = hashlib.sha256((text + evidence + str(time.time_ns())).encode()).hexdigest()[:20]
            item = dict(id=digest, text=text, evidence=evidence, project_id=project_id,
                        provenance="user-reviewed; not independently verified", created=int(time.time()))
            rows.append(item)
            self._save(rows)
            return item

    def remove(self, lesson_id: str) -> bool:
        with self.lock:
            rows = self.list()
            remain = [x for x in rows if x.get("id") != lesson_id]
            if len(remain) == len(rows):
                return False
            self._save(remain)
            return True

    def context(self, task: str, project_id: str | None, max_chars: int = 1600) -> str:
        q = _words(task)
        candidates = []
        for row in self.list(project_id):
            # Reuse only when the current task overlaps specific lesson terms.
            words = _words(row["text"])
            shared = q & words
            if len(shared) >= 2:
                candidates.append((len(shared), row))
        candidates.sort(key=lambda x: (-x[0], x[1]["id"]))
        if not candidates:
            return ""
        lines = ["User-reviewed local engineering lessons (may be stale; verify in this checkout):"]
        for _, row in candidates[:4]:
            lines.append("- " + row["text"] + " | Claimed evidence: " + row["evidence"])
        return "\n".join(lines)[:max_chars]

    def _save(self, rows: list[dict]):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
        if os.name != "nt":
            self.path.chmod(0o600)


def summary() -> dict:
    data = catalogue()
    return {"version": VERSION, "platform_count": len(data["platforms"]),
            "playbook_count": len(data["playbooks"]), "reference_count": len(data["sources"]),
            "platforms": [{"id": p["id"], "title": p["title"]} for p in data["platforms"]],
            "playbooks": [{"id": p["id"], "title": p["title"]} for p in data["playbooks"]],
            "note": "Curated local knowledge, not model-weight training. Sources must be verified when used."}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Inspect Takumi emulator/decomp expertise retrieval")
    parser.add_argument("query", nargs="*", help="Search phrase")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    print(json.dumps(summary() if args.summary else retrieve(" ".join(args.query)), ensure_ascii=False, indent=2))
