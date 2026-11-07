import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from takumi import forge_bridge
from takumi.core import Agent
from takumi.gpu_scheduler import GpuScheduler
from takumi.source_index import SourceIndex
from takumi.vibe import agent_bench, client, coherence, context, diagnostics, edits, platforms, repomap, scaffold, tuning, oracle
from takumi.vibe.loop import Options, VibeRun
from takumi.vibe.manager import VibeManager
from takumi.vibe.platforms import Step


class Temp(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def write(self, name, body):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body) if isinstance(body, bytes) else target.write_text(body, encoding='utf-8', newline='')
        return target


class Diagnostics(unittest.TestCase):
    def test_compiler_formats_keep_location_and_code(self):
        cases = [
            (r"C:\p\src\A.cs(12,5): error CS0103: The name 'x' does not exist [C:\p\A.csproj]", ('src/A.cs', 12, 'CS0103'), r'C:\p'),
            ('src/a.cpp:7:3: error: use of undeclared identifier', ('src/a.cpp', 7, ''), ''),
            ('e: file:///C:/p/app/Main.kt:3:9 Unresolved reference: foo', ('C:/p/app/Main.kt', 3, ''), ''),
            ('src/app.ts(4,2): error TS2322: Type mismatch', ('src/app.ts', 4, 'TS2322'), ''),
            ('Lib.java:9: error: cannot find symbol', ('Lib.java', 9, ''), ''),
        ]
        for text, (file, line, code), root in cases:
            found = diagnostics.parse(text, project=root)
            self.assertEqual((found[0].file, found[0].line, found[0].code, found[0].severity), (file, line, code, 'error'), text)

    def test_msbuild_project_suffix_is_removed(self):
        found = diagnostics.parse(r"A.cs(1,1): error CS1: bad [C:\p\A.csproj]")
        self.assertEqual(found[0].message, 'bad')

    def test_python_failure_points_at_project_frame_not_library(self):
        log = 'Traceback (most recent call last):\n  File "/usr/lib/python3/x.py", line 9, in a\n  File "app.py", line 3, in b\nZeroDivisionError: division by zero\nFAILED test_a.py::t\n'
        found = diagnostics.parse(log)
        self.assertEqual((found[0].file, found[0].line), ('app.py', 3))
        self.assertIn('ZeroDivisionError', found[0].message)

    def test_a_traceback_keeps_the_raise_and_the_project_caller(self):
        log = ('Traceback (most recent call last):\n'
               '  File "/usr/lib/python3/x.py", line 9, in a\n'
               '  File "checkout.py", line 12, in pay\n'
               '  File "invoice.py", line 40, in save_invoice\n'
               'ZeroDivisionError: division by zero\n')
        found = diagnostics.parse(log)
        self.assertEqual([(item.file, item.line) for item in found], [('invoice.py', 40), ('checkout.py', 12)])

    def test_pytest_short_trace_keeps_the_raise_and_the_caller(self):
        log = ('checkout.py:12: in pay\n    return save_invoice(0)\n'
               'invoice.py:40: in save_invoice\n    return 1 / order\n'
               'E   ZeroDivisionError: division by zero\n'
               'invoice.py:40: ZeroDivisionError\n')
        found = diagnostics.parse(log)
        self.assertEqual([(item.file, item.line) for item in found], [('invoice.py', 40), ('checkout.py', 12)])
        self.assertEqual(found[0].message, 'ZeroDivisionError: division by zero')

    def test_node_test_assertion_keeps_the_diff_and_the_stack_line(self):
        log = ("TAP version 13\n# Subtest: greeting\nnot ok 1 - greeting\n  ---\n"
               "  location: '/tmp/app/logic.test.js:3:1'\n  error: |-\n"
               "    Expected values to be strictly equal:\n\n    + 'Hello, world'\n    - 'Hello, Sumi'\n"
               "  code: 'ERR_ASSERTION'\n  name: 'AssertionError'\n  stack: |-\n"
               "    TestContext.<anonymous> (/tmp/app/logic.test.js:4:10)\n"
               "    Test.run (node:internal/test_runner/test:1047:25)\n  ...\n")
        found = diagnostics.parse(log, project='/tmp/app')
        self.assertEqual(found[0].file, 'logic.test.js')
        self.assertEqual(found[0].line, 4)
        self.assertIn('Hello, Sumi', found[0].message)
        self.assertNotIn('node:internal', ' '.join(item.file for item in found))

    @unittest.skipUnless(shutil.which('node'), 'node')
    def test_a_real_node_test_failure_points_at_the_assertion(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            (folder / 'logic.test.js').write_text(
                "const assert = require('node:assert');\nconst test = require('node:test');\n"
                "test('greeting', () => {\n  assert.strictEqual('Hello, world', 'Hello, Sumi');\n});\n",
                encoding='utf-8')
            result = subprocess.run(['node', '--test', 'logic.test.js'], cwd=folder, capture_output=True, text=True, timeout=30)
            found = diagnostics.parse(result.stdout + result.stderr, project=str(folder))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((found[0].file, found[0].line), ('logic.test.js', 4))
        self.assertIn('Hello, Sumi', found[0].message)

    def test_a_go_test_failure_keeps_the_line_and_both_values(self):
        text = '--- FAIL: TestGreeting (0.00s)\n    main_test.go:8: Greeting("Sumi") = "Hi, Sumi", want "Hello, Sumi"\nFAIL\n'
        found = diagnostics.parse(text)
        self.assertEqual((found[0].file, found[0].line, found[0].message), ('main_test.go', 8, 'Greeting("Sumi") = "Hi, Sumi", want "Hello, Sumi"'))
        pytest_line = diagnostics.parse('checkout.py:12: in pay\ninvoice.py:40: in save_invoice\nE   ZeroDivisionError: division by zero\ninvoice.py:40: ZeroDivisionError\n')
        self.assertEqual([(item.file, item.line) for item in pytest_line if item.file], [('invoice.py', 40), ('checkout.py', 12)])
        self.assertIn('ZeroDivisionError', pytest_line[0].message)

    @unittest.skipUnless(shutil.which('go'), 'go')
    def test_real_go_test_reports_the_comparison(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / 'go.mod').write_text('module example.com/g\n\ngo 1.22\n', encoding='utf-8')
            (root / 'main.go').write_text('package main\n\nfunc Greeting(name string) string { return "Hi, " + name }\n', encoding='utf-8')
            (root / 'main_test.go').write_text(
                'package main\n\nimport "testing"\n\nfunc TestGreeting(t *testing.T) {\n\tgot := Greeting("Sumi")\n\tif got != "Hello, Sumi" {\n\t\tt.Fatalf("Greeting(%q) = %q, want %q", "Sumi", got, "Hello, Sumi")\n\t}\n}\n',
                encoding='utf-8')
            result = subprocess.run(['go', 'test', '.'], cwd=root, capture_output=True, text=True, timeout=60)
            found = diagnostics.parse(result.stdout + result.stderr, project=str(root))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((found[0].file, found[0].line), ('main_test.go', 8))
        self.assertIn('Hi, Sumi', found[0].message)
        self.assertIn('Hello, Sumi', found[0].message)

    def test_go_and_rust_compile_errors_keep_the_source_line(self):
        go = diagnostics.parse('./main.go:4:10: undefined: Missing')
        self.assertEqual((go[0].file, go[0].line, go[0].column, go[0].message), ('main.go', 4, 10, 'undefined: Missing'))
        rust = diagnostics.parse('error[E0425]: cannot find value `missing` in this scope\n --> src/main.rs:2:20\nerror: could not compile `sample` (bin "sample") due to 1 previous error\n')
        self.assertEqual((rust[0].file, rust[0].line, rust[0].column, rust[0].code), ('src/main.rs', 2, 20, 'E0425'))
        self.assertIn('missing', rust[0].message)
        self.assertEqual(len(rust), 1)

    @unittest.skipUnless(shutil.which('go') and shutil.which('cargo'), 'go and cargo')
    def test_real_go_and_rust_compilers_point_at_the_broken_line(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            go = root / 'goapp'
            go.mkdir()
            (go / 'go.mod').write_text('module example.com/c\n\ngo 1.22\n', encoding='utf-8')
            (go / 'main.go').write_text('package main\n\nfunc main() {\n\tprintln(Missing)\n}\n', encoding='utf-8')
            rust = root / 'rustapp'
            (rust / 'src').mkdir(parents=True)
            (rust / 'Cargo.toml').write_text('[package]\nname = "sample"\nversion = "0.1.0"\nedition = "2021"\n', encoding='utf-8')
            (rust / 'src' / 'main.rs').write_text('fn main() {\n    println!("{}", missing);\n}\n', encoding='utf-8')
            go_run = subprocess.run(['go', 'build', '-o', 'app', '.'], cwd=go, capture_output=True, text=True, timeout=60)
            rust_run = subprocess.run(['cargo', 'build', '--quiet'], cwd=rust, capture_output=True, text=True, timeout=120)
            go_found = diagnostics.parse(go_run.stderr + go_run.stdout, project=str(go))
            rust_found = diagnostics.parse(rust_run.stderr + rust_run.stdout, project=str(rust))
        self.assertNotEqual(go_run.returncode, 0)
        self.assertEqual((go_found[0].file, go_found[0].line), ('main.go', 4))
        self.assertIn('Missing', go_found[0].message)
        self.assertNotEqual(rust_run.returncode, 0)
        self.assertEqual((rust_found[0].file, rust_found[0].line, rust_found[0].code), ('src/main.rs', 2, 'E0425'))

    def test_a_rust_panic_keeps_the_assertion_and_the_line(self):
        log = ("thread 'tests::greets_by_name' panicked at src/main.rs:15:9:\n"
               "assertion `left == right` failed\n"
               "  left: \"Hi, Sumi\"\n"
               " right: \"Hello, Sumi\"\n"
               "note: run with `RUST_BACKTRACE=1`\n"
               "error: test failed, to rerun pass `--bin sample`\n")
        found = diagnostics.parse(log)
        self.assertEqual((found[0].file, found[0].line), ('src/main.rs', 15))
        self.assertIn('Hello, Sumi', found[0].message)
        self.assertIn('Hi, Sumi', found[0].message)
        self.assertNotIn('to rerun', found[0].message)
        old = diagnostics.parse("thread 'tests::greets_by_name' panicked at 'assertion failed', src/lib.rs:4:5\n")
        self.assertEqual((old[0].file, old[0].line, old[0].message), ('src/lib.rs', 4, 'assertion failed'))

    def test_a_javascript_stack_keeps_the_raise_first(self):
        log = ('AssertionError: expected 1 to equal 2\n'
               '    at pay (checkout.js:12:5)\n'
               '    at Object.<anonymous> (invoice.test.js:4:1)\n')
        found = diagnostics.parse(log)
        self.assertEqual([(item.file, item.line) for item in found], [('checkout.js', 12), ('invoice.test.js', 4)])

    def test_fingerprint_ignores_line_numbers_but_not_messages(self):
        a = diagnostics.parse('a.cpp:7:3: error: bad thing')
        b = diagnostics.parse('a.cpp:90:1: error: bad thing')
        c = diagnostics.parse('a.cpp:7:3: error: other thing')
        self.assertEqual(diagnostics.fingerprint(a), diagnostics.fingerprint(b))
        self.assertNotEqual(diagnostics.fingerprint(a), diagnostics.fingerprint(c))

    def test_warnings_do_not_count_as_errors(self):
        found = diagnostics.parse('a.c:1:1: warning: unused')
        self.assertEqual(diagnostics.error_count(found), 0)
        self.assertEqual(diagnostics.fingerprint(found), '')


class Edits(Temp):
    def edit(self, path, search, replace):
        return edits.Edit(path, search, replace)

    def apply(self, *items, **kwargs):
        journal = edits.Journal(self.root)
        edits.apply(self.root, list(items), journal, **kwargs)
        return journal

    def test_parse_takes_path_from_previous_line_and_ignores_thinking_and_fences(self):
        text = '<think>a.py\n<<<<<<< SEARCH\nno\n=======\nno\n>>>>>>> REPLACE</think>\nHere:\n```python\nsrc/a.py\n<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE\n```'
        self.assertEqual(edits.parse(text), [edits.Edit('src/a.py', 'old', 'new')])

    def test_unterminated_block_is_reported(self):
        with self.assertRaisesRegex(edits.EditError, 'missing'):
            edits.parse('a.py\n<<<<<<< SEARCH\nold\n=======\nnew\n')

    def test_a_copied_omission_banner_still_changes_the_return(self):
        body = 'func Greeting() string {\n' + '\n'.join(f'\tstep{i} := {i}' for i in range(40)) + '\n\treturn "Hi"\n}\n'
        self.write('main.go', body)
        search = 'func Greeting() string {\n\tstep0 := 0\n... omitted middle ...\n\treturn "Hi"\n}'
        replace = 'func Greeting() string {\n\tstep0 := 0\n... omitted middle ...\n\treturn "Hello"\n}'
        self.apply(self.edit('main.go', search, replace))
        text = (self.root / 'main.go').read_text()
        self.assertIn('\treturn "Hello"\n', text)
        self.assertIn('\tstep20 := 20\n', text)
        wrapped = '... lines 1-4 of 40 ...\n\treturn "Hello"\n... rest of file omitted ...'
        self.apply(self.edit('main.go', wrapped, '... lines 1-4 of 40 ...\n\treturn "Hi"\n... rest of file omitted ...'))
        self.assertIn('\treturn "Hi"\n', (self.root / 'main.go').read_text())

    def test_ambiguous_search_is_rejected_not_guessed(self):
        self.write('a.py', 'x = 1\nx = 1\n')
        with self.assertRaisesRegex(edits.EditError, '2 times'):
            self.apply(self.edit('a.py', 'x = 1', 'x = 2'))
        self.assertEqual((self.root / 'a.py').read_text(), 'x = 1\nx = 1\n')

    def test_indentation_drift_keeps_the_files_own_indent(self):
        self.write('a.py', 'def f():\n        return 1\n')
        self.apply(self.edit('a.py', 'return 1', 'return 2'))
        self.assertEqual((self.root / 'a.py').read_text(), 'def f():\n        return 2\n')
        self.write('b.py', 'def f():\n        a = 1\n        return a\n')
        self.apply(self.edit('b.py', '    a = 1\n    return a', '    a = 2\n    return a'))
        self.assertEqual((self.root / 'b.py').read_text(), 'def f():\n        a = 2\n        return a\n')

    def test_crlf_files_stay_crlf(self):
        self.write('a.cs', b'class A\r\n{\r\n}\r\n')
        self.apply(self.edit('a.cs', 'class A\n{', 'class B\n{'))
        self.assertEqual((self.root / 'a.cs').read_bytes(), b'class B\r\n{\r\n}\r\n')

    def test_create_requires_empty_search(self):
        self.apply(self.edit('new/dir/a.txt', '', 'hello'))
        self.assertEqual((self.root / 'new/dir/a.txt').read_text(), 'hello\n')
        with self.assertRaisesRegex(edits.EditError, 'does not exist'):
            self.apply(self.edit('missing.txt', 'old', 'new'))

    def test_all_or_nothing(self):
        self.write('a.py', 'one\n')
        self.write('b.py', 'two\n')
        with self.assertRaises(edits.EditError):
            self.apply(self.edit('a.py', 'one', 'ONE'), self.edit('b.py', 'absent', 'x'))
        self.assertEqual((self.root / 'a.py').read_text(), 'one\n')

    def test_paths_outside_project_and_secrets_are_refused(self):
        for name in ('../x.py', 'C:/Windows/x.py', '/etc/passwd', '.git/config', 'a/.env', 'keys/server.pem', '.ssh/id_rsa'):
            with self.subTest(name), self.assertRaises(edits.EditError):
                edits.safe_target(self.root, name)

    @unittest.skipUnless(hasattr(os, 'symlink'), 'symlinks')
    def test_symlink_escape_is_refused(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside)
        try:
            os.symlink(outside, self.root / 'link', target_is_directory=True)
        except OSError:
            self.skipTest('symlink privilege unavailable')
        with self.assertRaisesRegex(edits.EditError, 'outside'):
            edits.safe_target(self.root, 'link/a.py')

    def test_build_configuration_needs_explicit_permission(self):
        self.write('package.json', '{}\n')
        change = self.edit('package.json', '{}', '{"scripts":{"test":"curl evil"}}')
        with self.assertRaisesRegex(edits.EditError, 'approval'):
            self.apply(change)
        self.apply(change, allow_config=True)
        self.assertIn('curl', (self.root / 'package.json').read_text())

    def test_rollback_restores_and_removes_new_files(self):
        self.write('a.py', 'one\n')
        journal = self.apply(self.edit('a.py', 'one', 'two'), self.edit('b.py', '', 'new'))
        self.assertEqual(journal.changed(), ['a.py', 'b.py'])
        self.assertIn('+two', journal.diff())
        self.assertEqual(journal.rollback(), [])
        self.assertEqual((self.root / 'a.py').read_text(), 'one\n')
        self.assertFalse((self.root / 'b.py').exists())

    def test_rollback_keeps_edits_made_by_someone_else(self):
        self.write('a.py', 'one\n')
        journal = self.apply(self.edit('a.py', 'one', 'two'))
        self.write('a.py', 'owner typed this\n')
        self.assertEqual(journal.rollback(), ['a.py'])
        self.assertEqual((self.root / 'a.py').read_text(), 'owner typed this\n')


class RepoMapAndContext(Temp):
    def test_map_ranks_task_files_and_their_dependencies(self):
        self.write('billing.py', 'def compute_invoice(x):\n    return tax_rate(x)\n')
        self.write('tax.py', 'def tax_rate(x):\n    return x\n')
        for i in range(12):
            self.write(f'noise{i}.py', f'def noise_{i}():\n    return {i}\n')
        result = repomap.build(self.root, task='compute_invoice is wrong')
        self.assertEqual(result['files'][0], 'billing.py')
        self.assertEqual(result['files'][1], 'tax.py')

    def test_a_repeated_word_does_not_outrank_the_named_function(self):
        for i in range(10):
            self.write(f'notes{i}.py', 'def update_status():\n    return "invoice invoice invoice"\n')
        self.write('invoice.py', 'def save_invoice():\n    return 1\n')
        self.write('checkout.py', 'def pay():\n    return save_invoice()\n')
        result = repomap.build(self.root, task='save_invoice drops the tax')
        self.assertEqual(result['files'][0], 'invoice.py')
        self.assertEqual(result['files'][1], 'checkout.py')
        self.assertTrue(all(not name.startswith('notes') for name in result['files'][:2]))
        self.assertGreater(result['ranks']['invoice.py'], result['ranks']['notes0.py'])

    def test_agent_system_benchmark_meets_its_gates(self):
        report = agent_bench.measure()
        self.assertTrue(report['passed'], report)
        self.assertIsNone(report['model'])
        self.assertEqual(report['retrieval']['keyword_files_in_top2'], 0)
        self.assertEqual(report['retrieval']['keyword_files_selected'], 0)
        self.assertFalse(report['routing']['local_fix_searches'])

    def test_map_respects_its_budget(self):
        for i in range(60):
            self.write(f'm{i}.py', '\n'.join(f'def function_number_{i}_{j}():\n    pass' for j in range(30)))
        result = repomap.build(self.root, budget_tokens=200)
        self.assertLessEqual(len(result['text']), 200 * repomap.CHARS_PER_TOKEN + 50)

    def test_generated_folders_are_not_indexed(self):
        self.write('node_modules/x/index.js', 'function hidden() {}\n')
        self.write('app.js', 'function shown() {}\n')
        self.assertEqual(repomap.list_sources(self.root), ['app.js'])

    def test_repair_lesson_teaches_the_served_model_without_held_out_answers(self):
        in_operator = diagnostics.Diagnostic('javascript in-operator', 'error', 'The `in` operator throws on numbers and strings.')
        named = diagnostics.Diagnostic('task names', 'error', 'The task names "app.takumi.inbox" but no project file contains it.', source='static')
        in_text = context.failure_prompt([in_operator])
        name_text = context.failure_prompt([named])
        self.assertIn('Object(value)', in_text)
        self.assertIn('app.takumi.inbox', name_text)
        for text in (in_text, name_text, context.lesson_for([in_operator]), context.lesson_for([named])):
            self.assertNotIn('dev.takumi.link', text)
            self.assertNotIn('CFBundleURLTypes', text)
            self.assertNotIn('path.split', text)
        self.assertEqual(context.lesson_for([diagnostics.Diagnostic('tests', 'error', 'value must be 2')]), '')

    def test_full_file_is_not_repeated_as_a_signature(self):
        self.write('a.py', 'def value():\n    return 1\n')
        messages, _ = context.build_messages(self.root, 'fix value', platforms=['python'], diagnostics=[], touched=[])
        user = messages[1]['content']
        self.assertIn('def value():', user.split('Files:', 1)[1])
        self.assertNotIn('def value():', user.split('Files:', 1)[0])

    def test_a_failure_that_names_another_file_reads_that_file(self):
        for i in range(8):
            self.write(f'notes{i}.py', 'def update_status():\n    return 1\n')
        self.write('a.py', 'def ready(name):\n    return name\n')
        self.write('b.py', 'import a\n\ndef use():\n    return a.ready()\n')
        diagnostic = diagnostics.Diagnostic('imports', 'error', 'b.py calls a.ready with 0 argument(s), but a.py defines it with 1.', 'b.py', 3)
        messages, info = context.build_messages(self.root, 'fix the crash', platforms=['python'], diagnostics=[diagnostic], touched=[])
        self.assertEqual(info['files'][:2], ['b.py', 'a.py'])
        self.assertIn('def ready(name)', messages[1]['content'])

    def test_unused_diagnostic_room_is_spent_on_source(self):
        self.write('a.py', 'value = 1\n' * 5000)
        short = diagnostics.Diagnostic('tests', 'error', 'bad', 'a.py', 1)
        crowded = diagnostics.Diagnostic('tests', 'error', 'x' * 4000, 'a.py', 1)
        open_turn, _ = context.build_messages(self.root, 'read value', platforms=['python'], diagnostics=[short], touched=[], budget=context.REI_BUDGET)
        tight_turn, _ = context.build_messages(self.root, 'read value', platforms=['python'], diagnostics=[crowded], touched=[], budget=context.REI_BUDGET)
        self.assertGreater(len(open_turn[1]['content']), len(tight_turn[1]['content']))

    def test_an_uncommitted_file_is_read_even_when_the_task_does_not_name_it(self):
        subprocess = __import__('subprocess')
        for i in range(8):
            self.write(f'notes{i}.py', 'def update_status():\n    return 1\n')
        self.write('checkout.py', 'def pay():\n    return 1\n')
        env = {**os.environ, 'GIT_AUTHOR_NAME': 'Takumi', 'GIT_AUTHOR_EMAIL': 'takumi@example.com',
               'GIT_COMMITTER_NAME': 'Takumi', 'GIT_COMMITTER_EMAIL': 'takumi@example.com'}
        subprocess.run(['git', 'init'], cwd=self.root, check=True, capture_output=True)
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'base'], cwd=self.root, check=True, capture_output=True, env=env)
        self.write('checkout.py', 'def pay():\n    return 2\n')
        messages, info = context.build_messages(self.root, 'fix the crash', platforms=['python'], diagnostics=[], touched=[])
        self.assertIn('checkout.py', info['files'][:2])
        self.assertIn('return 2', messages[1]['content'])

    def test_a_fresh_index_shows_the_definition_instead_of_the_file_head(self):
        body = '\n'.join(f'x{i} = {i}' for i in range(4000)) + '\ndef save_invoice():\n    return 1\n'
        self.write('a.py', body)
        index = SourceIndex(self.root / 'index.sqlite3')
        snapshot = index.refresh(self.root)
        self.assertIn('a.py', context.symbol_anchors(snapshot, self.root, 'save_invoice drops the tax'))
        def shown(messages):
            return messages[1]['content'].split('Files:', 1)[1].split('Task:', 1)[0]
        without, _ = context.build_messages(self.root, 'save_invoice drops the tax', platforms=['python'], diagnostics=[], touched=[], budget=context.REI_BUDGET)
        with_index, _ = context.build_messages(self.root, 'save_invoice drops the tax', platforms=['python'], diagnostics=[], touched=[], budget=context.REI_BUDGET, snapshot=snapshot)
        # Without the index the loop can still open a long file at a named definition,
        # so only the indexed result is asserted here.
        self.assertIn('def save_invoice', shown(without) + shown(with_index))
        self.assertIn('def save_invoice():\n    return 1', shown(with_index))
        self.write('b.py', 'def save_invoice():\n    return 2\n')
        shared = index.refresh(self.root)
        self.assertEqual(context.symbol_anchors(shared, self.root, 'save_invoice'), {})
        self.write('a.py', '\n'.join(f'y{i} = {i}' for i in range(4000)) + '\n')
        self.assertEqual(context.symbol_anchors(snapshot, self.root, 'save_invoice'), {})
        stale, _ = context.build_messages(self.root, 'save_invoice drops the tax', platforms=['python'], diagnostics=[], touched=[], budget=context.REI_BUDGET, snapshot=snapshot)
        self.assertNotIn('def save_invoice', shown(stale).split('--- a.py', 1)[1])

    def test_a_fresh_index_windows_the_call_and_ignores_a_shared_short_name(self):
        head = '\n'.join(f'x{i} = {i}' for i in range(4000))
        self.write('invoice.py', head + '\ndef save_invoice():\n    return 1\n')
        self.write('checkout.py', head + '\nfrom invoice import save_invoice\n\ndef pay():\n    return save_invoice()\n')
        self.write('other.py', head + '\nbox = None\n\ndef use():\n    return box.save_invoice()\n')
        index = SourceIndex(self.root / 'index.sqlite3')
        snapshot = index.refresh(self.root)
        anchors = context.symbol_anchors(snapshot, self.root, 'save_invoice drops the tax')
        self.assertIn('invoice.py', anchors)
        self.assertIn('checkout.py', anchors)
        self.assertNotIn('other.py', anchors)
        def section(messages, name):
            return messages[1]['content'].split(f'--- {name}', 1)[1].split('Task:', 1)[0]
        with_index, _ = context.build_messages(self.root, 'save_invoice drops the tax', platforms=['python'], diagnostics=[], touched=[], budget=context.REI_BUDGET, snapshot=snapshot)
        self.assertIn('return save_invoice()', section(with_index, 'checkout.py'))
        self.write('checkout.py', head + '\nfrom invoice import save_invoice\n\ndef pay():\n    return 0\n')
        self.assertNotIn('checkout.py', context.symbol_anchors(snapshot, self.root, 'save_invoice drops the tax'))
        for i in range(6):
            self.write(f'c{i}.py', 'from invoice import save_invoice\n\ndef run():\n    return save_invoice()\n')
        many = index.refresh(self.root)
        callers = [name for name in context.symbol_anchors(many, self.root, 'save_invoice') if name.startswith('c')]
        self.assertEqual(callers, ['c0.py', 'c1.py', 'c2.py', 'c3.py'])

    def test_a_shared_name_follows_the_call_or_the_file_the_task_names(self):
        head = '\n'.join(f'x{i} = {i}' for i in range(4000))
        self.write('billing/invoice.py', 'def save(order):\n    return order\n')
        self.write('notes/draft.py', head + '\ndef save(text):\n    return text\n')
        self.write('checkout.py', head + '\nfrom billing.invoice import save\n\ndef pay(order):\n    return save(order)\n')
        for i in range(6):
            self.write(f'n{i}.py', 'def update_status():\n    return 1\n')
        index = SourceIndex(self.root / 'index.sqlite3')
        snapshot = index.refresh(self.root)
        anchors = context.symbol_anchors(snapshot, self.root, 'save drops the tax')
        self.assertIn('billing/invoice.py', anchors)
        self.assertIn('checkout.py', anchors)
        self.assertNotIn('notes/draft.py', anchors)
        _messages, info = context.build_messages(self.root, 'save drops the tax', platforms=['python'], diagnostics=[], touched=[],
                                                 budget=context.REI_BUDGET, snapshot=snapshot)
        self.assertIn('billing/invoice.py', info['files'])
        self.assertNotIn('notes/draft.py', info['files'])
        self.assertIn('def save(order)', _messages[1]['content'])
        self.assertIn('return save(order)', _messages[1]['content'])
        self.write('other.py', 'from notes.draft import save\n\ndef keep(text):\n    return save(text)\n')
        both = index.refresh(self.root)
        both_anchors = context.symbol_anchors(both, self.root, 'save drops the tax')
        self.assertNotIn('billing/invoice.py', both_anchors)
        self.assertNotIn('notes/draft.py', both_anchors)
        named = context.symbol_anchors(both, self.root, 'Fix save in notes/draft.py')
        self.assertIn('notes/draft.py', named)
        self.assertNotIn('billing/invoice.py', named)
        failure = diagnostics.Diagnostic('tests', 'error', 'wrong', 'checkout.py', 4002)
        quiet, _ = context.build_messages(self.root, 'fix save', platforms=['python'], diagnostics=[failure], touched=[], last_reply='I set save to return 0')
        loud, _ = context.build_messages(self.root, 'fix save', platforms=['python'], diagnostics=[failure], touched=[], last_reply='I set save to return 0', disk_wins=True)
        self.assertNotIn('on disk', quiet[-1]['content'])
        self.assertIn('on disk', loud[-1]['content'])

    def test_a_constructor_call_is_windowed_at_the_method(self):
        head = '\n'.join(f'x{i} = {i}' for i in range(4000))
        self.write('a.py', 'class Box:\n    def save(self, item):\n        return item\n')
        self.write('checkout.py', head + '\nfrom a import Box\n\ndef pay():\n    return Box().save(1)\n')
        self.write('other.py', head + '\nbox = None\n\ndef use():\n    return box.save(1)\n')
        self.write('bound.py', head + '\nfrom a import Box\n\ndef pay():\n    box = Box()\n    return box.save(1)\n')
        snapshot = SourceIndex(self.root / 'index.sqlite3').refresh(self.root)
        anchors = context.symbol_anchors(snapshot, self.root, 'save drops the tax')
        self.assertIn('checkout.py', anchors)
        self.assertIn('a.py', anchors)
        self.assertIn('bound.py', anchors)
        self.assertNotIn('other.py', anchors)
        messages, _ = context.build_messages(self.root, 'save drops the tax', platforms=['python'], diagnostics=[], touched=[], budget=context.REI_BUDGET, snapshot=snapshot)
        shown = messages[1]['content'].split('--- checkout.py', 1)[1].split('Task:', 1)[0]
        bound = messages[1]['content'].split('--- bound.py', 1)[1].split('Task:', 1)[0]
        self.assertIn('return Box().save(1)', shown)
        self.assertIn('return box.save(1)', bound)

    def test_a_javascript_constructor_binding_is_windowed_at_the_method(self):
        head = '\n'.join(f'const x{i} = {i}' for i in range(4000))
        self.write('a.mjs', 'export class Box {\n  save(item) {\n    return item\n  }\n}\n')
        self.write('checkout.mjs', head + "\nimport { Box } from './a.mjs'\nexport function pay() {\n  const box = new Box()\n  return box.save(1)\n}\n")
        self.write('other.mjs', head + "\nimport { Box } from './a.mjs'\nconst box = null\nexport function use() {\n  return box.save(1)\n}\n")
        snapshot = SourceIndex(self.root / 'index.sqlite3').refresh(self.root)
        anchors = context.symbol_anchors(snapshot, self.root, 'save drops the tax')
        self.assertIn('a.mjs', anchors)
        self.assertIn('checkout.mjs', anchors)
        self.assertNotIn('other.mjs', anchors)
        messages, _ = context.build_messages(self.root, 'save drops the tax', platforms=['web'], diagnostics=[], touched=[], budget=context.REI_BUDGET, snapshot=snapshot)
        shown = messages[1]['content'].split('--- checkout.mjs', 1)[1].split('Task:', 1)[0]
        self.assertIn('return box.save(1)', shown)
        self.assertIn('save(item)', messages[1]['content'])

    def test_an_unbound_call_does_not_rank_as_the_indexed_caller(self):
        self.write('a.mjs', 'export class Box {\n  save(item) { return item }\n}\n')
        self.write('checkout.mjs', "import { Box } from './a.mjs'\nexport function pay() {\n  const box = new Box()\n  return box.save(1)\n}\n")
        self.write('other.mjs', "import { Box } from './a.mjs'\nconst box = null\nexport function use() {\n  return box.save(1)\n}\n")
        for i in range(6):
            self.write(f'n{i}.mjs', 'export function update_status() { return 1 }\n')
        snapshot = SourceIndex(self.root / 'index.sqlite3').refresh(self.root)
        _messages, info = context.build_messages(self.root, 'save drops the tax', platforms=['web'], diagnostics=[], touched=[],
                                                 budget=context.REI_BUDGET, snapshot=snapshot)
        self.assertIn('a.mjs', info['files'])
        self.assertIn('checkout.mjs', info['files'])
        self.assertNotIn('other.mjs', info['files'])
        _without, loose = context.build_messages(self.root, 'save drops the tax', platforms=['web'], diagnostics=[], touched=[],
                                                 budget=context.REI_BUDGET)
        self.assertIn('other.mjs', loose['files'])

    def test_a_failure_names_a_function_the_failing_file_does_not(self):
        self.write('main.go', 'package main\n\nfunc Greeting(name string) string {\n\treturn "Hi, " + name\n}\n')
        self.write('check_test.go', 'package main\n\nimport "testing"\n\nfunc TestIt(t *testing.T) {\n\tt.Fatal(label())\n}\nfunc label() string { return "" }\n')
        for i in range(8):
            self.write(f'note{i}.go', f'package main\n\nfunc Note{i}() int {{ return {i} }}\n')
        found = diagnostics.parse('check_test.go:5: Greeting("Sumi") = "Hi, Sumi", want "Hello, Sumi"\n', project=str(self.root))
        messages, info = context.build_messages(self.root, 'fix the crash', platforms=['go'], diagnostics=found, touched=[], budget=context.REI_BUDGET)
        self.assertEqual(info['files'][:2], ['check_test.go', 'main.go'])
        self.assertIn('func Greeting', messages[1]['content'])
        plain = repomap.build(self.root, task='fix the crash')
        named = repomap.build(self.root, task='fix the crash', mention=found[0].message)
        self.assertGreater(named['ranks']['main.go'], named['ranks']['note0.go'])
        self.assertLessEqual(plain['ranks']['main.go'], plain['ranks']['note0.go'])

    def test_a_long_file_opens_at_the_definition_the_failure_names(self):
        head = '\n'.join(f'x{i} := {i}' for i in range(4000))
        self.write('main.go', head + '\nfunc Greeting(name string) string {\n\treturn "Hi, " + name\n}\n')
        self.write('check_test.go', 'package main\n\nimport "testing"\n\nfunc TestIt(t *testing.T) {\n\tt.Fatal(label())\n}\nfunc label() string { return "" }\n')
        found = diagnostics.parse('check_test.go:5: Greeting("Sumi") = "Hi, Sumi", want "Hello, Sumi"\n', project=str(self.root))
        messages, _info = context.build_messages(self.root, 'fix the crash', platforms=['go'], diagnostics=found, touched=[], budget=context.REI_BUDGET)
        shown = messages[1]['content'].split('--- main.go', 1)[1].split('Task:', 1)[0]
        self.assertIn('func Greeting(name string) string', shown)
        self.assertIn('return "Hi, "', shown)
        plain, _ = context.build_messages(self.root, 'fix the crash', platforms=['go'], diagnostics=[], touched=[], budget=context.REI_BUDGET)
        head_shown = plain[1]['content'].split('--- main.go', 1)[1].split('Task:', 1)[0]
        self.assertNotIn('func Greeting', head_shown)

    def test_a_name_shared_by_a_few_files_opens_each_definition(self):
        head = '\n'.join(f'x{i} := {i}' for i in range(4000))
        body = '\nfunc Greeting(name string) string {\n\treturn "Hi, " + name\n}\n'
        self.write('a.go', head + body)
        self.write('b.go', head + body)
        self.write('check_test.go', 'package main\n\nimport "testing"\n\nfunc TestIt(t *testing.T) {\n\tt.Fatal(label())\n}\nfunc label() string { return "" }\n')
        for i in range(8):
            self.write(f'note{i}.go', f'package main\n\nfunc Note{i}() int {{ return {i} }}\n')
        found = diagnostics.parse('check_test.go:5: Greeting("Sumi") = "Hi, Sumi", want "Hello, Sumi"\n', project=str(self.root))
        messages, info = context.build_messages(self.root, 'fix the crash', platforms=['go'], diagnostics=found, touched=[], budget=context.REI_BUDGET)
        self.assertIn('a.go', info['files'])
        self.assertIn('b.go', info['files'])
        text = messages[1]['content']
        for name in ('a.go', 'b.go'):
            shown = text.split(f'--- {name}', 1)[1].split('---', 1)[0]
            self.assertIn('func Greeting', shown)
            self.assertIn('return "Hi, "', shown)
        for i in range(4):
            self.write(f'also{i}.go', head + body)
        wide = repomap.build(self.root, task='fix the crash', mention=found[0].message)
        self.assertNotIn('a.go', wide.get('hits') or {})

    def test_a_long_function_keeps_the_return_the_failure_compares(self):
        head = '\n'.join(f'x{i} := {i}' for i in range(200))
        middle = '\n'.join(f'\tnote := "brace }} {i}"\n\tstep{i} := {i}' for i in range(90))
        self.write('main.go', head + '\nfunc Greeting(name string) string {\n' + middle + '\n\treturn "Hi, " + name\n}\n')
        self.write('check_test.go', 'package main\n\nimport "testing"\n\nfunc TestIt(t *testing.T) {\n\tt.Fatal(label())\n}\nfunc label() string { return "" }\n')
        found = diagnostics.parse('check_test.go:5: Greeting("Sumi") = "Hi, Sumi", want "Hello, Sumi"\n', project=str(self.root))
        messages, _info = context.build_messages(self.root, 'fix the crash', platforms=['go'], diagnostics=found, touched=[], budget=context.REI_BUDGET)
        shown = messages[1]['content'].split('--- main.go', 1)[1].split('Task:', 1)[0]
        self.assertIn('func Greeting(name string) string', shown)
        self.assertIn('return "Hi, " + name', shown)
        self.assertNotIn('step40 := 40', shown)

    def test_a_long_python_function_keeps_its_return(self):
        head = '\n'.join(f'x{i} = {i}' for i in range(3000))
        body = 'def save(item):\n' + '\n'.join(f'    step{i} = {i}' for i in range(200)) + '\n    return item\n'
        self.write('a.py', head + '\n' + body)
        snapshot = SourceIndex(self.root / 'index.sqlite3').refresh(self.root)
        messages, _info = context.build_messages(self.root, 'save drops the tax', platforms=['python'], diagnostics=[], touched=[], budget=context.REI_BUDGET, snapshot=snapshot)
        shown = messages[1]['content'].split('--- a.py', 1)[1].split('Task:', 1)[0]
        self.assertIn('def save(item):', shown)
        self.assertIn('return item', shown)
        self.assertNotIn('step100 = 100', shown)

    def test_a_traceback_reads_the_raise_and_the_caller(self):
        head = '\n'.join(f'x{i} = {i}' for i in range(4000))
        self.write('invoice.py', head + '\ndef save_invoice(order):\n    return 1 / order\n')
        self.write('checkout.py', head + '\ndef pay():\n    return save_invoice(0)\n')
        log = ('Traceback (most recent call last):\n'
               '  File "checkout.py", line 4002, in pay\n'
               '  File "invoice.py", line 4002, in save_invoice\n'
               'ZeroDivisionError: division by zero\n')
        found = diagnostics.parse(log, project=str(self.root))
        messages, info = context.build_messages(self.root, 'fix the crash', platforms=['python'], diagnostics=found, touched=[], budget=context.REI_BUDGET)
        self.assertEqual(info['files'][:2], ['invoice.py', 'checkout.py'])
        text = messages[1]['content']
        self.assertIn('return 1 / order', text.split('--- invoice.py', 1)[1])
        self.assertIn('return save_invoice(0)', text.split('--- checkout.py', 1)[1])
        pytest_log = ('checkout.py:4002: in pay\n    return save_invoice(0)\n'
                      'invoice.py:4002: in save_invoice\n    return 1 / order\n'
                      'E   ZeroDivisionError: division by zero\n')
        pytest_found = diagnostics.parse(pytest_log, project=str(self.root))
        pytest_messages, pytest_info = context.build_messages(self.root, 'fix the crash', platforms=['python'], diagnostics=pytest_found, touched=[], budget=context.REI_BUDGET)
        self.assertEqual(pytest_info['files'][:2], ['invoice.py', 'checkout.py'])
        self.assertIn('return 1 / order', pytest_messages[1]['content'].split('--- invoice.py', 1)[1])

    def test_a_crash_names_the_class_the_task_does_not(self):
        head = '\n'.join(f'x{i} = {i}' for i in range(4000))
        self.write('a.py', head + '\nclass Box:\n    def save(self, item):\n        return item\n')
        self.write('b.py', 'import a\n\ndef use():\n    return a.Box().store(1)\n')
        for i in range(8):
            self.write(f'n{i}.py', 'def update_status():\n    return 1\n')
        snapshot = SourceIndex(self.root / 'index.sqlite3').refresh(self.root)
        crash = diagnostics.Diagnostic('tests', 'error', "AttributeError: 'Box' object has no attribute 'store'", 'b.py', 3)
        vague = diagnostics.Diagnostic('tests', 'error', 'value must be 2', 'b.py', 3)
        messages, info = context.build_messages(self.root, 'fix the crash', platforms=['python'], diagnostics=[crash], touched=[], budget=context.REI_BUDGET, snapshot=snapshot)
        shown = messages[1]['content'].split('--- a.py', 1)[1].split('Task:', 1)[0]
        self.assertIn('a.py', info['files'])
        self.assertIn('def save(self, item)', shown)
        missed, _ = context.build_messages(self.root, 'fix the crash', platforms=['python'], diagnostics=[vague], touched=[], budget=context.REI_BUDGET, snapshot=snapshot)
        vague_shown = missed[1]['content'].split('--- a.py', 1)[1].split('Task:', 1)[0] if '--- a.py' in missed[1]['content'] else ''
        self.assertNotIn('def save', vague_shown)

    def test_a_javascript_import_call_is_windowed_at_the_call(self):
        head = '\n'.join(f'const x{i} = {i}' for i in range(4000))
        self.write('a.js', head + '\nexport function save_invoice() {\n  return 1\n}\n')
        self.write('checkout.js', head + '\nimport { save_invoice } from "./a.js"\nexport function pay() {\n  return save_invoice()\n}\n')
        snapshot = SourceIndex(self.root / 'index.sqlite3').refresh(self.root)
        anchors = context.symbol_anchors(snapshot, self.root, 'save_invoice drops the tax')
        self.assertIn('a.js', anchors)
        self.assertIn('checkout.js', anchors)
        messages, _ = context.build_messages(self.root, 'save_invoice drops the tax', platforms=['web'], diagnostics=[], touched=[], budget=context.REI_BUDGET, snapshot=snapshot)
        shown = messages[1]['content'].split('--- checkout.js', 1)[1].split('Task:', 1)[0]
        self.assertIn('return save_invoice()', shown)

    def test_the_prompt_lists_real_checks_and_keeps_a_blocked_one_blocked(self):
        self.write('a.py', 'def f():\n    return 1\n')
        ready = platforms.Step('python tests', 'test', argv=['/secret/python', '-m', 'unittest'])
        blocked = platforms.Step('dotnet build', 'build', blocked='The .NET SDK is not installed.')
        messages, _ = context.build_messages(self.root, 'fix f', platforms=['python'], diagnostics=[], touched=[], checks=[ready, blocked])
        text = messages[1]['content']
        self.assertLess(text.index('Files:'), text.index('Checks this run will execute:'))
        self.assertLess(text.index('Checks this run will execute:'), text.index('Task:'))
        self.assertIn('- python tests (test): will run', text)
        self.assertIn('- dotnet build (build): blocked. The .NET SDK is not installed.', text)
        self.assertNotIn('/secret/python', text)
        self.assertNotIn('unittest', text)

    def test_repair_turn_shares_the_cacheable_prefix(self):
        self.write('a.py', 'def f():\n    return 1\n')
        first, _ = context.build_messages(self.root, 'fix f', platforms=['python'], diagnostics=[], touched=[])
        failure = diagnostics.parse('a.py:1:1: error: nope')
        second, _ = context.build_messages(self.root, 'fix f', platforms=['python'], diagnostics=failure, touched=['a.py'], last_reply='r')
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[1], second[1])
        self.assertIn('nope', second[-1]['content'])

    def test_large_file_is_windowed_around_the_failure_with_explicit_elision(self):
        body = '\n'.join(f'line {i}' for i in range(40000))
        shown = context._window(body, [20000], 4000)
        self.assertIn('line 19999', shown)
        self.assertIn('omitted', shown)
        self.assertLessEqual(len(shown), 4200)

    def test_prompt_stays_within_budget(self):
        budget = context.Budget(num_ctx=2048, num_predict=512, map_tokens=100, file_tokens=600, diagnostics_tokens=100)
        for i in range(8):
            self.write(f'big{i}.py', 'x = 1\n' * 4000)
        messages, info = context.build_messages(self.root, 'task', platforms=[], diagnostics=[], touched=[], budget=budget)
        self.assertLessEqual(info['estimated_prompt_tokens'], budget.prompt_tokens)

    def test_rei_keeps_its_own_smaller_budget(self):
        self.assertEqual(context.budget_for('rei-1.0:latest').num_ctx, 8192)
        self.assertEqual(context.budget_for('sumi-1.0:latest').num_ctx, 32768)


