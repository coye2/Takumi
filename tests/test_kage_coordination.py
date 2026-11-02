import json
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from takumi import core, server
from takumi.kage import KageManager


class FixtureAgent:
    calls = 0
    entered = threading.Event()
    release = threading.Event()
    def __init__(self): self.cancel = threading.Event()
    def stop(self): self.cancel.set(); self.release.set()
    def run(self, *args, **kwargs):
        type(self).calls += 1
        yield {'type': 'agent_session', 'id': 'fixture-native'}
        self.entered.set()
        while not self.release.wait(.02) and not self.cancel.is_set(): pass
        yield {'type': 'delta', 'text': 'TAKUMI_STATUS: COMPLETE\nFixture report, no real model or compilation claimed.'}


class KageCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.project = self.root/'project'; self.project.mkdir()
        self.runtime = server.Runtime(core.Storage(self.root/'data'), agent_cls=FixtureAgent)
        self.runtime.kage.stop()
        self.pid = self.runtime.store.register_project(str(self.project))['id']
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.handler_factory(self.runtime))
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = 'http://127.0.0.1:' + str(self.http.server_port)
        FixtureAgent.calls = 0; FixtureAgent.entered.clear(); FixtureAgent.release.clear()
        self.patches = [patch('takumi.server.open_code_path', return_value='fixture-only'),
                        patch('takumi.server.safe_temperature', return_value={'available': False}),
                        patch('takumi.server.should_research', return_value=False)]
        for p in self.patches: p.start()

    def tearDown(self):
        FixtureAgent.release.set()
        for _ in range(100):
            if not self.runtime.busy: break
            time.sleep(.02)
        self.http.shutdown(); self.http.server_close()
        self.runtime.kage.stop(); self.runtime.companion.stop(); self.runtime.bots.close(stop_bots=True)
        for p in self.patches: p.stop()
        self.temp.cleanup()

    def post(self, path, body):
        request = urllib.request.Request(self.url+path, data=json.dumps(body).encode(), headers={'Content-Type':'application/json','X-Takumi-Token':self.runtime.token})
        with urllib.request.urlopen(request, timeout=10) as response: return json.loads(response.read())

    def queue(self):
        return self.post('/api/kage/goals', {'project_id': self.pid, 'prompt': 'Inspect the fixture', 'seconds': 30, 'model': 'fixture'})['job']

    def test_scoped_queue_serialization_stop_and_notification_ack(self):
        first = self.queue(); second = self.queue()
        self.runtime.kage.coordinate()
        self.assertTrue(FixtureAgent.entered.wait(3))
        self.runtime.kage.coordinate()
        self.assertEqual(FixtureAgent.calls, 1)
        jobs = self.runtime.kage.status()['jobs']
        self.assertEqual(jobs[0]['state'], 'running'); self.assertEqual(jobs[1]['state'], 'queued')
        self.post('/api/kage/goals/stop', {'job_id': second['id']})
        self.post('/api/kage/goals/stop', {'job_id': first['id']})
        for _ in range(100):
            if not self.runtime.busy: break
            time.sleep(.02)
        self.runtime.kage.coordinate()
        unread = self.runtime.kage.status()['unread']
        self.assertEqual(len(unread), 1)
        self.assertEqual(unread[0]['session_id'], first['session_id'])
        self.assertEqual(unread[0]['project_id'], self.pid)
        self.post('/api/kage/notifications/read', {'ids': [unread[0]['id']]})
        self.assertEqual(self.runtime.kage.status()['unread'], [])
        restarted = KageManager(self.runtime.store)
        self.assertEqual(restarted.status()['unread'], [])
        self.assertEqual(FixtureAgent.calls, 1)

    def test_waiting_budget_and_recovery_never_restart_clock_or_replay_tools(self):
        job = self.queue()
        actual = self.runtime.kage.jobs[0]
        actual['deadline'] = time.time() - 1; actual['payload']['goal_deadline'] = actual['deadline']
        self.runtime.kage.coordinate()
        self.assertEqual(self.runtime.kage.status()['jobs'][0]['state'], 'budget_exhausted')
        self.assertEqual(FixtureAgent.calls, 0)
        other = self.queue(); actual = self.runtime.kage.jobs[-1]
        run, _ = self.runtime.runs.create(other['session_id'], actual['payload']['request_id'], actual['payload'])
        self.runtime.runs.recover()
        self.runtime.kage.coordinate()
        self.assertEqual(self.runtime.kage.status()['jobs'][-1]['state'], 'outcome_unknown')
        self.assertEqual(FixtureAgent.calls, 0)
