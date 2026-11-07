"""A ranked, token-budgeted map of a repository.

Same idea as aider's repo map (Apache-2.0): rank files by a reference graph
personalised to the task, then show only signatures. This is an independent,
standard-library implementation using regular expressions rather than
tree-sitter, so its symbol coverage is heuristic. Compiler-accurate navigation
remains the job of takumi.source_index.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
from pathlib import Path
import re
import subprocess
import time

SKIP_DIRS = {'.git', 'node_modules', 'bin', 'obj', 'build', 'dist', 'out', 'target', '.gradle', '.idea', '.vs', '.vscode',
             '__pycache__', '.venv', 'venv', 'Pods', 'DerivedData', '.next', 'coverage', '.takumi-build', '.cxx'}
LANGUAGE = {'.py': 'python', '.js': 'js', '.mjs': 'js', '.cjs': 'js', '.jsx': 'js', '.ts': 'js', '.tsx': 'js', '.html': 'web',
            '.css': 'web', '.webmanifest': 'web', '.json': 'web', '.cs': 'cs', '.fs': 'cs', '.c': 'cpp', '.h': 'cpp', '.cc': 'cpp',
            '.cpp': 'cpp', '.cxx': 'cpp', '.hpp': 'cpp', '.java': 'java', '.kt': 'kotlin', '.kts': 'kotlin', '.swift': 'swift',
            '.m': 'cpp', '.mm': 'cpp', '.rs': 'rust', '.go': 'go', '.xml': 'web', '.plist': 'web'}
DEFINITIONS = {
    'python': [r'^\s*(?:async\s+)?def\s+(\w+)', r'^\s*class\s+(\w+)'],
    'js': [r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+(\w+)', r'^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)',
           r'^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>', r'^\s*(?:export\s+)?(?:interface|type|enum)\s+(\w+)',
           r'^\s{2,4}(?:async\s+|static\s+|get\s+|set\s+)*(\w+)\s*\([^)]*\)\s*\{'],
    'cs': [r'\b(?:class|interface|struct|enum|record)\s+(\w+)', r'^\s*(?:public|internal|protected|private)[\w\s<>\[\],?]*?\s(\w+)\s*\([^;]*$'],
    'cpp': [r'^\s*(?:class|struct|enum(?:\s+class)?|namespace)\s+(\w+)', r'^[\w:<>,*&\s~]+?\s[*&]?(\w+)\s*\([^;]*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:\{|$)'],
    'java': [r'\b(?:class|interface|enum|record)\s+(\w+)', r'^\s*(?:public|protected|private)[\w\s<>\[\],]*?\s(\w+)\s*\([^;]*$'],
    'kotlin': [r'\b(?:class|interface|object)\s+(\w+)', r'\bfun\s+(?:<[^>]+>\s*)?(?:\w+\.)?(\w+)\s*\('],
    'swift': [r'\b(?:class|struct|enum|protocol|actor|extension)\s+(\w+)', r'\bfunc\s+(\w+)'],
    'rust': [r'\bfn\s+(\w+)', r'\b(?:struct|enum|trait|mod)\s+(\w+)'],
    'go': [r'^func\s+(?:\([^)]*\)\s*)?(\w+)', r'^type\s+(\w+)'],
    'web': [],
}
COMPILED = {key: [re.compile(p) for p in value] for key, value in DEFINITIONS.items()}
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]{2,}')
STOP = {'def', 'class', 'function', 'return', 'public', 'private', 'static', 'const', 'void', 'self', 'this', 'import', 'from', 'for',
        'while', 'else', 'true', 'false', 'null', 'None', 'new', 'int', 'string', 'bool', 'var', 'let', 'func', 'fun', 'the', 'and'}
CAMEL = re.compile(r'(?<=[a-z0-9])(?=[A-Z])|_')
CHARS_PER_TOKEN = 3.2


def changed_files(project: Path) -> list[str]:
    """Source files the working tree has changed, or an empty list when this is not a checkout."""
    project = Path(project)
    try:
        result = subprocess.run(['git', '-C', str(project), 'status', '--porcelain=v1'],
                                capture_output=True, timeout=3, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    names = []
    for line in result.stdout.decode('utf-8', 'replace').splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if ' -> ' in path:
            path = path.split(' -> ', 1)[1].strip()
        if Path(path).suffix.lower() not in LANGUAGE:
            continue
        if (project / path).is_file() and path not in names:
            names.append(path)
    return names


def list_sources(project: Path, *, max_files: int = 4000) -> list[str]:
    project = Path(project)
    names: list[str] = []
    try:
        result = subprocess.run(['git', '-C', str(project), 'ls-files', '-co', '--exclude-standard', '-z'], capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode == 0:
            names = [n for n in result.stdout.decode('utf-8', 'replace').split('\0') if n]
    except (OSError, subprocess.SubprocessError):
        names = []
    if not names:
        names = [p.relative_to(project).as_posix() for p in project.rglob('*')
                 if p.is_file() and not SKIP_DIRS & set(p.relative_to(project).parts)]
    names = [n for n in names if not SKIP_DIRS & set(n.split('/')) and Path(n).suffix.lower() in LANGUAGE and (project / n).is_file()]
    return sorted(names)[:max_files]


def _scan(project: Path, name: str):
    try:
        path = project / name
        if path.stat().st_size > 300_000:
            return None
        text = path.read_text('utf-8', 'replace')
    except OSError:
        return None
    definitions = []
    patterns = COMPILED[LANGUAGE[Path(name).suffix.lower()]]
    for number, line in enumerate(text.split('\n'), 1):
        for pattern in patterns:
            match = pattern.search(line)
            if match and match.group(1) not in STOP:
                definitions.append((match.group(1), number, line.strip()[:140]))
                break
    return definitions, Counter(x for x in IDENT.findall(text) if x not in STOP)


def _terms(text: str) -> set[str]:
    words = set(IDENT.findall(text or ''))
    return {w.lower() for word in words for w in CAMEL.split(word) if len(w) > 2} | {w.lower() for w in words}


def pagerank(graph: dict[str, Counter], nodes: list[str], personal: dict[str, float], *, damping: float = 0.85, rounds: int = 25) -> dict[str, float]:
    if not nodes:
        return {}
    total = sum(personal.get(n, 0) for n in nodes)
    base = {n: personal.get(n, 0) / total for n in nodes} if total else {n: 1 / len(nodes) for n in nodes}
    rank = dict(base)
    weight = {n: sum(graph[n].values()) for n in nodes}
    for _ in range(rounds):
        spill = damping * sum(rank[n] for n in nodes if not weight[n])
        next_rank = {n: (1 - damping) * base[n] + spill * base[n] for n in nodes}
        for node in nodes:
            if weight[node]:
                share = damping * rank[node] / weight[node]
                for other, amount in graph[node].items():
                    next_rank[other] += share * amount
        rank = next_rank
    return rank


def build(project: Path, *, task: str = '', focus: list[str] | None = None, budget_tokens: int = 1500, deadline: float = 4.0,
          resolved: set[str] | None = None, resolved_files: set[str] | None = None, mention: str = '') -> dict:
    """Return the ranked files and a rendered map within the token budget."""
    project = Path(project)
    started = time.monotonic()
    names, scans, partial = list_sources(project), {}, False
    for name in names:
        if time.monotonic() - started > deadline:
            partial = True
            break
        scan = _scan(project, name)
        if scan:
            scans[name] = scan
    defined: dict[str, set[str]] = defaultdict(set)
    for name, (definitions, _) in scans.items():
        for symbol, _, _ in definitions:
            defined[symbol].add(name)
    nodes = list(scans)
    graph: dict[str, Counter] = {n: Counter() for n in nodes}
    resolved, resolved_files = resolved or set(), resolved_files or set()
    for name, (_, used) in scans.items():
        for symbol, count in used.items():
            # The index already decided this word is not a call to the definition.
            if symbol in resolved and name not in resolved_files:
                continue
            owners = defined.get(symbol)
            if not owners or owners == {name}:
                continue
            # Common names say little; a symbol with one owner says a lot.
            weight = math.sqrt(count) / len(owners)
            for owner in owners:
                if owner != name:
                    graph[name][owner] += weight
    frequency: Counter = Counter()
    for name, (definitions, used) in scans.items():
        terms = {symbol.lower() for symbol, _, _ in definitions}
        terms.update(token.lower() for token in used)
        terms.update(_terms(name))
        frequency.update(terms)
    # A word that shows up in most files is not a reason to read them.
    crowded = {term for term, count in frequency.items()
               if len(scans) >= 6 and count >= 4 and count / len(scans) >= 0.4}
    # A failure can name the function when the task and the failing file do not.
    query = (task or '') + '\n' + (mention or '')
    wanted, focus_set = _terms(query) - crowded, set(focus or [])
    # One definition is the usual hit. A few definitions of the same name are all opened.
    # A name defined across the tree is left alone.
    mentioned = {symbol for symbol, owners in defined.items()
                 if 1 <= len(owners) <= 3 and re.search(r'\b' + re.escape(symbol) + r'\b', query)}
    personal = {}
    hits: dict[str, list[int]] = {}
    for name, (definitions, used) in scans.items():
        score = 20.0 if name in focus_set else 0.0
        score += 3 * len(_terms(name) & wanted)
        for symbol, number, _signature in definitions:
            if symbol in mentioned:
                score += 12
                hits.setdefault(name, []).append(number)
        score += sum(1 for symbol, _, _ in definitions if symbol.lower() in wanted and symbol not in mentioned)
        uses = mentioned & set(used)
        if resolved:
            uses = {symbol for symbol in uses if symbol not in resolved or name in resolved_files}
        if uses:
            score += 15
        if score:
            personal[name] = score
    rank = pagerank(graph, nodes, personal)
    ordered = sorted(nodes, key=lambda n: (-rank.get(n, 0), n))
    out, used, limit = [], 0, budget_tokens * CHARS_PER_TOKEN
    for name in ordered:
        lines = [name + ':'] + [f'  {number}: {signature}' for _, number, signature in scans[name][0][:18]]
        block = '\n'.join(lines) if len(lines) > 1 else name
        if used + len(block) > limit:
            if used + len(name) > limit:
                break
            block = name
        out.append(block)
        used += len(block) + 1
    return {'files': ordered, 'text': '\n'.join(out), 'indexed': len(scans), 'listed': len(names), 'partial': partial,
            'hits': hits,
            'method': 'regex symbols, reference PageRank, index calls override a shared word' if resolved else 'regex symbols, reference PageRank, common words ignored',
            'ranks': {n: rank.get(n, 0) for n in ordered},
            'seconds': round(time.monotonic() - started, 3)}