class Detection(Temp):
    def test_each_platform_is_recognized(self):
        cases = {
            'web': {'package.json': '{"scripts":{"test":"node --test"}}', 'a.js': ''},
            'dotnet': {'A.csproj': '<Project/>'},
            'cpp': {'CMakeLists.txt': 'project(x)', 'a.cpp': ''},
            'android': {'settings.gradle.kts': '', 'app/src/main/AndroidManifest.xml': '<manifest/>'},
            'ios': {'Package.swift': '', 'Sources/A/a.swift': ''},
            'python': {'a.py': ''},
        }
        for platform, files in cases.items():
            with self.subTest(platform), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                for name, body in files.items():
                    (root / name).parent.mkdir(parents=True, exist_ok=True)
                    (root / name).write_text(body)
                self.assertIn(platform, platforms.detect(root)['platforms'])

    def test_missing_toolchain_blocks_with_instructions(self):
        self.write('A.csproj', '<Project/>')
        with mock.patch.object(platforms.shutil, 'which', return_value=None):
            plan = platforms.detect(self.root)
        self.assertIn('winget', plan['blocked'][0]['blocked'])
        self.assertFalse(plan['can_prove'])

    def test_ios_without_mac_says_so_and_does_not_pretend(self):
        self.write('Package.swift', '')
        with mock.patch.object(platforms.shutil, 'which', return_value=None):
            plan = platforms.detect(self.root)
        self.assertIn('macOS', plan['blocked'][0]['blocked'])

    def test_a_runtime_task_is_not_labeled_a_test(self):
        self.write('takumi.tasks.json', json.dumps({'version': 1, 'tasks': [{'id': 'run', 'argv': [sys.executable, '-c', 'print(1)']}]}))
        plan = platforms.detect(self.root)
        self.assertEqual([(s.kind, s.platform) for s in plan['steps']], [('run', 'project')])
        self.assertTrue(plan['can_prove'])
        self.assertFalse(plan['has_tests'])

    def test_cpp_starter_builds_and_its_test_is_not_counted_as_the_build(self):
        root = self.root / 'app'
        scaffold.create('cpp', root, 'Sample')
        plan = platforms.detect(root)
        self.assertIn('cpp', plan['platforms'])
        if any(step.blocked for step in plan['steps']):
            self.skipTest(next(step.blocked for step in plan['steps'] if step.blocked))
        result = platforms.run_steps(root, plan['steps'])
        detail = result['diagnostics'][0].message if result['diagnostics'] else result['state']
        self.assertEqual(result['state'], 'passed', detail)
        self.assertTrue(result['built'])
        self.assertTrue(result['tested'])
        self.assertFalse(result['ran'])
        self.assertEqual([step['kind'] for step in result['steps'] if step['status'] == 'passed'], ['build', 'build', 'test'])
        app = root / '.takumi-build' / 'app'
        self.assertTrue(app.is_file())
        if os.name == 'nt' and app.read_bytes()[:2] != b'MZ':
            return  # built and tested through a non-native toolchain; Windows cannot launch it directly
        ran = subprocess.run([str(app)], cwd=root, capture_output=True, text=True, timeout=20)
        self.assertEqual(ran.returncode, 0)
        self.assertEqual(ran.stdout.strip(), 'Hello, world')

    def test_project_tasks_take_priority(self):
        self.write('package.json', '{"scripts":{"test":"node --test"}}')
        self.write('takumi.tasks.json', json.dumps({'version': 1, 'tasks': [{'id': 'test', 'argv': ['node', '--version']}]}))
        plan = platforms.detect(self.root)
        self.assertEqual([s.platform for s in plan['steps'] if s.kind != 'static'], ['project'])

    def test_uninstalled_dependencies_block_instead_of_failing_confusingly(self):
        self.write('package.json', '{"scripts":{"test":"vitest"},"devDependencies":{"vitest":"1"}}')
        self.assertIn('npm ci', platforms.detect(self.root)['blocked'][0]['blocked'])


