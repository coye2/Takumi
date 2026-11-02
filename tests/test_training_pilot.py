import json
from pathlib import Path
import tempfile
import unittest
from training.pilot.fixtures import TASKS, compile_candidate, verify, write_dataset


class PilotTests(unittest.TestCase):
    def test_references_pass_independent_oracles_and_holdouts_are_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'data.json'
            audit=write_dataset(path)
            self.assertEqual(sum(x['checked'] for x in audit),14*256)
            records=json.loads(path.read_text())['examples']
            self.assertEqual(len(records),8)
            self.assertEqual({x['id'] for x in records},{t[0] for t in TASKS if t[1]=='train'})
            train_projects={t[2] for t in TASKS if t[1]=='train'}
            self.assertFalse(train_projects & {t[2] for t in TASKS if t[1]!='train'})

    def test_candidate_cannot_execute_imports_or_unbounded_allocations(self):
        for code in ["def f(x):\n return __import__('os').getcwd()",
                     'def f(x):\n return 1<<99999999',
                     'def f(x):\n return [0]*99999999']:
            with self.subTest(code=code),self.assertRaises((ValueError,KeyError,TypeError)):
                compile_candidate(code,1)(1)
        self.assertFalse(verify('def f(a,b):\n return 0',TASKS[0])['passed'])
