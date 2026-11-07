import json
import tempfile
import threading
import sys
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from takumi import server


class FakeAgent:
    calls = []
    def __init__(self):
        self.cancel=threading.Event()
    def stop(self):
        self.cancel.set()
    def run(self, prompt, project, model, native_session, variant):
        self.__class__.calls.append((prompt, str(project), native_session))
        yield {'type':'agent_session','id':'ses_real'}
        yield {'type':'activity','activity':{'kind':'tool','tool':'bash','title':'Ran regression tests','status':'completed','output':'2 tests passed'}}
        yield {'type':'delta','text':'Verified the change.\n'}
        yield {'type':'agent_result','text':'Verified the change.','status':'CONTINUE' if native_session is None else 'COMPLETE','native_session':'ses_real'}


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.runtime=server.Runtime(server.Storage(Path(cls.temp.name)),agent_cls=FakeAgent)
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.handler_factory(cls.runtime))
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
        cls.host='http://127.0.0.1:'+str(cls.http.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.temp.cleanup()

    def request(self, route, body=None, token=True):
        data=json.dumps(body).encode() if body is not None else None
        headers={'Content-Type':'application/json'}
        if token: headers['X-Takumi-Token']=self.runtime.token
        req=urllib.request.Request(self.host+route,data=data,headers=headers,method='POST' if data is not None else 'GET')
        with urllib.request.urlopen(req,timeout=10) as r: return r.read().decode()

    def test_bootstrap_and_static(self):
        got=json.loads(self.request('/api/bootstrap'))
        self.assertEqual(got['token'],self.runtime.token)
        html=self.request('/')
        self.assertIn('Takumi', html)
        self.assertIn('Chat', html)

    def test_post_requires_local_token(self):
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.request('/api/sessions',{},token=False)
        self.assertEqual(err.exception.code,403)

    def test_identity_does_not_advertise_edited_files_as_loaded_backend(self):
        before = json.loads(self.request('/api/runtime-identity'))
        changed_disk = '0' * 64
        with mock.patch('takumi.server.build_stamp', return_value=changed_disk):
            after = json.loads(self.request('/api/runtime-identity'))
        self.assertEqual(after['build_stamp'], before['build_stamp'])
        self.assertNotEqual(after['build_stamp'], changed_disk)
        self.assertEqual(after['disk_build_stamp'], changed_disk)
        self.assertTrue(after['source_changed_since_start'])
        self.assertEqual(after['startup_frontend_sha256'], before['startup_frontend_sha256'])

    def test_invalid_project_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.request('/api/projects', {'path':'/a/nonexistent/path'})
        self.assertEqual(err.exception.code,400)

    def test_project_preflight_requires_auth_and_resolves_edit_intent(self):
        prompt = 'Add this GitHub repository to my website: https://github.com/example/tool'
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.request('/api/project/preflight', {'prompt':prompt}, token=False)
        self.assertEqual(err.exception.code, 403)
        check = json.loads(self.request('/api/project/preflight', {'prompt':prompt,'mode':'auto'}))
        self.assertTrue(check['needs_project'])
        project = self.runtime.store.register_project(self.temp.name)
        check = json.loads(self.request('/api/project/preflight',
            {'prompt':prompt, 'mode':'auto', 'project_id':project['id']}))
        self.assertEqual(check, {'mode':'agent','needs_project':False})
        with self.assertRaises(urllib.error.HTTPError):
            self.request('/api/project/preflight', {'prompt':prompt,'project_id':'missing'})
    def test_agent_auto_continuation_and_polishing(self):
        FakeAgent.calls=[]
        root=Path(self.temp.name)/'repo';root.mkdir(exist_ok=True)
        project=json.loads(self.request('/api/projects',{'path':str(root)}))['project']
        session=json.loads(self.request('/api/sessions',{'project_id':project['id']}))
        with mock.patch('takumi.server.installed_models',return_value=['bonsai-2:27b']), mock.patch('takumi.server.open_code_path',return_value='/fake/opencode'),mock.patch('takumi.server.polish_explanation',return_value=('I verified two regression tests.',True)):
            packets=self.request('/api/run',{'session_id':session['id'],'prompt':'Fix failure','mode':'agent','model':'bonsai-2:27b','reasoning':'default','polish':True,'autocontinue':True,'passes':4})
        events=[json.loads(x[6:]) for x in packets.split('\n') if x.startswith('data: ')]
        self.assertEqual([x['index'] for x in events if x['type']=='cycle'],[1,2])
        self.assertEqual(len(FakeAgent.calls),2)
        self.assertEqual(FakeAgent.calls[1][2],'ses_real')
        self.assertEqual(next(x for x in events if x['type']=='done')['text'],'I verified two regression tests.')
        stored=json.loads(self.request('/api/session/'+session['id']))
        self.assertEqual(stored['messages'][-1]['original'],'Verified the change.')
        self.assertEqual(len(stored['events']),2)

    def test_chat_stream_persists_original_and_does_not_run_agent(self):
        FakeAgent.calls=[]
        sess=json.loads(self.request('/api/sessions',{}))
        def stream(*args):
            yield {'type':'delta','text':'Accurate local answer.'}
            yield {'type':'metrics','eval_count':24,'total_duration':45000}
        with mock.patch('takumi.server.installed_models',return_value=['bonsai-2:27b']), mock.patch('takumi.server.ui_event_stream',side_effect=stream):
            result=self.request('/api/run',{'session_id':sess['id'],'prompt':'What is Vulkan?','mode':'chat','model':'bonsai-2:27b','reasoning':'default'})
        packets=[json.loads(x[6:]) for x in result.split('\n') if x.startswith('data: ')]
        self.assertEqual(next(x for x in packets if x['type']=='done')['text'],'Accurate local answer.')
        self.assertEqual(len(FakeAgent.calls),0)
        saved=json.loads(self.request('/api/session/'+sess['id']))
        self.assertEqual(saved['messages'][0]['content'],'What is Vulkan?')
        self.assertEqual(saved['messages'][1]['content'],'Accurate local answer.')

    def test_runtime_rejects_parallel_runs(self):
        agent=FakeAgent()
        first=self.runtime.claim('example_1',agent)
        try:
            with self.assertRaisesRegex(ValueError, 'already active'):
                self.runtime.claim('example_2',FakeAgent())
        finally:
            self.runtime.release('example_1')

    def test_github_chat_and_access_followup_use_private_reader_not_public_search(self):
        session=json.loads(self.request('/api/sessions',{})); contexts=[]
        def stream(url, messages, model, level, cancel, speed=None, **kwargs):
            contexts.append(kwargs.get('research_context',''))
            yield {'type':'delta','text':'Read the pinned repository source.'}
        packet={'kind':'github_repository','account':'fixture','freshness':'live_source_files','results':[],
                'snapshots':[{'branch':'work/switch2','commit':'a'*40,'files':[{'excerpt':'actual private source'}]}]}
        prompt='review my current switch 2 work in my nxinfinity developer repo on github'
        with mock.patch('takumi.server.installed_models',return_value=['fixture']), mock.patch('takumi.server.ui_event_stream',new=stream), mock.patch.object(self.runtime,'get_research',side_effect=AssertionError('private request leaked to public search')), mock.patch('takumi.server.collect_repository_review',return_value=packet) as read:
            for question in (prompt,'you have github access'):
                self.request('/api/run',{'session_id':session['id'],'prompt':question,'mode':'chat','model':'fixture'})
        self.assertEqual([call.args[0] for call in read.call_args_list],[prompt,prompt])
        self.assertTrue(all('actual private source' in context for context in contexts))

    def test_generated_code_repair_keeps_original_and_counts_both_model_calls(self):
        session = json.loads(self.request('/api/sessions', {}))
        calls = []
        def stream(url, messages, model, level, cancel, **kwargs):
            calls.append(messages)
            yield {'type':'first_token','milliseconds':100 if len(calls)==1 else 999}
            yield {'type':'delta','text':'```python\ndef answer(\n```' if len(calls)==1 else '```python\ndef answer(): return 42\n```'}
            yield {'type':'metrics','eval_count':10,'eval_duration':1000000000,'done_reason':'stop'}
        with mock.patch('takumi.server.installed_models',return_value=['fixture']), mock.patch('takumi.server.ui_event_stream',side_effect=stream), mock.patch('takumi.server.should_research',return_value=False):
            packets = self.request('/api/run', {'session_id':session['id'],'prompt':'Generate a Python function returning 42','model':'fixture','mode':'chat','speed':'standard','effort':'balanced'})
        events = [json.loads(x[6:]) for x in packets.split('\n') if x.startswith('data: ')]
        final = next(e for e in events if e['type']=='done')
        self.assertTrue(final['code_repaired'])
        self.assertEqual(final['metrics']['eval_count'],20)
        self.assertEqual(final['metrics']['generation_attempts'],2)
        self.assertEqual(final['initial_code_validation']['status'],'needs_review')
        self.assertEqual(final['code_validation']['results'][0]['status'],'passed')
        self.assertIn('def answer(', final['original'])
        self.assertNotEqual(final['text'],final['original'])
        self.assertEqual(len([e for e in events if e['type']=='first_token']),1)

    def test_deletion(self):
        sess=json.loads(self.request('/api/sessions',{}))
        removed=json.loads(self.request('/api/sessions/delete',{'session_id':sess['id']}))
        self.assertTrue(removed['deleted'])

    def test_goal_rejects_false_completion_and_replans_from_real_test_output(self):
        root = Path(self.temp.name)/'checked-goal'; root.mkdir()
        (root/'value.txt').write_text('bad')
        (root/'check.py').write_text('from pathlib import Path\nassert Path("value.txt").read_text() == "good", "fixture still broken"')
        (root/'takumi.tasks.json').write_text(json.dumps({'version':1, 'tasks':[
            {'id':'check', 'argv':['python','check.py'], 'completion_check':True, 'protected_files':['check.py']}]}))
        class OverconfidentAgent(FakeAgent):
            count = 0
            def run(self, prompt, project, *args, **kwargs):
                type(self).count += 1
                if self.count == 2:
                    assert 'fixture still broken' in prompt
                    (project/'value.txt').write_text('good')
                yield {'type':'agent_session','id':'checked-session'}
                yield {'type':'agent_result','text':'I claim it is fixed.', 'status':'COMPLETE', 'native_session':'checked-session'}
        project = json.loads(self.request('/api/projects', {'path':str(root)}))['project']
        session = json.loads(self.request('/api/sessions', {'project_id':project['id']}))
        with mock.patch.object(self.runtime, 'agent_cls', OverconfidentAgent), mock.patch('takumi.server.open_code_path',return_value='fixture'), mock.patch('takumi.server.should_research',return_value=False), mock.patch('takumi.harness.shutil.which',return_value=sys.executable):
            packets = self.request('/api/run', {'session_id':session['id'],'prompt':'/goal Fix the value', 'model':'fixture', 'effort':'quick','speed':'boost','polish':False,'goal_seconds':20})
        events = [json.loads(x[6:]) for x in packets.split('\n') if x.startswith('data: ')]
        self.assertEqual([e['report']['status'] for e in events if e['type']=='completion_verification'], ['failed','passed'])
        final = next(e for e in events if e['type']=='done')
        self.assertEqual(final['status_origin'],'declared-checks')
        self.assertEqual(OverconfidentAgent.count, 2)

    def test_goal_never_completes_from_unfinished_unmarked_report(self):
        root = Path(self.temp.name)/'unfinished-goal'; root.mkdir()
        class UnfinishedAgent(FakeAgent):
            count = 0
            def run(self, *args, **kwargs):
                type(self).count += 1
                yield {'type':'agent_session','id':'unfinished-session'}
                yield {'type':'agent_result','text':'Next I will inspect the failure:', 'status':None, 'native_session':'unfinished-session'}
        project = json.loads(self.request('/api/projects', {'path':str(root)}))['project']
        session = json.loads(self.request('/api/sessions', {'project_id':project['id']}))
        with mock.patch.object(self.runtime, 'agent_cls', UnfinishedAgent), mock.patch('takumi.server.open_code_path',return_value='fixture'), mock.patch('takumi.server.should_research',return_value=False):
            packets = self.request('/api/run', {'session_id':session['id'],'prompt':'/goal Diagnose the failure', 'model':'fixture','effort':'quick','speed':'boost','polish':False,'goal_seconds':20})
        events = [json.loads(x[6:]) for x in packets.split('\n') if x.startswith('data: ')]
        final = next(e for e in events if e['type']=='done')
        self.assertEqual(final['run_state'],'blocked')
        self.assertEqual(final['goal_state'],'blocked')
        self.assertEqual(UnfinishedAgent.count,3)

if __name__=='__main__':unittest.main()