class StaticChecks(Temp):
    def test_pwa_contract_names_each_missing_piece(self):
        self.write('manifest.webmanifest', '{"name":"A","start_url":"/","display":"standalone","icons":[{"src":"i.png","sizes":"192x192"}]}')
        self.write('index.html', '<html></html>')
        text = '\n'.join(d.message for d in platforms.check_pwa(self.root, 'manifest.webmanifest'))
        for phrase in ('i.png', '512x512', 'rel="manifest"', 'service worker'):
            self.assertIn(phrase, text)

    def test_android_exported_rule(self):
        self.write('AndroidManifest.xml', '<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application><activity android:name=".M"><intent-filter/></activity></application></manifest>')
        self.assertIn('exported', platforms.check_android_manifest(self.root, 'AndroidManifest.xml')[0].message)

    def test_plist_problems(self):
        self.write('Info.plist', '<plist version="1.0"><dict><key>CFBundleName</key><string>A</string></dict></plist>')
        self.assertIn('CFBundleIdentifier', platforms.check_plist(self.root, 'Info.plist')[0].message)
        self.write('Info.plist', 'not xml')
        self.assertIn('valid', platforms.check_plist(self.root, 'Info.plist')[0].message)

    def test_in_operator_on_a_nullable_value_is_an_error(self):
        self.write('app.mjs', 'if (current == null || !(part in current)) return 1\n')
        found = platforms.check_js_in(self.root)
        self.assertIn('Object(current)', found[0].message)
        self.write('app.mjs', 'if (current != null && part in current) return 1\n')
        self.assertIn('Object(current)', platforms.check_js_in(self.root)[0].message)
        self.write('app.mjs', 'if (current == null || !(key in Object(current))) return 1\n')
        self.assertEqual(platforms.check_js_in(self.root), [])
        self.write('app.mjs', "if ('serviceWorker' in navigator) register()\n")
        self.assertEqual(platforms.check_js_in(self.root), [])

    def test_html_mismatch_is_located(self):
        self.write('a.html', '<div>\n<span>\n</div>\n')
        found = platforms.check_web(self.root)
        self.assertTrue(any('span' in d.message and d.line == 2 for d in found))


