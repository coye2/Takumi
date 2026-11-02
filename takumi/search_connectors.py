"""Optional research providers that augment Takumi's existing no-key search.

Credentials never appear in model prompts, logs or public search caches. These
services receive only research.public_query(), not local files or transcripts.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote
from typing import Callable


def key_status(base: Path) -> dict:
    """Only boolean connection status is returned to the browser."""
    cfg = _read(base)
    return {"tavily": bool(cfg.get("tavily") or os.getenv("TAKUMI_TAVILY_API_KEY")),
            "google": bool(cfg.get("google") or os.getenv("TAKUMI_GEMINI_API_KEY")),
            "google_enabled": cfg.get("google_enabled") is True}


def _path(base: Path) -> Path:
    return Path(base) / "research-keys.json"


def _read(base: Path) -> dict:
    try:
        path = _path(base)
        if path.stat().st_size > 8192:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def set_key(base: Path, provider: str, value: str = "", *, google_enabled: bool | None = None) -> dict:
    if provider not in ("tavily", "google"):
        raise ValueError("Unsupported research provider")
    if value and (not 8 <= len(value) <= 300 or not re.fullmatch(r"[A-Za-z0-9_\-\.]+", value)):
        raise ValueError("Invalid API key format")
    cfg = _read(base)
    if value:
        cfg[provider] = value
    else:
        cfg.pop(provider, None)
    if google_enabled is not None:
        cfg["google_enabled"] = bool(google_enabled)
    path = _path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    # 0600 on POSIX. Windows users should keep Takumi's data directory private;
    # a Windows installer may additionally apply a per-user ACL.
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(cfg, stream)
    os.replace(tmp, path)
    return key_status(base)


def _post(url: str, payload: dict, headers: dict, timeout: float = 4.0) -> dict:
    req = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urlopen(req, timeout=timeout) as response:
        raw = response.read(450_001)
    if len(raw) > 450_000:
        raise ValueError("Research response exceeds the size limit")
    answer = json.loads(raw)
    if not isinstance(answer, dict):
        raise ValueError("Invalid research provider reply")
    return answer


def tavily(query: str, key: str, *, post: Callable = _post) -> list[dict]:
    """Tavily free-tier search (external API key, never sent to the model)."""
    data = post("https://api.tavily.com/search", {"query": query, "search_depth": "basic", "max_results": 6,
                                                  "include_answer": False, "include_raw_content": False},
                {"Content-Type": "application/json", "Authorization": "Bearer " + key}, timeout=4.5)
    found = []
    for item in data.get("results", [])[:6]:
        if not isinstance(item, dict):
            continue
        found.append({"title": str(item.get("title", ""))[:160], "url": str(item.get("url", ""))[:1200],
                      "snippet": str(item.get("content", ""))[:520], "source": "web", "provider": "tavily"})
    return found


def gemini_grounded_links(query: str, key: str, *, post: Callable = _post) -> list[dict]:
    """Optional, *explicitly enabled* Google-grounded search references.

    This sends a public research query to Google's hosted model, not the user's
    source files. Quotas and pricing vary; never enabled solely by adding a key.
    """
    data = post("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=" + quote(key),
                {"contents": [{"parts": [{"text": query}]}], "tools": [{"google_search": {}}],
                 "generationConfig": {"maxOutputTokens": 350}},
                {"Content-Type": "application/json"}, timeout=5)
    found = []
    for candidate in data.get("candidates", [])[:1]:
        md = candidate.get("groundingMetadata", {}) if isinstance(candidate, dict) else {}
        for entry in md.get("groundingChunks", [])[:8]:
            website = entry.get("web", {}) if isinstance(entry, dict) else {}
            if website.get("uri"):
                found.append({"title": str(website.get("title", "Reference"))[:160],
                              "url": str(website["uri"])[:1200], "snippet": "Google-grounded source lead",
                              "source": "web", "provider": "google-grounding"})
    return found


def configured_providers(base: Path) -> list[tuple[str, Callable, str]]:
    config = _read(base)
    providers = []
    tv = config.get("tavily") or os.getenv("TAKUMI_TAVILY_API_KEY", "")
    if tv:
        providers.append(("tavily", tavily, tv))
    gg = config.get("google") or os.getenv("TAKUMI_GEMINI_API_KEY", "")
    if gg and config.get("google_enabled") is True:
        providers.append(("google-grounding", gemini_grounded_links, gg))
    return providers
