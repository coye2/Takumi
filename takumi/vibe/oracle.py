"""Two checks on tests the model wrote for itself.

A test that passes proves little if its expected value is wrong or if it would
pass whatever the code did. The held-out `js-ordinal` run did both harms at once:
the model wrote ordinal(121) == '121th', changed correct code to match, and the
run still ended verified.
"""
from __future__ import annotations

import difflib
import re

# The reviewer gets the task and the new test lines. It never gets the code,
# because code that already agrees with a wrong test argues for that test.
REVIEW_SYSTEM = """You review test expectations against a task description. You cannot see the implementation, and you must not guess it.
For each numbered line that states an expected result, work out the right answer by hand from the task alone.
A line is wrong only when the task's own words give a different result, or when the expected text does not follow the format the task states. A line about behaviour the task does not state is not wrong.
Finish with exactly one last line, either:
OK
or:
WRONG: followed by the wrong line numbers, separated by commas"""
_VERDICT = re.compile(r'^\s*(OK|WRONG\s*:\s*[\d,\s]+)\s*\.?\s*$', re.I)
REVIEW_LINE_LIMIT = 160


def added_lines(old: str, new: str) -> list[str]:
    """Non-blank lines present in new and not in old, in order."""
    lines = []
    for line in difflib.ndiff(old.replace('\r\n', '\n').split('\n'), new.replace('\r\n', '\n').split('\n')):
        if line.startswith('+ ') and line[2:].strip():
            lines.append(line[2:])
    return lines


def review_messages(task: str, lines: list[str]) -> list[dict]:
    numbered = '\n'.join(f'{index}: {line}' for index, line in enumerate(lines[:REVIEW_LINE_LIMIT], 1))
    return [{'role': 'system', 'content': REVIEW_SYSTEM},
            {'role': 'user', 'content': f'Task: {task.strip()}\n\nNew test lines:\n{numbered}'}]


def parse_verdict(text: str, count: int) -> list[int] | None:
    """Wrong line numbers, an empty list for OK, or None when there is no usable verdict."""
    for line in reversed((text or '').strip().split('\n')):
        if not line.strip():
            continue
        match = _VERDICT.match(line.replace('`', '').replace('*', ''))
        if not match:
            return None
        if match.group(1).upper().startswith('OK'):
            return []
        found = sorted({int(n) for n in re.findall(r'\d+', match.group(1)) if 1 <= int(n) <= count})
        return found or None
    return None


_CODE_SUFFIXES = {'.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.cs', '.cpp', '.cc', '.h', '.hpp', '.c', '.java', '.kt', '.go', '.rs', '.swift'}
_SKIP_LINE = re.compile(r'^\s*(?:#|//|/\*|\*|import\b|from\b|using\b|package\b|namespace\b|use\b|$)')
_STRING = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`")
_SWAPS = (
    (r' === ', ' !== '), (r' !== ', ' === '), (r' == ', ' != '), (r' != ', ' == '),
    (r' <= ', ' < '), (r' >= ', ' > '), (r' < ', ' <= '), (r' > ', ' >= '),
    (r' && ', ' || '), (r' \|\| ', ' && '), (r'\band\b', 'or'), (r'\bor\b', 'and'),
    (r' \+ ', ' - '), (r' - ', ' + '),
    (r'\bTrue\b', 'False'), (r'\bFalse\b', 'True'), (r'\btrue\b', 'false'), (r'\bfalse\b', 'true'),
    (r'(?<![\w.])1(?![\w.])', '2'), (r'(?<![\w.])0(?![\w.])', '1'),
)


def is_code_file(name: str) -> bool:
    return any(name.lower().endswith(suffix) for suffix in _CODE_SUFFIXES)


def mutate_line(line: str) -> str | None:
    """The first small behaviour change available outside string literals."""
    if _SKIP_LINE.match(line):
        return None
    masked = _STRING.sub(lambda match: '\0' * len(match.group(0)), line)
    for pattern, replacement in _SWAPS:
        match = re.search(pattern, masked)
        if match:
            return line[:match.start()] + replacement + line[match.end():]
    return None


def mutants(old: str, new: str, limit: int = 4) -> list[dict]:
    """Up to `limit` one-line breaks spread across the lines this run changed."""
    new_lines = new.replace('\r\n', '\n').split('\n')
    matcher = difflib.SequenceMatcher(a=old.replace('\r\n', '\n').split('\n'), b=new_lines, autojunk=False)
    found = []
    for tag, _, _, low, high in matcher.get_opcodes():
        if tag in ('replace', 'insert'):
            for index in range(low, high):
                changed = mutate_line(new_lines[index])
                if changed is not None:
                    found.append({'line': index + 1, 'original': new_lines[index], 'mutated': changed})
    if len(found) <= limit:
        return found
    return [found[(index * len(found)) // limit] for index in range(limit)]


def apply_mutant(text: str, mutant: dict) -> str:
    lines = text.replace('\r\n', '\n').split('\n')
    lines[mutant['line'] - 1] = mutant['mutated']
    return '\n'.join(lines)