PY = [sys.executable]


def python_step(code, name='check', kind='test', **kwargs):
    return Step(name, kind, PY + ['-c', code], **kwargs)


class Runner(Temp):
    def test_a_passing_test_is_not_a_passing_build(self):
        result = platforms.run_steps(self.root, [python_step('pass', 'syntax', kind='static'), python_step('pass', 'tests')])
        self.assertEqual(result['state'], 'passed')
        self.assertTrue(result['static'])
        self.assertTrue(result['tested'])
        self.assertFalse(result['built'])
        self.assertFalse(result['ran'])
        result = platforms.run_steps(self.root, [python_step('pass', 'build', kind='build'), python_step('pass', 'app', kind='run')])
        self.assertTrue(result['built'])
        self.assertTrue(result['ran'])
        self.assertFalse(result['tested'])
        self.assertFalse(result['static'])

    def test_nothing_run_is_not_a_pass(self):
        result = platforms.run_steps(self.root, [Step('x', 'build', blocked='missing')])
        self.assertEqual(result['state'], 'unverified')
        self.assertFalse(result['built'])

    def test_stops_at_first_failure(self):
        result = platforms.run_steps(self.root, [python_step('raise SystemExit(3)', 'a'), python_step('pass', 'b')])
        self.assertEqual(result['state'], 'failed')
        self.assertEqual([s['name'] for s in result['steps']], ['a'])
        self.assertTrue(result['diagnostics'])

    def test_exit_code_wins_over_clean_output(self):
        result = platforms.run_steps(self.root, [python_step('print("all good"); raise SystemExit(1)')])
        self.assertEqual(result['state'], 'failed')
        self.assertIn('all good', result['diagnostics'][0].message)

    def test_timeout_kills_and_reports(self):
        started = time.monotonic()
        result = platforms.run_steps(self.root, [python_step('import time; time.sleep(60)', timeout=1)])
        self.assertLess(time.monotonic() - started, 20)
        self.assertIn('Timed out', result['diagnostics'][0].message)

    def test_cancel_stops_a_running_check(self):
        cancel = threading.Event()
        threading.Timer(0.4, cancel.set).start()
        started = time.monotonic()
        result = platforms.run_steps(self.root, [python_step('import time; time.sleep(60)')], cancel=cancel)
        self.assertEqual(result['state'], 'cancelled')
        self.assertLess(time.monotonic() - started, 20)

    def test_secrets_are_not_passed_to_project_code(self):
        with mock.patch.dict(os.environ, {'GITHUB_TOKEN': 'abc', 'MY_API_KEY': 'abc', 'KEEP': 'yes'}):
            env = platforms._env()
        self.assertNotIn('GITHUB_TOKEN', env)
        self.assertNotIn('MY_API_KEY', env)
        self.assertEqual(env['KEEP'], 'yes')


class LongPath(unittest.TestCase):
    def test_short_path_is_untouched(self):
        self.assertEqual(platforms._short_path('a;b;a'), 'a;b;a')

    def test_oversized_path_fits_and_keeps_toolchains_first(self):
        folder = str(Path(sys.executable).parent)
        long = os.pathsep.join(['C:/dup'] * 3 + [f'C:/entry{i}/' + 'x' * 80 for i in range(200)] + [folder])
        with mock.patch.object(platforms.shutil, 'which', side_effect=lambda name: sys.executable if name == 'node' else None):
            short = platforms._short_path(long)
        self.assertLessEqual(len(short), 7000)
        self.assertEqual(short.split(os.pathsep)[0], folder)
        self.assertEqual(short.count('C:/dup'), 1)


class Scripted:
    model = 'sumi-1.0:latest'

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(text, Exception):
            raise text
        return {'text': text, 'output_tokens': 5, 'done_reason': 'stop'}


TEST_FILE = "import sys\nsys.path.insert(0, '.')\nimport a\nif a.value() != 2:\n    print('a.py:2:1: error: value must be 2')\n    raise SystemExit(1)\n"


def good(old='return 1', new='return 2'):
    return f'a.py\n<<<<<<< SEARCH\n{old}\n=======\n{new}\n>>>>>>> REPLACE'


class OracleHelpers(unittest.TestCase):
    def test_verdicts_are_read_from_the_last_line_only(self):
        self.assertEqual(oracle.parse_verdict('reasoning\nOK', 3), [])
        self.assertEqual(oracle.parse_verdict('line 2 is off\nWRONG: 2, 3', 3), [2, 3])
        self.assertEqual(oracle.parse_verdict('**WRONG: 9**', 3), None)
        self.assertEqual(oracle.parse_verdict('I think it is fine', 3), None)

    def test_mutants_leave_strings_comments_and_imports_alone(self):
        self.assertIsNone(oracle.mutate_line("    text = 'a == b'"))
        self.assertIsNone(oracle.mutate_line('    # a == b'))
        self.assertIsNone(oracle.mutate_line('import a'))
        self.assertEqual(oracle.mutate_line('    if a == b:'), '    if a != b:')
        self.assertEqual(oracle.mutate_line('    return x + 1'), '    return x - 1')

    def test_only_changed_lines_are_mutated(self):
        found = oracle.mutants('if a == b:\n    pass\n', 'if a == b:\n    return c > d\n')
        self.assertEqual([m['line'] for m in found], [2])

    def test_added_lines_ignore_what_was_already_there(self):
        self.assertEqual(oracle.added_lines('a\nb\n', 'a\nb\nc\n\n'), ['c'])


