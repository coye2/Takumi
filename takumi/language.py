"""Small, offline language-preference layer.

Language selection is a hint to the local model, not a translation service and
not a claim that every language is equally strong. Code, paths, diagnostics,
and identifiers stay in their original form. Nothing is sent to an external
translator.
"""
from __future__ import annotations

import re

# id, native name, speech tag, text direction.
_ROWS = (
    ('en', 'English', 'en-US', 'ltr'),
    ('it', 'Italiano', 'it-IT', 'ltr'),
    ('es', 'Español', 'es-ES', 'ltr'),
    ('fr', 'Français', 'fr-FR', 'ltr'),
    ('de', 'Deutsch', 'de-DE', 'ltr'),
    ('pt', 'Português', 'pt-BR', 'ltr'),
    ('nl', 'Nederlands', 'nl-NL', 'ltr'),
    ('pl', 'Polski', 'pl-PL', 'ltr'),
    ('sv', 'Svenska', 'sv-SE', 'ltr'),
    ('da', 'Dansk', 'da-DK', 'ltr'),
    ('no', 'Norsk', 'nb-NO', 'ltr'),
    ('fi', 'Suomi', 'fi-FI', 'ltr'),
    ('cs', 'Čeština', 'cs-CZ', 'ltr'),
    ('ro', 'Română', 'ro-RO', 'ltr'),
    ('hu', 'Magyar', 'hu-HU', 'ltr'),
    ('el', 'Ελληνικά', 'el-GR', 'ltr'),
    ('tr', 'Türkçe', 'tr-TR', 'ltr'),
    ('ru', 'Русский', 'ru-RU', 'ltr'),
    ('uk', 'Українська', 'uk-UA', 'ltr'),
    ('ar', 'العربية', 'ar-SA', 'rtl'),
    ('he', 'עברית', 'he-IL', 'rtl'),
    ('hi', 'हिन्दी', 'hi-IN', 'ltr'),
    ('ja', '日本語', 'ja-JP', 'ltr'),
    ('ko', '한국어', 'ko-KR', 'ltr'),
    ('zh', '中文', 'zh-CN', 'ltr'),
    ('vi', 'Tiếng Việt', 'vi-VN', 'ltr'),
    ('id', 'Bahasa Indonesia', 'id-ID', 'ltr'),
    ('th', 'ไทย', 'th-TH', 'ltr'),
)

_META = {code: {'name': name, 'speech': speech, 'dir': direction, 'english': english}
         for code, name, speech, direction, english in (
             (*row, english) for row, english in zip(_ROWS, (
                 'English', 'Italian', 'Spanish', 'French', 'German', 'Portuguese',
                 'Dutch', 'Polish', 'Swedish', 'Danish', 'Norwegian', 'Finnish',
                 'Czech', 'Romanian', 'Hungarian', 'Greek', 'Turkish', 'Russian',
                 'Ukrainian', 'Arabic', 'Hebrew', 'Hindi', 'Japanese', 'Korean',
                 'Chinese', 'Vietnamese', 'Indonesian', 'Thai',
             )))}
_LANGUAGES = {'auto', *_META}

