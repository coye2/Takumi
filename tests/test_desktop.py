import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from takumi.core import Storage
from takumi.desktop_controls import ProcessSupervisor
from takumi.remote import Pairing
from takumi.intelligence import profile_for


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Storage(self.root / 'data')
        self.proj = self.root / 'project'
        self.proj.mkdir()
        self.id = self.store.register_project(str(self.proj))['id']
        self.manager = ProcessSupervisor(self.store)

    def tearDown(self):
        for item in self.manager.list():
            self.manager.stop(item['id'])
        self.temp.cleanup()

    def test_reject_escape_and_missing(self):
        for path in ('../outside.exe','/usr/bin/sh','missing.exe'):
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.manager.launch(self.id, path)

    @unittest.skipIf(os.name == 'nt', 'Windows launch is checked in Windows CI')
    def test_start_and_stop_registered_executable(self):
        script = self.proj / 'worker.sh'
        script.write_text('#!/bin/sh\nsleep 30\n')
        script.chmod(0o755)
        started = self.manager.launch(self.id, 'worker.sh')
        self.assertEqual(started['project_id'], self.id)
        self.assertEqual(len(self.manager.list()), 1)
        self.assertTrue(self.manager.stop(started['id']))
        self.assertEqual(self.manager.list(), [])


class PairingTests(unittest.TestCase):
    def test_pair_and_revoke(self):
        with tempfile.TemporaryDirectory() as root:
            db = Pairing(Path(root))
            issued = db.new_code()['code']
            granted = db.pair(issued, 'test-phone')
            self.assertTrue(db.authorized(granted['token']))
            self.assertFalse(db.authorized('fake'))
            reloaded = Pairing(Path(root))
            self.assertTrue(reloaded.authorized(granted['token']))
            listing = reloaded.list_devices()
            self.assertEqual(len(listing),1)
            self.assertTrue(reloaded.revoke(listing[0]['id']))
            self.assertFalse(reloaded.authorized(granted['token']))

    def test_wrong_code_limited(self):
        with tempfile.TemporaryDirectory() as root:
            db = Pairing(Path(root))
            db.new_code()
            for i in range(6):
                with self.assertRaises(ValueError):
                    db.pair('wrong')
            with self.assertRaisesRegex(ValueError, 'Too many'):
                db.pair('00000000')


class ProfileTests(unittest.TestCase):
    def test_speed_changes_work_budget(self):
        normal = profile_for('standard', 'balanced')
        fast = profile_for('boost', 'balanced')
        self.assertLess(fast.context, normal.context)
        self.assertLess(fast.output_tokens, normal.output_tokens)
        self.assertFalse(fast.prefer_thinking)
        self.assertEqual(profile_for('boost', 'maximum').name, 'maximum')