class Interface(Temp):
    def test_plain_argument_counts_must_match(self):
        self.write('a.mjs', 'export function ready(name) { return name; }\n')
        self.write('b.mjs', "import { ready } from './a.mjs';\nexport function use() { return ready(); }\n")
        self.assertIn('0 argument', coherence.interface_diagnostics(self.root)[0].message)
        self.write('b.mjs', "import { ready } from './a.mjs';\nexport function use() { return ready('x'); }\n")
        self.assertEqual(coherence.interface_diagnostics(self.root), [])
        self.write('a.py', 'def ready(name, title="n"):\n    return name\n')
        self.write('b.py', 'import a\n\ndef use():\n    return a.ready()\n')
        self.assertIn('1 to 2', coherence.interface_diagnostics(self.root)[0].message)
        self.write('b.py', 'import a\n\ndef use():\n    return a.ready("x")\n')
        self.assertEqual(coherence.interface_diagnostics(self.root), [])
        self.write('b.py', 'from a import ready\n\ndef use():\n    return ready("x", "y", "z")\n')
        self.assertIn('3 argument', coherence.interface_diagnostics(self.root)[0].message)

    def test_a_method_call_on_another_files_class_must_name_a_real_method(self):
        self.write('a.py', 'class Box:\n    def save(self, item):\n        return item\n')
        self.write('b.py', 'import a\n\ndef use():\n    return a.Box().store(1)\n')
        self.assertIn('does not define Box.store', coherence.interface_diagnostics(self.root)[0].message)
        self.write('b.py', 'import a\n\ndef use():\n    return a.Box().save(1)\n')
        self.assertEqual(coherence.interface_diagnostics(self.root), [])
        self.write('b.py', 'import a\n\ndef use():\n    return a.Box().save()\n')
        self.assertIn('0 argument', coherence.interface_diagnostics(self.root)[0].message)
        self.write('b.py', 'from a import Box\n\ndef use():\n    return Box().store(1)\n')
        self.assertIn('does not define Box.store', coherence.interface_diagnostics(self.root)[0].message)
        self.write('a.py', 'class Box(dict):\n    def save(self):\n        return 1\n')
        self.write('b.py', 'import a\n\ndef use():\n    return a.Box().missing()\n')
        self.assertEqual(coherence.interface_diagnostics(self.root), [])
        self.write('a.py', 'class Box:\n    def save(self, item):\n        return item\n')
        self.write('b.py', 'from a import Box\n\ndef use():\n    box = Box()\n    return box.store(1)\n')
        self.assertIn('does not define Box.store', coherence.interface_diagnostics(self.root)[0].message)
        self.write('b.py', 'from a import Box\n\ndef use():\n    box = Box()\n    return box.save(1)\n')
        self.assertEqual(coherence.interface_diagnostics(self.root), [])
        self.write('b.py', 'from a import Box\n\ndef use():\n    box = Box()\n    return box.save()\n')
        self.assertIn('0 argument', coherence.interface_diagnostics(self.root)[0].message)
        self.write('b.py', 'from a import Box\n\ndef use(box):\n    return box.store(1)\n')
        self.assertEqual(coherence.interface_diagnostics(self.root), [])
        self.write('a.mjs', 'export class Box {\n  save(item) { return item }\n}\n')
        self.write('b.mjs', "import { Box } from './a.mjs'\nexport function use() {\n  const box = new Box()\n  return box.store(1)\n}\n")
        self.assertIn('does not define Box.store', coherence.interface_diagnostics(self.root)[0].message)
        self.write('b.mjs', "import { Box } from './a.mjs'\nexport function use() {\n  const box = new Box()\n  return box.save(1)\n}\n")
        self.assertEqual([item for item in coherence.interface_diagnostics(self.root) if item.file == 'b.mjs'], [])
        self.write('b.mjs', "import { Box } from './a.mjs'\nexport function use() {\n  return new Box().save()\n}\n")
        self.assertIn('0 argument', coherence.interface_diagnostics(self.root)[0].message)

    def test_a_go_package_catches_a_missing_method_and_a_wrong_count(self):
        self.write('box.go', 'package main\n\ntype Box struct{}\n\nfunc (b *Box) Save(item string) string {\n\treturn item\n}\n\nfunc Greeting(name string) string {\n\treturn "Hi, " + name\n}\n')
        self.write('use.go', 'package main\n\nimport "fmt"\n\nfunc use(items []string) {\n\tfmt.Println(len(items))\n\tbox := Box{}\n\tbox.Store(1)\n}\n')
        missing = coherence.interface_diagnostics(self.root)
        self.assertEqual([item.message for item in missing], [missing[0].message])
        self.assertIn('does not define Box.Store', missing[0].message)
        self.assertIn('Save(item string)', missing[0].message)
        self.assertEqual(missing[0].file, 'use.go')
        self.write('use.go', 'package main\n\nfunc use() string {\n\treturn (&Box{}).Save()\n}\n')
        counted = coherence.interface_diagnostics(self.root)
        self.assertTrue(any('0 argument' in item.message and 'Box.Save' in item.message for item in counted), counted)
        self.write('use.go', 'package main\n\nfunc use() string {\n\tbox := Box{}\n\treturn box.Save("a")\n}\n')
        self.assertEqual(coherence.interface_diagnostics(self.root), [])
        self.write('use.go', 'package main\n\nfunc use() string {\n\treturn Greeting()\n}\n')
        arity = coherence.interface_diagnostics(self.root)
        self.assertIn('1 parameter', arity[0].message)
        self.assertIn('Greeting', arity[0].message)
        self.write('use.go', 'package main\n\nfunc use() string {\n\treturn Missing("a")\n}\n')
        self.assertIn('does not define it', coherence.interface_diagnostics(self.root)[0].message)
        gap = '\n'.join(f'var x{i} = {i}' for i in range(400))
        self.write('box.go', 'package main\n\ntype Box struct{}\n\n' + gap + '\n\nfunc (b *Box) Save(item string) string {\n\treturn item\n}\n')
        self.write('use.go', 'package main\n\nfunc use() {\n\tbox := Box{}\n\tbox.Store(1)\n}\n')
        found = coherence.interface_diagnostics(self.root)
        messages, _info = context.build_messages(self.root, 'fix the crash', platforms=['go'], diagnostics=found, touched=[], budget=context.REI_BUDGET)
        shown = messages[1]['content'].split('--- box.go', 1)[1].split('---', 1)[0]
        self.assertIn('func (b *Box) Save(item string) string', shown)
        self.assertIn('return item', shown)

