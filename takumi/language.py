"""Small, offline language-preference layer.

Language selection is a *hint* to the local model, not translation of code or
claims of language competence. Do not send requests to an external translator.
"""
from __future__ import annotations

import re

_LANGUAGES = {'auto', 'en', 'it'}
_ITALIAN_HINTS = frozenset({
    'ciao','grazie','perché','perche','vorrei','voglio','potresti','puoi',
    'scrivi','mostrami','spiega','spiegami','come','quali','quale','questo',
    'questa','questi','quello','quella','anche','invece','funziona','funzionare',
    'migliorare','migliora','codice','applicazione','programma','errore',
    'velocità','velocita','schermo','risposta','rispondi','lingua',
    'italiano','italiana','file','non','sono','abbiamo','devo','devi','siamo',
    'vorrei','dovrebbe','oppure','senza','quando','dove','grazie',
})
_ENGLISH_HINTS = frozenset({'the','and','please','how','what','why','can','could','would',
                             'should','with','from','fix','make','code','works','this',
                             'that','where','when','want','build','test','error','does',
                             'have','need','about','thanks','hello'})


def resolve_language(message: str, preference: str = 'auto') -> str:
    if preference not in _LANGUAGES:
        raise ValueError('Unsupported language preference')
    if preference != 'auto':
        return preference
    text = re.sub(r'```[\s\S]*?```', ' ', str(message or ''))[-5000:]
    text = re.sub(r'`[^`]+`', ' ', text).lower()
    if re.search(r'\b(?:rispondi|scrivi|parla)\s+in\s+italiano\b', text):
        return 'it'
    if re.search(r'\b(?:reply|write|speak)\s+in\s+english\b', text):
        return 'en'
    words = re.findall(r"[a-zàèéìòù]+", text)
    italian = sum(word in _ITALIAN_HINTS for word in words)
    english = sum(word in _ENGLISH_HINTS for word in words)
    if italian >= 2 and italian >= english + 1:
        return 'it'
    if len(words) <= 3 and any(w in ('ciao','grazie','perché','perche') for w in words):
        return 'it'
    return 'en'


def model_instruction(language: str) -> str:
    if language == 'it':
        return ('Answer in fluent, professional Italian. Understand Italian questions and coding instructions. '
                'Keep code, compiler diagnostics, filenames, paths, command-line flags, API signatures and quotes '
                'in their original form. Do not translate identifiers. Never invent verification results.')
    return ('Answer in the user\'s preferred language; use precise, natural English by default. '
            'Keep code, compiler diagnostics, filenames, paths, API signatures and commands unchanged.')
