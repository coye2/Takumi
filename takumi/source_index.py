"""Persistent, project-scoped source evidence. Static edges are not execution traces.

Python AST and Clang JSON AST are used instead of text-shaped guesses. Unsupported
languages remain searchable text through the existing tool. Build scripts and
compile_commands commands are never executed by the indexer.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
import bisect
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import threading
import time

from .compiler_checks import find_tool, preprocessing_view
from .plugins import in_project

EXTENSIONS = {'.py', '.c', '.cc', '.cpp', '.cxx', '.h', '.hpp', '.hxx'}
HEADERS = {'.h', '.hpp', '.hxx'}
IGNORE = {'.git', '.venv', 'venv', 'node_modules', 'dist', 'build', '__pycache__', '.idea', '.vs'}
FUNCTIONS = {'FunctionDecl', 'CXXMethodDecl', 'CXXConstructorDecl', 'CXXDestructorDecl', 'CXXConversionDecl'}
SCHEMA = 3


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _python(path: str, source: str) -> dict:
    result = {'symbols': [], 'calls': [], 'references': [], 'strings': [], 'parser': 'python-ast', 'complete': True}
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return {**result, 'complete': False, 'diagnostic': str(exc)[:500]}
    scopes = []
    module = path.removesuffix('.py').replace('/', '.')
    imports = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for item in node.names:
                imports[item.asname or item.name] = node.module + '.' + item.name
        elif isinstance(node, ast.Import):
            for item in node.names:
                imports[item.asname or item.name] = item.name

    class Visitor(ast.NodeVisitor):
        def definition(self, node, kind):
            name = '.'.join([module, *scopes, node.name])
            result['symbols'].append({'id': 'py:' + name, 'name': node.name, 'qualified': name,
                                      'kind': kind, 'path': path, 'line': node.lineno,
                                      'end_line': node.end_lineno, 'definition': True})
            scopes.append(node.name)
            self.generic_visit(node)
            scopes.pop()
        def visit_FunctionDef(self, node): self.definition(node, 'function')
        def visit_AsyncFunctionDef(self, node): self.definition(node, 'function')
        def visit_ClassDef(self, node): self.definition(node, 'struct')
        def visit_Call(self, node):
            name = ast.unparse(node.func)
            # A lexical binding is a candidate, never proof of dynamic dispatch.
            first, *rest = name.split('.')
            target = imports.get(first, module + '.' + first) + ('.' + '.'.join(rest) if rest else '')
            result['calls'].append({'name': name.rsplit('.', 1)[-1], 'target': 'py:' + target,
                'caller': 'py:' + '.'.join([module, *scopes]), 'path': path, 'line': node.lineno,
                'resolution': 'lexical_candidate', 'kind': 'call'})
            self.generic_visit(node)
        def visit_Name(self, node):
            result['references'].append({'name': node.id, 'path': path, 'line': node.lineno,
                'access': 'write' if isinstance(node.ctx, ast.Store) else 'read',
                'caller': 'py:' + '.'.join([module, *scopes])})
        def visit_Constant(self, node):
            if isinstance(node.value, str) and len(node.value) <= 240:
                result['strings'].append({'value': node.value, 'path': path, 'line': node.lineno})
    Visitor().visit(tree)
    return result


def _clang(path: str, snapshot: Path, sources: dict, *, timeout: float, cancel=None) -> dict:
    result = {'symbols': [], 'calls': [], 'references': [], 'strings': [], 'parser': 'clang-ast', 'complete': False}
    exe = find_tool('clang')
    if not exe:
        return {**result, 'diagnostic': 'Clang is unavailable; C/C++ semantics are unverified.'}
    args = [exe, '--no-default-config', '-x', 'c' if path.endswith('.c') else 'c++',
            '-std=c17' if path.endswith('.c') else '-std=c++20', '-nostdinc', '-nostdinc++',
            '-fsyntax-only', '-fno-color-diagnostics', '-ferror-limit=3', '-Xclang', '-ast-dump=json', path]
    env = {k: v for k, v in os.environ.items() if k.upper() not in
           {'CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH', 'INCLUDE', 'CL', '_CL_', 'CCC_OVERRIDE_OPTIONS'}}
    try:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen(args, cwd=snapshot, env=env, stdout=output, stderr=errors,
                                    creationflags=0x08000000 if os.name == 'nt' else 0)
            deadline = time.monotonic() + timeout
            while proc.poll() is None:
                if time.monotonic() >= deadline or (cancel is not None and cancel.is_set()):
                    proc.kill(); proc.wait()
                    return {**result, 'diagnostic': 'Indexing interrupted or timed out.'}
                time.sleep(.025)
            errors.seek(0)
            result.update(complete=proc.returncode == 0, diagnostic=errors.read(1500).decode('utf-8', 'replace'), tool=exe)
            if output.tell() > 12_000_000:
                return {**result, 'complete': False, 'diagnostic': 'AST exceeded the bounded index size.'}
            output.seek(0)
            tree = json.load(output)
    except (OSError, ValueError) as exc:
        return {**result, 'diagnostic': str(exc)[:300]}

    offsets = {p: [0] + [m.end() for m in re.finditer(b'\n', raw)] for p, raw in sources.items()}
    declarations = {}
    last_file = path

    def source_path(value):
        p = Path(value)
        try:
            return (p if p.is_absolute() else snapshot / p).resolve().relative_to(snapshot).as_posix()
        except ValueError:
            return None

    def position(node, file):
        point = node.get('loc') or node.get('range', {}).get('begin', {})
        point = point.get('expansionLoc', point)
        return bisect.bisect_right(offsets.get(file, [0]), point.get('offset', 0))

    def collect(node, scope='', internal=False):
        nonlocal last_file
        loc = node.get('loc', {})
        if loc.get('file'):
            last_file = source_path(loc['file'])
        file = last_file
        kind = node.get('kind')
        name = node.get('name', '')
        inner = node.get('inner', [])
        internal = internal or (kind == 'NamespaceDecl' and not name)
        if kind in FUNCTIONS and name:
            key = node.get('mangledName') or (scope + name + ':' + node.get('type', {}).get('qualType', ''))
            if internal or node.get('storageClass') == 'static':
                key = path + ':' + key
            identity = 'c:' + key
            declarations[node['id']] = (identity, file, name)
            if file in sources:
                result['symbols'].append({'id': identity, 'name': name, 'qualified': scope + name,
                    'kind': 'function', 'path': file, 'line': position(node, file),
                    'end_line': bisect.bisect_right(offsets[file], node.get('range', {}).get('end', {}).get('offset', 0)),
                    'signature': node.get('type', {}).get('qualType', ''),
                    'definition': any(x.get('kind') in ('CompoundStmt', 'CXXTryStmt') for x in inner)})
        elif kind in ('RecordDecl', 'CXXRecordDecl', 'EnumDecl') and name and file in sources and not node.get('isImplicit'):
            result['symbols'].append({'id': 'type:' + file + ':' + scope + name, 'name': name,
                'qualified': scope + name, 'kind': 'struct', 'path': file, 'line': position(node, file),
                'definition': bool(node.get('completeDefinition') or inner)})
        if kind in ('NamespaceDecl', 'RecordDecl', 'CXXRecordDecl') and name:
            scope += name + '::'
        for child in inner:
            collect(child, scope, internal)
    collect(tree)

    def callee(node):
        if node.get('kind') in ('CallExpr', 'CXXMemberCallExpr', 'CXXOperatorCallExpr', 'ConditionalOperator'):
            return None
        ref = node.get('referencedDecl', {})
        if ref.get('kind') in FUNCTIONS:
            return declarations.get(ref.get('id'))
        if node.get('referencedMemberDecl'):
            return declarations.get(node['referencedMemberDecl'])
        for child in node.get('inner', []):
            found = callee(child)
            if found: return found
        return None

    def walk(node, owner=None):
        kind = node.get('kind')
        if kind in FUNCTIONS:
            owner = declarations.get(node.get('id'))
        if owner and owner[1] in sources:
            file = owner[1]
            line = position(node, file)
            if kind in ('CallExpr', 'CXXMemberCallExpr', 'CXXOperatorCallExpr'):
                children = node.get('inner', [])
                target = callee(children[0]) if children else None
                result['calls'].append({'name': target[2] if target else '<indirect>',
                    'target': target[0] if target else None, 'caller': owner[0], 'path': file,
                    'line': line, 'offset': node.get('range', {}).get('begin', {}).get('expansionLoc', node.get('range', {}).get('begin', {})).get('offset'),
                    'kind': 'call', 'resolution': 'static' if target else 'unresolved'})
            elif kind == 'DeclRefExpr' and node.get('referencedDecl', {}).get('kind') not in FUNCTIONS:
                result['references'].append({'name': node.get('referencedDecl', {}).get('name', ''),
                    'caller': owner[0], 'path': file, 'line': line, 'access': 'reference'})
            elif kind == 'StringLiteral':
                result['strings'].append({'value': node.get('value', '')[:240], 'path': file, 'line': line})
        for child in node.get('inner', []):
            walk(child, owner)
    walk(tree)
    return result


class SourceIndex:
    def __init__(self, database: Path):
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS snapshots(root TEXT PRIMARY KEY, content TEXT NOT NULL)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database, timeout=10)
        try:
            with db: yield db
        finally:
            db.close()

    def refresh(self, project: Path, *, cancel=None, budget: float = 15) -> dict:
        root = project.resolve(strict=True)
        if not root.is_dir(): raise ValueError('Choose a project directory')
        with self.lock:
            return self._refresh(root, cancel=cancel, budget=budget)

    def _refresh(self, root: Path, *, cancel=None, budget: float = 15) -> dict:
        started = time.monotonic()
        key = str(root)
        with self._db() as db:
            row = db.execute('SELECT content FROM snapshots WHERE root=?', (key,)).fetchone()
        previous = json.loads(row[0]) if row else {}
        files, omitted, total = {}, [], 0
        for parent, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in IGNORE and not (Path(parent) / d).is_symlink()
                             and (Path(parent) / d).resolve().is_relative_to(root))
            for name in sorted(names):
                p = Path(parent) / name
                if p.suffix.lower() not in EXTENSIONS: continue
                rel = p.relative_to(root).as_posix()
                if p.is_symlink() or not p.resolve().is_relative_to(root):
                    omitted.append({'path': rel, 'reason': 'Outside project or symbolic link'}); continue
                if len(files) >= 500 or total > 8_000_000:
                    omitted.append({'path': rel, 'reason': 'Index budget'}); continue
                try:
                    raw = p.read_bytes() if p.stat().st_size <= 250_000 else None
                    if raw is None or b'\0' in raw:
                        omitted.append({'path': rel, 'reason': 'Large or binary source'}); continue
                    raw.decode('utf-8')
                    files[rel] = raw; total += len(raw)
                except (OSError, UnicodeError):
                    omitted.append({'path': rel, 'reason': 'Unreadable UTF-8 source'})
        hashes = {p: digest(raw) for p, raw in files.items()}
        compiler = find_tool('clang')
        parser_id = str(SCHEMA) + ':' + str(compiler)
        if compiler:
            info = Path(compiler).stat()
            parser_id += ':' + str(info.st_size) + ':' + str(info.st_mtime_ns)
        headers_changed = (previous.get('parser_id') != parser_id or
            {p: h for p, h in hashes.items() if Path(p).suffix.lower() in HEADERS} !=
            {p: h for p, h in previous.get('hashes', {}).items() if Path(p).suffix.lower() in HEADERS})
        units = {}
        with tempfile.TemporaryDirectory(prefix='takumi-index-') as temporary:
            snapshot = Path(temporary).resolve()
            for path, raw in files.items():
                target = snapshot / path
                target.parent.mkdir(parents=True, exist_ok=True)
                # Reject computed/absolute/parent includes, embeds and compiler
                # pragmas. The snapshot has no other project or profile data.
                source = raw.decode('utf-8')
                unsafe = False
                for directive in re.findall(r'^\s*#\s*([^\n]*)', preprocessing_view(source), re.M):
                    if re.match(r'(include|include_next|import|embed)\b', directive):
                        match = re.fullmatch(r'include\s*[<"]([A-Za-z0-9_./-]+)[>"]\s*(?://[^\n]*)?', directive)
                        if not match or '..' in match[1].split('/') or match[1].startswith('/'):
                            unsafe = True
                    if re.match(r'pragma\s+(?!once\b)', directive): unsafe = True
                if re.search(r'\b(?:import|__has_include|_Pragma|__pragma)\b|\?\?[/=]|%:|##', source): unsafe = True
                if unsafe and Path(path).suffix != '.py':
                    target.write_text('#error Unsupported file-dependent preprocessing\n', encoding='utf-8')
                else:
                    target.write_bytes(raw)
            for path, raw in files.items():
                prior = previous.get('units', {}).get(path)
                changed = previous.get('hashes', {}).get(path) != hashes[path]
                if prior and prior.get('complete') and not changed and (path.endswith('.py') or not headers_changed):
                    units[path] = prior; continue
                if time.monotonic() - started >= budget or (cancel is not None and cancel.is_set()):
                    omitted.append({'path': path, 'reason': 'Index time budget or cancellation'}); continue
                units[path] = _python(path, raw.decode('utf-8')) if path.endswith('.py') else _clang(
                    path, snapshot, files, timeout=min(2, budget - (time.monotonic() - started)), cancel=cancel)
        commit = None
        try:
            cp = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=2)
            if cp.returncode == 0: commit = cp.stdout.strip()
        except (OSError, subprocess.TimeoutExpired): pass
        result = {'schema': SCHEMA, 'root': key, 'commit': commit, 'hashes': hashes, 'parser_id': parser_id,
                  'units': units, 'omitted': omitted[:500], 'indexed_at': time.time(),
                  'complete': not omitted and all(u['complete'] for u in units.values()),
                  'scope': 'Static source snapshot. File hashes identify working-copy content; commit alone does not prove a clean tree. C++ virtual dispatch and Python calls are not runtime traces. References are not interprocedural data-flow proof.'}
        with self._db() as db:
            db.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?)', (key, json.dumps(result)))
        return result

    @staticmethod
    def query(snapshot: dict, query: str, kind: str = 'all', limit: int = 100) -> dict:
        query = query.strip()
        if not query or len(query) > 160: raise ValueError('Enter a symbol name under 161 characters')
        if kind not in ('all', 'symbols', 'calls', 'references', 'strings'): raise ValueError('Unknown index query kind')
        matches, seen = [], set()
        for unit in snapshot['units'].values():
            for category in ('symbols', 'calls', 'references', 'strings') if kind == 'all' else (kind,):
                for item in unit.get(category, []):
                    if not (query in (item.get('name'), item.get('qualified'), item.get('id')) or
                            (category == 'strings' and query.casefold() in item.get('value', '').casefold())): continue
                    marker = (category, item.get('id') or item.get('caller'), item['path'], item['line'], item.get('offset'), item.get('target'), item.get('definition'))
                    if marker in seen: continue
                    seen.add(marker)
                    matches.append({**item, 'category': category, 'sha256': snapshot['hashes'].get(item['path']),
                                    'parser_complete': unit['complete']})
        matches.sort(key=lambda x: (x['path'], x['line'], x['category']))
        return {'query': query, 'commit': snapshot['commit'], 'matches': matches[:limit], 'total': len(matches),
                'truncated': len(matches) > limit, 'index_complete': snapshot['complete'],
                'omitted': snapshot['omitted'], 'scope': snapshot['scope']}

    @staticmethod
    def trace(snapshot: dict, start: str, end: str) -> dict:
        symbols = {}
        edges = {}
        for unit in snapshot['units'].values():
            for item in unit.get('symbols', []):
                if item.get('definition') and item['kind'] == 'function': symbols[item['id']] = item
            for item in unit.get('calls', []):
                if item.get('target') and item['resolution'] == 'static' and unit['complete']:
                    edges.setdefault(item['caller'], []).append(item)
        starts = [k for k, v in symbols.items() if start in (k, v['name'], v['qualified'])]
        ends = [k for k, v in symbols.items() if end in (k, v['name'], v['qualified'])]
        if len(starts) != 1 or len(ends) != 1:
            return {'status': 'unverified', 'reason': 'Use unambiguous qualified symbol IDs', 'starts': starts, 'ends': ends}
        queue, visited = [(starts[0], [])], set()
        while queue and len(visited) < 5000:
            current, path = queue.pop(0)
            if current == ends[0]:
                return {'status': 'static_path_found', 'functions': [symbols.get(starts[0])] + [symbols.get(e['target'], {'id': e['target']}) for e in path],
                        'calls': [{**e, 'sha256': snapshot['hashes'].get(e['path'])} for e in path],
                        'commit': snapshot['commit'], 'index_complete': snapshot['complete'], 'scope': snapshot['scope']}
            if current in visited: continue
            visited.add(current)
            if len(path) < 80:
                queue.extend((e['target'], path + [e]) for e in edges.get(current, []))
        return {'status': 'unverified', 'reason': 'No complete static path found; indirect, missing and unsupported calls are not guessed.'}

    @staticmethod
    def context(snapshot: dict, prompt: str, limit: int = 4500) -> str:
        terms = list(dict.fromkeys(re.findall(r'\b[A-Za-z_]\w{3,100}\b', prompt)))[:60]
        evidence = []
        if re.search(r'\b(trace|path|chain)\b', prompt, re.I):
            known = {s['name'] for unit in snapshot['units'].values() for s in unit['symbols'] if s['kind'] == 'function' and s.get('definition')}
            named = [term for term in terms if term in known]
            if len(named) >= 2:
                trace = SourceIndex.trace(snapshot, named[0], named[-1])
                if trace['status'] == 'static_path_found':
                    compact = {'static_path': [{'name': f.get('qualified', f['id']), 'path': f.get('path'), 'line': f.get('line')} for f in trace['functions']],
                               'scope': 'Ordered static call path; does not prove runtime execution or indirect dispatch.'}
                    if len(json.dumps(compact)) < limit - 500: evidence.append(compact)
        for term in terms:
            matches = SourceIndex.query(snapshot, term, limit=8)['matches']
            if matches:
                for item in matches:
                    if item.get('definition') and item['category'] == 'symbols':
                        try:
                            source = in_project(Path(snapshot['root']), item['path']).read_bytes()
                            if digest(source) == item['sha256']:
                                lines = source.decode('utf-8').splitlines()
                                end = min(item.get('end_line', item['line'] + 12), item['line'] + 20)
                                item['excerpt'] = '\n'.join(f'{i+1}: {lines[i]}' for i in range(item['line']-1, min(end, len(lines))))[:1800]
                        except (OSError, UnicodeError): pass
                candidate = {'symbol': term, 'observations': matches}
                if len(json.dumps(evidence + [candidate])) < limit - 500:
                    evidence.append(candidate)
        if not evidence: return ''
        return ('SOURCE SNAPSHOT: use exact paths/lines/hashes; observations of names/calls do not prove behavior. '
                'Mark incomplete behavioral traces UNVERIFIED. Working-copy files may differ from commit.\n' +
                json.dumps({'commit': snapshot['commit'], 'complete': snapshot['complete'], 'evidence': evidence}, ensure_ascii=False))