class Loop(Temp):
    def setUp(self):
        super().setUp()
        self.write('a.py', 'def value():\n    return 1\n')
        self.write('check.py', TEST_FILE)
        self.plan = {'platforms': ['python'], 'steps': [Step('tests', 'test', PY + ['check.py'], platform='python')], 'runnable': 1, 'has_tests': True, 'can_prove': True, 'blocked': []}

    def run_with(self, *replies, plan=None, source_index=None, **options):
        scripted = Scripted(*replies)
        report = VibeRun(self.root, 'make value return 2', scripted, plan=plan or self.plan, source_index=source_index,
                         options=Options(**options)).run()
        return report, scripted

    def source(self):
        return (self.root / 'a.py').read_text()

    def test_a_runtime_step_verifies_without_counting_as_a_test_or_a_build(self):
        plan = {'platforms': ['python'], 'steps': [python_step('pass', 'app', kind='run')],
                'runnable': 1, 'has_tests': False, 'can_prove': True, 'blocked': []}
        report, _ = self.run_with(good(), plan=plan)
        self.assertEqual(report['state'], 'verified')
        self.assertTrue(report['verification']['runtime_passed'])
        self.assertTrue(report['verification']['end_to_end'])
        self.assertFalse(report['verification']['tests_passed'])
        self.assertFalse(report['verification']['build_passed'])

    def test_static_checks_alone_are_not_an_end_to_end_pass(self):
        plan = {'platforms': ['python'], 'steps': [python_step('pass', 'syntax', kind='static')],
                'runnable': 1, 'has_tests': False, 'can_prove': False, 'blocked': []}
        report, _ = self.run_with(good(), plan=plan)
        self.assertEqual(report['state'], 'partially_verified')
        self.assertTrue(report['verification']['source_validated'])
        self.assertFalse(report['verification']['build_passed'])
        self.assertFalse(report['verification']['tests_passed'])
        self.assertFalse(report['verification']['runtime_passed'])
        self.assertFalse(report['verification']['end_to_end'])

    def test_verified_only_after_checks_pass(self):
        report, scripted = self.run_with(good())
        self.assertEqual(report['state'], 'verified')
        self.assertIn('- tests (test): will run', scripted.calls[0][1]['content'])
        self.assertEqual(report['changed_files'], ['a.py'])
        self.assertTrue(report['tests_ran'])
        self.assertEqual(report['verification'], {'source_changed': True, 'source_validated': False, 'checks_executed': True,
                                                  'build_passed': False, 'tests_passed': True, 'runtime_passed': False, 'end_to_end': True})
        self.assertEqual(report['ledger'][0]['edited'], ['a.py'])

    def test_a_renamed_export_is_not_accepted_while_tests_pass(self):
        self.write('app.mjs', 'export function ready() {}\n')
        self.write('main.mjs', "import { ready } from './app.mjs'\n")
        plan = {'platforms': ['web'], 'steps': [Step('tests', 'test', PY + ['-c', 'print("ok")'], platform='web')],
                'runnable': 1, 'has_tests': True, 'can_prove': True, 'blocked': []}
        broken = 'app.mjs\n<<<<<<< SEARCH\nexport function ready() {}\n=======\nexport function start() {}\n>>>>>>> REPLACE'
        restored = 'app.mjs\n<<<<<<< SEARCH\nexport function start() {}\n=======\nexport function ready() {}\n>>>>>>> REPLACE'
        scripted = Scripted(broken, restored)
        report = VibeRun(self.root, 'Rename the helper.', scripted, plan=plan, options=Options(baseline=False)).run()
        self.assertEqual((report['state'], report['attempts']), ('verified', 2))
        self.assertIn('does not export it', scripted.calls[1][-1]['content'])
        self.assertIn('export function ready', (self.root / 'app.mjs').read_text(encoding='utf-8'))

    def test_each_attempt_refreshes_the_index_before_it_reads(self):
        head = '\n'.join(f'x{i} = {i}' for i in range(4000))
        self.write('a.py', head + '\ndef value():\n    return 0\n')
        index = SourceIndex(self.root / 'index.sqlite3')
        index.refresh(self.root)
        self.write('a.py', head + '\ndef value():\n    return 1\n')
        seen = []
        scripted = Scripted(good('return 1', 'return 3'), good('return 3', 'return 2'))
        report = VibeRun(self.root, 'make value return 2', scripted, plan=self.plan, source_index=index,
                         on_event=seen.append, options=Options(baseline=False, budget=context.REI_BUDGET)).run()
        self.assertEqual((report['state'], report['attempts']), ('verified', 2))
        first = scripted.calls[0][1]['content'].split('--- a.py', 1)[1]
        second = scripted.calls[1][1]['content'].split('--- a.py', 1)[1]
        self.assertIn('def value():\n    return 1', first)
        self.assertIn('x0 = 0', second)
        self.assertIn('def value():\n    return 3', second)
        retrievals = [event for event in seen if event.get('name') == 'context_retrieval']
        self.assertEqual(retrievals[0]['parsed'], 1)
        self.assertGreaterEqual(retrievals[1]['reused'], 1)

    def test_repairs_from_the_real_error(self):
        report, scripted = self.run_with(good('return 1', 'return 3'), good('return 3', 'return 2'))
        self.assertEqual((report['state'], report['attempts']), ('verified', 2))
        self.assertIn('value must be 2', scripted.calls[1][-1]['content'])

    def test_declared_name_must_be_written_before_verified(self):
        self.write('Info.plist', '<plist><dict>\n<key>CFBundleName</key>\n<string>Sample</string>\n</dict></plist>\n')
        plan = {'platforms': ['ios'], 'steps': [Step('tests', 'test', PY + ['-c', 'print("ok")'], platform='ios')],
                'runnable': 1, 'has_tests': True, 'can_prove': True, 'blocked': []}
        omitted = 'Info.plist\n<<<<<<< SEARCH\n<key>CFBundleName</key>\n=======\n<key>CFBundleURLSchemes</key>\n<array><string>takumi</string></array>\n<key>CFBundleName</key>\n>>>>>>> REPLACE'
        named = 'Info.plist\n<<<<<<< SEARCH\n<key>CFBundleURLSchemes</key>\n=======\n<key>CFBundleURLName</key>\n<string>dev.takumi.link</string>\n<key>CFBundleURLSchemes</key>\n>>>>>>> REPLACE'
        scripted = Scripted(omitted, named)
        report = VibeRun(self.root, 'Register the takumi scheme using the name dev.takumi.link.', scripted,
                         plan=plan, options=Options(baseline=False)).run()
        self.assertEqual((report['state'], report['attempts']), ('verified', 2))
        self.assertIn('dev.takumi.link', scripted.calls[1][-1]['content'])
        self.assertIn('dev.takumi.link', (self.root / 'Info.plist').read_text(encoding='utf-8'))

    def edge_plan(self):
        """Runs every test_*.py beside the project, the way a real test command finds new files."""
        runner = 'import glob,runpy,sys\nfor f in sorted(glob.glob("test_*.py")): runpy.run_path(f)\nrunpy.run_path("check.py")\n'
        self.write('runner.py', runner)
        return {**self.plan, 'steps': [Step('tests', 'test', PY + ['runner.py'], platform='python')]}

    EDGE_PASS = 'test_edge.py\n<<<<<<< SEARCH\n=======\nimport a\nassert a.value() == 2\n>>>>>>> REPLACE'
    EDGE_FAIL = 'test_edge.py\n<<<<<<< SEARCH\n=======\nimport a\nassert a.value() == 9, "edge says 9"\n>>>>>>> REPLACE'

    def test_edge_step_is_off_unless_asked_for(self):
        report, scripted = self.run_with(good(), plan=self.edge_plan())
        self.assertEqual((report['state'], len(scripted.calls), report['edge_tests']['state']), ('verified', 1, 'off'))

    def test_edge_tests_that_pass_are_kept_and_named(self):
        report, scripted = self.run_with(good(), self.EDGE_PASS, plan=self.edge_plan(), edge_tests=True)
        self.assertEqual(report['state'], 'verified')
        self.assertEqual(report['edge_tests']['state'], 'passed')
        self.assertEqual(report['edge_tests']['new_files'], ['test_edge.py'])
        self.assertIn('test_edge.py', report['detail'])
        self.assertTrue((self.root / 'test_edge.py').is_file())
        self.assertIn('try to break your change', scripted.calls[1][-1]['content'])

    def test_edge_failure_goes_back_as_evidence_and_a_bug_gets_fixed(self):
        wrong_side = 'test_edge.py\n<<<<<<< SEARCH\nassert a.value() == 9, "edge says 9"\n=======\nassert a.value() == 2\n>>>>>>> REPLACE'
        report, scripted = self.run_with(good(), self.EDGE_FAIL, wrong_side, plan=self.edge_plan(), edge_tests=True)
        self.assertEqual((report['state'], report['edge_tests']['attempts']), ('verified', 2))
        self.assertIn('edge says 9', scripted.calls[2][-1]['content'])
        self.assertIn('decide which side is wrong', scripted.calls[2][-1]['content'])

    def test_edge_step_that_never_passes_is_undone_and_not_called_verified(self):
        report, _ = self.run_with(good(), self.EDGE_FAIL, 'nothing', 'nothing', plan=self.edge_plan(), edge_tests=True)
        self.assertEqual(report['state'], 'partially_verified')
        self.assertEqual(report['edge_tests']['state'], 'failed')
        self.assertFalse((self.root / 'test_edge.py').exists())
        self.assertIn('return 2', self.source())
        self.assertIn('did not finish', report['detail'])

    def test_a_repair_that_changes_nothing_is_called_out(self):
        same = good('return 2', 'return 2')
        report, scripted = self.run_with(good(), self.EDGE_FAIL, same, plan=self.edge_plan(), edge_tests=True)
        self.assertEqual(report['state'], 'partially_verified')
        self.assertIn('changed nothing', scripted.calls[3][-1]['content'])

    def test_reasoning_is_asked_for_only_on_the_chosen_turns(self):
        class Noting(Scripted):
            seen = []
            def complete(self, messages, **kwargs):
                self.seen.append(kwargs.get('think'))
                return super().complete(messages, **kwargs)
        tests = 'test_edge.py\n<<<<<<< SEARCH\n=======\nimport unittest\n>>>>>>> REPLACE\n'
        for options, wanted in ((Options(edge_tests=True), None), (Options(edge_tests=True, edge_think=True), True)):
            Noting.seen = []
            scripted = Noting(good(), tests)
            VibeRun(self.root, 'make value return 2', scripted, plan=self.edge_plan(), options=options).run()
            self.assertEqual(scripted.seen[:2], [None, wanted])
            self.setUp()
        Noting.seen = []
        scripted = Noting(good())
        VibeRun(self.root, 'make value return 2', scripted, plan=self.edge_plan(), options=Options(main_think=True)).run()
        self.assertEqual(scripted.seen, [True])

    def review_project(self):
        """Correct code, and a check that passes unless value() stops returning 2."""
        self.write('a.py', 'def value():\n    return 1\n')
        self.write('check.py', 'import a\nassert a.value() in (2, 9)\n')

    REVIEW_TEST = 'test_edge.py\n<<<<<<< SEARCH\n=======\nimport a\nassert a.value() == 9, "edge says 9"\n>>>>>>> REPLACE'
    BEND = 'a.py\n<<<<<<< SEARCH\n    return 2\n=======\n    return 9\n>>>>>>> REPLACE'
    FIXED_TEST = 'test_edge.py\n<<<<<<< SEARCH\nassert a.value() == 9, "edge says 9"\n=======\nassert a.value() == 2\n>>>>>>> REPLACE'

    def reviewed_run(self, replies, verdicts, **options):
        """Review calls are told apart by their system prompt, so they get their own script."""
        class Both(Scripted):
            def __init__(self):
                super().__init__(*replies)
                self.verdicts, self.review_prompts, self.review_kwargs = list(verdicts), [], []
            def complete(self, messages, **kwargs):
                if messages[0]['content'] == oracle.REVIEW_SYSTEM:
                    self.review_prompts.append(messages)
                    self.review_kwargs.append(kwargs)
                    return {'text': self.verdicts.pop(0) if self.verdicts else 'OK', 'output_tokens': 1, 'done_reason': 'stop'}
                return super().complete(messages, **kwargs)
        scripted = Both()
        plan = self.edge_plan()
        report = VibeRun(self.root, 'make value return 2', scripted, plan=plan, options=Options(edge_tests=True, edge_review=True, **options)).run()
        return report, scripted

    def test_code_bent_to_a_rejected_test_does_not_stay(self):
        self.review_project()
        report, scripted = self.reviewed_run([good(), self.REVIEW_TEST, self.BEND, self.FIXED_TEST], ['WRONG: 2', 'OK'])
        self.assertIn('return 2', self.source())
        self.assertNotIn('return 9', self.source())
        self.assertEqual(report['state'], 'verified')
        self.assertEqual(report['edge_tests']['rejected'], ['assert a.value() == 9, "edge says 9"'])

    def test_a_wrong_test_never_fixed_is_not_verified(self):
        self.review_project()
        report, scripted = self.reviewed_run([good(), self.REVIEW_TEST, self.REVIEW_TEST], ['WRONG: 2'])
        self.assertEqual(report['state'], 'partially_verified')
        self.assertIn('return 2', self.source())
        self.assertFalse((self.root / 'test_edge.py').exists())

    def test_the_reviewer_never_sees_the_code(self):
        self.review_project()
        self.write('a.py', 'def value():\n    secret_marker = 1\n    return 1\n')
        report, scripted = self.reviewed_run([good(), self.REVIEW_TEST, self.FIXED_TEST], ['WRONG: 2', 'OK'])
        self.assertTrue(scripted.review_prompts)
        for messages in scripted.review_prompts:
            joined = '\n'.join(m['content'] for m in messages)
            self.assertNotIn('secret_marker', joined)
            self.assertNotIn('def value', joined)
            self.assertIn('make value return 2', joined)
        self.assertTrue(scripted.review_kwargs[0]['think'])

    def test_a_replayed_ordinal_bend_is_rejected(self):
        self.write('a.py', "def ordinal(n):\n    return 1\n")
        self.write('check.py', 'import a\n')
        start = "a.py\n<<<<<<< SEARCH\n    return 1\n=======\n    if n % 100 in (11, 12, 13):\n        return str(n) + 'th'\n    return str(n) + ['th', 'st', 'nd', 'rd', 'th', 'th', 'th', 'th', 'th', 'th'][n % 10]\n>>>>>>> REPLACE"
        wrong = "test_edge.py\n<<<<<<< SEARCH\n=======\nimport a\nassert a.ordinal(121) == '121th'\n>>>>>>> REPLACE"
        bend = "a.py\n<<<<<<< SEARCH\n    if n % 100 in (11, 12, 13):\n=======\n    if n % 100 in (11, 12, 13) or (n % 10 == 1 and n > 10):\n>>>>>>> REPLACE"
        report, scripted = self.reviewed_run([start, wrong, bend, wrong], ['WRONG: 2'])
        self.assertNotIn('n > 10', self.source())
        self.assertNotEqual(report['state'], 'verified')

    def test_tests_that_survive_mutation_are_not_verified(self):
        self.write('a.py', 'def value(x):\n    return 1\n')
        self.write('check.py', 'import a\n')
        change = 'a.py\n<<<<<<< SEARCH\n    return 1\n=======\n    if x > 0:\n        return x + 1\n    return x == 0\n>>>>>>> REPLACE'
        weak = 'test_edge.py\n<<<<<<< SEARCH\n=======\nimport a\nassert a.value is not None\n>>>>>>> REPLACE'
        also = lambda line, more: f'test_edge.py\n<<<<<<< SEARCH\n{line}\n=======\n{line}\n{more}\n>>>>>>> REPLACE'
        report, scripted = self.reviewed_run([change, weak, also('assert a.value is not None', 'assert a is not None'),
                                              also('assert a is not None', 'assert a.value')], [])
        self.assertEqual(report['state'], 'partially_verified')
        self.assertIn('deliberately broken', report['detail'])
        self.assertEqual(report['edge_tests']['mutants_survived'], report['edge_tests']['mutants'])
        self.assertIn('x + 1', self.source())

    def test_tests_that_catch_mutation_stay_verified(self):
        self.write('a.py', 'def value(x):\n    return 1\n')
        self.write('check.py', 'import a\n')
        change = 'a.py\n<<<<<<< SEARCH\n    return 1\n=======\n    if x > 0:\n        return x + 1\n    return x\n>>>>>>> REPLACE'
        strong = 'test_edge.py\n<<<<<<< SEARCH\n=======\nimport a\nassert a.value(3) == 4\nassert a.value(0) == 0\nassert a.value(1) == 2\n>>>>>>> REPLACE'
        report, scripted = self.reviewed_run([change, strong], [])
        self.assertEqual(report['state'], 'verified')
        self.assertGreaterEqual(report['edge_tests']['mutants'], 2)
        self.assertEqual(report['edge_tests']['mutants_survived'], 0)
        self.assertNotIn('<= 0', self.source())

    def test_stop_during_mutation_restores_the_file(self):
        self.write('a.py', 'def value(x):\n    return 1\n')
        self.write('check.py', 'import a\n')
        change = 'a.py\n<<<<<<< SEARCH\n    return 1\n=======\n    if x > 0:\n        return x + 1\n    return x\n>>>>>>> REPLACE'
        run = VibeRun(self.root, 'task', Scripted(change), plan=self.edge_plan(), options=Options(edge_tests=True, edge_review=True))
        edits.apply(self.root, edits.parse(change), run.journal)
        changed = self.source()
        real = platforms.run_steps
        def stop_then_run(*args, **kwargs):
            run.cancel.set()
            raise RuntimeError('stopped mid-check')
        platforms.run_steps = stop_then_run
        try:
            with self.assertRaises(RuntimeError):
                run._surviving_mutants()
        finally:
            platforms.run_steps = real
        self.assertEqual(self.source(), changed)

    def test_a_runaway_reply_ends_the_edge_step_at_once(self):
        class Runaway(Scripted):
            def complete(self, messages, **kwargs):
                reply = super().complete(messages, **kwargs)
                return {**reply, 'done_reason': 'length'} if len(self.calls) > 1 else reply
        scripted = Runaway(good(), 'test_edge.py\n<<<<<<< SEARCH\n=======\nassert')
        report = VibeRun(self.root, 'make value return 2', scripted, plan=self.edge_plan(), options=Options(edge_tests=True)).run()
        self.assertEqual((report['state'], len(scripted.calls)), ('partially_verified', 2))
        self.assertIn('length limit', report['detail'])
        self.assertIn('return 2', self.source())

    def test_a_reply_with_no_tests_does_not_count(self):
        source_only = good('return 2', 'return 2 # same')
        report, _ = self.run_with(good(), source_only, source_only, source_only, plan=self.edge_plan(), edge_tests=True)
        self.assertEqual(report['state'], 'partially_verified')
        self.assertIn('added no tests', report['edge_tests']['detail'])
        self.assertNotIn('# same', self.source())

    def test_an_assertion_beside_the_function_cannot_be_rewritten(self):
        self.write('src/main.rs', 'fn greeting(name: &str) -> String {\n    format!("Hi, {name}")\n}\n\n#[test]\nfn greets() {\n    assert_eq!(\n        greeting("Sumi"),\n        "Hello, Sumi",\n    );\n}\n')
        run = VibeRun(self.root, 'make greeting say Hello', Scripted('unused'),
                      plan={'platforms': ['rust'], 'steps': [], 'runnable': 0, 'has_tests': False, 'can_prove': False, 'blocked': []})
        run._original_tests = {}
        weaken = edits.parse('src/main.rs\n<<<<<<< SEARCH\n        "Hello, Sumi",\n=======\n        "Hi, Sumi",\n>>>>>>> REPLACE')
        edits.apply(self.root, weaken, run.journal)
        self.assertIn('assertion', run._weakened_tests())
        run.journal.rollback()
        fix = edits.parse('src/main.rs\n<<<<<<< SEARCH\n    format!("Hi, {name}")\n=======\n    format!("Hello, {name}")\n>>>>>>> REPLACE')
        edits.apply(self.root, fix, run.journal)
        self.assertEqual(run._weakened_tests(), '')
        self.assertIn('assert_eq!', (self.root / 'src/main.rs').read_text(encoding='utf-8'))
        self.assertIn('Hello, {name}', (self.root / 'src/main.rs').read_text(encoding='utf-8'))

    def test_a_repair_cannot_make_a_test_pass_by_changing_it(self):
        self.write('test_value.py', 'import a\nraise SystemExit(0 if a.value() == 2 else 1)\n')
        plan = {'platforms': ['python'], 'steps': [Step('tests', 'test', PY + ['test_value.py'], platform='python')],
                'runnable': 1, 'has_tests': True, 'can_prove': True, 'blocked': []}
        weaken = 'test_value.py\n<<<<<<< SEARCH\nraise SystemExit(0 if a.value() == 2 else 1)\n=======\nraise SystemExit(0)\n>>>>>>> REPLACE'
        scripted = Scripted(weaken, good())
        report = VibeRun(self.root, 'make value return 2', scripted, plan=plan, options=Options(baseline=False)).run()
        self.assertEqual(report['state'], 'verified')
        self.assertIn('return 2', self.source())
        self.assertIn('a.value() == 2', (self.root / 'test_value.py').read_text(encoding='utf-8'))
        self.assertIn('test line', scripted.calls[1][-1]['content'])

    def test_a_task_that_names_a_test_file_may_change_it(self):
        self.write('test_value.py', 'import a\nraise SystemExit(0 if a.value() == 2 else 1)\n')
        plan = {'platforms': ['python'], 'steps': [Step('tests', 'test', PY + ['test_value.py'], platform='python')],
                'runnable': 1, 'has_tests': True, 'can_prove': True, 'blocked': []}
        weaken = 'test_value.py\n<<<<<<< SEARCH\nraise SystemExit(0 if a.value() == 2 else 1)\n=======\nraise SystemExit(0)\n>>>>>>> REPLACE'
        scripted = Scripted(weaken)
        report = VibeRun(self.root, 'Update test_value.py so the current value passes', scripted, plan=plan, options=Options(baseline=False)).run()
        self.assertEqual(report['state'], 'verified')
        self.assertIn('raise SystemExit(0)\n', (self.root / 'test_value.py').read_text(encoding='utf-8'))

    def test_existing_tests_cannot_be_weakened_in_the_edge_step(self):
        plan = self.edge_plan()
        self.write('test_old.py', 'import a\nassert a.value() == 2\n')
        weaken = 'test_old.py\n<<<<<<< SEARCH\nassert a.value() == 2\n=======\nassert True\n>>>>>>> REPLACE'
        report, scripted = self.run_with(good(), weaken, self.EDGE_PASS, plan=plan, edge_tests=True)
        self.assertEqual(report['state'], 'verified')
        self.assertIn('assert a.value() == 2', (self.root / 'test_old.py').read_text())
        self.assertIn('existed before', scripted.calls[2][-1]['content'])

    def test_adding_to_an_existing_test_counts(self):
        plan = self.edge_plan()
        self.write('test_old.py', 'import a\nassert a.value() == 2\n')
        extend = 'test_old.py\n<<<<<<< SEARCH\nassert a.value() == 2\n=======\nassert a.value() == 2\nassert a.value() > 0\n>>>>>>> REPLACE'
        report, _ = self.run_with(good(), extend, plan=plan, edge_tests=True)
        self.assertEqual((report['state'], report['edge_tests']['files'], report['edge_tests']['new_files']), ('verified', ['test_old.py'], []))

    def test_a_new_test_the_command_never_runs_is_rejected(self):
        self.edge_plan()
        report, scripted = self.run_with(good(), self.EDGE_PASS, 'nothing', 'nothing', edge_tests=True)
        self.assertEqual(report['state'], 'partially_verified')
        self.assertFalse((self.root / 'test_edge.py').exists())
        self.assertIn('does not run test_edge.py', scripted.calls[2][-1]['content'])

    def test_project_without_a_test_step_skips_the_edge_step(self):
        plan = {**self.plan, 'steps': [Step('build', 'build', PY + ['check.py'], platform='python')]}
        report, scripted = self.run_with(good(), plan=plan, edge_tests=True)
        self.assertEqual((report['state'], report['edge_tests']['state'], len(scripted.calls)), ('verified', 'skipped', 1))

    def test_failed_run_restores_the_project(self):
        report, _ = self.run_with(good('return 1', 'return 3'), good('return 3', 'return 4'), max_attempts=2)
        self.assertEqual(report['state'], 'failed')
        self.assertTrue(report['rolled_back'])
        self.assertEqual(self.source(), 'def value():\n    return 1\n')

    def test_repeated_failure_stops_early_as_stuck(self):
        replies = [good('return 1', 'return 3')] + [good('return 3', 'return 3 ')] * 6
        report, scripted = self.run_with(*replies, max_attempts=8)
        self.assertEqual(report['state'], 'stuck')
        self.assertLess(report['attempts'], 8)
        self.assertTrue(any('different approach' in m['content'] for call in scripted.calls for m in call))

    def test_unusable_replies_are_fed_back_then_reported(self):
        report, scripted = self.run_with('I would change the function.', max_attempts=2)
        self.assertEqual(report['state'], 'no_change')
        self.assertIn('could not be applied', scripted.calls[1][-1]['content'])
        self.assertEqual(self.source(), 'def value():\n    return 1\n')

    def test_build_config_edit_is_held_back(self):
        self.write('package.json', '{}\n')
        report, _ = self.run_with('package.json\n<<<<<<< SEARCH\n{}\n=======\n{"a":1}\n>>>>>>> REPLACE', max_attempts=1)
        self.assertEqual(report['state'], 'no_change')
        self.assertEqual((self.root / 'package.json').read_text(), '{}\n')

    def test_no_checks_means_unverified_and_keeps_the_edit(self):
        plan = {'platforms': [], 'steps': [], 'runnable': 0, 'has_tests': False, 'can_prove': False, 'blocked': []}
        report, _ = self.run_with(good(), plan=plan)
        self.assertEqual(report['state'], 'unverified')
        self.assertIn('return 2', self.source())

    def test_blocked_check_can_never_become_verified(self):
        plan = dict(self.plan, steps=self.plan['steps'] + [Step('swift build', 'build', blocked='needs macOS', platform='ios')])
        report, _ = self.run_with(good(), plan=plan)
        self.assertEqual(report['state'], 'partially_verified')
        self.assertIn('needs macOS', report['detail'])

    def test_static_checks_alone_are_not_verified(self):
        plan = dict(self.plan, steps=[Step('compile', 'static', PY + ['-c', 'pass'], platform='python')])
        report, _ = self.run_with(good(), plan=plan)
        self.assertEqual(report['state'], 'partially_verified')

    def test_model_outage_is_reported_and_nothing_changes(self):
        report, _ = self.run_with(RuntimeError('connection refused'))
        self.assertEqual(report['state'], 'model_unavailable')
        self.assertEqual(self.source(), 'def value():\n    return 1\n')

    def test_cancel_during_generation_rolls_back(self):
        cancel = threading.Event()

        class Stopper(Scripted):
            def complete(self, messages, **kwargs):
                if len(self.calls) == 1:
                    cancel.set()
                return super().complete(messages, **kwargs)
        stopper = Stopper(good('return 1', 'return 3'), good('return 3', 'return 2'))
        report = VibeRun(self.root, 'x', stopper, plan=self.plan, cancel=cancel).run()
        self.assertEqual(report['state'], 'cancelled')
        self.assertEqual(self.source(), 'def value():\n    return 1\n')

    def test_touching_tests_is_flagged(self):
        report, _ = self.run_with(good() + '\n\ntest_extra.py\n<<<<<<< SEARCH\n=======\nx = 1\n>>>>>>> REPLACE')
        self.assertTrue(report['tests_edited'])

    def test_events_name_only_steps_that_happened(self):
        run = VibeRun(self.root, 'make value return 2', Scripted(good()), plan=self.plan, options=Options())
        report = run.run()
        names = [event.get('name') for event in run.events]
        self.assertEqual(report['state'], 'verified')
        self.assertEqual(names[0], 'task_started')
        for name in ('project_inspection', 'plan_updated', 'context_retrieval', 'file_changed', 'test_started', 'test_completed', 'checkpoint_saved', 'task_completed'):
            self.assertIn(name, names)
        self.assertNotIn('repair_started', names)

    def test_recall_stays_off_the_stable_prefix(self):
        note = 'Previous run on these exact files ended failed. End-to-end verified: False.'
        _, scripted = self.run_with(good(), recall=note)
        self.assertNotIn('Previous run', scripted.calls[0][1]['content'])
        self.assertIn(note, scripted.calls[0][-1]['content'])

    def test_unchanged_files_do_not_run_a_passing_check_again(self):
        counter = self.root.parent / (self.root.name + '-count.txt')
        code = f'from pathlib import Path\np=Path({str(counter)!r})\np.write_text(str(int(p.read_text())+1) if p.exists() else "1")\n'
        plan = {'platforms': ['python'], 'steps': [Step('tests', 'test', PY + ['-c', code], platform='python')],
                'runnable': 1, 'has_tests': True, 'can_prove': True, 'blocked': []}
        same = good('return 1', 'return 1')
        run = VibeRun(self.root, 'keep the value', Scripted(same), plan=plan, options=Options())
        report = run.run()
        self.assertEqual(report['state'], 'verified')
        self.assertEqual(counter.read_text(encoding='utf-8'), '1')
        self.assertTrue(any(event.get('reused') for event in run.events))

    def test_a_call_with_the_wrong_number_of_arguments_is_not_accepted(self):
        self.write('a.py', 'def ready(name):\n    return name\n')
        (self.root / 'check.py').unlink()
        self.write('b.py', 'import a\n\ndef use():\n    return a.ready("x")\n')
        plan = {'platforms': ['python'], 'steps': [Step('tests', 'test', PY + ['-c', 'print("ok")'], platform='python')],
                'runnable': 1, 'has_tests': True, 'can_prove': True, 'blocked': []}
        broken = good('def ready(name):\n    return name', 'def ready():\n    return 1')
        restored = good('def ready():\n    return 1', 'def ready(name):\n    return name')
        scripted = Scripted(broken, restored)
        report = VibeRun(self.root, 'Keep ready(name).', scripted, plan=plan, options=Options(baseline=False)).run()
        self.assertEqual((report['state'], report['attempts']), ('verified', 2))
        self.assertIn('1 argument', scripted.calls[1][-1]['content'])

    def test_a_call_to_a_removed_name_is_not_accepted(self):
        self.write('b.py', 'import a\n\ndef use():\n    return a.value()\n')
        plan = {'platforms': ['python'], 'steps': [Step('tests', 'test', PY + ['-c', 'print("ok")'], platform='python')],
                'runnable': 1, 'has_tests': True, 'can_prove': True, 'blocked': []}
        broken = good('def value():\n    return 1', 'def other():\n    return 1')
        restored = good('def other():\n    return 1', 'def value():\n    return 1')
        scripted = Scripted(broken, restored)
        report = VibeRun(self.root, 'Rename the helper.', scripted, plan=plan, options=Options(baseline=False)).run()
        self.assertEqual((report['state'], report['attempts']), ('verified', 2))
        self.assertIn('does not define it', scripted.calls[1][-1]['content'])


