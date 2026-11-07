"""Named imports must name something the target file actually exports.

A test suite can pass while a sibling module imports a name that was renamed.
This check reads the files. It does not execute them and it does not guess
through `export *`.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from takumi.source_index import _inside, _plain_count, bound_attribute_calls, mask_script, script_bound_calls, script_surface

from . import platforms
from .diagnostics import Diagnostic

_SCRIPT = {'.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx'}
_SUFFIXES = ('.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx')


def _resolve(project: Path, importer: str, spec: str) -> Path | None:
    if not spec.startswith('.') or '?' in spec or '#' in spec:
        return None
    base = (project / importer).parent / spec
    candidates = [base] if base.suffix else [base.with_suffix(ext) for ext in _SUFFIXES]
    if not base.suffix:
        candidates += [base / ('index' + ext) for ext in _SUFFIXES]
    root = project.resolve()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    return None


def import_diagnostics(project: Path) -> list[Diagnostic]:
    project = Path(project)
    found: list[Diagnostic] = []
    seen: set[tuple] = set()
    for item in platforms._walk(project):
        if item.suffix.lower() not in _SCRIPT:
            continue
        path = project / item
        try:
            if path.stat().st_size > 200_000:
                continue
            source = path.read_text('utf-8', 'replace')
        except OSError:
            continue
        surface = script_surface(source)
        for binding in surface['imports']:
            target = _resolve(project, item.as_posix(), binding['spec'])
            if target is None:
                continue
            try:
                if target.stat().st_size > 200_000:
                    continue
                exported = script_surface(target.read_text('utf-8', 'replace'))
            except OSError:
                continue
            if exported['reexports_all']:
                continue
            available = set(exported['exports'])
            rel = target.relative_to(project).as_posix()
            for name in binding['names']:
                marker = (item.as_posix(), binding['line'], name, rel)
                if name in available or marker in seen:
                    continue
                seen.add(marker)
                found.append(Diagnostic(
                    'imports', 'error',
                    f'{item.as_posix()} imports {{{name}}} from {binding["spec"]}, but {rel} does not export it.',
                    item.as_posix(), binding['line'], source='static'))
    return found


def script_call_diagnostics(project: Path) -> list[Diagnostic]:
    """A named import called with a different number of plain arguments than its function declares."""
    project = Path(project)
    declared: dict[str, dict[str, int]] = {}
    files: list[tuple[Path, str, dict]] = []
    for item in platforms._walk(project):
        if item.suffix.lower() not in _SCRIPT:
            continue
        path = project / item
        try:
            if path.stat().st_size > 200_000:
                continue
            source = path.read_text('utf-8', 'replace')
        except OSError:
            continue
        surface = script_surface(source)
        rel = item.as_posix()
        declared[rel] = {name: surface['functions'][name] for name in surface['exports'] if name in surface['functions']}
        files.append((rel, surface, source))
    found: list[Diagnostic] = []
    seen: set[tuple] = set()
    modules = {rel.rsplit('.', 1)[0].replace('/', '.'): (rel, surface['methods']) for rel, surface, _source in files}
    for rel, surface, source in files:
        masked = mask_script(source, string_fill='x')
        local = set(surface['functions'])
        for binding in surface['imports']:
            resolved = _resolve(project, rel, binding['spec'])
            if resolved is None:
                continue
            try:
                target_rel = resolved.relative_to(project).as_posix()
            except ValueError:
                target_rel = resolved.relative_to(project.resolve()).as_posix()
            table = declared.get(target_rel, {})
            for name in binding['names']:
                if name in local or name not in table:
                    continue
                for match in re.finditer(r'(?<![\w$.])' + re.escape(name) + r'\s*\(', masked):
                    before = masked[max(0, match.start() - 24):match.start()]
                    if re.search(r'\b(?:async\s+)?function\s*\*?\s*$', before) or re.search(r'\bnew\s*$', before):
                        continue
                    count = _plain_count(_inside(masked, match.end() - 1))
                    if count is None or count == table[name]:
                        continue
                    line = masked.count('\n', 0, match.start()) + 1
                    marker = (rel, line, name)
                    if marker in seen:
                        continue
                    seen.add(marker)
                    found.append(Diagnostic(
                        'imports', 'error',
                        f'{rel} calls {name} with {count} argument(s), but {target_rel} defines it with {table[name]}.',
                        rel, line, source='static'))
        for call in script_bound_calls(rel, source):
            owner = modules.get(call['owner'])
            table = owner[1].get(call['class_name']) if owner else None
            if table is None:
                continue
            label = f'{call["receiver"]}.{call["method"]}' if call['receiver'] else f'new {call["class_name"]}().{call["method"]}'
            if call['method'] not in table:
                marker = (rel, call['line'], call['class_name'], call['method'], 'missing')
                if marker in seen:
                    continue
                seen.add(marker)
                found.append(Diagnostic(
                    'imports', 'error',
                    f'{rel} calls {label}, but {owner[0]} does not define {call["class_name"]}.{call["method"]}.',
                    rel, call['line'], source='static'))
                continue
            expect = table[call['method']]
            if expect is None or call['arguments'] is None or call['arguments'] == expect:
                continue
            marker = (rel, call['line'], call['class_name'], call['method'], 'arity')
            if marker in seen:
                continue
            seen.add(marker)
            found.append(Diagnostic(
                'imports', 'error',
                f'{rel} calls {label} with {call["arguments"]} argument(s), but {owner[0]} defines {call["class_name"]}.{call["method"]} with {expect}.',
                rel, call['line'], source='static'))
    return found


def _arity(node: ast.FunctionDef | ast.AsyncFunctionDef, *, bound: bool = False) -> tuple[int, int] | None:
    """Required and maximum positional arguments, or None when the count is not fixed.

    A bound method does not count self or cls. The caller passes the remaining arguments.
    """
    args = node.args
    if args.vararg or args.kwarg or any(default is None for default in args.kw_defaults):
        return None
    positional = args.posonlyargs + args.args
    if bound:
        if not positional or positional[0].arg not in ('self', 'cls'):
            return None
        positional = positional[1:]
    defaults = args.defaults[-len(positional):] if positional else []
    required = len(positional) - len(defaults)
    return required, len(positional)


def _functions(path: Path) -> dict[str, tuple[int, int]] | None:
    try:
        tree = ast.parse(path.read_text('utf-8', 'replace'))
    except (OSError, SyntaxError, ValueError, RecursionError):
        return None
    if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == '__getattr__' for node in tree.body):
        return None
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arity = _arity(node)
            if arity:
                found[node.name] = arity
    return found


def _plain_class(node: ast.ClassDef) -> bool:
    if not node.bases:
        return True
    return len(node.bases) == 1 and isinstance(node.bases[0], ast.Name) and node.bases[0].id == 'object'


def _methods(path: Path) -> dict[str, dict[str, tuple[int, int] | None]] | None:
    """Methods of module-level classes that do not inherit. None means the file was not read."""
    try:
        tree = ast.parse(path.read_text('utf-8', 'replace'))
    except (OSError, SyntaxError, ValueError, RecursionError):
        return None
    if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == '__getattr__' for node in tree.body):
        return None
    found: dict[str, dict[str, tuple[int, int] | None]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or not _plain_class(node):
            continue
        if any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == '__getattr__' for item in node.body):
            continue
        methods: dict[str, tuple[int, int] | None] = {}
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods[item.name] = _arity(item, bound=True)
        found[node.name] = methods
    return found


def _constructor_method(node: ast.Call):
    """Class().method or module.Class().method. The constructor's own arguments are ignored."""
    func = node.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Call):
        return None
    ctor = func.value.func
    if isinstance(ctor, ast.Name):
        return ('imported', ctor.id, func.attr)
    if isinstance(ctor, ast.Attribute) and isinstance(ctor.value, ast.Name):
        return ('module', ctor.value.id, ctor.attr, func.attr)
    return None


