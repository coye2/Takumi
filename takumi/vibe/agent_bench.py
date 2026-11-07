"""Measurements of the agent system. No model and no GPU.

    python -m takumi.vibe.agent_bench
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from takumi.research import should_research
from takumi.vibe import repomap
from takumi.vibe.coherence import python_interface_diagnostics
from takumi.vibe.diagnostics import Diagnostic
from takumi.source_index import SourceIndex
from takumi.vibe.context import REI_BUDGET, build_messages, select_files, symbol_anchors
from takumi.vibe.context import Budget
from takumi.vibe.edits import Edit, Journal, apply


def _retrieval(root: Path) -> dict:
    for i in range(10):
        (root / f'notes{i}.py').write_text('def update_status():\n    return "invoice invoice invoice"\n', encoding='utf-8')
    (root / 'invoice.py').write_text('def save_invoice():\n    return 1\n', encoding='utf-8')
    (root / 'checkout.py').write_text('def pay():\n    return save_invoice()\n', encoding='utf-8')
    ranked = repomap.build(root, task='save_invoice drops the tax')
    selected = select_files(root, 'save_invoice drops the tax', [], [], Budget())
    chosen = [name for name, _ in selected['files']]
    top = ranked['files'][:3]
    return {'top3': top, 'definition_first': ranked['files'][0] == 'invoice.py',
            'caller_second': ranked['files'][1] == 'checkout.py',
            'keyword_files_in_top2': sum(name.startswith('notes') for name in ranked['files'][:2]),
            'selected_files': chosen,
            'keyword_files_selected': sum(name.startswith('notes') for name in chosen)}


def _coherence(root: Path) -> dict:
    (root / 'a.py').write_text('def other():\n    return 1\n', encoding='utf-8')
    (root / 'b.py').write_text('import a\n\ndef use():\n    return a.value()\n', encoding='utf-8')
    missing = python_interface_diagnostics(root)
    (root / 'a.py').write_text('def value():\n    return 1\n', encoding='utf-8')
    present = python_interface_diagnostics(root)
    (root / 'a.py').write_text('class Box:\n    def save(self, item):\n        return item\n', encoding='utf-8')
    (root / 'b.py').write_text('import a\n\ndef use():\n    return a.Box().store(1)\n', encoding='utf-8')
    missing_method = python_interface_diagnostics(root)
    (root / 'b.py').write_text('import a\n\ndef use():\n    return a.Box().save(1)\n', encoding='utf-8')
    present_method = python_interface_diagnostics(root)
    return {'missing_name_reported': any('does not define it' in item.message for item in missing),
            'present_name_clear': present == [],
            'missing_method_reported': any('does not define Box.store' in item.message for item in missing_method),
            'present_method_clear': present_method == []}


def _go_calls(root: Path) -> dict:
    from takumi.vibe.coherence import go_interface_diagnostics
    gap = '\n'.join(f'var x{i} = {i}' for i in range(400))
    (root / 'box.go').write_text('package main\n\ntype Box struct{}\n\n' + gap + '\n\nfunc (b *Box) Save(item string) string {\n\treturn item\n}\n\nfunc Greeting(name string) string {\n\treturn name\n}\n', encoding='utf-8')
    (root / 'use.go').write_text('package main\n\nimport "fmt"\n\nfunc use(items []string) {\n\tfmt.Println(len(items))\n\tbox := Box{}\n\tbox.Store(1)\n}\n', encoding='utf-8')
    missing = go_interface_diagnostics(root)
    messages, _info = build_messages(root, 'fix the crash', platforms=['go'], diagnostics=missing, touched=[], budget=REI_BUDGET)
    shown = messages[1]['content'].split('--- box.go', 1)[1].split('---', 1)[0]
    (root / 'use.go').write_text('package main\n\nfunc use() string {\n\tbox := Box{}\n\treturn box.Save("a") + Greeting("Sumi")\n}\n', encoding='utf-8')
    clear = go_interface_diagnostics(root)
    (root / 'use.go').write_text('package main\n\nfunc use() string {\n\treturn Greeting()\n}\n', encoding='utf-8')
    arity = go_interface_diagnostics(root)
    return {'missing_method_reported': any('does not define Box.Store' in item.message for item in missing),
            'standard_library_left_alone': len(missing) == 1,
            'names_the_existing_method': any('Save(item string)' in item.message for item in missing),
            'opens_the_existing_method': 'func (b *Box) Save' in shown and 'return item' in shown,
            'present_call_clear': clear == [],
            'wrong_count_reported': any('Greeting' in item.message and '1 parameter' in item.message for item in arity)}


def _caller_window(root: Path) -> dict:
    head = '\n'.join(f'x{i} = {i}' for i in range(4000))
    (root / 'invoice.py').write_text(head + '\ndef save_invoice():\n    return 1\n', encoding='utf-8')
    (root / 'checkout.py').write_text(head + '\nfrom invoice import save_invoice\n\ndef pay():\n    return save_invoice()\n', encoding='utf-8')
    snapshot = SourceIndex(root / 'index.sqlite3').refresh(root)
    messages, _ = build_messages(root, 'save_invoice drops the tax', platforms=['python'], diagnostics=[], touched=[],
                                 budget=REI_BUDGET, snapshot=snapshot)
    checkout = messages[1]['content'].split('--- checkout.py', 1)[1].split('Task:', 1)[0]
    return {'call_window_has_the_call': 'return save_invoice()' in checkout,
            'definition_window_has_the_body': 'def save_invoice():\n    return 1' in messages[1]['content']}


def _constructor_window(root: Path) -> dict:
    head = '\n'.join(f'x{i} = {i}' for i in range(4000))
    (root / 'a.py').write_text('class Box:\n    def save(self, item):\n        return item\n', encoding='utf-8')
    (root / 'checkout.py').write_text(head + '\nfrom a import Box\n\ndef pay():\n    return Box().save(1)\n', encoding='utf-8')
    (root / 'other.py').write_text(head + '\nbox = None\n\ndef use():\n    return box.save(1)\n', encoding='utf-8')
    (root / 'bound.py').write_text(head + '\nfrom a import Box\n\ndef pay():\n    box = Box()\n    return box.save(1)\n', encoding='utf-8')
    snapshot = SourceIndex(root / 'index.sqlite3').refresh(root)
    anchors = symbol_anchors(snapshot, root, 'save drops the tax')
    messages, _ = build_messages(root, 'save drops the tax', platforms=['python'], diagnostics=[], touched=[],
                                 budget=REI_BUDGET, snapshot=snapshot)
    checkout = messages[1]['content'].split('--- checkout.py', 1)[1].split('Task:', 1)[0]
    bound = messages[1]['content'].split('--- bound.py', 1)[1].split('Task:', 1)[0]
    return {'constructor_call_windowed': 'return Box().save(1)' in checkout,
            'bound_instance_windowed': 'return box.save(1)' in bound,
            'definition_anchored': 'a.py' in anchors,
            'instance_call_not_anchored': 'other.py' not in anchors}


def _diagnostic_window(root: Path) -> dict:
    head = '\n'.join(f'x{i} = {i}' for i in range(4000))
    (root / 'a.py').write_text(head + '\nclass Box:\n    def save(self, item):\n        return item\n', encoding='utf-8')
    (root / 'b.py').write_text('import a\n\ndef use():\n    return a.Box().store(1)\n', encoding='utf-8')
    snapshot = SourceIndex(root / 'index.sqlite3').refresh(root)
    crash = Diagnostic('tests', 'error', "AttributeError: 'Box' object has no attribute 'store'", 'b.py', 3)
    messages, _ = build_messages(root, 'fix the crash', platforms=['python'], diagnostics=[crash], touched=[],
                                 budget=REI_BUDGET, snapshot=snapshot)
    shown = messages[1]['content'].split('--- a.py', 1)[1].split('Task:', 1)[0]
    return {'crash_windows_the_method': 'def save(self, item)' in shown}


def _traceback_frames(root: Path) -> dict:
    from takumi.vibe.diagnostics import parse
    head = '\n'.join(f'x{i} = {i}' for i in range(4000))
    (root / 'invoice.py').write_text(head + '\ndef save_invoice(order):\n    return 1 / order\n', encoding='utf-8')
    (root / 'checkout.py').write_text(head + '\ndef pay():\n    return save_invoice(0)\n', encoding='utf-8')
    log = ('Traceback (most recent call last):\n'
           '  File "/usr/lib/python3/x.py", line 9, in a\n'
           '  File "checkout.py", line 4002, in pay\n'
           '  File "invoice.py", line 4002, in save_invoice\n'
           'ZeroDivisionError: division by zero\n')
    found = parse(log, project=str(root))
    messages, info = build_messages(root, 'fix the crash', platforms=['python'], diagnostics=found, touched=[], budget=REI_BUDGET)
    text = messages[1]['content']
    pytest_log = ('checkout.py:4002: in pay\n    return save_invoice(0)\n'
                   'invoice.py:4002: in save_invoice\n    return 1 / order\n'
                   'E   ZeroDivisionError: division by zero\n')
    pytest_found = parse(pytest_log, project=str(root))
    rust_log = ("thread 'tests::greets_by_name' panicked at src/main.rs:15:9:\n"
                "assertion `left == right` failed\n  left: \"Hi, Sumi\"\n right: \"Hello, Sumi\"\n"
                "note: run with `RUST_BACKTRACE=1`\nerror: test failed, to rerun pass `--bin sample`\n")
    rust_found = parse(rust_log)
    return {'frames': [(item.file, item.line) for item in found],
            'rust_panic': {'file': rust_found[0].file, 'line': rust_found[0].line, 'message': rust_found[0].message} if rust_found else {},
            'pytest_frames': [(item.file, item.line) for item in pytest_found],
            'pytest_message': pytest_found[0].message if pytest_found else '',
            'files': info['files'][:2],
            'raise_windowed': 'return 1 / order' in text.split('--- invoice.py', 1)[1],
            'caller_windowed': 'return save_invoice(0)' in text.split('--- checkout.py', 1)[1]}


def _node_assertion(root: Path) -> dict:
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        return {'ran': False}
    (root / 'logic.test.js').write_text(
        "const assert = require('node:assert');\nconst test = require('node:test');\n"
        "test('greeting', () => {\n  assert.strictEqual('Hello, world', 'Hello, Sumi');\n});\n", encoding='utf-8')
    result = subprocess.run([node, '--test', 'logic.test.js'], cwd=root, capture_output=True, text=True, timeout=30)
    from takumi.vibe.diagnostics import parse
    found = parse(result.stdout + result.stderr, project=str(root))
    first = found[0] if found else None
    return {'ran': True, 'exit': result.returncode, 'file': first.file if first else '',
            'line': first.line if first else 0,
            'names_expected': bool(first and 'Hello, Sumi' in first.message)}


def _shared_name(root: Path) -> dict:
    head = '\n'.join(f'x{i} = {i}' for i in range(4000))
    (root / 'billing').mkdir()
    (root / 'notes').mkdir()
    (root / 'billing' / 'invoice.py').write_text('def save(order):\n    return order\n', encoding='utf-8')
    (root / 'notes' / 'draft.py').write_text(head + '\ndef save(text):\n    return text\n', encoding='utf-8')
    (root / 'checkout.py').write_text(head + '\nfrom billing.invoice import save\n\ndef pay(order):\n    return save(order)\n', encoding='utf-8')
    for i in range(6):
        (root / f'n{i}.py').write_text('def update_status():\n    return 1\n', encoding='utf-8')
    snapshot = SourceIndex(root / 'index.sqlite3').refresh(root)
    _messages, info = build_messages(root, 'save drops the tax', platforms=['python'], diagnostics=[], touched=[],
                                     budget=REI_BUDGET, snapshot=snapshot)
    return {'reads_called_definition': 'billing/invoice.py' in info['files'],
            'reads_the_caller': 'checkout.py' in info['files'],
            'skips_the_other_definition': 'notes/draft.py' not in info['files']}


def _js_method(root: Path) -> dict:
    head = '\n'.join(f'const x{i} = {i}' for i in range(4000))
    (root / 'a.mjs').write_text('export class Box {\n  save(item) {\n    return item\n  }\n}\n', encoding='utf-8')
    (root / 'checkout.mjs').write_text(head + "\nimport { Box } from './a.mjs'\nexport function pay() {\n  const box = new Box()\n  return box.save(1)\n}\n", encoding='utf-8')
    (root / 'other.mjs').write_text(head + "\nimport { Box } from './a.mjs'\nconst box = null\nexport function use() {\n  return box.save(1)\n}\n", encoding='utf-8')
    for i in range(6):
        (root / f'n{i}.mjs').write_text('export function update_status() { return 1 }\n', encoding='utf-8')
    snapshot = SourceIndex(root / 'index.sqlite3').refresh(root)
    anchors = symbol_anchors(snapshot, root, 'save drops the tax')
    messages, _info = build_messages(root, 'save drops the tax', platforms=['web'], diagnostics=[], touched=[],
                                     budget=REI_BUDGET, snapshot=snapshot)
    checkout = messages[1]['content'].split('--- checkout.mjs', 1)[1].split('Task:', 1)[0]
    return {'reads_the_method': 'save(item)' in messages[1]['content'],
            'reads_the_bound_call': 'return box.save(1)' in checkout,
            'unbound_not_anchored': 'other.mjs' not in anchors,
            'unbound_not_selected': 'other.mjs' not in _info['files']}


def _failure_name(root: Path) -> dict:
    (root / 'main.go').write_text('package main\n\nfunc Greeting(name string) string {\n\treturn "Hi, " + name\n}\n', encoding='utf-8')
    (root / 'check_test.go').write_text('package main\n\nimport "testing"\n\nfunc TestIt(t *testing.T) {\n\tt.Fatal(label())\n}\nfunc label() string { return "" }\n', encoding='utf-8')
    for i in range(8):
        (root / f'note{i}.go').write_text(f'package main\n\nfunc Note{i}() int {{ return {i} }}\n', encoding='utf-8')
    from takumi.vibe.diagnostics import parse
    found = parse('check_test.go:5: Greeting("Sumi") = "Hi, Sumi", want "Hello, Sumi"\n', project=str(root))
    _messages, info = build_messages(root, 'fix the crash', platforms=['go'], diagnostics=found, touched=[], budget=REI_BUDGET)
    plain = repomap.build(root, task='fix the crash')
    named = repomap.build(root, task='fix the crash', mention=found[0].message)
    long = root / 'long'
    long.mkdir()
    head = '\n'.join(f'x{i} := {i}' for i in range(4000))
    (long / 'main.go').write_text(head + '\nfunc Greeting(name string) string {\n\treturn "Hi, " + name\n}\n', encoding='utf-8')
    (long / 'check_test.go').write_text((root / 'check_test.go').read_text(encoding='utf-8'), encoding='utf-8')
    long_messages, _long_info = build_messages(long, 'fix the crash', platforms=['go'], diagnostics=found, touched=[], budget=REI_BUDGET)
    long_shown = long_messages[1]['content'].split('--- main.go', 1)[1].split('Task:', 1)[0]
    shared = root / 'shared'
    shared.mkdir()
    body = '\nfunc Greeting(name string) string {\n\treturn "Hi, " + name\n}\n'
    (shared / 'a.go').write_text(head + body, encoding='utf-8')
    (shared / 'b.go').write_text(head + body, encoding='utf-8')
    (shared / 'check_test.go').write_text((root / 'check_test.go').read_text(encoding='utf-8'), encoding='utf-8')
    for i in range(8):
        (shared / f'note{i}.go').write_text(f'package main\n\nfunc Note{i}() int {{ return {i} }}\n', encoding='utf-8')
    shared_messages, shared_info = build_messages(shared, 'fix the crash', platforms=['go'], diagnostics=found, touched=[], budget=REI_BUDGET)
    shared_text = shared_messages[1]['content']
    both_windows = all('return "Hi, "' in shared_text.split(f'--- {name}', 1)[1].split('---', 1)[0] for name in ('a.go', 'b.go'))
    tall = root / 'tall'
    tall.mkdir()
    middle = '\n'.join(f'\tnote := "brace }} {i}"\n\tstep{i} := {i}' for i in range(90))
    (tall / 'main.go').write_text(head + '\nfunc Greeting(name string) string {\n' + middle + '\n\treturn "Hi, " + name\n}\n', encoding='utf-8')
    (tall / 'check_test.go').write_text((root / 'check_test.go').read_text(encoding='utf-8'), encoding='utf-8')
    tall_messages, _tall_info = build_messages(tall, 'fix the crash', platforms=['go'], diagnostics=found, touched=[], budget=REI_BUDGET)
    tall_shown = tall_messages[1]['content'].split('--- main.go', 1)[1].split('Task:', 1)[0]
    return {'definition_selected': info['files'][:2] == ['check_test.go', 'main.go'],
            'body_shown': 'func Greeting' in _messages[1]['content'],
            'failure_outranks_notes': named['ranks']['main.go'] > named['ranks']['note0.go'],
            'task_alone_does_not': plain['ranks']['main.go'] <= plain['ranks']['note0.go'],
            'long_file_windows_the_definition': 'return "Hi, "' in long_shown,
            'shared_name_windows_each_definition': 'a.go' in shared_info['files'] and 'b.go' in shared_info['files'] and both_windows,
            'long_function_keeps_the_return': 'return "Hi, " + name' in tall_shown and 'step40 := 40' not in tall_shown}


def _elision(root: Path) -> dict:
    body = 'func Greeting() string {\n' + '\n'.join(f'\tstep{i} := {i}' for i in range(40)) + '\n\treturn "Hi"\n}\n'
    (root / 'main.go').write_text(body, encoding='utf-8')
    search = 'func Greeting() string {\n\tstep0 := 0\n... omitted middle ...\n\treturn "Hi"\n}'
    replace = 'func Greeting() string {\n\tstep0 := 0\n... omitted middle ...\n\treturn "Hello"\n}'
    apply(root, [Edit('main.go', search, replace)], Journal(root))
    text = (root / 'main.go').read_text(encoding='utf-8')
    return {'return_updated': '\treturn "Hello"\n' in text, 'middle_kept': '\tstep20 := 20\n' in text}


def _incremental(root: Path) -> dict:
    for i in range(12):
        (root / f'file{i}.py').write_text(f'def item_{i}():\n    return {i}\n', encoding='utf-8')
    index = SourceIndex(root / 'index.sqlite3')
    first = index.refresh(root)
    (root / 'file0.py').write_text('def item_0():\n    return 9\n', encoding='utf-8')
    second = index.refresh(root)
    return {'first_parsed': first['parsed'], 'first_reused': first['reused'],
            'second_parsed': second['parsed'], 'second_reused': second['reused'],
            'changed_symbol': second['units']['file0.py']['symbols'][0]['name'] == 'item_0',
            'kept_symbol': second['units']['file11.py']['symbols'][0]['name'] == 'item_11'}


def measure() -> dict:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        retrieval = _retrieval(root)
        other = root / 'coherence'
        other.mkdir()
        coherence = _coherence(other)
        window = root / 'window'
        window.mkdir()
        caller_window = _caller_window(window)
        constructed = root / 'constructed'
        constructed.mkdir()
        constructor_window = _constructor_window(constructed)
        diagnosed = root / 'diagnosed'
        diagnosed.mkdir()
        diagnostic_window = _diagnostic_window(diagnosed)
        traced = root / 'traced'
        traced.mkdir()
        traceback_frames = _traceback_frames(traced)
        node_root = root / 'nodetest'
        node_root.mkdir()
        node_assertion = _node_assertion(node_root)
        incremental_root = root / 'incremental'
        incremental_root.mkdir()
        incremental = _incremental(incremental_root)
        shared_root = root / 'shared'
        shared_root.mkdir()
        shared_name = _shared_name(shared_root)
        script_root = root / 'script'
        script_root.mkdir()
        js_method = _js_method(script_root)
        named = root / 'named'
        named.mkdir()
        failure_name = _failure_name(named)
        go_root = root / 'gocalls'
        go_root.mkdir()
        go_calls = _go_calls(go_root)
        elided = root / 'elision'
        elided.mkdir()
        elision = _elision(elided)
    routing = {
        'local_fix_searches': should_research('Fix the source of the startup error in the local backend.'),
        'explicit_lookup_searches': should_research('Fix the crash, then look up the current OAuth documentation.'),
    }
    passed = (retrieval['definition_first'] and retrieval['caller_second'] and retrieval['keyword_files_in_top2'] == 0
              and retrieval['keyword_files_selected'] == 0 and coherence['missing_name_reported'] and coherence['present_name_clear']
              and coherence['missing_method_reported'] and coherence['present_method_clear']
              and caller_window['call_window_has_the_call'] and caller_window['definition_window_has_the_body']
              and constructor_window['constructor_call_windowed'] and constructor_window['bound_instance_windowed']
              and constructor_window['definition_anchored']
              and constructor_window['instance_call_not_anchored']
              and diagnostic_window['crash_windows_the_method']
              and traceback_frames['frames'] == [('invoice.py', 4002), ('checkout.py', 4002)]
              and traceback_frames['raise_windowed'] and traceback_frames['caller_windowed']
              and traceback_frames['pytest_frames'] == [('invoice.py', 4002), ('checkout.py', 4002)]
              and traceback_frames['pytest_message'] == 'ZeroDivisionError: division by zero'
              and node_assertion['ran'] and node_assertion['exit'] != 0
              and node_assertion['file'] == 'logic.test.js' and node_assertion['line'] == 4
              and node_assertion['names_expected']
              and traceback_frames['rust_panic'].get('file') == 'src/main.rs'
              and traceback_frames['rust_panic'].get('line') == 15
              and 'Hello, Sumi' in traceback_frames['rust_panic'].get('message', '')
              and 'to rerun' not in traceback_frames['rust_panic'].get('message', '')
              and incremental['first_parsed'] == 12 and incremental['first_reused'] == 0
              and incremental['second_parsed'] == 1 and incremental['second_reused'] == 11
              and incremental['changed_symbol'] and incremental['kept_symbol']
              and shared_name['reads_called_definition'] and shared_name['reads_the_caller']
              and shared_name['skips_the_other_definition']
              and js_method['reads_the_method'] and js_method['reads_the_bound_call']
              and js_method['unbound_not_anchored'] and js_method['unbound_not_selected']
              and failure_name['definition_selected'] and failure_name['body_shown']
              and failure_name['failure_outranks_notes'] and failure_name['task_alone_does_not']
              and failure_name['long_file_windows_the_definition']
              and failure_name['shared_name_windows_each_definition']
              and failure_name['long_function_keeps_the_return']
              and go_calls['missing_method_reported'] and go_calls['standard_library_left_alone']
              and go_calls['names_the_existing_method'] and go_calls['opens_the_existing_method']
              and go_calls['present_call_clear'] and go_calls['wrong_count_reported']
              and elision['return_updated'] and elision['middle_kept']
              and routing['local_fix_searches'] is False and routing['explicit_lookup_searches'] is True)
    return {'kind': 'agent-system', 'model': None, 'gpu': False, 'retrieval': retrieval, 'coherence': coherence,
            'caller_window': caller_window, 'constructor_window': constructor_window,
            'diagnostic_window': diagnostic_window, 'traceback_frames': traceback_frames,
            'node_assertion': node_assertion,
            'incremental': incremental, 'shared_name': shared_name, 'js_method': js_method,
            'failure_name': failure_name, 'go_calls': go_calls, 'elision': elision, 'routing': routing, 'passed': passed}


if __name__ == '__main__':
    print(json.dumps(measure(), indent=1))