class Manager(Temp):
    def make(self, scripted):
        return VibeManager(self.root / 'data', GpuScheduler(), client_factory=lambda model, budget=None: scripted)

    def test_refuses_while_gpu_is_reserved(self):
        manager = self.make(Scripted('x'))
        manager.scheduler.acquire('training-1')
        with self.assertRaisesRegex(ValueError, 'reserved'):
            manager.start(self.root, 'task')

    def test_only_sumi_and_rei(self):
        with self.assertRaisesRegex(ValueError, 'Sumi'):
            self.make(Scripted('x')).start(self.root, 'task', model='llama3:latest')

    def test_named_events_are_delivered_and_a_file_change_drops_memory(self):
        from takumi.vibe.memory import EngineeringMemory
        seen = []
        project = self.root / 'proj'
        project.mkdir()
        (project / 'a.py').write_text('x = 1\n', encoding='utf-8')
        manager = self.make(Scripted('no edits'))
        manager.start(project, 'task', max_attempts=1, listen=seen.append)
        manager.thread.join(60)
        self.assertIn('task_started', [event.get('name') for event in seen])
        self.assertIn('task_completed', [event.get('name') for event in seen])
        recalled = manager.memory.recall(project)
        self.assertTrue(recalled['fresh'])
        self.assertIn('ended no_change', recalled['summary'])
        self.assertNotIn('End-to-end verified: True', recalled['summary'])
        (project / 'a.py').write_text('x = 2\n', encoding='utf-8')
        dropped = manager.memory.recall(project)
        self.assertFalse(dropped['fresh'])
        self.assertEqual(dropped['reason'], 'files changed')
        self.assertIsInstance(manager.memory, EngineeringMemory)

    def test_runs_in_background_and_saves_a_report(self):
        self.write('a.py', 'def value():\n    return 1\n')
        manager = self.make(Scripted('no edits'))
        manager.start(self.root, 'task', max_attempts=1)
        manager.thread.join(60)
        status = manager.status()
        self.assertEqual(status['current']['state'], 'no_change')
        self.assertEqual(len(status['history']), 1)

    def test_blocks_training_while_running(self):
        release = threading.Event()

        class Slow(Scripted):
            def complete(self, messages, **kwargs):
                release.wait(20)
                return super().complete(messages, **kwargs)
        self.write('a.py', 'x = 1\n')
        manager = self.make(Slow('none'))
        scheduler = GpuScheduler(is_model_busy=manager.is_busy)
        manager.start(self.root, 'task', max_attempts=1)
        with self.assertRaises(ValueError):
            scheduler.acquire('training-1')
        release.set()
        manager.thread.join(60)


