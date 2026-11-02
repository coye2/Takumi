import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from takumi.core import Storage
from takumi.goals import Goals
from takumi.harness import Harness, completion_contract
from takumi.completion import verify_completion


class GoalVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)/'project'; self.root.mkdir()
        self.store = Storage(Path(self.temp.name)/'data')
        self.pid = self.store.register_project(str(self.root))['id']
        self.harness = Harness(self.store)
        (self.root/'implementation.py').write_text('value = 0')
        (self.root/'oracle.py').write_text('from implementation import value\nassert value == 1, "expected value 1"\n')
        (self.root/'takumi.tasks.json').write_text(json.dumps({'version': 1, 'tasks': [
            {'id': 'regression', 'argv': ['python', 'oracle.py'], 'completion_check': True, 'protected_files': ['oracle.py']}]}))
        self.contract = completion_contract(self.root)
        self.lookup = patch('takumi.harness.shutil.which', return_value=sys.executable); self.lookup.start()

    def tearDown(self):
        self.lookup.stop()
        for run in self.harness.inspect():
            if run['status'] == 'running': self.harness.stop(run['id'])
        end = time.time() + 5
        while any(r['status'] == 'running' for r in self.harness.inspect()) and time.time() < end: time.sleep(.02)
        self.temp.cleanup()

    def verify(self, cancel=None, deadline=None):
        return verify_completion(self.harness, self.pid, self.contract, cancel or threading.Event(), deadline or time.time()+10)

    def test_real_process_rejects_failure_then_accepts_fixed_implementation(self):
        failed = self.verify()
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('expected value 1', failed['checks'][0]['tail'])
        # Different size avoids CPython's same-second .pyc timestamp/size cache.
        (self.root/'implementation.py').write_text('value = 1\n# fixed')
        passed = self.verify()
        self.assertEqual(passed['status'], 'passed')
        self.assertEqual(passed['checks'][0]['exit_code'], 0)

    def test_modified_oracle_and_restart_cannot_weaken_contract(self):
        goals = Goals(self.store.base)
        goal = goals.create('s', self.pid, 'fix the value', 30)
        goal['completion_contract'] = self.contract; goals.save(goal)
        self.contract = Goals(self.store.base).resume(goal['id'], 's', self.pid)['completion_contract']
        (self.root/'oracle.py').write_text('print("fake success")')
        self.assertEqual(self.verify()['status'], 'blocked')
        self.assertEqual(self.harness.inspect(), [])

    def test_stop_interrupts_only_owned_check_process(self):
        (self.root/'oracle.py').write_text('import time\ntime.sleep(30)')
        self.contract = completion_contract(self.root)
        cancel = threading.Event()
        timer = threading.Timer(.2, cancel.set); timer.start()
        started = time.monotonic()
        result = self.verify(cancel)
        timer.join()
        self.assertEqual(result['status'], 'stopped')
        self.assertLess(time.monotonic()-started, 4)

    def test_undeclared_and_gpu_checks_never_get_false_pass(self):
        self.assertEqual(verify_completion(self.harness, self.pid, {'tasks': []}, threading.Event(), time.time()+1)['status'], 'unverified')
        self.contract['tasks'][0]['gpu_exclusive'] = True
        self.assertEqual(self.verify()['status'], 'blocked')
        self.assertFalse(self.harness.inspect())


if __name__ == '__main__': unittest.main()
