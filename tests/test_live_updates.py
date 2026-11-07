"""Active-user follow-ups are queued and incorporated without restarting tasks.

Tests exercise real local HTTP, but use deterministic fake models/agents.
"""
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


class LiveUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.rt=server.Runtime(core.Storage(Path(self.temp.name)))
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.handler_factory(self.rt))
        self.thread=threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url=f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown(); self.http.server_close()
        self.rt.companion.stop(); self.rt.kage.stop(); self.rt.bots.close(stop_bots=True)
        self.temp.cleanup()

    def request(self, path, body=None):
        req=urllib.request.Request(self.url+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'X-Takumi-Token':self.rt.token,'Content-Type':'application/json'},
            method='POST' if body is not None else 'GET')
        with urllib.request.urlopen(req,timeout=15) as r:
            return r.read().decode()

    def test_chat_accepts_user_followup_in_same_sse_run(self):
        s=json.loads(self.request('/api/sessions',{}))
        entered=threading.Event(); release=threading.Event(); output={}; histories=[]
        def fake_stream(url,messages,model,level,cancel,**kwargs):
            histories.append([dict(item) for item in messages])
            if len(histories)==1:
                yield {'type':'delta','text':'Initial analysis.'}
                entered.set()
                if not release.wait(8): raise RuntimeError('test timeout')
            else:
                yield {'type':'delta','text':'Updated answer incorporating your instruction.'}
        def worker():
            output['body']=self.request('/api/run',{
                'session_id':s['id'],'prompt':'Analyze the renderer',
                'mode':'chat','model':'rei-1.0:latest','reasoning':'default',
                'speed':'boost','effort':'balanced'})
        try:
            with mock.patch('takumi.server.installed_models', return_value=['rei-1.0:latest']),\
                 mock.patch('takumi.server.ui_event_stream',side_effect=fake_stream),\
                 mock.patch('takumi.server.should_research',return_value=False):
                thread=threading.Thread(target=worker,daemon=True)
                thread.start()
                self.assertTrue(entered.wait(5),'chat stream never started')
                queued=json.loads(self.request('/api/run/update',{'session_id':s['id'],'prompt':'Also test the GPU fence'}))
                self.assertTrue(queued['accepted'])
                release.set()
                thread.join(10)
                self.assertFalse(thread.is_alive())
        finally:release.set()
        events=[json.loads(line[6:]) for line in output['body'].splitlines() if line.startswith('data: ')]
        self.assertEqual(len([e for e in events if e['type']=='done']),1)
        done=next(e for e in events if e['type']=='done')
        self.assertIn('Initial analysis.',done['text'])
        self.assertIn('Updated answer',done['text'])
        self.assertTrue(any(m['role']=='user' and 'GPU fence' in m['content'] for m in histories[1]))
        saved=json.loads(self.request('/api/session/'+s['id']))
        self.assertTrue(any(m.get('queued') for m in saved['messages']))

    def test_agent_update_extends_single_pass_without_reset(self):
        sproj=Path(self.temp.name)/'project';sproj.mkdir()
        project=json.loads(self.request('/api/projects',{'path':str(sproj)}))['project']
        session=json.loads(self.request('/api/sessions',{'project_id':project['id']}))
        entered=threading.Event(); release=threading.Event(); calls=[]; output={}
        class FakeAgent:
            def __init__(self):self.cancel=threading.Event()
            def stop(self):self.cancel.set()
            def run(self,prompt,project,model,native_session,variant,**kwargs):
                calls.append({'prompt':prompt,'session':native_session,'model':model})
                yield {'type':'agent_session','id':'ses_kept'}
                if len(calls)==1:
                    entered.set()
                    if not release.wait(8):raise RuntimeError('test timeout')
                yield {'type':'agent_result','text':'Verified change '+str(len(calls)),
                       'status':'COMPLETE','native_session':'ses_kept'}
        self.rt.agent_cls=FakeAgent
        def worker():
            output['body']=self.request('/api/run',{
                'session_id':session['id'],'prompt':'Fix the renderer',
                'mode':'agent','model':'rei-1.0:latest','reasoning':'default',
                'autocontinue':False,'passes':1,'speed':'standard'})
        try:
            with mock.patch('takumi.server.open_code_path',return_value='/fake/agent'),\
                 mock.patch('takumi.server.installed_models',return_value=['rei-1.0:latest']),\
                 mock.patch('takumi.server.should_research',return_value=False):
                thread=threading.Thread(target=worker,daemon=True);thread.start()
                self.assertTrue(entered.wait(5),'agent never started')
                receipt=json.loads(self.request('/api/run/update',{
                    'session_id':session['id'],'prompt':'Also add a deterministic fence test'}))
                self.assertTrue(receipt['accepted'])
                release.set();thread.join(10)
                self.assertFalse(thread.is_alive())
        finally:release.set()
        self.assertEqual(len(calls),2)
        self.assertIsNone(calls[0]['session'])
        self.assertEqual(calls[1]['session'],'ses_kept')
        self.assertIn('deterministic fence test',calls[1]['prompt'])
        self.assertIn('Rei 1.1',calls[0]['prompt'])
        events=[json.loads(line[6:]) for line in output['body'].splitlines() if line.startswith('data: ')]
        self.assertEqual([e['index'] for e in events if e['type']=='cycle'],[1,2])

    def test_reject_followup_without_active_run(self):
        s=json.loads(self.request('/api/sessions',{}))
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.request('/api/run/update',{'session_id':s['id'],'prompt':'Hello'})
        self.assertEqual(exc.exception.code,400)


if __name__=='__main__':unittest.main()
