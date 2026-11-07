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

EXTENSIONS = {'.py', '.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx', '.c', '.cc', '.cpp', '.cxx', '.h', '.hpp', '.hxx'}
SCRIPT_EXTENSIONS = {'.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx'}
HEADERS = {'.h', '.hpp', '.hxx'}
IGNORE = {'.git', '.venv', 'venv', 'node_modules', 'dist', 'build', 'target', '__pycache__', '.idea', '.vs'}
FUNCTIONS = {'FunctionDecl', 'CXXMethodDecl', 'CXXConstructorDecl', 'CXXDestructorDecl', 'CXXConversionDecl'}
_SCRIPT_KEYWORDS = {'if', 'for', 'while', 'switch', 'catch', 'function', 'return', 'typeof', 'new', 'await',
                    'import', 'export', 'super', 'class', 'with', 'do', 'else', 'case', 'throw', 'delete', 'void', 'yield'}
SCHEMA = 6


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _from_prefix(module: str, node: ast.ImportFrom) -> str | None:
    """Module prefix for `from prefix import name`, including one relative package."""
    if node.level:
        package = module.rsplit('.', 1)[0] if '.' in module else ''
        parts = package.split('.') if package else []
        drop = node.level - 1
        if drop > len(parts) or not node.module:
            return None
        base = parts[:len(parts) - drop] if drop else list(parts)
        return '.'.join([*base, *node.module.split('.')])
    return node.module or None


def _python_imports(module: str, tree: ast.AST) -> dict[str, str]:
    imports = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            prefix = _from_prefix(module, node)
            if not prefix:
                continue
            for item in node.names:
                if item.name != '*':
                    imports[item.asname or item.name] = prefix + '.' + item.name
        elif isinstance(node, ast.Import):
            for item in node.names:
                imports[item.asname or item.name] = item.name
    return imports


def _receiver(node: ast.AST, imports: dict[str, str], module: str, resolve=None) -> str | None:
    if isinstance(node, ast.Name):
        bound = resolve(node.id) if resolve else None
        if bound:
            return bound
        return imports.get(node.id, module + '.' + node.id)
    if isinstance(node, ast.Attribute):
        base = _receiver(node.value, imports, module, resolve)
        return base + '.' + node.attr if base else None
    if isinstance(node, ast.Call):
        # Box().save names Box.save. The constructor arguments are not part of the target.
        return _call_target(node.func, imports, module, resolve)
    return None


def _call_target(func: ast.AST, imports: dict[str, str], module: str, resolve=None) -> str:
    """Lexical target of a call. A receiver that is not a name, attribute, or constructor stays unresolved."""
    if isinstance(func, ast.Name):
        bound = resolve(func.id) if resolve else None
        if bound:
            return bound
        return imports.get(func.id, module + '.' + func.id)
    if isinstance(func, ast.Attribute):
        base = _receiver(func.value, imports, module, resolve)
        if base:
            return base + '.' + func.attr
    return module + '.<unresolved>'


def _note(bindings: dict[str, str | None], name: str, target: str | None) -> None:
    if not name:
        return
    if name not in bindings:
        bindings[name] = target
    elif bindings[name] != target:
        bindings[name] = None


def _bind_names(node: ast.AST, bindings: dict[str, str | None], target: str | None) -> None:
    if isinstance(node, ast.Name):
        _note(bindings, node.id, target)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            _bind_names(elt, bindings, None)
    elif isinstance(node, ast.Starred):
        _bind_names(node.value, bindings, None)