_ITALIAN_HINTS = frozenset({
    'ciao', 'grazie', 'perché', 'perche', 'vorrei', 'voglio', 'potresti', 'puoi',
    'scrivi', 'mostrami', 'spiega', 'spiegami', 'come', 'quali', 'quale', 'questo',
    'questa', 'questi', 'quello', 'quella', 'anche', 'invece', 'funziona', 'funzionare',
    'migliorare', 'migliora', 'codice', 'applicazione', 'programma', 'errore',
    'velocità', 'velocita', 'schermo', 'risposta', 'rispondi', 'lingua',
    'italiano', 'italiana', 'file', 'non', 'sono', 'abbiamo', 'devo', 'devi', 'siamo',
    'dovrebbe', 'oppure', 'senza', 'quando', 'dove',
})
_HINTS = {
    'en': frozenset({'the', 'and', 'please', 'how', 'what', 'why', 'can', 'could', 'would',
                     'should', 'with', 'from', 'fix', 'make', 'code', 'works', 'this',
                     'that', 'where', 'when', 'want', 'build', 'test', 'error', 'does',
                     'have', 'need', 'about', 'thanks', 'hello'}),
    'it': _ITALIAN_HINTS,
    'es': frozenset({'hola', 'gracias', 'puedes', 'puedo', 'explicar', 'explica', 'este',
                     'esta', 'código', 'codigo', 'funciona', 'quiero', 'necesito',
                     'archivo', 'programa', 'mejorar', 'escribe', 'responde', 'porqué',
                     'porque', 'español', 'espanol'}),
    'fr': frozenset({'bonjour', 'merci', 'pouvez', 'peux', 'expliquer', 'pourquoi',
                     'programme', 'fichier', 'erreur', 'corriger', 'comment', 'fonctionne',
                     'français', 'francais', 'écris', 'ecris', 'voudrais'}),
    'de': frozenset({'hallo', 'danke', 'kannst', 'können', 'koennen', 'erklären', 'erklaeren',
                     'dieser', 'diesen', 'fehler', 'bitte', 'programm', 'datei',
                     'funktioniert', 'warum', 'schreibe', 'deutsch'}),
    'pt': frozenset({'olá', 'ola', 'obrigado', 'obrigada', 'você', 'voce', 'pode', 'explicar',
                     'porque', 'código', 'codigo', 'arquivo', 'programa', 'erro',
                     'escreva', 'português', 'portugues', 'funciona'}),
    'nl': frozenset({'hallo', 'dank', 'kun', 'uitleggen', 'waarom', 'bestand', 'fout',
                     'programma', 'werkt', 'schrijf', 'nederlands', 'alsjeblieft'}),
    'pl': frozenset({'cześć', 'czesc', 'dziękuję', 'dziekuje', 'możesz', 'mozesz', 'dlaczego',
                     'plik', 'błąd', 'blad', 'program', 'napisz', 'polsku', 'działa', 'dziala'}),
    'sv': frozenset({'hej', 'tack', 'kan', 'förklara', 'forklara', 'varför', 'varfor',
                     'fil', 'fel', 'program', 'skriv', 'svenska', 'fungerar'}),
    'tr': frozenset({'merhaba', 'teşekkürler', 'tesekkurler', 'neden', 'dosya', 'hata',
                     'program', 'yazar', 'türkçe', 'turkce', 'çalışmıyor', 'calismiyor',
                     'lütfen', 'lutfen'}),
    'vi': frozenset({'xin', 'chào', 'chao', 'cảm', 'cam', 'ơn', 'tại', 'sao', 'tệp', 'tep',
                     'lỗi', 'loi', 'chương', 'chuong', 'trình', 'trinh', 'tiếng', 'viet'}),
    'id': frozenset({'halo', 'terima', 'kasih', 'mengapa', 'berkas', 'kesalahan', 'program',
                     'tulis', 'indonesia', 'tolong', 'bagaimana'}),
}

_EXPLICIT = (
    ('it', re.compile(r'\b(?:rispondi|scrivi|parla)\s+in\s+italiano\b', re.I)),
    ('en', re.compile(r'\b(?:reply|write|speak|answer)\s+in\s+english\b', re.I)),
    ('es', re.compile(r'\b(?:contesta|responde|escribe|habla)\s+en\s+espa[nñ]ol\b', re.I)),
    ('fr', re.compile(r'\b(?:réponds|reponds|écris|ecris|parle)\s+en\s+fran[cç]ais\b', re.I)),
    ('de', re.compile(r'\b(?:antworte|schreibe|sprich)\s+auf\s+deutsch\b', re.I)),
    ('pt', re.compile(r'\b(?:responda|escreva|fale)\s+em\s+portugu[eê]s\b', re.I)),
    ('nl', re.compile(r'\b(?:antwoord|schrijf|spreek)\s+in\s+het\s+nederlands\b', re.I)),
    ('pl', re.compile(r'\b(?:odpowiedz|napisz|mów|mow)\s+po\s+polsku\b', re.I)),
    ('ja', re.compile(r'日本語で')),
    ('zh', re.compile(r'用中文')),
    ('ko', re.compile(r'한국어로')),
    ('ru', re.compile(r'(?:ответь|напиши)\s+по-русски', re.I)),
    ('uk', re.compile(r'(?:відповідай|напиши)\s+українською', re.I)),
    ('ar', re.compile(r'بالعربية')),
    ('he', re.compile(r'בעברית')),
    ('hi', re.compile(r'हिंदी में|हिन्दी में')),
    ('tr', re.compile(r'\btürkçe\s+(?:yanıtla|cevapla|yaz)\b', re.I)),
)

