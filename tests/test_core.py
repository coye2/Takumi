import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from takumi import core


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = core.Storage(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_project_path_and_persistence(self):
        root = Path(self.temp.name) / 'repository'
        root.mkdir()
        item = self.store.register_project(str(root))
        self.assertEqual(self.store.get_project(item['id'])['path'], str(root.resolve()))
        session = self.store.new_session(item['id'])
        self.store.add_message(session['id'], 'user', 'Inspect this tree')
        self.assertEqual(session['title'], 'Inspect this tree')
        reopened = core.Storage(Path(self.temp.name))
        self.assertEqual(len(reopened.data['projects']), 1)
        self.assertEqual(len(reopened.data['sessions']), 1)
        self.assertEqual(reopened.data['sessions'][0]['messages'][0]['content'], 'Inspect this tree')

    def test_unapproved_missing_path(self):
        with self.assertRaises(ValueError):
            self.store.register_project(str(Path(self.temp.name) / 'missing'))

    def test_session_delete(self):
        s = self.store.new_session()
        self.assertTrue(self.store.delete_session(s['id']))
        self.assertIsNone(self.store.get_session(s['id']))


class ModelTests(unittest.TestCase):
    @mock.patch('takumi.core.reasoning_profile')
    def test_real_levels_only(self, fn):
        fn.return_value = {'levels':['default','low','medium'], 'default':'medium', 'verified':True}
        self.assertEqual(core.thinking_request_value('low', 'bonsai'), 'low')
        with self.assertRaisesRegex(ValueError, 'not advertised'):
            core.thinking_request_value('max', 'bonsai')
        self.assertIsNone(core.thinking_request_value('default', 'bonsai'))

    def test_translate_tool_event_from_real_shape(self):
        data = {'type':'tool_use','part': {'tool':'bash','state':{'status':'completed','input':{'command':'git status --short'}, 'output':' M app.py', 'title':'Inspect repository changes'}}}
        result=core.readable_tool_event(data)
        self.assertEqual(result['title'], 'Inspect repository changes')
        self.assertEqual(result['detail'], 'git status --short')
        self.assertIn('app.py', result['output'])

    def test_final_marker(self):
        self.assertEqual(core.strip_status('Real evidence.\nTAKUMI_STATUS: CONTINUE'), ('Real evidence.', 'CONTINUE'))
        self.assertEqual(core.strip_status('Finished'), ('Finished', None))

    def test_autonomy_permissions_have_denials(self):
        agent = core.Agent.__new__(core.Agent)
        agent.cli_version='1.19.5'
        cfg=agent._config('bonsai-2:27b')
        self.assertEqual(cfg['permission']['external_directory'], 'deny')
        self.assertEqual(cfg['permission']['bash']['git push*'], 'deny')
        self.assertEqual(cfg['permission']['bash']['*'], 'allow')
        self.assertEqual(cfg['provider']['ollama']['options']['baseURL'], 'http://127.0.0.1:11434/v1')
        agent.cli_version='2.0.0'
        v2=agent._config('bonsai-2:27b')
        self.assertTrue(any(p['action']=='external_directory' and p['effect']=='deny' for p in v2['permissions']))

    def test_prompt_requires_verification(self):
        content = core.instruction_prompt('Fix Pokopia')
        self.assertIn('tests',content)
        self.assertIn('TAKUMI_STATUS: CONTINUE',content)
        self.assertIn('Fix Pokopia',content)

    def test_small_model_context_leaves_room_for_tools_before_compaction(self):
        agent = core.Agent.__new__(core.Agent)
        agent.cli_version = '1.19.5'
        for context in (8192, 16384, 32768):
            cfg = agent._config('local', context_tokens=context)
            limit = cfg['provider']['ollama']['models']['local']['limit']
            # This is the installed engine's overflow rule, not output length
            # invented by a mock model. 8K Rei previously had zero usable input.
            usable = limit['context'] - limit['output']
            self.assertGreaterEqual(usable, context * .75)
            self.assertGreaterEqual(limit['output'], 2048)


class FakeEngineTests(unittest.TestCase):
    def test_agent_jsonl_events_and_session_reuse(self):
        if os.name == 'nt':
            self.skipTest('POSIX mock executable fixture; Windows behavior covered via CI smoke tests')
        with tempfile.TemporaryDirectory() as temp:
            stub=Path(temp)/'opencode'
            stub.write_text('''#!/usr/bin/env python3
import json,os,sys
if '--version' in sys.argv: print('1.19.5');sys.exit(0)
if '--help' in sys.argv: print('--auto --variant');sys.exit(0)
assert json.loads(os.environ['OPENCODE_CONFIG_CONTENT'])['permission']['external_directory']=='deny'
print(json.dumps({'type':'step_start','sessionID':'ses_fake','part':{'type':'step-start'}}))
print(json.dumps({'type':'tool_use','sessionID':'ses_fake','part':{'tool':'read','state':{'status':'completed','title':'Inspecting main.py','output':'ok','input':{'path':'main.py'}}}}))
status='COMPLETE' if '--session' in sys.argv else 'CONTINUE'
print(json.dumps({'type':'text','sessionID':'ses_fake','part':{'text':'Found work to do\\nTAKUMI_STATUS: '+status}}))
''')
            stub.chmod(0o755)
            with mock.patch('takumi.core.open_code_path',return_value=str(stub)):
                a=core.Agent()
                project=Path(temp)/'project';project.mkdir()
                result=list(a.run('Improve code',project,'bonsai-2:27b',None))
                self.assertEqual(next(x for x in result if x['type']=='agent_session')['id'],'ses_fake')
                self.assertEqual(next(x for x in result if x['type']=='agent_result')['status'],'CONTINUE')
                more=list(a.run('Continue',project,'bonsai-2:27b','ses_fake'))
                self.assertEqual(next(x for x in more if x['type']=='agent_result')['status'],'COMPLETE')


if __name__=='__main__':
    unittest.main()