def _ctor_target(node: ast.AST | None, imports: dict[str, str], module: str) -> str | None:
    """The constructor a value calls, such as Box() or module.Box(). Anything else is not one."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, (ast.Name, ast.Attribute)):
        return None
    target = _call_target(node.func, imports, module)
    if '.<unresolved>' in target:
        return None
    return target


def _pattern_bind(pattern: ast.AST, bindings: dict[str, str | None]) -> None:
    if isinstance(pattern, ast.MatchAs) and pattern.name:
        _note(bindings, pattern.name, None)
    for child in ast.iter_child_nodes(pattern):
        if isinstance(child, ast.pattern):
            _pattern_bind(child, bindings)


_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_COMP = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)


def _scan(node: ast.AST, bindings: dict[str, str | None], imports: dict[str, str], module: str) -> None:
    """Assignments in this scope. Nested functions, classes, and comprehensions have their own names."""
    if isinstance(node, _NESTED) or isinstance(node, _COMP):
        return
    if isinstance(node, ast.Assign):
        target = _ctor_target(node.value, imports, module)
        for item in node.targets:
            _bind_names(item, bindings, target)
    elif isinstance(node, ast.AnnAssign):
        _bind_names(node.target, bindings, _ctor_target(node.value, imports, module) if node.value is not None else None)
    elif isinstance(node, ast.AugAssign):
        _bind_names(node.target, bindings, None)
    elif isinstance(node, ast.NamedExpr):
        _bind_names(node.target, bindings, _ctor_target(node.value, imports, module))
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        _bind_names(node.target, bindings, None)
    elif isinstance(node, ast.With):
        for item in node.items:
            if item.optional_vars is not None:
                _bind_names(item.optional_vars, bindings, None)
    elif isinstance(node, ast.ExceptHandler) and node.name:
        _note(bindings, node.name, None)
    elif isinstance(node, ast.Match):
        for case in node.cases:
            _pattern_bind(case.pattern, bindings)
    for child in ast.iter_child_nodes(node):
        _scan(child, bindings, imports, module)


def _param_names(node: ast.AST) -> list[str]:
    args = getattr(node, 'args', None)
    if args is None:
        return []
    names = [item.arg for item in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    if args.vararg:
        names.append(args.vararg.arg)
    if args.kwarg:
        names.append(args.kwarg.arg)
    return names


def _scope_bindings(statements, imports: dict[str, str], module: str, params=()) -> dict[str, str | None]:
    """Names assigned only to one constructor. A parameter or a conflicting assignment is unknown."""
    bindings: dict[str, str | None] = {}
    for name in params:
        _note(bindings, name, None)
    for statement in statements:
        _scan(statement, bindings, imports, module)
    return bindings


def _comp_targets(node: ast.AST) -> dict[str, str | None]:
    frame: dict[str, str | None] = {}
    for gen in getattr(node, 'generators', ()):
        _bind_names(gen.target, frame, None)
    return frame


def bound_attribute_calls(tree: ast.AST, module: str) -> list[tuple[int, str, str, str, int]]:
    """Calls of the form name.method() where every assignment of name is one constructor.

    Each row is (line, receiver, qualified class, method, argument count). Keywords,
    stars, parameters, conflicting assignments, and comprehension variables are omitted.
    """
    imports = _python_imports(module, tree)
    module_bindings = _scope_bindings(tree.body, imports, module)
    found: list[tuple[int, str, str, str, int]] = []

    def merged(local: dict[str, str | None]) -> dict[str, str | None]:
        view = {key: value for key, value in module_bindings.items() if value}
        view.update(local)
        return view

    def collect(node: ast.AST, bindings: dict[str, str | None]) -> None:
        if isinstance(node, _NESTED):
            return
        if isinstance(node, _COMP):
            frame = dict(bindings)
            for gen in node.generators:
                _bind_names(gen.target, frame, None)
            if isinstance(node, ast.DictComp):
                collect(node.key, frame)
                collect(node.value, frame)
            else:
                collect(node.elt, frame)
            for gen in node.generators:
                collect(gen.iter, bindings)
                for item in gen.ifs:
                    collect(item, frame)
            return
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and not node.keywords
                and not any(isinstance(arg, ast.Starred) for arg in node.args)):
            ctor = bindings.get(node.func.value.id)
            if ctor:
                found.append((node.lineno, node.func.value.id, ctor, node.func.attr, len(node.args)))
        for child in ast.iter_child_nodes(node):
            collect(child, bindings)

    def walk(statements, local: dict[str, str | None]) -> None:
        bindings = merged(local)
        for stmt in statements:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(stmt.body, _scope_bindings(stmt.body, imports, module, _param_names(stmt)))
                continue
            if isinstance(stmt, ast.ClassDef):
                walk(stmt.body, {})
                continue
            collect(stmt, bindings)

    walk(tree.body, {})
    return found


def _python(path: str, source: str) -> dict:
    result = {'symbols': [], 'calls': [], 'references': [], 'strings': [], 'parser': 'python-ast', 'complete': True}
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return {**result, 'complete': False, 'diagnostic': str(exc)[:500]}
    scopes = []
    module = path.removesuffix('.py').replace('/', '.')
    imports = _python_imports(module, tree)
    module_bindings = _scope_bindings(tree.body, imports, module)

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.local = module_bindings
            self.comp: list[dict[str, str | None]] = []

        def resolve(self, name: str) -> str | None:
            for frame in reversed(self.comp):
                if name in frame:
                    return frame[name] or None
            if name in self.local:
                return self.local[name] or None
            if self.local is not module_bindings:
                return module_bindings.get(name) or None
            return None

        def definition(self, node, kind):
            name = '.'.join([module, *scopes, node.name])
            result['symbols'].append({'id': 'py:' + name, 'name': node.name, 'qualified': name,
                                      'kind': kind, 'path': path, 'line': node.lineno,
                                      'end_line': node.end_lineno, 'definition': True})
            scopes.append(node.name)
            if kind == 'function':
                previous = self.local
                self.local = _scope_bindings(node.body, imports, module, _param_names(node))
                self.generic_visit(node)
                self.local = previous
            else:
                self.generic_visit(node)
            scopes.pop()

        def visit_FunctionDef(self, node): self.definition(node, 'function')
        def visit_AsyncFunctionDef(self, node): self.definition(node, 'function')
        def visit_ClassDef(self, node): self.definition(node, 'struct')

        def visit_Lambda(self, node):
            self.comp.append({name: None for name in _param_names(node)})
            self.generic_visit(node)
            self.comp.pop()

        def _comp(self, node):
            self.comp.append(_comp_targets(node))
            self.generic_visit(node)
            self.comp.pop()

        def visit_ListComp(self, node): self._comp(node)
        def visit_SetComp(self, node): self._comp(node)
        def visit_GeneratorExp(self, node): self._comp(node)
        def visit_DictComp(self, node): self._comp(node)

        def visit_Call(self, node):
            name = ast.unparse(node.func)
            # A lexical binding is a candidate, never proof of dynamic dispatch.
            # box = Box(); box.save() names Box.save. box = None and a parameter stay on box.
            target = _call_target(node.func, imports, module, self.resolve)
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


def _blank_comments(source: str) -> str:
    """Blank comments and keep string text, so import paths stay readable."""
    out, i, n = [], 0, len(source)
    while i < n:
        c, nxt = source[i], source[i + 1] if i + 1 < n else ''
        if c == '/' and nxt == '/':
            while i < n and source[i] != '\n':
                out.append(' ')
                i += 1
            continue
        if c == '/' and nxt == '*':
            out.extend('  ')
            i += 2
            while i + 1 < n and not (source[i] == '*' and source[i + 1] == '/'):
                out.append('\n' if source[i] == '\n' else ' ')
                i += 1
            if i + 1 < n:
                out.extend('  ')
                i += 2
            continue
        if c in '"\'`':
            quote = c
            out.append(c)
            i += 1
            while i < n:
                out.append(source[i])
                if source[i] == '\\' and i + 1 < n:
                    out.append(source[i + 1])
                    i += 2
                    continue
                if source[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def mask_script(source: str, *, string_fill: str = ' ') -> str:
    """Blank comments and string contents so declaration scans keep line numbers.

    string_fill replaces each character inside a string. A space hides the text.
    Another character keeps a string argument visible to an argument counter.
    """
    fill = string_fill[:1] or ' '
    commented = _blank_comments(source)
    out, i, n = [], 0, len(commented)
    while i < n:
        c = commented[i]
        if c in '"\'`':
            quote = c
            out.append(fill)
            i += 1
            while i < n:
                if commented[i] == '\\':
                    out.append(fill)
                    if i + 1 < n:
                        out.append('\n' if commented[i + 1] == '\n' else fill)
                    i += 2
                    continue
                if commented[i] == quote:
                    out.append(fill)
                    i += 1
                    break
                out.append('\n' if commented[i] == '\n' else fill)
                i += 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def _inside(source: str, open_at: int) -> str | None:
    """Text between a parenthesis and its match, or None when the source is truncated."""
    if open_at >= len(source) or source[open_at] != '(':
        return None
    depth = 0
    for index in range(open_at, len(source)):
        char = source[index]
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                return source[open_at + 1:index]
    return None


def _plain_count(params: str | None) -> int | None:
    """A positional count, or None when defaults or rest arguments make it variable."""
    if params is None or '=' in params or '...' in params:
        return None
    text, depth, count = params.strip(), 0, 1
    if not text:
        return 0
    for char in text:
        if char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        elif char == ',' and depth == 0:
            count += 1
    return count


def _binding_names(block: str, *, exported: bool) -> list[str]:
    names = []
    for part in block.split(','):
        part = part.strip()
        if not part or part.startswith('type '):
            continue
        if ' as ' in part:
            local, alias = [piece.strip() for piece in part.split(' as ', 1)]
            token = alias if exported else local.split()[-1]
        else:
            token = part.split()[-1]
        if re.fullmatch(r'[A-Za-z_$][\w$]*', token) and token != 'type':
            names.append(token)
    return names


def _param_idents(text: str) -> list[str]:
    names = []
    for part in text.split(','):
        for token in re.findall(r'[A-Za-z_$][\w$]*', part):
            if token in {'public', 'private', 'protected', 'readonly', 'override'}:
                continue
            names.append(token)
            break
    return names


def script_classes(masked: str) -> tuple[dict[str, dict[str, int | None]], set[int], list[dict]]:
    """Class methods and the source offsets of their names.

    A method is a name( at the class body's own depth. A nested call is not a method.
    The count is None when a default or rest argument makes it variable.
    """
    methods: dict[str, dict[str, int | None]] = {}
    offsets: set[int] = set()
    records: list[dict] = []
    head = re.compile(r'(?:(?:public|private|protected|readonly|static|async|get|set|override|abstract)\s+|\*\s*)*([A-Za-z_$][\w$]*)\s*\(')
    for match in re.finditer(r'\bclass\s+([A-Za-z_$][\w$]*)', masked):
        class_name = match.group(1)
        brace = masked.find('{', match.end())
        if brace < 0 or brace > match.end() + 80:
            continue
        end = _brace_end(masked, brace)
        if end is None:
            continue
        depth, index = 1, brace + 1
        while index < end:
            char = masked[index]
            if char == '{':
                depth += 1
                index += 1
                continue
            if char == '}':
                depth -= 1
                index += 1
                continue
            if depth == 1:
                method = head.match(masked, index)
                if method and method.group(1) not in _SCRIPT_KEYWORDS:
                    name = method.group(1)
                    params = _inside(masked, method.end() - 1)
                    count = _plain_count(params)
                    methods.setdefault(class_name, {}).setdefault(name, count)
                    offsets.add(method.start(1))
                    records.append({'class': class_name, 'name': name, 'offset': method.start(1), 'count': count})
                    index = method.end()
                    continue
            index += 1
    return methods, offsets, records


def _brace_end(masked: str, open_at: int) -> int | None:
    if open_at >= len(masked) or masked[open_at] != '{':
        return None
    depth = 0
    for index in range(open_at, len(masked)):
        char = masked[index]
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return index
    return None


def script_bound_calls(path: str, source: str) -> list[dict]:
    """new Class().method() and name.method() when name is assigned only to new Class().

    A parameter, a for-loop variable, a second constructor, or any other assignment
    leaves the name unbound. The argument count is None when it is not fixed.
    """
    masked = mask_script(source)
    counted = mask_script(source, string_fill='x')
    classes, _offsets, _records = script_classes(masked)
    module = path.rsplit('.', 1)[0].replace('/', '.')
    imported: dict[str, str] = {}
    readable = _blank_comments(source)
    for match in re.finditer(r'\bimport\s*\{([^}]*)\}\s*from\s*[\'"](\.[^\'"]+)[\'"]', readable):
        owner = _imported_module(path, match.group(2))
        if not owner:
            continue
        for name in _binding_names(match.group(1), exported=False):
            imported.setdefault(name, owner)

    def qualify(class_name: str) -> str:
        owner = module if class_name in classes else imported.get(class_name, module)
        return owner + '.' + class_name

    # A function parameter applies inside that function, not to a different function's box.
    functions: list[tuple[int, int, set[str]]] = []
    for match in re.finditer(r'\bfunction\s*\*?\s*(?:[A-Za-z_$][\w$]*\s*)?\(', masked):
        params = _inside(masked, match.end() - 1) or ''
        brace = masked.find('{', match.end())
        end = _brace_end(masked, brace) if 0 <= brace <= match.end() + len(params) + 8 else None
        if end is None:
            continue
        functions.append((brace, end, set(_param_idents(params))))
    hidden = _method_params(masked)

    def scope_bindings(start: int, end: int, params: set[str], *, own: tuple[int, int] | None) -> tuple[dict[str, str], set[str]]:
        poison, news, new_at, declared = set(params), {}, set(), set(params)

        def contained(at: int) -> bool:
            if not (start <= at < end):
                return False
            for inner, outer, _names in functions:
                if inner <= at <= outer and (inner, outer) != own:
                    return False
            return True

        for match in re.finditer(r'(?:(?:const|let|var)\s+)?(?<![.\w$])([A-Za-z_$][\w$]*)\s*=\s*new\s+([A-Za-z_$][\w$]*)\s*\(', masked):
            if contained(match.start(1)):
                news.setdefault(match.group(1), set()).add(qualify(match.group(2)))
                new_at.add(match.start(1))
                declared.add(match.group(1))
        for match in re.finditer(r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\b', masked):
            if contained(match.start(1)) and match.start(1) not in new_at:
                declared.add(match.group(1))
                poison.add(match.group(1))
        for match in re.finditer(r'(?<![=!<>])([A-Za-z_$][\w$]*)\s*=(?!=)', masked):
            if contained(match.start(1)) and match.start(1) not in new_at:
                declared.add(match.group(1))
                poison.add(match.group(1))
        for match in re.finditer(r'\bcatch\s*\(\s*([A-Za-z_$][\w$]*)', masked):
            if contained(match.start(1)):
                poison.add(match.group(1))
                declared.add(match.group(1))
        for match in re.finditer(r'\bfor\s*\(\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)', masked):
            if contained(match.start(1)):
                poison.add(match.group(1))
                declared.add(match.group(1))
        for match in re.finditer(r'(?:\(([^)]*)\)|([A-Za-z_$][\w$]*))\s*=>', masked):
            if contained(match.start()):
                poison.update(_param_idents(match.group(1) or match.group(2) or ''))
                declared.update(_param_idents(match.group(1) or match.group(2) or ''))
        bound = {name: next(iter(owners)) for name, owners in news.items() if name not in poison and len(owners) == 1}
        return bound, declared

    module_bindings, module_declared = scope_bindings(0, len(masked), set(), own=None)
    local_facts = {(start, end): scope_bindings(start, end, params, own=(start, end)) for start, end, params in functions}

    def resolve(at: int, name: str) -> str | None:
        if name in hidden:
            return None
        chosen = None
        for start, end, _params in functions:
            if start <= at <= end and (chosen is None or end - start < chosen[1] - chosen[0]):
                chosen = (start, end)
        if chosen:
            bound, declared = local_facts[chosen]
            if name in declared:
                return bound.get(name)
        if name in module_declared or name in module_bindings:
            return module_bindings.get(name)
        return None

    found = []
    for match in re.finditer(r'\bnew\s+([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*\.\s*([A-Za-z_$][\w$]*)\s*\(', masked):
        arguments = _plain_count(_inside(counted, match.end() - 1))
        found.append({'line': masked.count('\n', 0, match.start(2)) + 1, 'receiver': '',
                      'owner': qualify(match.group(1)).rsplit('.', 1)[0], 'class_name': match.group(1),
                      'method': match.group(2), 'arguments': arguments,
                      'target': 'js:' + qualify(match.group(1)) + '.' + match.group(2)})
    for match in re.finditer(r'(?<![\w$])([A-Za-z_$][\w$]*)\s*\.\s*([A-Za-z_$][\w$]*)\s*\(', masked):
        qualified = resolve(match.start(1), match.group(1))
        if not qualified or match.group(2) in _SCRIPT_KEYWORDS:
            continue
        arguments = _plain_count(_inside(counted, match.end() - 1))
        owner, class_name = qualified.rsplit('.', 1)
        found.append({'line': masked.count('\n', 0, match.start(2)) + 1, 'receiver': match.group(1),
                      'owner': owner, 'class_name': class_name, 'method': match.group(2),
                      'arguments': arguments, 'target': 'js:' + qualified + '.' + match.group(2)})
    return found


def _method_params(masked: str) -> set[str]:
    """Parameter names of class methods. They are not constructor bindings."""
    names: set[str] = set()
    _classes, offsets, _records = script_classes(masked)
    if not offsets:
        return names
    for match in re.finditer(r'([A-Za-z_$][\w$]*)\s*\(', masked):
        if match.start(1) not in offsets:
            continue
        params = _inside(masked, match.end() - 1)
        if params:
            names.update(_param_idents(params))
    return names


def script_surface(source: str) -> dict:
    """Lexical exports, relative named imports, and call names. Not a type check."""
    readable = _blank_comments(source)
    masked = mask_script(source)
    exports, imports, functions = [], [], {}
    for match in re.finditer(r'\b(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(', masked):
        count = _plain_count(_inside(masked, match.end() - 1))
        if count is not None:
            functions.setdefault(match.group(1), count)
    for match in re.finditer(r'\bexport\s+(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)', masked):
        exports.append(match.group(1))
    for match in re.finditer(r'\bexport\s+(?:default\s+)?class\s+([A-Za-z_$][\w$]*)', masked):
        exports.append(match.group(1))
    for match in re.finditer(r'\bexport\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)', masked):
        exports.append(match.group(1))
    for match in re.finditer(r'\bexport\s*\{([^}]*)\}', masked):
        exports.extend(_binding_names(match.group(1), exported=True))
    for match in re.finditer(r'\bimport\s*\{([^}]*)\}\s*from\s*[\'"](\.[^\'"]+)[\'"]', readable):
        imports.append({'names': _binding_names(match.group(1), exported=False), 'spec': match.group(2),
                        'line': masked.count('\n', 0, match.start()) + 1})
    return {'exports': list(dict.fromkeys(exports)), 'imports': [item for item in imports if item['names']],
            'functions': functions, 'methods': script_classes(masked)[0],
            'reexports_all': bool(re.search(r'\bexport\s*\*\s*from\b', readable)), 'masked': masked}


def _imported_module(path: str, spec: str) -> str | None:
    """Module id for a relative import spec, or None when it leaves the tree."""
    if not spec.startswith('.'):
        return None
    parts: list[str] = []
    for part in Path(path).parent.joinpath(spec).as_posix().split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        return None
    stem = parts[-1]
    for suffix in ('.mjs', '.cjs', '.jsx', '.tsx', '.js', '.ts'):
        if stem.endswith(suffix):
            parts[-1] = stem[:-len(suffix)]
            break
    return '.'.join(parts) if parts[-1] else None


def _script(path: str, source: str) -> dict:
    result = {'symbols': [], 'calls': [], 'references': [], 'strings': [], 'parser': 'script-lexical', 'complete': True}
    surface = script_surface(source)
    masked = surface['masked']
    module = path.rsplit('.', 1)[0].replace('/', '.')
    imported = {}
    for item in surface['imports']:
        module_name = _imported_module(path, item['spec'])
        if module_name:
            for binding in item['names']:
                imported.setdefault(binding, module_name)
    seen = set()
    defined_at = set()
    for kind, pattern in (('function', r'\b(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\('),
                          ('struct', r'\bclass\s+([A-Za-z_$][\w$]*)')):
        for match in re.finditer(pattern, masked):
            name = match.group(1)
            defined_at.add(match.start(1))
            if name in seen:
                continue
            seen.add(name)
            line = masked.count('\n', 0, match.start(1)) + 1
            result['symbols'].append({'id': 'js:' + module + '.' + name, 'name': name, 'qualified': module + '.' + name,
                                      'kind': kind, 'path': path, 'line': line, 'end_line': line, 'definition': True})
    classes, method_at, methods = script_classes(masked)
    for method in methods:
        line = masked.count('\n', 0, method['offset']) + 1
        class_name, name = method['class'], method['name']
        result['symbols'].append({'id': 'js:' + module + '.' + class_name + '.' + name, 'name': name,
                                  'qualified': module + '.' + class_name + '.' + name, 'kind': 'function',
                                  'path': path, 'line': line, 'end_line': line, 'definition': True})
    defined_at.update(method_at)
    for match in re.finditer(r'\b([A-Za-z_$][\w$]*)\s*\(', masked):
        name = match.group(1)
        if name in _SCRIPT_KEYWORDS or match.start(1) in defined_at:
            continue
        previous = masked[:match.start(1)].rstrip()
        if previous.endswith('.') or previous.endswith('?.'):
            continue
        line = masked.count('\n', 0, match.start(1)) + 1
        # A local function keeps the call. An imported name points at the other file.
        owner = module if name in seen else imported.get(name, module)
        result['calls'].append({'name': name, 'target': 'js:' + owner + '.' + name, 'caller': 'js:' + module,
                                 'path': path, 'line': line, 'resolution': 'lexical_candidate', 'kind': 'call'})
    for call in script_bound_calls(path, source):
        result['calls'].append({'name': call['method'], 'target': call['target'], 'caller': 'js:' + module,
                                 'path': path, 'line': call['line'], 'resolution': 'lexical_candidate', 'kind': 'call'})
    for item in surface['imports']:
        for name in item['names']:
            result['references'].append({'name': name, 'path': path, 'line': item['line'], 'access': 'import',
                                         'caller': 'js:' + module, 'spec': item['spec']})
    result['exports'] = surface['exports']
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

    def load(self, project: Path) -> dict | None:
        """The saved snapshot for this project, or None when it was never indexed."""
        key = str(Path(project).resolve())
        with self.lock:
            with self._db() as db:
                row = db.execute('SELECT content FROM snapshots WHERE root=?', (key,)).fetchone()
        if not row:
            return None
        try:
            data = json.loads(row[0])
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

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
        reusable = previous.get('schema') == SCHEMA
        compiler = find_tool('clang')
        parser_id = str(SCHEMA) + ':' + str(compiler)
        if compiler:
            info = Path(compiler).stat()
            parser_id += ':' + str(info.st_size) + ':' + str(info.st_mtime_ns)
        headers_changed = (previous.get('parser_id') != parser_id or
            {p: h for p, h in hashes.items() if Path(p).suffix.lower() in HEADERS} !=
            {p: h for p, h in previous.get('hashes', {}).items() if Path(p).suffix.lower() in HEADERS})
        units = {}
        reused = parsed = 0
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
                suffix = Path(path).suffix.lower()
                if unsafe and suffix not in {'.py', *SCRIPT_EXTENSIONS}:
                    target.write_text('#error Unsupported file-dependent preprocessing\n', encoding='utf-8')
                else:
                    target.write_bytes(raw)
            for path, raw in files.items():
                prior = previous.get('units', {}).get(path)
                changed = previous.get('hashes', {}).get(path) != hashes[path]
                suffix = Path(path).suffix.lower()
                independent = suffix == '.py' or suffix in SCRIPT_EXTENSIONS
                if reusable and prior and prior.get('complete') and not changed and (independent or not headers_changed):
                    units[path] = prior
                    reused += 1
                    continue
                if time.monotonic() - started >= budget or (cancel is not None and cancel.is_set()):
                    omitted.append({'path': path, 'reason': 'Index time budget or cancellation'}); continue
                text = raw.decode('utf-8')
                parsed += 1
                if suffix == '.py':
                    units[path] = _python(path, text)
                elif suffix in SCRIPT_EXTENSIONS:
                    units[path] = _script(path, text)
                else:
                    units[path] = _clang(path, snapshot, files, timeout=min(2, budget - (time.monotonic() - started)), cancel=cancel)
        commit = None
        try:
            cp = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=2)
            if cp.returncode == 0: commit = cp.stdout.strip()
        except (OSError, subprocess.TimeoutExpired): pass
        result = {'schema': SCHEMA, 'root': key, 'commit': commit, 'hashes': hashes, 'parser_id': parser_id,
                  'units': units, 'reused': reused, 'parsed': parsed, 'omitted': omitted[:500], 'indexed_at': time.time(),
                  'complete': not omitted and all(u['complete'] for u in units.values()),
                  'scope': 'Static source snapshot. File hashes identify working-copy content; commit alone does not prove a clean tree. C++ virtual dispatch and Python calls are not runtime traces. JavaScript and TypeScript names are lexical, not resolved bindings. References are not interprocedural data-flow proof.'}
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
