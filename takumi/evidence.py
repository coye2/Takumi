"""Conservative response evidence audit: citations prove location, not semantics."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .plugins import in_project

CITATION = re.compile(r'\[source:([^\]\r\n:]{1,300}):L(\d+)(?:-L?(\d+))?\]')


def response_contract(prompt: str, *, acting: bool = False) -> str:
    lower = prompt.lower()
    kind = 'review' if re.search(r'\breview\b', lower) else 'tutorial' if re.search(r'\b(teach|tutorial|explain how)\b', lower) else 'spec' if re.search(r'\b(schema|specification|format layout)\b', lower) else 'diagnosis'
    structures = {
        'diagnosis': 'State the observed failure, evidence, likely cause, smallest fix and verification/remaining uncertainty.',
        'review': 'Explain the most important findings and their practical effect in plain language. Cite the relevant source where useful. Avoid restating the diff or listing every file.',
        'tutorial': 'State the invariant, walk through a concrete example, then show how to check it. Do not end by repeating the introduction.',
        'spec': 'State scope/version, explicit layout or interface, invariants, unsupported cases and validation examples.'}
    contract = ('ENGINEERING RESPONSE CONTRACT (' + kind + '): ' + structures[kind] +
            ' Honor explicitly requested JSON-only or code-only output; include only the structure useful to that request. Keep heading levels consistent; state each caveat once. For code-specific claims locate the symbol, trace the relevant control flow, '
            'and cite [source:relative/path:L12-L18] when a local source location supports a finding. A name match alone does not verify behavior. '
            'Explain uncertainty naturally, such as "I suspect" or "I have not tested this"; do not force bracketed evidence labels into ordinary prose. '
            'Never claim a test passed without its actual result. '
            'For questions crossing domains, identify the shared representation or invariant before explaining both consequences. '
            'These are reasoning checks, not mandatory sections or a list to print. '
            'Generated code is a draft until the separate syntax/build checks finish. Do not claim syntax checks establish runtime correctness.')
    if acting:
        contract += (' Before an investigation requiring more than three tool steps, write a compact plan with expected observations and an alternative '
            'for each failure. After every unexpected tool result, state the changed hypothesis and next check; do not blindly repeat. '
            'For each proposed verification, explain why the original defect would fail and the corrected behavior would pass. '
            'A test that exercises neither the failing boundary nor the affected consumer cannot establish the fix.')
    return contract


def audit_answer(answer: str, project: Path | None, snapshot: dict | None) -> dict:
    claims = []
    in_code = False
    lines = answer.splitlines()
    for line in lines:
        if line.lstrip().startswith(('```', '~~~')):
            in_code = not in_code
            continue
        if in_code or not line.strip() or line.lstrip().startswith('#'):
            continue
        tag = 'inferred' if '[inferred]' in line.lower() else 'analogy' if '[analogy]' in line.lower() else 'unverified'
        citations = []
        for match in CITATION.finditer(line):
            path, first, last = match.group(1), int(match.group(2)), int(match.group(3) or match.group(2))
            citation = {'path': path, 'first_line': first, 'last_line': last, 'status': 'unverified'}
            try:
                if not project or not snapshot: raise ValueError('No source snapshot for this answer')
                if not 1 <= first <= last <= first + 30: raise ValueError('Invalid or oversized source range')
                target = in_project(project, path)
                if not target.is_file() or target.stat().st_size > 250000: raise ValueError('Source unavailable or too large')
                raw = target.read_bytes()
                current = hashlib.sha256(raw).hexdigest()
                if snapshot['hashes'].get(target.relative_to(project.resolve()).as_posix()) != current:
                    raise ValueError('Source changed since indexing or was not indexed')
                source_lines = raw.decode('utf-8').splitlines()
                if last > len(source_lines): raise ValueError('Source range does not exist')
                citation.update(status='source_located', sha256=current, commit=snapshot.get('commit'),
                                excerpt='\n'.join(source_lines[first-1:last]))
            except (ValueError, OSError, UnicodeError) as exc:
                citation['reason'] = str(exc)[:200]
            citations.append(citation)
        if tag == 'unverified' and '[unverified]' not in line.lower() and citations and all(c['status'] == 'source_located' for c in citations):
            tag = 'source_located'
        claims.append({'text': line.strip()[:2000], 'category': tag, 'citations': citations,
                       'behavior_verified': False})
    return {'claims': claims[:100], 'total': len(claims), 'truncated': len(claims) > 100,
            'scope': 'Source locations are checked against current file hashes. This audit does not prove the model’s behavioral claims or correctness. Untagged claims remain unverified.'}


def structure_report(answer: str) -> dict:
    issues, seen, levels = [], set(), []
    for paragraph in re.split(r'\n\s*\n', re.sub(r'```[\s\S]*?```', '', answer)):
        normalized = ' '.join(paragraph.lower().split())
        if len(normalized) > 70 and normalized in seen: issues.append('Repeated paragraph')
        seen.add(normalized)
        for heading in re.findall(r'^(#{1,6})\s', paragraph, re.M):
            level = len(heading)
            if levels and level > levels[-1] + 1: issues.append('Skipped heading level')
            levels.append(level)
    return {'issues': list(dict.fromkeys(issues)), 'scope': 'Structure only; technical correctness and useful conclusions require evaluation.'}