_KEEP = ('Keep code, compiler diagnostics, filenames, paths, command-line flags, API signatures and quotes '
         'in their original form. Do not translate identifiers. Never invent verification results.')


def supported_codes() -> list[str]:
    return ['auto', *(row[0] for row in _ROWS)]


def language_menu() -> list[dict]:
    # chrome says how much of the app's own text follows the choice. Only a few
    # Italian labels are translated, so the menu must not imply more than that.
    menu = [{'id': 'auto', 'name': 'Automatic', 'english': 'Automatic', 'speech': '', 'dir': 'ltr', 'chrome': 'english'}]
    for code, name, speech, direction in _ROWS:
        menu.append({'id': code, 'name': name, 'english': _META[code]['english'],
                     'speech': speech, 'dir': direction,
                     'chrome': 'full' if code == 'en' else 'partial' if code == 'it' else 'english'})
    return menu


def resolve_language(message: str, preference: str = 'auto') -> str:
    if preference not in _LANGUAGES:
        raise ValueError('Unsupported language preference')
    if preference != 'auto':
        return preference
    text = re.sub(r'```[\s\S]*?```', ' ', str(message or ''))[-5000:]
    text = re.sub(r'`[^`]+`', ' ', text)
    lowered = text.lower()
    for code, pattern in _EXPLICIT:
        if pattern.search(lowered) or pattern.search(text):
            return code
    script = _script_language(text)
    if script:
        return script
    words = re.findall(r"[a-zàèéìòùáíóúäöüßñçăâđêôơưæøå]+", lowered)
    scores = {code: sum(word in hints for word in words) for code, hints in _HINTS.items()}
    english = scores.get('en', 0)
    best = max((code for code in scores if code != 'en'), key=lambda code: scores[code], default='en')
    if scores.get(best, 0) >= 2 and scores[best] >= english + 1:
        return best
    if len(words) <= 3 and any(word in ('ciao', 'grazie', 'perché', 'perche') for word in words):
        return 'it'
    return 'en'


def _script_language(text: str) -> str:
    kana = len(re.findall(r'[\u3040-\u30ff]', text))
    hangul = len(re.findall(r'[\uac00-\ud7af]', text))
    hebrew = len(re.findall(r'[\u0590-\u05ff]', text))
    arabic = len(re.findall(r'[\u0600-\u06ff]', text))
    devanagari = len(re.findall(r'[\u0900-\u097f]', text))
    thai = len(re.findall(r'[\u0e00-\u0e7f]', text))
    han = len(re.findall(r'[\u4e00-\u9fff]', text))
    cyr = re.findall(r'[а-яіїєґыэъё]', text.lower())
    if kana >= 1:
        return 'ja'
    if hangul >= 1:
        return 'ko'
    if hebrew >= 1:
        return 'he'
    if arabic >= 1:
        return 'ar'
    if devanagari >= 2:
        return 'hi'
    if thai >= 2:
        return 'th'
    if han >= 2:
        return 'zh'
    if len(cyr) >= 2:
        return 'uk' if re.search(r'[іїєґ]', text.lower()) else 'ru'
    return ''


def model_instruction(language: str) -> str:
    if language == 'it':
        return ('Answer in fluent, professional Italian. Understand Italian questions and coding instructions. '
                + _KEEP)
    meta = _META.get(language)
    if meta and language != 'en':
        name = meta['english']
        return (f'Answer in fluent, professional {name}. Understand {name} questions and coding instructions. '
                + _KEEP)
    return ('Answer in the user\'s preferred language; use precise, natural English by default. '
            'Keep code, compiler diagnostics, filenames, paths, API signatures and commands unchanged.')


def speech_tag(language: str) -> str:
    meta = _META.get(language)
    return meta['speech'] if meta else 'en-US'
