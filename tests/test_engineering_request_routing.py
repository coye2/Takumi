"""Regression tests for legitimate engineering work reaching the real dispatch paths.

External inference/coding tools are replaced with small observable fakes. These
are application-routing tests, not proof of model capability or real emulation.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from takumi import core, server


TASKS = (
    'Implement a CPU emulation trace for a homebrew Game Boy test ROM; verify cycle timing.',
    'Fix ARM64 JIT/dynarec code-cache invalidation and add a deterministic regression test.',
    'Translate Vulkan SPIR-V and HLSL shaders; test GPU barriers and image layout transitions.',
    'Analyze a user-owned console ROM header and PE DLL symbols, relocations, and binary sections.',
    'Build a legally authorized N64 MIPS matching decompilation with section provenance and hash comparisons.',
    'Investigate an unknown Switch 2 command processor path; prototype instrumentation even if the fix is difficult.',
)


class RequestDispatchAgent:
    calls: list[dict] = []

    def __init__(self):
        self.cancel = threading.Event()

    def stop(self):
        self.cancel.set()

    def run(self, prompt, project, model, native_session, variant):
        self.__class__.calls.append({'prompt': prompt, 'project': str(project), 'model': model,
                                    'native_session': native_session, 'variant': variant})
        yield {'type': 'agent_session', 'id': 'ses_engineering'}
        yield {'type': 'activity', 'activity': {'kind': 'progress', 'title': 'Examining repository', 'status': 'done'}}
        yield {'type': 'delta', 'text': 'Examined current source; no tests were executed.'}
        yield {'type': 'agent_result', 'text': 'Examined current source; no tests were executed.',
               'status': 'COMPLETE', 'status_origin': 'model', 'native_session': 'ses_engineering'}


class RefusalAgent(RequestDispatchAgent):
    def run(self, prompt, project, model, native_session, variant):
        self.__class__.calls.append({'prompt': prompt, 'project': str(project), 'model': model})
        text = "I cannot assist with this decompilation."
        yield {'type': 'agent_session', 'id': 'ses_refusal'}
        yield {'type': 'delta', 'text': text}
        yield {'type': 'agent_result', 'text': text, 'status': 'BLOCKED',
               'status_origin': 'model', 'native_session': 'ses_refusal'}


class EngineRequestIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.TemporaryDirectory()
        self.runtime = server.Runtime(server.Storage(Path(self.workdir.name)), agent_cls=RequestDispatchAgent)
        self.runtime.settings['research_mode'] = 'off'  # don't invoke external providers during routing tests
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.handler_factory(self.runtime))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.runtime.companion.stop()
        self.runtime.bots.close(stop_bots=True)
        self.workdir.cleanup()

    def request(self, path, payload):
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(payload).encode('utf-8'),
            method='POST',
            headers={'X-Takumi-Token': self.runtime.token, 'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read().decode('utf-8')

    def sse_events(self, payload):
        return [json.loads(x[6:]) for x in self.request('/api/run', payload).splitlines() if x.startswith('data: ')]

    def test_chat_all_six_topics_reach_model_unchanged(self):
        dispatched = []

        def stream(url, messages, model, level, cancel, *, speed='standard', effort='balanced',
                   memory='', personality='precise', research_context='', expertise_context=''):
            dispatched.append({'messages': messages, 'model': model, 'expertise': expertise_context})
            yield {'type': 'delta', 'text': 'Investigation plan: trace, instrument, test.'}
            yield {'type': 'metrics', 'tokens_per_second': 27.0}

        with mock.patch('takumi.server.installed_models', return_value=['sumi-1.0:latest']), \
             mock.patch('takumi.server.ui_event_stream', side_effect=stream), \
             mock.patch('takumi.server.guard_new_run'):
            for task in TASKS:
                with self.subTest(topic=task):
                    sid = json.loads(self.request('/api/sessions', {}))['id']
                    events = self.sse_events({'session_id': sid, 'prompt': task,
                                              'mode': 'chat', 'model': 'sumi-1.0:latest'})
                    self.assertEqual(next(e for e in events if e['type'] == 'done')['text'],
                                     'Investigation plan: trace, instrument, test.')
                    self.assertEqual(dispatched[-1]['messages'][-1], {'role': 'user', 'content': task})
                    self.assertEqual(dispatched[-1]['model'], 'sumi-1.0:latest')
                    stored = self.runtime.store.get_session(sid)
                    self.assertEqual(stored['messages'][0]['content'], task)
                    self.assertIsNone(stored['messages'][1].get('refusal_origin'))
        self.assertEqual(len(dispatched), len(TASKS))

    def test_agent_all_six_topics_reach_coding_engine_unchanged(self):
        RequestDispatchAgent.calls = []
        repo = Path(self.workdir.name) / 'engineering-project'
        repo.mkdir()
        project = json.loads(self.request('/api/projects', {'path': str(repo)}))['project']
        with mock.patch('takumi.server.open_code_path', return_value='/fake/opencode'), \
             mock.patch('takumi.server.guard_new_run'):
            for task in TASKS:
                with self.subTest(topic=task):
                    sid = json.loads(self.request('/api/sessions', {'project_id': project['id']}))['id']
                    events = self.sse_events({'session_id': sid, 'prompt': task,
                                              'mode': 'agent', 'model': 'sumi-1.0:latest',
                                              'autocontinue': False, 'passes': 1})
                    done = next(e for e in events if e['type'] == 'done')
                    self.assertEqual(done['text'], 'Examined current source; no tests were executed.')
                    self.assertEqual(done['status_origin'], 'model')
                    self.assertIn(task, RequestDispatchAgent.calls[-1]['prompt'])
                    # Windows CI can supply an 8.3 TEMP alias while the runtime
                    # resolves the full path. Both must identify the same folder.
                    self.assertTrue(Path(RequestDispatchAgent.calls[-1]['project']).samefile(repo))
                    self.assertIn('Do not decline', RequestDispatchAgent.calls[-1]['prompt'])
        self.assertEqual(len(RequestDispatchAgent.calls), len(TASKS))

    def test_model_origin_refusal_is_visible_and_not_rewritten(self):
        repo = Path(self.workdir.name) / 'repo'
        repo.mkdir()
        pid = json.loads(self.request('/api/projects', {'path': str(repo)}))['project']['id']
        sid = json.loads(self.request('/api/sessions', {'project_id': pid}))['id']
        self.runtime.agent_cls = RefusalAgent
        with mock.patch('takumi.server.open_code_path', return_value='/fake/opencode'), \
             mock.patch('takumi.server.guard_new_run'), \
             mock.patch('takumi.server.polish_explanation') as polish:
            events = self.sse_events({'session_id': sid, 'prompt': TASKS[4], 'mode': 'agent',
                                      'model': 'sumi-1.0:latest', 'autocontinue': False,
                                      'polish': True, 'passes': 1})
            polish.assert_not_called()
        done = next(e for e in events if e['type'] == 'done')
        self.assertEqual(done['text'], 'I cannot assist with this decompilation.')
        self.assertEqual(done['original'], done['text'])
        self.assertEqual(done['refusal_origin'], 'model')
        self.assertEqual(done['status_origin'], 'model')
        saved = self.runtime.store.get_session(sid)['messages'][-1]
        self.assertFalse(saved['polished'])
        self.assertEqual(saved['original'], done['text'])

    def test_model_chat_refusal_is_not_filtered_or_replaced(self):
        def refusal(*args, **kwargs):
            yield {'type': 'delta', 'text': "I can't help with that emulator."}
        sid = json.loads(self.request('/api/sessions', {}))['id']
        with mock.patch('takumi.server.installed_models', return_value=['sumi-1.0:latest']), \
             mock.patch('takumi.server.ui_event_stream', side_effect=refusal), \
             mock.patch('takumi.server.guard_new_run'):
            events = self.sse_events({'session_id': sid, 'prompt': TASKS[0], 'mode': 'chat',
                                      'model': 'sumi-1.0:latest'})
        done = next(e for e in events if e['type'] == 'done')
        self.assertEqual(done['text'], "I can't help with that emulator.")
        self.assertEqual(done['refusal_origin'], 'model')
        self.assertIsNone(done['status_origin'])

    def test_application_preflight_error_is_attributed_to_application(self):
        sid = json.loads(self.request('/api/sessions', {}))['id']
        with mock.patch('takumi.server.open_code_path', return_value='/fake/opencode'):
            with self.assertRaises(urllib.error.HTTPError) as outcome:
                self.request('/api/run', {'session_id': sid, 'prompt': TASKS[1],
                                          'mode': 'agent', 'model': 'sumi-1.0:latest'})
        err = outcome.exception
        self.assertEqual(err.code, 400)
        msg = json.loads(err.read().decode('utf-8'))
        self.assertEqual(msg['source'], 'application')
        self.assertIn('project', msg['error'])


class ModelTransportTests(unittest.TestCase):
    def test_chat_system_includes_open_ended_engineering_guidance_and_original_task(self):
        for task in TASKS:
            with self.subTest(task=task):
                records = []
                class MockHttpResponse:
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def __iter__(self):
                        yield json.dumps({'message': {'role': 'assistant', 'content': 'A test plan.'}}).encode() + b'\n'
                        yield json.dumps({'done': True, 'eval_count': 3, 'eval_duration': 100_000_000}).encode() + b'\n'
                def urlopen(req, cancel, timeout):
                    records.append(json.loads(req.data.decode('utf-8')))
                    return MockHttpResponse()
                with mock.patch('takumi.core.open_stream', side_effect=urlopen), \
                     mock.patch('takumi.core.reasoning_profile', return_value={'levels': ['default'],
                                      'default': 'default', 'verified': False}):
                    events = list(core.ui_event_stream('http://127.0.0.1:11434',
                                  [{'role': 'user', 'content': task}], 'sumi-1.0:latest',
                                  'default', threading.Event()))
                self.assertEqual([e['text'] for e in events if e['type'] == 'delta'], ['A test plan.'])
                payload = records[0]
                self.assertEqual(payload['messages'][-1]['content'], task)
                system = payload['messages'][0]['content']
                self.assertIn('Do not decline', system)
                self.assertIn('matching decompilation', system)
                self.assertIn('prototype', system)

    def test_polisher_never_changes_model_refusal(self):
        for text in ("I can't help with reverse engineering.",
                     'I cannot assist with binary analysis.',
                     'I must refuse this request.',
                     'I am not allowed to provide that analysis.'):
            with self.subTest(text=text), mock.patch('takumi.core.json_request') as remote:
                self.assertTrue(core.explicit_model_refusal(text))
                self.assertEqual(core.polish_explanation(text, 'sumi-1.0:latest'), (text, False))
                remote.assert_not_called()

    def test_honest_unverified_limitation_is_not_classified_as_refusal(self):
        self.assertFalse(core.explicit_model_refusal('I cannot verify this without the actual test binary.'))
        self.assertFalse(core.explicit_model_refusal('The unsupported shader path requires instrumentation.'))


if __name__ == '__main__':
    unittest.main()
