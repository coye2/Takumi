"""Verified build from the paired iPhone PWA over Tailscale Serve."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from takumi.core import Storage
from takumi.server import Runtime, handler_factory

PHONE = {'Host': '127.0.0.1', 'X-Forwarded-Host': 'pc.tailnet.ts.net', 'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '100.40.20.1', 'X-Takumi-Token': 'phone'}


class VibeOverTailscale(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.runtime = Runtime(Storage(Path(cls.temp.name) / 'data'))
        cls.project = Path(cls.temp.name) / 'project'
        cls.project.mkdir()
        cls.project_id = cls.runtime.store.register_project(str(cls.project))['id']
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_factory(cls.runtime))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.runtime.companion.stop()
        cls.runtime.bots.close(stop_bots=False)
        cls.temp.cleanup()

    def setUp(self):
        for patcher in (mock.patch.dict(os.environ, {'TAKUMI_TAILSCALE_SERVE': '1'}), mock.patch.object(self.runtime.pairing, 'authorized', lambda token: token == 'phone')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def post(self, path, body, headers=PHONE):
        request = urllib.request.Request(self.base + path, json.dumps(body).encode(), {**headers, 'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)

    def status_of(self, path, body, headers=PHONE):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post(path, body, headers)
        return caught.exception.code

    def test_paired_phone_can_start_and_stop_a_run(self):
        with mock.patch.object(self.runtime.vibe, 'start', return_value={'current': None}) as start:
            status, _ = self.post('/api/vibe/start', {'project_id': self.project_id, 'task': 'fix it'})
        self.assertEqual(status, 202)
        self.assertEqual(start.call_args.args[1], 'fix it')
        self.assertFalse(start.call_args.kwargs['allow_config'])
        self.assertEqual(self.post('/api/vibe/stop', {})[0], 200)

    def test_unpaired_device_is_refused(self):
        self.assertIn(self.status_of('/api/vibe/start', {'project_id': self.project_id, 'task': 'x'}, {**PHONE, 'X-Takumi-Token': 'wrong'}), (401, 403))

    def test_pc_only_actions_stay_on_the_pc(self):
        self.assertEqual(self.status_of('/api/vibe/start', {'project_id': self.project_id, 'task': 'x', 'allow_config': True}), 403)
        self.assertEqual(self.status_of('/api/vibe/scaffold', {'platform': 'web', 'path': str(self.project / 'new')}), 403)
        self.assertFalse((self.project / 'new').exists())
