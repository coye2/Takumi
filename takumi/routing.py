"""Bounded intent routing: one chat, optional action-taking engine.

Only clear imperative requests may start a coding agent automatically, and only
when the user already selected a specific registered project. Questions and
explanations remain in chat. This is not a content safety or topic filter.
"""
from __future__ import annotations
import re

_IMPERATIVE = re.compile(r"^\s*(?:(?:please|can you|i want you to|go ahead and)\s+)?"
    r"(fix|implement|patch|build|test|debug|refactor|compile|run|inspect|trace|develop|modify|write|create|repair|port|integrate|review|continue)\b", re.I)
_QUESTION = re.compile(r"^\s*(?:what|why|who|where|how|when|do|does|is|are|would|could|should|tell me|explain|come|perché|perche|cosa|che|quale|quali|quando|dove|chi|dimmi|spiegami)\b", re.I)
_ITALIAN_IMPERATIVE = re.compile(r"^\s*(?:(?:per\s+favore|puoi|potresti|vorrei\s+che\s+tu)\s+)?(?:correggi|correggere|implementa|implementare|crea|creare|compila|compilare|testa|testare|esegui|eseguire|analizza|analizzare|integra|integrare|continua|continuare|sviluppa|sviluppare|sistema|ripara|riparare|modifica|modificare|debugga|debuggare)\b", re.I)


def choose_mode(requested: str, prompt: str, has_project: bool) -> str:
    if requested in ("chat", "agent"):
        return requested
    if requested != "auto":
        raise ValueError("Unknown mode")
    from .github_reader import requested_repository
    if requested_repository(prompt) and re.match(r'^\s*(?:please\s+)?(?:review|inspect|read|show|check)\b', prompt, re.I):
        return "chat"
    if not has_project or _QUESTION.search(prompt):
        return "chat"
    return "agent" if (_IMPERATIVE.search(prompt) or _ITALIAN_IMPERATIVE.search(prompt)) else "chat"
