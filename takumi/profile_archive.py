"""Portable Windows profile export/import for chat history and local settings.

This intentionally omits API keys, Discord tokens, device pairings, private
source files, execution state, and absolute paths when exported for sharing.
"""
from __future__ import annotations
import json
import secrets
import time
from pathlib import Path

EXPORTABLE_SETTINGS = {"theme", "speed", "effort", "personality", "research_mode", "show_progress", "show_citations", "send_on_enter", "model_failover"}


def export_profile(runtime) -> dict:
    """User-initiated, desktop-only export; never includes arbitrary filesystem content."""
    with runtime.store.lock:
        sessions = []
        for row in runtime.store.data["sessions"][:200]:
            messages = [{"role": str(m.get("role", "")), "content": str(m.get("content", ""))[:90000]}
                        for m in row.get("messages", [])[:100]
                        if m.get("role") in ("user", "assistant")]
            sessions.append({"title": str(row.get("title", "Chat"))[:120], "messages": messages})
    return {"format": "takumi-profile", "version": 1, "exported": int(time.time()),
            "display_profile": runtime.profiles.current(), "settings": {k: v for k, v in runtime.settings.items() if k in EXPORTABLE_SETTINGS},
            "sessions": sessions, "memory": [{"text": str(note.get("text", ""))[:900]}
                                            for note in runtime.memory.list()[:100]]}


def validate_import(payload: dict) -> dict:
    if not isinstance(payload, dict) or payload.get("format") != "takumi-profile" or payload.get("version") != 1:
        raise ValueError("Choose a Takumi profile export (version 1)")
    if not isinstance(payload.get("sessions"), list) or len(payload["sessions"]) > 200:
        raise ValueError("Too many conversations in profile export")
    if not isinstance(payload.get("settings", {}), dict) or not isinstance(payload.get("memory", []), list):
        raise ValueError("Invalid profile metadata")
    if len(payload.get("memory", [])) > 100:
        raise ValueError("Too many notes in profile export")
    sessions=[]
    for row in payload["sessions"]:
        if not isinstance(row, dict) or not isinstance(row.get("messages"), list) or len(row["messages"]) > 100:
            raise ValueError("Invalid exported conversation")
        messages=[]
        for m in row["messages"]:
            if not isinstance(m, dict) or m.get("role") not in ("assistant", "user") or not isinstance(m.get("content"), str) or len(m["content"]) > 90000:
                raise ValueError("Invalid exported message")
            messages.append({"role": m["role"], "content": m["content"]})
        sessions.append({"title": str(row.get("title", "Imported conversation"))[:120], "messages": messages})
    settings={k:v for k,v in payload.get("settings", {}).items() if k in EXPORTABLE_SETTINGS}
    notes=[]
    for row in payload.get("memory", []):
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            raise ValueError("Invalid exported note")
        if 1 <= len(row["text"].strip()) <= 900:
            notes.append(row["text"].strip())
    return {"sessions": sessions, "settings": settings, "memory": notes}


def import_profile(runtime, payload: dict) -> dict:
    """Append imported conversations without erasing current local history.

    Save a byte-for-byte workspace backup before importing. Existing project
    paths are not imported because they may refer to another person's PC.
    """
    data = validate_import(payload)
    # Validate settings *before* making persistent changes.
    runtime.update_settings(data["settings"])
    with runtime.store.lock:
        backup = runtime.store.base / ("workspace-before-import-" + str(int(time.time())) + ".json")
        if runtime.store.path.exists():
            backup.write_bytes(runtime.store.path.read_bytes())
        for row in data["sessions"]:
            runtime.store.data["sessions"].insert(0, {"id": secrets.token_hex(16), "title": row["title"],
                "project_id": None, "messages": row["messages"], "events": [], "open_code_session": None,
                "updated": int(time.time())})
        runtime.store.save()
    imported_notes = 0
    for note in data["memory"]:
        try:
            runtime.memory.add(note)
            imported_notes += 1
        except ValueError:
            pass
    runtime.profiles.select("windows")
    return {"conversations": len(data["sessions"]), "notes": imported_notes,
            "settings": len(data["settings"]), "profile": runtime.profiles.current()}
