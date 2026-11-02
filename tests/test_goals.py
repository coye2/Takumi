import tempfile
import unittest
from pathlib import Path
from takumi.goals import Goals


class GoalTests(unittest.TestCase):
    def test_deadline_survives_restart_and_waiting_and_cannot_expand(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = [100.0]
            goals = Goals(Path(tmp), clock=lambda: clock[0])
            goal = goals.create('s', 'p', 'Fix a tested bug', 10)
            goals.checkpoint(goal, state='blocked', run_id='r', native_session='native', report='Need evidence')
            clock[0] = 107
            restarted = Goals(Path(tmp), clock=lambda: clock[0])
            resumed = restarted.resume(goal['id'], 's', 'p')
            self.assertEqual(restarted.remaining(resumed), 3)
            self.assertEqual(resumed['deadline'], 110)
            self.assertEqual(resumed['checkpoints'][0]['native_session'], 'native')
            clock[0] = 111
            with self.assertRaisesRegex(ValueError, 'expired'):
                restarted.resume(goal['id'], 's', 'p')
            self.assertEqual(restarted.get(goal['id'])['state'], 'budget_exhausted')
            with self.assertRaises(ValueError):
                restarted.create('s', 'p', 'unbounded', 43201)
            with self.assertRaises(ValueError):
                restarted.resume(goal['id'], 'another', 'p')
            absolute = restarted.create('s', 'p', 'Queued with a preexisting deadline', 10, deadline=115)
            self.assertEqual(absolute['deadline'], 115)
            clock[0] = 115
            with self.assertRaisesRegex(ValueError, 'expired'):
                restarted.create('s', 'p', 'Expired while preparing', 10, deadline=115)


if __name__ == '__main__':
    unittest.main()
