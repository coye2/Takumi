"""How Takumi shows its work, what it knows about today, and when it looks things up."""
from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import unittest

from takumi.core import time_context
from takumi.research import should_research

ROOT = Path(__file__).resolve().parents[1]


class CurrentFacts(unittest.TestCase):
    def test_the_model_is_told_the_date_and_not_to_guess(self):
        text = time_context(datetime(2026, 11, 6, 14, 5))
        self.assertIn('Friday, November 6, 2026', text)
        self.assertIn('who holds an office', text)
        self.assertIn('Never present it as current', text)

    def test_every_answer_path_gets_the_date(self):
        core = (ROOT / 'takumi' / 'core.py').read_text('utf-8')
        self.assertGreaterEqual(core.count('time_context()'), 2)
        self.assertIn('time_context()', (ROOT / 'takumi' / 'server.py').read_text('utf-8'))

    def test_questions_about_now_look_things_up(self):
        for question in ('who is the president', 'whos president rn', 'who is the prime minister of canada', 'who won the game last night', 'any news on the merger'):
            with self.subTest(question):
                self.assertTrue(should_research(question, 'auto'))

    def test_questions_that_need_no_source_do_not_search(self):
        for question in ('what is 2+2', 'what time is it', "what's the date today", '12 * 4'):
            with self.subTest(question):
                self.assertFalse(should_research(question, 'auto'))

    def test_local_coding_stays_local(self):
        self.assertFalse(should_research('fix the current test', 'auto'))


class Activity(unittest.TestCase):
    def setUp(self):
        self.app = (ROOT / 'web' / 'app.js').read_text('utf-8')

    def test_raw_reasoning_starts_closed_but_stays_available(self):
        self.assertIn("reason.className='reasoning-live';reason.open=false", self.app)
        self.assertIn('Text from the model. This is not the answer.', self.app)

    def test_internal_noise_is_gone_from_the_feed(self):
        for noise in ('First response received', 'tokens/sec measured', 'Found ${sources.length} reference leads', 'providers unavailable', 'Live steps and verification results', 'Connection lost · reconnecting'):
            self.assertNotIn(noise, self.app)

    def test_steps_read_as_finished_after_they_finish(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        start = self.app.index('const FINISHED_WORDS=')
        end = self.app.index('const TAKUMI_THEMES=')
        script = self.app[start:end] + "console.log(finishedStep('Inspecting project')+'|'+finishedStep('Checking live references')+'|'+finishedStep('Syntax check'))"
        done = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.stdout.strip(), 'Inspected project|Checked live references|Syntax check')

    def test_a_routine_resume_is_not_reported_as_an_outage(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        done = subprocess.run([node, str(ROOT / 'tests' / 'test_wake_reconnect.cjs')], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == '__main__':
    unittest.main()
