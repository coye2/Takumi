import tempfile
import unittest
from pathlib import Path

from takumi.compiler_checks import find_tool
from takumi.source_index import SourceIndex


class SourceIndexTests(unittest.TestCase):
    def test_python_persistence_edit_delete_and_project_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); root = base / 'one'; root.mkdir(); other = base / 'two'; other.mkdir()
            (root / 'fence.py').write_text('def wait(value):\n    return value + 1\n\ndef render():\n    return wait(1)\n')
            index = SourceIndex(base / 'index.sqlite')
            first = index.refresh(root)
            self.assertEqual(index.query(first, 'wait', 'calls')['total'], 1)
            self.assertEqual(index.query(index.refresh(other), 'wait')['total'], 0)
            index = SourceIndex(base / 'index.sqlite')
            self.assertEqual(index.query(index.refresh(root), 'wait', 'symbols')['total'], 1)
            (root / 'fence.py').write_text('def release(value):\n    return value\n')
            self.assertEqual(index.query(index.refresh(root), 'wait')['total'], 0)
            (root / 'fence.py').unlink()
            self.assertEqual(index.query(index.refresh(root), 'release')['total'], 0)

    def test_a_constructor_call_targets_the_class_method(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'a.py').write_text('class Box:\n    def save(self, item):\n        return item\n', encoding='utf-8')
            (root / 'b.py').write_text('from a import Box\nimport a\n\ndef pay():\n    return Box().save(1)\n\ndef other():\n    return a.Box().save(1)\n', encoding='utf-8')
            (root / 'c.py').write_text('box = None\n\ndef use():\n    return box.save(1)\n', encoding='utf-8')
            (root / 'd.py').write_text('from a import Box\n\ndef pay():\n    box = Box()\n    return box.save(1)\n', encoding='utf-8')
            (root / 'e.py').write_text('from a import Box\n\ndef pay(box):\n    return box.save(1)\n', encoding='utf-8')
            (root / 'f.py').write_text('from a import Box\nclass Other:\n    def save(self):\n        return 0\n\ndef pay():\n    box = Box()\n    box = Other()\n    return box.save()\n', encoding='utf-8')
            (root / 'g.py').write_text('from a import Box\nbox = Box()\n\ndef pay():\n    return box.save(1)\n', encoding='utf-8')
            (root / 'h.py').write_text(
                'from a import Box\n\ndef shadowed(items):\n    box = Box()\n    return [box.save(1) for box in items]\n\n'
                'def kept(items):\n    box = Box()\n    return [box.save(1) for item in items]\n', encoding='utf-8')
            (root / 'pkg').mkdir()
            (root / 'pkg' / 'a.py').write_text('class Box:\n    def save(self):\n        return 1\n', encoding='utf-8')
            (root / 'pkg' / 'b.py').write_text('from .a import Box\n\ndef pay():\n    return Box().save()\n', encoding='utf-8')
            data = SourceIndex(root / 'index.sqlite').refresh(root)
            found = []
            for unit in data['units'].values():
                for call in unit['calls']:
                    if call['name'] == 'save':
                        found.append((call['path'], call['target']))
            self.assertIn(('b.py', 'py:a.Box.save'), found)
            self.assertEqual(found.count(('b.py', 'py:a.Box.save')), 2)
            self.assertIn(('c.py', 'py:c.box.save'), found)
            self.assertIn(('d.py', 'py:a.Box.save'), found)
            self.assertIn(('e.py', 'py:e.box.save'), found)
            self.assertIn(('f.py', 'py:f.box.save'), found)
            self.assertIn(('g.py', 'py:a.Box.save'), found)
            self.assertIn(('h.py', 'py:h.box.save'), found)
            self.assertEqual(sum(path == 'h.py' and target == 'py:a.Box.save' for path, target in found), 1)
            self.assertIn(('pkg/b.py', 'py:pkg.a.Box.save'), found)
            self.assertNotIn('()', ''.join(target for _, target in found))

    def test_an_unchanged_file_is_not_parsed_again(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'a.py').write_text('def stay():\n    return 1\n', encoding='utf-8')
            (root / 'b.py').write_text('def move():\n    return 1\n', encoding='utf-8')
            index = SourceIndex(root / 'index.sqlite')
            first = index.refresh(root)
            self.assertEqual((first['parsed'], first['reused']), (2, 0))
            again = index.refresh(root)
            self.assertEqual((again['parsed'], again['reused']), (0, 2))
            (root / 'b.py').write_text('def move():\n    return 2\n', encoding='utf-8')
            third = index.refresh(root)
            self.assertEqual((third['parsed'], third['reused']), (1, 1))
            self.assertEqual(third['units']['a.py']['symbols'][0]['name'], 'stay')
            self.assertIn('return 2', Path(root / 'b.py').read_text(encoding='utf-8'))

    def test_javascript_symbols_and_calls_are_indexed_without_clang(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); root = base / 'web'; root.mkdir()
            (root / 'view.mjs').write_text('export function paint() { return 1 }\n')
            (root / 'app.mjs').write_text("import { paint } from './view.mjs'\nexport function ready() { return paint() }\n")
            data = SourceIndex(base / 'index.sqlite').refresh(root)
            self.assertTrue(data['units']['app.mjs']['complete'])
            self.assertEqual(data['units']['app.mjs']['parser'], 'script-lexical')
            index = SourceIndex(base / 'index.sqlite')
            self.assertEqual(index.query(data, 'ready', 'symbols')['total'], 1)
            paint = index.query(data, 'paint', 'calls')['matches']
            self.assertEqual([(item['path'], item['target']) for item in paint], [('app.mjs', 'js:view.paint')])
            self.assertIn('paint', data['units']['view.mjs']['exports'])
            (root / 'a.mjs').write_text('export class Box {\n  save(item) {\n    return item\n  }\n}\n', encoding='utf-8')
            (root / 'b.mjs').write_text(
                "import { Box } from './a.mjs'\nexport function pay() {\n  const box = new Box()\n  return box.save(1)\n}\n"
                "export function made() {\n  return new Box().save(1)\n}\n"
                "export function loose(box) {\n  return box.save(1)\n}\n", encoding='utf-8')
            (root / 'c.mjs').write_text("import { Box } from './a.mjs'\nconst box = null\nexport function use() {\n  return box.save(1)\n}\n", encoding='utf-8')
            data = index.refresh(root)
            save = [symbol for symbol in data['units']['a.mjs']['symbols'] if symbol['name'] == 'save'][0]
            self.assertEqual((save['id'], save['line']), ('js:a.Box.save', 2))
            calls = [(call['path'], call['target']) for unit in data['units'].values() for call in unit['calls'] if call['name'] == 'save']
            self.assertIn(('b.mjs', 'js:a.Box.save'), calls)
            self.assertEqual(calls.count(('b.mjs', 'js:a.Box.save')), 2)
            self.assertNotIn(('b.mjs', 'js:b.box.save'), calls)
            self.assertFalse(any(path == 'c.mjs' and target.endswith('Box.save') for path, target in calls))

    @unittest.skipUnless(find_tool('clang'), 'Actual Clang is required')
    def test_forty_file_exact_cpu_to_gpu_path_and_header_invalidation(self):
        # The oracle is the ordered public entry-point list, not an index dump.
        expected = ['cpu_dispatch'] + [f'render_stage_{n}' for n in range(1, 39)] + ['gpu_submit']
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); root = base / 'repo'; root.mkdir()
            for i, name in enumerate(expected):
                text = f'void {expected[i+1]}();\nvoid {name}() {{ {expected[i+1]}(); }}\n' if i < 39 else 'void gpu_submit() {}\n'
                (root / f'unit_{i:02}.cpp').write_text(text)
            index = SourceIndex(base / 'index.sqlite'); data = index.refresh(root, budget=30)
            self.assertTrue(data['complete'], data['omitted'])
            trace = index.trace(data, 'cpu_dispatch', 'gpu_submit')
            self.assertEqual([x['name'] for x in trace['functions']], expected)
            self.assertEqual(len(trace['calls']), 39)
            (root / 'unit_19.cpp').write_text('void render_stage_19() {}\n')
            self.assertEqual(index.trace(index.refresh(root, budget=30), 'cpu_dispatch', 'gpu_submit')['status'], 'unverified')
            (root / 'defs.h').write_text('void before();\n#define TARGET before\n')
            (root / 'header.cpp').write_text('#include "defs.h"\nvoid entry() { TARGET(); }\n')
            self.assertEqual(index.query(index.refresh(root, budget=30), 'before', 'calls')['total'], 1)
            (root / 'defs.h').write_text('void after();\n#define TARGET after\n')
            changed = index.refresh(root, budget=30)
            self.assertEqual(index.query(changed, 'before', 'calls')['total'], 0)
            self.assertEqual(index.query(changed, 'after', 'calls')['total'], 1)

    @unittest.skipUnless(find_tool('clang'), 'Actual Clang is required')
    def test_fifty_file_calls_precision_recall_overloads_and_indirect(self):
        expected = {f'render_{i}.cpp' for i in range(20)} | {f'audio_{i}.cpp' for i in range(10)}
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); root = base / 'repo'; root.mkdir()
            for name in expected:
                caller = name[:-4]
                (root / name).write_text(f'void gpu_fence_wait(int);\nvoid {caller}(){{gpu_fence_wait(1);}}\n')
            for i in range(20):
                (root / f'decoy_{i}.cpp').write_text(f'// gpu_fence_wait(2)\nvoid gpu_fence_wait_extra(){{}}\nconst char* label_{i}="gpu_fence_wait";\n')
            index = SourceIndex(base / 'index.sqlite'); data = index.refresh(root, budget=30)
            hits = index.query(data, 'gpu_fence_wait', 'calls')
            self.assertEqual(hits['total'], 30)
            self.assertEqual({x['path'] for x in hits['matches']}, expected)
            (root / 'special.cpp').write_text('void gpu_fence_wait(int); void gpu_fence_wait(double);\n'
                'void render(){gpu_fence_wait(1);gpu_fence_wait(1.0);}\n'
                'void indirect(void (*f)(int)){ f(1); }\n')
            changed = index.refresh(root, budget=30)
            calls = [x for x in index.query(changed, 'gpu_fence_wait', 'calls')['matches'] if x['path'] == 'special.cpp']
            self.assertEqual(len(calls), 2)
            self.assertNotEqual(calls[0]['target'], calls[1]['target'])
            self.assertEqual(index.query(changed, '<indirect>', 'calls')['total'], 1)

    @unittest.skipUnless(find_tool('clang'), 'Actual Clang is required')
    def test_outside_include_is_not_read_and_indirect_result_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); root = base / 'repo'; root.mkdir()
            (base / 'secret.h').write_text('#error SECRET_CONTENT\n')
            (root / 'unsafe.cpp').write_text('#include "../secret.h"\n')
            (root / 'commented.cpp').write_text('#/**/include "../secret.h"\n')
            (root / 'digraph.cpp').write_text('%:include "../secret.h"\n')
            (root / 'calls.cpp').write_text('using Fn=void(*)(); Fn factory(); void entry(){factory()();}\n')
            idx = SourceIndex(base / 'i.sqlite'); data = idx.refresh(root)
            self.assertFalse(data['complete'])
            self.assertNotIn('SECRET_CONTENT', str(data))
            self.assertEqual(idx.query(data, 'factory', 'calls')['total'], 1)
            self.assertEqual(idx.query(data, '<indirect>', 'calls')['total'], 1)