class Scaffold(Temp):
    def test_never_overwrites(self):
        self.write('existing.txt', 'keep')
        with self.assertRaisesRegex(ValueError, 'empty'):
            scaffold.create('web', self.root)
        self.assertEqual((self.root / 'existing.txt').read_text(), 'keep')

    def test_every_platform_is_detected_as_itself(self):
        for platform in scaffold.PLATFORMS:
            with self.subTest(platform):
                folder = self.root / platform
                scaffold.create(platform, folder, 'Notes App')
                self.assertIn(platform, platforms.detect(folder)['platforms'])

    def test_pwa_passes_its_own_contract(self):
        scaffold.create('pwa', self.root / 'p')
        self.assertEqual(platforms.check_pwa(self.root / 'p', 'manifest.webmanifest'), [])

    def test_android_manifest_and_ios_plist_pass_static_checks(self):
        scaffold.create('android', self.root / 'a')
        self.assertEqual(platforms.check_android_manifest(self.root / 'a', 'app/src/main/AndroidManifest.xml'), [])
        scaffold.create('ios', self.root / 'i')
        self.assertEqual(platforms.check_plist(self.root / 'i', 'Info.plist'), [])

    @unittest.skipUnless(shutil.which('node'), 'node')
    def test_web_starter_really_passes_with_node(self):
        scaffold.create('web', self.root / 'w')
        plan = platforms.detect(self.root / 'w')
        self.assertEqual(platforms.run_steps(self.root / 'w', plan['steps'])['state'], 'passed')

    def test_a_test_sdk_project_keeps_dotnet_test(self):
        folder = self.root / 'sdk'
        folder.mkdir()
        (folder / 'App.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"></Project>', encoding='utf-8')
        (folder / 'App.Tests.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PackageReference Include="Microsoft.NET.Test.Sdk" /></Project>', encoding='utf-8')
        steps = platforms.detect(folder)['steps']
        self.assertEqual([(step.name, step.kind) for step in steps], [('dotnet build', 'build'), ('dotnet test', 'test')])
        self.assertEqual(steps[0].argv[2], 'App.Tests.csproj')
        self.assertIn('test', steps[1].argv)
        self.assertNotIn('run', steps[1].argv)

    def test_plain_dotnet_tests_build_the_test_project_then_run_it(self):
        folder = self.root / 'plain'
        scaffold.create('dotnet', folder, 'Sample')
        steps = platforms.detect(folder)['steps']
        self.assertEqual([(step.name, step.kind) for step in steps], [('dotnet build', 'build'), ('dotnet run tests', 'test')])
        self.assertEqual(steps[0].argv[2], 'tests/Sample.Tests.csproj')
        self.assertEqual(steps[1].argv[3], 'tests/Sample.Tests.csproj')
        self.assertIn('--no-build', steps[1].argv)

    @unittest.skipUnless(shutil.which('dotnet'), 'dotnet')
    def test_dotnet_starter_really_builds_and_tests_the_greeting(self):
        folder = self.root / 'd'
        scaffold.create('dotnet', folder, 'Sample')
        plan = platforms.detect(folder)
        result = platforms.run_steps(folder, plan['steps'])
        self.assertEqual(result['state'], 'passed', result['diagnostics'])
        self.assertTrue(result['built'])
        self.assertTrue(result['tested'])
        self.assertFalse(result['ran'])
        program = folder / 'Program.cs'
        program.write_text(program.read_text(encoding='utf-8').replace('Hello, ', 'Hi, '), encoding='utf-8')
        failed = platforms.run_steps(folder, plan['steps'])
        self.assertEqual(failed['state'], 'failed')
        self.assertTrue(any('wrong greeting' in item.message for item in failed['diagnostics']))

    def test_a_test_harness_that_ran_nothing_is_recognized(self):
        self.assertTrue(platforms._no_tests_ran('running 0 tests\ntest result: ok. 0 passed; 0 failed'))
        self.assertTrue(platforms._no_tests_ran('running 1 test\ntest result: ok. 0 passed; 0 failed; 1 ignored'))
        self.assertFalse(platforms._no_tests_ran('running 0 tests\n0 passed; 0 failed\nrunning 1 test\n1 passed; 0 failed'))
        self.assertFalse(platforms._no_tests_ran('running 0 tests\nrunning 1 test\n.'))
        self.assertTrue(platforms._no_tests_ran('--- SKIP: TestGreeting (0.00s)\nPASS\nok \texample.com/z\t0.001s'))
        self.assertFalse(platforms._no_tests_ran('--- PASS: TestOther (0.00s)\n--- SKIP: TestGreeting (0.00s)\nPASS\nok'))
        self.assertTrue(platforms._no_tests_ran('# pass 0\n# fail 0\n# skipped 1'))
        self.assertFalse(platforms._no_tests_ran('# pass 1\n# fail 0\n# skipped 1'))
        self.assertTrue(platforms._no_tests_ran('Ran 1 test in 0.000s\n\nOK (skipped=1)'))
        self.assertFalse(platforms._no_tests_ran('Ran 2 tests in 0.000s\n\nOK (skipped=1)'))
        self.assertTrue(platforms._no_tests_ran('ok  example.com/z  0.001s [no tests to run]'))
        self.assertFalse(platforms._no_tests_ran('ok  example.com/z  0.001s'))

    def test_a_finished_command_closes_its_output_pipe(self):
        import gc
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always', ResourceWarning)
            code, output, timed_out = platforms._run_quiet([sys.executable, '-c', 'print("ok")'], self.root, 30)
            gc.collect()
        self.assertEqual((code, timed_out), (0, False))
        self.assertIn('ok', output)
        self.assertEqual([item for item in caught if issubclass(item.category, ResourceWarning)], [])

    @unittest.skipUnless(shutil.which('go'), 'go')
    def test_go_starter_builds_and_tests_the_greeting(self):
        folder = self.root / 'goapp'
        scaffold.create('go', folder, 'Sample')
        plan = platforms.detect(folder)
        self.assertEqual([step.name for step in plan['steps']], ['go build', 'go test'])
        result = platforms.run_steps(folder, plan['steps'])
        self.assertEqual(result['state'], 'passed', result['diagnostics'])
        self.assertTrue(result['built'])
        self.assertTrue(result['tested'])
        self.assertFalse(result['ran'])
        program = folder / 'main.go'
        program.write_text(program.read_text(encoding='utf-8').replace('Hello, ', 'Hi, '), encoding='utf-8')
        failed = platforms.run_steps(folder, plan['steps'])
        self.assertEqual(failed['state'], 'failed')
        self.assertTrue(failed['built'])
        self.assertFalse(failed['tested'])
        self.assertTrue(any(item.file == 'greeting_test.go' and 'Hi, Sumi' in item.message and 'Hello, Sumi' in item.message for item in failed['diagnostics']))

    @unittest.skipUnless(shutil.which('go'), 'go')
    def test_a_failing_go_test_is_repaired_through_the_loop(self):
        folder = self.root / 'repair'
        folder.mkdir()
        steps = '\n'.join(['\ttotal := 0'] + [f'\ttotal += {i}' for i in range(30)])
        (folder / 'go.mod').write_text('module example.com/greet\n\ngo 1.22\n', encoding='utf-8')
        (folder / 'main.go').write_text(
            'package main\n\nimport "fmt"\n\nfunc Greeting(name string) string {\n' + steps + '\n\tif total < 0 {\n\t\treturn name\n\t}\n\treturn "Hi, " + name\n}\n\nfunc main() {\n\tfmt.Println(Greeting("world"))\n}\n',
            encoding='utf-8')
        (folder / 'greeting_test.go').write_text(
            'package main\n\nimport "testing"\n\nfunc TestGreeting(t *testing.T) {\n\tgot := Greeting("Sumi")\n\tif got != "Hello, Sumi" {\n\t\tt.Fatalf("Greeting(%q) = %q, want %q", "Sumi", got, "Hello, Sumi")\n\t}\n}\n',
            encoding='utf-8')
        reply = ('main.go\n<<<<<<< SEARCH\nfunc Greeting(name string) string {\n\ttotal := 0\n... omitted middle ...\n\treturn "Hi, " + name\n}\n'
                 '=======\nfunc Greeting(name string) string {\n\ttotal := 0\n... omitted middle ...\n\treturn "Hello, " + name\n}\n>>>>>>> REPLACE')
        scripted = Scripted(reply)
        run = VibeRun(folder, 'Greeting should say Hello', scripted, options=Options(baseline=True))
        report = run.run()
        self.assertEqual(report['state'], 'verified', report)
        self.assertEqual(report['verification'], {'source_changed': True, 'source_validated': False, 'checks_executed': True,
                                                  'build_passed': True, 'tests_passed': True, 'runtime_passed': False, 'end_to_end': True})
        text = (folder / 'main.go').read_text(encoding='utf-8')
        self.assertIn('\treturn "Hello, " + name\n', text)
        self.assertIn('\ttotal += 20\n', text)
        self.assertIn('Hello, Sumi', scripted.calls[0][-1]['content'])
        names = [event.get('name') for event in run.events]
        for name in ('task_started', 'project_inspection', 'plan_updated', 'context_retrieval', 'diagnostic_found', 'file_changed', 'build_started', 'build_completed', 'test_started', 'test_completed', 'checkpoint_saved', 'task_completed'):
            self.assertIn(name, names)

    @unittest.skipUnless(shutil.which('cargo'), 'cargo')
    def test_rust_starter_builds_and_tests_the_greeting(self):
        folder = self.root / 'rustapp'
        scaffold.create('rust', folder, 'Sample')
        plan = platforms.detect(folder)
        self.assertEqual([step.name for step in plan['steps']], ['cargo build', 'cargo test'])
        result = platforms.run_steps(folder, plan['steps'])
        self.assertEqual(result['state'], 'passed', result['diagnostics'])
        self.assertTrue(result['built'])
        self.assertTrue(result['tested'])
        self.assertFalse(result['ran'])
        program = folder / 'src' / 'main.rs'
        program.write_text(program.read_text(encoding='utf-8').replace('Hello, {name}', 'Hi, {name}'), encoding='utf-8')
        failed = platforms.run_steps(folder, plan['steps'])
        self.assertEqual(failed['state'], 'failed')
        self.assertTrue(failed['built'])
        self.assertFalse(failed['tested'])
        self.assertTrue(any(item.file == 'src/main.rs' and 'Hello, Sumi' in item.message and 'Hi, Sumi' in item.message for item in failed['diagnostics']))

    @unittest.skipUnless(shutil.which('cargo'), 'cargo')
    def test_a_rust_assertion_in_main_cannot_be_deleted_to_pass(self):
        folder = self.root / 'rustfix'
        scaffold.create('rust', folder, 'Sample')
        program = folder / 'src' / 'main.rs'
        program.write_text(program.read_text(encoding='utf-8').replace('format!("Hello, {name}")', 'format!("Hi, {name}")', 1), encoding='utf-8')
        weaken = ('src/main.rs\n<<<<<<< SEARCH\n        assert_eq!(greeting("Sumi"), "Hello, Sumi");\n=======\n'
                  '        let _ = greeting("Sumi");\n>>>>>>> REPLACE')
        fix = ('src/main.rs\n<<<<<<< SEARCH\n    format!("Hi, {name}")\n=======\n    format!("Hello, {name}")\n>>>>>>> REPLACE')
        scripted = Scripted(weaken, fix)
        report = VibeRun(folder, 'make greeting say Hello', scripted, options=Options(baseline=True)).run()
        self.assertEqual(report['state'], 'verified', report)
        text = program.read_text(encoding='utf-8')
        self.assertIn('assert_eq!(greeting("Sumi"), "Hello, Sumi");', text)
        self.assertIn('format!("Hello, {name}")', text)
        self.assertIn('assertion', scripted.calls[1][-1]['content'])
        self.assertEqual(report['verification']['build_passed'], True)
        self.assertEqual(report['verification']['tests_passed'], True)
        self.assertEqual(report['verification']['end_to_end'], True)

    @unittest.skipUnless(shutil.which('cargo'), 'cargo')
    def test_removing_the_test_attribute_does_not_count_as_a_pass(self):
        folder = self.root / 'rustzero'
        scaffold.create('rust', folder, 'Sample')
        program = folder / 'src' / 'main.rs'
        program.write_text(program.read_text(encoding='utf-8').replace('format!("Hello, {name}")', 'format!("Hi, {name}")', 1), encoding='utf-8')
        weaken = 'src/main.rs\n<<<<<<< SEARCH\n    #[test]\n=======\n>>>>>>> REPLACE'
        fix = ('src/main.rs\n<<<<<<< SEARCH\n    fn greets_by_name() {\n=======\n    #[test]\n    fn greets_by_name() {\n>>>>>>> REPLACE\n'
               'src/main.rs\n<<<<<<< SEARCH\n    format!("Hi, {name}")\n=======\n    format!("Hello, {name}")\n>>>>>>> REPLACE')
        scripted = Scripted(weaken, fix)
        report = VibeRun(folder, 'make greeting say Hello', scripted, options=Options(baseline=True)).run()
        self.assertEqual(report['state'], 'verified', report)
        text = program.read_text(encoding='utf-8')
        self.assertIn('#[test]', text)
        self.assertIn('assert_eq!(greeting("Sumi"), "Hello, Sumi");', text)
        self.assertIn('format!("Hello, {name}")', text)
        self.assertIn('without running a test', scripted.calls[1][-1]['content'])

    @unittest.skipUnless(shutil.which('go'), 'go')
    def test_skipping_the_only_go_test_does_not_count_as_a_pass(self):
        folder = self.root / 'goskip'
        scaffold.create('go', folder, 'Sample')
        program = folder / 'main.go'
        program.write_text(program.read_text(encoding='utf-8').replace('return "Hello, " + name', 'return "Hi, " + name', 1), encoding='utf-8')
        weaken = ('greeting_test.go\n<<<<<<< SEARCH\nfunc TestGreeting(t *testing.T) {\n\tgot := Greeting("Sumi")\n=======\n'
                  'func TestGreeting(t *testing.T) {\n\tt.Skip("later")\n\tgot := Greeting("Sumi")\n>>>>>>> REPLACE')
        fix = ('greeting_test.go\n<<<<<<< SEARCH\n\tt.Skip("later")\n=======\n>>>>>>> REPLACE\n'
               'main.go\n<<<<<<< SEARCH\n\treturn "Hi, " + name\n=======\n\treturn "Hello, " + name\n>>>>>>> REPLACE')
        scripted = Scripted(weaken, fix)
        report = VibeRun(folder, 'Greeting should say Hello', scripted, options=Options(baseline=True)).run()
        self.assertEqual(report['state'], 'verified', report)
        self.assertNotIn('t.Skip', (folder / 'greeting_test.go').read_text(encoding='utf-8'))
        self.assertIn('return "Hello, " + name', program.read_text(encoding='utf-8'))
        self.assertIn('without running a test', scripted.calls[1][-1]['content'])
        self.assertTrue(report['verification']['tests_passed'])
        self.assertTrue(report['verification']['build_passed'])

    def test_skipping_the_only_unittest_does_not_count_as_a_pass(self):
        folder = self.root / 'pyskip'
        folder.mkdir()
        (folder / 'a.py').write_text('def value():\n    return 1\n', encoding='utf-8')
        (folder / 'test_value.py').write_text('import unittest\nimport a\n\nclass T(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(a.value(), 2)\n', encoding='utf-8')
        weaken = ('test_value.py\n<<<<<<< SEARCH\n    def test_value(self):\n=======\n    @unittest.skip("later")\n    def test_value(self):\n>>>>>>> REPLACE')
        fix = ('test_value.py\n<<<<<<< SEARCH\n    @unittest.skip("later")\n=======\n>>>>>>> REPLACE\n'
               'a.py\n<<<<<<< SEARCH\n    return 1\n=======\n    return 2\n>>>>>>> REPLACE')
        scripted = Scripted(weaken, fix)
        report = VibeRun(folder, 'make value return 2', scripted, options=Options(baseline=True)).run()
        self.assertEqual(report['state'], 'verified', report)
        self.assertNotIn('skip', (folder / 'test_value.py').read_text(encoding='utf-8'))
        self.assertIn('return 2', (folder / 'a.py').read_text(encoding='utf-8'))
        self.assertIn('without running a test', scripted.calls[1][-1]['content'])
        self.assertTrue(report['verification']['tests_passed'])
        self.assertFalse(report['verification']['build_passed'])

    @unittest.skipUnless(shutil.which('cargo'), 'cargo')
    def test_ignoring_the_only_test_does_not_count_as_a_pass(self):
        folder = self.root / 'rustignore'
        scaffold.create('rust', folder, 'Sample')
        program = folder / 'src' / 'main.rs'
        program.write_text(program.read_text(encoding='utf-8').replace('format!("Hello, {name}")', 'format!("Hi, {name}")', 1), encoding='utf-8')
        weaken = ('src/main.rs\n<<<<<<< SEARCH\n    #[test]\n    fn greets_by_name() {\n=======\n    #[test]\n    #[ignore]\n    fn greets_by_name() {\n>>>>>>> REPLACE')
        fix = ('src/main.rs\n<<<<<<< SEARCH\n    #[ignore]\n=======\n>>>>>>> REPLACE\n'
               'src/main.rs\n<<<<<<< SEARCH\n    format!("Hi, {name}")\n=======\n    format!("Hello, {name}")\n>>>>>>> REPLACE')
        scripted = Scripted(weaken, fix)
        report = VibeRun(folder, 'make greeting say Hello', scripted, options=Options(baseline=True)).run()
        self.assertEqual(report['state'], 'verified', report)
        text = program.read_text(encoding='utf-8')
        self.assertNotIn('#[ignore]', text)
        self.assertIn('assert_eq!(greeting("Sumi"), "Hello, Sumi");', text)
        self.assertIn('format!("Hello, {name}")', text)
        self.assertIn('without running a test', scripted.calls[1][-1]['content'])


class BatchSafety(unittest.TestCase):
    def test_batch_tools_refuse_when_ram_is_low(self):
        with mock.patch.object(client, 'available_ram_gib', return_value=0.9):
            with self.assertRaisesRegex(SystemExit, 'Nothing was run'):
                client.require_batch_memory()

    def test_unknown_or_sufficient_memory_is_allowed(self):
        for value in (None, 12.0):
            with mock.patch.object(client, 'available_ram_gib', return_value=value):
                client.require_batch_memory()

    def test_sweep_does_not_probe_beyond_the_proven_size(self):
        self.assertEqual(max(tuning.SIZES), 32768)


class Forge(Temp):
    def configure(self):
        home = self.root / 'ForgeUniversal'
        home.mkdir()
        (home / 'installation.json').write_bytes(b'\xef\xbb\xbf' + json.dumps({'python': sys.executable, 'launcher': str(home / 'forge.py')}).encode())
        (home / 'forge.py').write_text('print("forge 2026.11.3")\n')
        (home / 'opencode-forge.json').write_text(json.dumps({'mcp': {'forge': {'type': 'local', 'command': ['py', 'forge.py', 'mcp'], 'enabled': True, 'timeout': 20000}}}))
        return home

    def agent(self):
        agent = Agent.__new__(Agent)
        agent.cli_version = '1.19.5'
        return agent

    def test_absent_forge_is_reported_plainly(self):
        with mock.patch.dict(os.environ, {'TAKUMI_FORGE_HOME': str(self.root / 'absent')}):
            self.assertFalse(forge_bridge.status()['installed'])
            self.assertIsNone(forge_bridge.mcp_entry())

    def test_status_reads_install_with_bom(self):
        with mock.patch.dict(os.environ, {'TAKUMI_FORGE_HOME': str(self.configure())}):
            status = forge_bridge.status()
        self.assertTrue(status['usable'])
        self.assertTrue(status['mcp_available'])

    def test_agent_gets_forge_tools_only_when_owner_opts_in(self):
        with mock.patch.dict(os.environ, {'TAKUMI_FORGE_HOME': str(self.configure())}):
            self.assertNotIn('forge', self.agent()._config('sumi-1.0').get('mcp', {}))
            config = self.agent()._config('sumi-1.0', forge_enabled=True)
        self.assertEqual(config['mcp']['forge']['command'], ['py', 'forge.py', 'mcp'])
        self.assertEqual(config['permission']['external_directory'], 'deny')

    def test_opting_in_without_forge_changes_nothing(self):
        with mock.patch.dict(os.environ, {'TAKUMI_FORGE_HOME': str(self.root / 'absent')}):
            self.assertEqual(self.agent()._config('sumi-1.0', forge_enabled=True), self.agent()._config('sumi-1.0'))
