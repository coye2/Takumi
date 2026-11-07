"""Bounded intent routing: one chat, optional action-taking engine.

Only clear imperative requests may start a coding agent automatically, and only
when the user already selected a specific registered project. Questions and
explanations remain in chat. This is not a content safety or topic filter.
"""
from __future__ import annotations
import re

_IMPERATIVE = re.compile(r"^\s*(?:(?:please|can you|i want you to|go ahead and)\s+)?"
    r"(add|edit|update|fix|implement|patch|build|test|debug|refactor|compile|run|inspect|trace|develop|modify|write|create|repair|port|integrate|review|continue|diagnose|verify|execute|deploy|publish|push|commit)\b", re.I)
_QUESTION = re.compile(r"^\s*(?:what|why|who|where|how|when|do|does|is|are|would|could|should|tell me|explain|come|perché|perche|cosa|che|quale|quali|quando|dove|chi|dimmi|spiegami)\b", re.I)
_ITALIAN_IMPERATIVE = re.compile(r"^\s*(?:(?:per\s+favore|puoi|potresti|vorrei\s+che\s+tu)\s+)?(?:correggi|correggere|implementa|implementare|crea|creare|compila|compilare|testa|testare|esegui|eseguire|analizza|analizzare|integra|integrare|continua|continuare|sviluppa|sviluppare|sistema|ripara|riparare|modifica|modificare|debugga|debuggare)\b", re.I)

# A phone request for local command execution must not fall into ordinary
# text-only Chat just because the owner says "diagnose" instead of "run".
# The selected registered project is still a hard prerequisite for Agent.
_HOST_EXECUTION = re.compile(
    r"^\s*(?:(?:please|can you|i want you to|go ahead and)\s+)?"
    r"(?:diagnose|confirm|run|execute|check|test|verify|inspect|deploy|publish|push|commit)\b"
    r"[\s\S]{0,2500}?\b(?:powershell|windows|on my pc|my computer|"
    r"gh auth|dotnet|whoami|get-location|git status|github release|"
    r"desktop backend|windows backend)\b", re.I)


def needs_host_execution(prompt: str) -> bool:
    return isinstance(prompt, str) and bool(_HOST_EXECUTION.search(prompt))


def choose_mode(requested: str, prompt: str, has_project: bool) -> str:
    if requested in ("chat", "agent"):
        return requested
    if requested != "auto":
        raise ValueError("Unknown mode")
    from .github_reader import requested_repository
    if requested_repository(prompt) and re.match(r'^\s*(?:please\s+)?(?:review|inspect|read|show|check)\b', prompt, re.I):
        return "chat"
    # Reviewing a pull request is reading, not editing the selected project.
    if re.match(r'^\s*(?:please\s+)?review\b', prompt, re.I) and re.search(r'\b(?:pull request|PR)\b', prompt, re.I):
        return "chat"
    if needs_host_execution(prompt):
        return "agent" if has_project else "chat"
    if not has_project or _QUESTION.search(prompt):
        return "chat"
    return "agent" if (_IMPERATIVE.search(prompt) or _ITALIAN_IMPERATIVE.search(prompt)) else "chat"


def project_preflight(requested: str, prompt: str, has_project: bool) -> dict:
    """Do not silently turn an unbound edit request into snippet-only chat.

    A path in a message is a hint, never authorization to register that folder.
    Explicit Chat remains Chat; questions and ordinary writing need no project.
    """
    if not isinstance(prompt, str) or len(prompt) > 32000:
        raise ValueError('Enter a request of at most 32,000 characters')
    mode = choose_mode(requested, prompt, has_project)
    wants_edit = bool(re.match(
        r'^\s*(?:(?:please|can you|i want you to|go ahead and)\s+)?'
        r'(?:add|edit|update|fix|implement|patch|build|refactor|modify|create|repair|port|integrate)\b', prompt, re.I)
        and re.search(r'\b(?:github|repo(?:sitory)?|website|code|files?|folder|directory|project|app|emulator)\b', prompt, re.I))
    return {'mode': mode, 'needs_project': not has_project and
            (requested == 'agent' or requested == 'auto' and (wants_edit or needs_host_execution(prompt)))}

