"""Real loopback HTTP/restart tests; inference is deterministic fixture data."""
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from takumi import core, server
from takumi.runs import RunJournal, data_directory_lease


class JournalTests(unittest.TestCase):
    def test_other_process_cannot_recover_owned_data_and_lock_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = ('from takumi.runs import data_directory_lease; from pathlib import Path; '
                    'import sys\nwith data_directory_lease(Path(sys.argv[1])): print("owned")')
            with data_directory_lease(Path(tmp)):
                held = subprocess.run([sys.executable, '-c', code, tmp], capture_output=True, text=True)
                self.assertNotEqual(held.returncode, 0)
                self.assertIn('Another Takumi backend', held.stderr)
            released = subprocess.run([sys.executable, '-c', code, tmp], capture_output=True, text=True)
            self.assertEqual(released.returncode, 0, released.stderr)

    def test_queued_input_edits_and_removal_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = RunJournal(Path(tmp))
            first = journal.queue_input('s', 'r', 'original')
            second = journal.queue_input('s', 'r', 'remove this')
            journal.edit_input('s', first['id'], 'edited')
            journal.edit_input('s', second['id'])
            restarted = RunJournal(Path(tmp))
            self.assertEqual([x['text'] for x in restarted.consume_inputs('s')], ['edited'])
            self.assertEqual(restarted.consume_inputs('s'), [])
            with self.assertRaises(ValueError):
                restarted.edit_input('s', first['id'], 'too late')

    def test_replay_restart_and_conflicting_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = RunJournal(Path(tmp))
            run, created = journal.create('session', 'client-1', {'prompt': 'hello'})
            self.assertTrue(created)
            same, created = journal.create('session', 'client-1', {'prompt': 'hello'})
            self.assertFalse(created)
            self.assertEqual(run['id'], same['id'])
            with self.assertRaises(ValueError):
                journal.create('session', 'client-1', {'prompt': 'changed'})
            journal.append(run['id'], {'type': 'delta', 'text': '日本語'}, state='running')
            restarted = RunJournal(Path(tmp))
            restarted.recover()
            self.assertEqual(restarted.get(run['id'])['state'], 'outcome_unknown')
            events = restarted.events(run['id'])
            self.assertEqual([x['seq'] for x in events], [1, 2, 3])
            self.assertEqual(events[1]['payload']['text'], '日本語')
            self.assertEqual(restarted.events(run['id'], 2), events[2:])
            with self.assertRaises(ValueError):
                restarted.append(run['id'], {'type': 'done'})
            restarted.delete_session('session')
            self.assertIsNone(restarted.get(run['id']))
            self.assertEqual(restarted.events(run['id']), [])


class DurableHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.rt = server.Runtime(core.Storage(Path(self.temp.name)))
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.handler_factory(self.rt))
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'
        self.sid = self.rt.store.new_session()['id']
        self.payload = dict(session_id=self.sid, request_id='retry-safe', prompt='Hello',
                            model='rei-1.0:latest', mode='chat', speed='boost')
        self.patches = [patch('takumi.server.installed_models', return_value=['rei-1.0:latest']),
                        patch('takumi.server.should_research', return_value=False),
                        patch('takumi.server.safe_temperature', return_value={'available': False})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.rt.companion.stop()
        self.rt.kage.stop()
        self.rt.bots.close(stop_bots=True)
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def request(self, path, body=None):
        return urllib.request.urlopen(urllib.request.Request(self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'X-Takumi-Token': self.rt.token, 'Content-Type': 'application/json'}), timeout=10)

    def wait_finished(self, rid):
        until = time.monotonic() + 5
        while self.sid in self.rt.busy and time.monotonic() < until:
            time.sleep(.01)
        self.assertNotIn(self.sid, self.rt.busy)
        return self.rt.runs.get(rid)

    def test_disconnect_replay_and_duplicate_submission_execute_once(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def stream(*args, **kwargs):
            calls.append(1)
            yield {'type': 'delta', 'text': 'First 日本語'}
            entered.set()
            if not release.wait(5):
                raise RuntimeError('fixture timed out')
            yield {'type': 'delta', 'text': ' and final'}
        with patch('takumi.server.ui_event_stream', side_effect=stream):
            with self.request('/api/run', self.payload) as response:
                # Read a real accepted SSE ID, then disconnect before completion.
                first = response.readline().decode().strip()
                self.assertTrue(first.startswith('id: '))
                rid = first[4:].split(':')[0]
                self.assertTrue(entered.wait(2))
            self.assertEqual(self.rt.runs.get(rid)['state'], 'running')
            release.set()
            self.assertEqual(self.wait_finished(rid)['state'], 'completed')
            with self.request('/api/run', self.payload) as response:
                replay = response.read().decode()
            self.assertEqual(calls, [1])
            self.assertIn('First 日本語 and final', replay)
            self.assertEqual(len(self.rt.store.get_session(self.sid)['messages']), 2)
            with self.request(f'/api/runs/{rid}/events?after=1') as response:
                canonical = [json.loads(x[6:]) for x in response.read().decode().splitlines() if x.startswith('data: ')]
            self.assertEqual(canonical[0]['seq'], 2)
            self.assertEqual(canonical[-1]['type'], 'run.completed')

    def test_partial_failure_is_saved_and_never_retried(self):
        def stream(*args, **kwargs):
            yield {'type': 'delta', 'text': 'Useful partial report'}
            raise OSError('provider disconnected')
        with patch('takumi.server.ui_event_stream', side_effect=stream) as provider:
            with self.request('/api/run', self.payload) as response:
                response.read()
        self.assertEqual(provider.call_count, 1)
        message = self.rt.store.get_session(self.sid)['messages'][-1]
        self.assertEqual(message['content'], 'Useful partial report')
        self.assertEqual(message['status'], 'BLOCKED')
        self.assertEqual(self.rt.runs.for_session(self.sid)[0]['state'], 'failed')

    def test_kage_chat_is_dedicated_persistent_and_project_scoped(self):
        with self.request('/api/kage/chat', {}) as response:
            first = json.load(response)
        with self.request('/api/kage/chat', {}) as response:
            again = json.load(response)
        self.assertEqual(first['id'], again['id'])
        self.assertNotEqual(first['id'], self.sid)
        self.assertEqual(first['purpose'], 'kage')
        project = Path(self.temp.name) / 'project'
        project.mkdir()
        registered = self.rt.store.register_project(str(project))
        with self.request('/api/kage/chat', {'project_id': registered['id']}) as response:
            scoped = json.load(response)
        self.assertNotEqual(scoped['id'], first['id'])
        self.assertEqual(scoped['project_id'], registered['id'])

    def test_goal_deadline_stops_the_active_provider_and_persists_budget(self):
        project = Path(self.temp.name) / 'goal-project'
        project.mkdir()
        registered = self.rt.store.register_project(str(project))
        self.rt.store.get_session(self.sid)['project_id'] = registered['id']
        class Agent:
            def __init__(self):
                self.cancel = threading.Event()
            def stop(self):
                self.cancel.set()
            def run(self, *args, **kwargs):
                yield {'type': 'agent_session', 'id': 'goal-native'}
                yield {'type': 'delta', 'text': 'Checkpoint evidence'}
                self.cancel.wait(3)
                yield {'type': 'cancelled'}
        self.rt.agent_cls = Agent
        with patch('takumi.server.open_code_path', return_value='fixture'):
            with self.request('/api/run', {**self.payload, 'prompt': '/goal Investigate the fixture',
                                          'goal_seconds': 1}) as response:
                result = response.read().decode()
        goal = self.rt.goals.for_session(self.sid)[0]
        self.assertEqual(goal['state'], 'budget_exhausted')
        self.assertEqual(goal['deadline'] - goal['created'], 1)
        self.assertIn('Checkpoint evidence', result)
        self.assertIn('budget_exhausted', result)

    def test_stop_acknowledgment_is_not_termination_and_stale_stop_cannot_cancel(self):
        entered, release = threading.Event(), threading.Event()
        def stream(url, history, model, level, cancel, **kwargs):
            yield {'type': 'delta', 'text': 'Partial'}
            entered.set()
            release.wait(5)
            if cancel.is_set():
                yield {'type': 'cancelled'}
        with patch('takumi.server.ui_event_stream', side_effect=stream):
            response = self.request('/api/run', self.payload)
            self.assertTrue(entered.wait(2))
            rid = self.rt.active_runs[self.sid]
            self.assertFalse(self.rt.stop(self.sid, 'old-run'))
            self.assertFalse(self.rt.busy[self.sid][0].is_set())
            with self.request('/api/stop', {'session_id': self.sid, 'run_id': rid}) as stop:
                receipt = json.load(stop)
            self.assertTrue(receipt['stop_requested'])
            self.assertNotIn('stopped', receipt)
            self.assertEqual(self.rt.runs.get(rid)['state'], 'stopping')
            self.assertTrue(self.rt.stop(self.sid, rid))
            release.set()
            response.read()
            response.close()
            self.assertEqual(self.wait_finished(rid)['state'], 'stopped')
            self.assertEqual(self.rt.store.get_session(self.sid)['messages'][-1]['content'], 'Partial')


if __name__ == '__main__':
    unittest.main()