def _defines(path: Path) -> set[str] | None:
    try:
        tree = ast.parse(path.read_text('utf-8', 'replace'))
    except (OSError, SyntaxError, ValueError, RecursionError):
        return None
    if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == '__getattr__' for node in tree.body):
        return None
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != '*':
                    names.add(alias.asname or alias.name.split('.')[0])
    return names


def _python_module(project: Path, importer: Path, module: str, level: int) -> Path | None:
    if level <= 0:
        base = project.joinpath(*module.split('.')) if module else None
    else:
        base = importer.parent
        for _ in range(level - 1):
            base = base.parent
        if module:
            base = base.joinpath(*module.split('.'))
    if base is None:
        return None
    candidate = base.with_suffix('.py')
    try:
        candidate.resolve().relative_to(project.resolve())
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def python_interface_diagnostics(project: Path) -> list[Diagnostic]:
    """A project file that imports or calls a missing name in another project file.

    Modules outside the project are left alone. A package, a syntax error, or
    `__getattr__` is not guessed.
    """
    project = Path(project)
    found: list[Diagnostic] = []
    seen: set[tuple] = set()
    for item in platforms._walk(project):
        if item.suffix != '.py':
            continue
        path = project / item
        try:
            if path.stat().st_size > 200_000:
                continue
            tree = ast.parse(path.read_text('utf-8', 'replace'))
        except (OSError, SyntaxError, ValueError, RecursionError):
            continue
        aliases: dict[str, str] = {}
        imported: dict[str, tuple[Path, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if '.' not in alias.name:
                        aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom):
                if node.module is None or any(alias.name == '*' for alias in node.names):
                    continue
                target = _python_module(project, path, node.module, node.level)
                defined = _defines(target) if target else None
                if defined is None:
                    continue
                for alias in node.names:
                    marker = (item.as_posix(), node.lineno, alias.name)
                    if alias.name not in defined and marker not in seen:
                        seen.add(marker)
                        found.append(Diagnostic(
                            'imports', 'error',
                            f'{item.as_posix()} imports {alias.name} from {node.module}, but {target.name} does not define it.',
                            item.as_posix(), node.lineno, source='static'))
                    elif alias.name in defined:
                        imported[alias.asname or alias.name] = (target, alias.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if node.keywords or any(isinstance(arg, ast.Starred) for arg in node.args):
                continue
            target = original = None
            label = ''
            constructed = _constructor_method(node)
            if constructed:
                if constructed[0] == 'module':
                    _, alias, class_name, method = constructed
                    module = aliases.get(alias)
                    target = _python_module(project, path, module, 0) if module else None
                    label = f'{alias}.{class_name}().{method}'
                else:
                    _, local, method = constructed
                    class_name = ''
                    if local in imported:
                        target, class_name = imported[local]
                        label = f'{local}().{method}'
                if target is not None and label:
                    methods = _methods(target)
                    table = (methods or {}).get(class_name) if methods is not None else None
                    if table is not None and method not in table:
                        marker = (item.as_posix(), node.lineno, class_name, method, 'missing')
                        if marker not in seen:
                            seen.add(marker)
                            found.append(Diagnostic(
                                'imports', 'error',
                                f'{item.as_posix()} calls {label}, but {target.name} does not define {class_name}.{method}.',
                                item.as_posix(), node.lineno, source='static'))
                    elif table is not None and table[method] and (len(node.args) < table[method][0] or len(node.args) > table[method][1]):
                        low, high = table[method]
                        expect = 'no parameters' if high == 0 else f'{low} parameter(s)' if low == high else f'{low} to {high} parameters'
                        marker = (item.as_posix(), node.lineno, class_name, method, 'arity')
                        if marker not in seen:
                            seen.add(marker)
                            found.append(Diagnostic(
                                'imports', 'error',
                                f'{item.as_posix()} calls {label} with {len(node.args)} argument(s), but {target.name} defines {class_name}.{method} with {expect}.',
                                item.as_posix(), node.lineno, source='static'))
                continue
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                module = aliases.get(node.func.value.id)
                if not module:
                    continue
                target = _python_module(project, path, module, 0)
                original = node.func.attr
                label = f'{node.func.value.id}.{original}'
            elif isinstance(node.func, ast.Name) and node.func.id in imported:
                target, original = imported[node.func.id]
                label = node.func.id
            else:
                continue
            if target is None or not original:
                continue
            defined = _defines(target)
            functions = _functions(target)
            if defined is None:
                continue
            if original not in defined:
                marker = (item.as_posix(), node.lineno, original, 'missing')
                if marker in seen:
                    continue
                seen.add(marker)
                found.append(Diagnostic(
                    'imports', 'error',
                    f'{item.as_posix()} calls {label}, but {target.name} does not define it.',
                    item.as_posix(), node.lineno, source='static'))
                continue
            arity = (functions or {}).get(original)
            if not arity or not (len(node.args) < arity[0] or len(node.args) > arity[1]):
                continue
            low, high = arity
            expect = 'no parameters' if high == 0 else f'{low} parameter(s)' if low == high else f'{low} to {high} parameters'
            marker = (item.as_posix(), node.lineno, original, 'arity')
            if marker in seen:
                continue
            seen.add(marker)
            found.append(Diagnostic(
                'imports', 'error',
                f'{item.as_posix()} calls {label} with {len(node.args)} argument(s), but {target.name} defines it with {expect}.',
                item.as_posix(), node.lineno, source='static'))
        module_name = item.as_posix().removesuffix('.py').replace('/', '.')
        for line, receiver, ctor, method, nargs in bound_attribute_calls(tree, module_name):
            class_name = ctor.rsplit('.', 1)[-1]
            owner = ctor.rsplit('.', 1)[0]
            target = path if owner == module_name else _python_module(project, path, owner, 0)
            methods = _methods(target) if target is not None else None
            table = (methods or {}).get(class_name) if methods is not None else None
            if table is None:
                continue
            label = f'{receiver}.{method}'
            if method not in table:
                marker = (item.as_posix(), line, receiver, class_name, method, 'missing')
                if marker in seen:
                    continue
                seen.add(marker)
                found.append(Diagnostic(
                    'imports', 'error',
                    f'{item.as_posix()} calls {label}, but {target.name} does not define {class_name}.{method}.',
                    item.as_posix(), line, source='static'))
                continue
            arity = table[method]
            if not arity or not (nargs < arity[0] or nargs > arity[1]):
                continue
            low, high = arity
            expect = 'no parameters' if high == 0 else f'{low} parameter(s)' if low == high else f'{low} to {high} parameters'
            marker = (item.as_posix(), line, receiver, class_name, method, 'arity')
            if marker in seen:
                continue
            seen.add(marker)
            found.append(Diagnostic(
                'imports', 'error',
                f'{item.as_posix()} calls {label} with {nargs} argument(s), but {target.name} defines {class_name}.{method} with {expect}.',
                item.as_posix(), line, source='static'))
    return found


_GO_BUILTIN = {'append', 'cap', 'clear', 'close', 'complex', 'copy', 'delete', 'imag', 'len', 'make', 'max', 'min', 'new',
               'panic', 'print', 'println', 'real', 'recover'}


def _mask_go(source: str) -> str:
    """Blank strings and comments without moving newlines, so call sites keep their lines."""
    out: list[str] = []
    index, size = 0, len(source)
    while index < size:
        if source.startswith('//', index):
            end = source.find('\n', index)
            end = size if end < 0 else end
            out.append(' ' * (end - index))
            index = end
            continue
        if source.startswith('/*', index):
            end = source.find('*/', index + 2)
            end = size if end < 0 else end + 2
            chunk = source[index:end]
            out.append(''.join(' ' if char != '\n' else '\n' for char in chunk))
            index = end
            continue
        if source[index] in '"\'`':
            quote = source[index]
            out.append(quote)
            index += 1
            while index < size:
                if quote != '`' and source[index] == '\\':
                    out.append('  ')
                    index += 2
                    continue
                if source[index] == quote:
                    out.append(quote)
                    index += 1
                    break
                out.append('\n' if source[index] == '\n' else ' ')
                index += 1
            continue
        out.append(source[index])
        index += 1
    return ''.join(out)


def _match_paren(source: str, open_at: int) -> int | None:
    depth = 0
    for index in range(open_at, len(source)):
        if source[index] == '(':
            depth += 1
        elif source[index] == ')':
            depth -= 1
            if depth == 0:
                return index
    return None


def _param_bounds(params: str) -> tuple[int, int]:
    """Plain Go parameters. A final `...` accepts that argument or more."""
    parts, depth, buf = [], 0, []
    for char in params:
        if char in '([{':
            depth += 1
            buf.append(char)
        elif char in ')]}':
            depth = max(0, depth - 1)
            buf.append(char)
        elif char == ',' and depth == 0:
            parts.append(''.join(buf))
            buf = []
        else:
            buf.append(char)
    parts.append(''.join(buf))
    fields = [part for part in parts if part.strip()]
    if not fields:
        return (0, 0)
    if '...' in fields[-1]:
        return (len(fields) - 1, 99)
    return (len(fields), len(fields))


def _receiver_type(text: str) -> str:
    parts = text.replace('*', ' ').split()
    if not parts:
        return ''
    name = parts[-1]
    if '.' in name or not name.isidentifier():
        return ''
    return name


def _go_signatures(source: str) -> list[tuple[str, str, tuple[int, int], int, str]]:
    found = []
    for match in re.finditer(r'(?m)^func\b', source):
        index = match.end()
        while index < len(source) and source[index] in ' \t':
            index += 1
        receiver = ''
        if index < len(source) and source[index] == '(':
            close = _match_paren(source, index)
            if close is None:
                continue
            receiver = _receiver_type(source[index + 1:close])
            index = close + 1
            while index < len(source) and source[index] in ' \t\n':
                index += 1
        name_match = re.match(r'(\w+)', source[index:])
        if not name_match:
            continue
        index += name_match.end()
        while index < len(source) and source[index] in ' \t\n':
            index += 1
        if index >= len(source) or source[index] != '(':
            continue
        close = _match_paren(source, index)
        if close is None:
            continue
        line = source.count('\n', 0, match.start()) + 1
        params = ' '.join(source[index + 1:close].split())[:80]
        found.append((name_match.group(1), receiver, _param_bounds(source[index + 1:close]), line, params))
    return found


def _arg_count(masked: str, open_at: int) -> int | None:
    depth, commas, any_token = 0, 0, False
    index = open_at
    while index < len(masked):
        char = masked[index]
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                return commas + 1 if any_token else 0
        elif depth == 1 and masked.startswith('...', index):
            return None
        elif depth == 1 and char == ',':
            commas += 1
        elif depth == 1 and not char.isspace():
            any_token = True
        index += 1
    return None


def _expect(bounds: tuple[int, int], count: int) -> str | None:
    low, high = bounds
    if low <= count <= high:
        return None
    if high >= 99:
        return f'at least {low} parameter(s)'
    if high == 0:
        return 'no parameters'
    if low == high:
        return f'{low} parameter(s)'
    return f'{low} to {high} parameters'


def go_interface_diagnostics(project: Path) -> list[Diagnostic]:
    """A Go call the same package can already answer: a missing function, or a method on a value built here.

    Another package, a method on an interface, and a value whose type is assigned more than once are left alone.
    """
    project = Path(project)
    files: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for item in platforms._walk(project):
        if item.suffix != '.go':
            continue
        path = project / item
        try:
            if path.stat().st_size > 200_000:
                continue
            source = path.read_text('utf-8', 'replace')
        except OSError:
            continue
        package = ''
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith('package '):
                package = stripped.split()[1] if len(stripped.split()) > 1 else ''
                break
        if not package:
            continue
        files.setdefault((item.parent.as_posix(), package), []).append((item.as_posix(), source))
    found: list[Diagnostic] = []
    seen: set[tuple] = set()
    for (_folder, package), members in files.items():
        functions: dict[str, tuple[int, int] | None] = {}
        methods: dict[str, dict[str, tuple[int, int] | None]] = {}
        defined_in: dict[tuple[str, str], str] = {}
        signatures: dict[tuple[str, str], str] = {}
        for rel, source in members:
            for name, receiver, bounds, _line, params in _go_signatures(source):
                if receiver:
                    table = methods.setdefault(receiver, {})
                    if name in table and table[name] != bounds:
                        table[name] = None
                    else:
                        table.setdefault(name, bounds)
                    defined_in.setdefault((receiver, name), rel)
                    signatures.setdefault((receiver, name), params)
                else:
                    if name in functions and functions[name] != bounds:
                        functions[name] = None
                    else:
                        functions.setdefault(name, bounds)
                    defined_in.setdefault(('', name), rel)
        known = set(methods)
        for rel, source in members:
            masked = _mask_go(source)
            bindings: dict[str, set[str]] = {}
            for match in re.finditer(r'(?<![\w.])(\w+)\s*:?=\s*&?(\w+)\s*\{', masked):
                bindings.setdefault(match.group(1), set()).add(match.group(2))
            bound = {name: next(iter(types)) for name, types in bindings.items() if len(types) == 1 and next(iter(types)) in known}

            def report(marker: tuple, message: str, line: int) -> None:
                if marker in seen:
                    return
                seen.add(marker)
                found.append(Diagnostic('imports', 'error', message, rel, line, source='static'))

            for match in re.finditer(r'&?(\w+)\s*\{', masked):
                type_name = match.group(1)
                if type_name not in known:
                    continue
                brace = masked.find('{', match.start())
                close = _match_brace(masked, brace)
                if close is None:
                    continue
                tail = masked[close + 1:]
                call = re.match(r'\s*\)?\s*\.\s*(\w+)\s*\(', tail)
                if not call:
                    continue
                method = call.group(1)
                open_at = close + 1 + call.end() - 1
                line = masked.count('\n', 0, match.start()) + 1
                _check_method(report, rel, package, type_name, method, f'{type_name}{{}}.{method}', open_at, masked, methods, defined_in, signatures)
            for match in re.finditer(r'(?<![\w.])(\w+)\.(\w+)\s*\(', masked):
                receiver, method = match.group(1), match.group(2)
                if receiver not in bound:
                    continue
                open_at = match.end() - 1
                _check_method(report, rel, package, bound[receiver], method, f'{receiver}.{method}', open_at, masked, methods, defined_in, signatures)
            for match in re.finditer(r'(?<![\w.])([A-Z]\w*)\s*\(', masked):
                name = match.group(1)
                if name in _GO_BUILTIN or name in known:
                    continue
                before = masked[max(0, match.start() - 80):match.start()]
                if re.search(r'\bfunc\s*(?:\([^)]*\)\s*)?$', before):
                    continue
                line = masked.count('\n', 0, match.start()) + 1
                count = _arg_count(masked, match.end() - 1)
                if name not in functions:
                    if re.search(r'(?<![\w.])' + re.escape(name) + r'\s*:?=', masked):
                        continue
                    report((rel, line, name, 'missing'), f'{rel} calls {name}, but package {package} does not define it.', line)
                    continue
                bounds = functions[name]
                if bounds is None or count is None:
                    continue
                expect = _expect(bounds, count)
                if not expect:
                    continue
                owner = defined_in.get(('', name), 'the package')
                report((rel, line, name, 'arity'),
                       f'{rel} calls {name} with {count} argument(s), but {owner} defines it with {expect}.', line)
    return found


def _match_brace(source: str, open_at: int) -> int | None:
    depth = 0
    for index in range(open_at, len(source)):
        if source[index] == '{':
            depth += 1
        elif source[index] == '}':
            depth -= 1
            if depth == 0:
                return index
    return None


def _methods_already_defined(type_name: str, table: dict, signatures: dict) -> str:
    shown = [f'{name}({signatures.get((type_name, name), "")})' for name in sorted(table)[:6]]
    if not shown:
        return f'{type_name} defines no methods.'
    more = f' and {len(table) - 6} more' if len(table) > 6 else ''
    return f'{type_name} defines ' + ', '.join(shown) + more + '.'


def _check_method(report, rel, package, type_name, method, label, open_at, masked, methods, defined_in, signatures) -> None:
    line = masked.count('\n', 0, open_at) + 1
    table = methods.get(type_name) or {}
    if method not in table:
        have = _methods_already_defined(type_name, table, signatures)
        report((rel, line, type_name, method, 'missing'),
               f'{rel} calls {label}, but package {package} does not define {type_name}.{method}. {have}', line)
        return
    bounds = table[method]
    count = _arg_count(masked, open_at)
    if bounds is None or count is None:
        return
    expect = _expect(bounds, count)
    if not expect:
        return
    owner = defined_in.get((type_name, method), 'the package')
    report((rel, line, type_name, method, 'arity'),
           f'{rel} calls {label} with {count} argument(s), but {owner} defines {type_name}.{method} with {expect}.', line)


def interface_diagnostics(project: Path) -> list[Diagnostic]:
    return (import_diagnostics(project) + script_call_diagnostics(project) + python_interface_diagnostics(project)
            + go_interface_diagnostics(project))
