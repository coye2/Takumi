"""Live localhost API tests for the new engineering and update routes.

This tests the Python control plane, not native Windows/WebView2, Ghidra or
GPU inference availability. All analysis input data is synthetic.
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

from takumi import server


class CompletionHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.runtime = server.Runtime(server.Storage(cls.root / 'data'))
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.handler_factory(cls.runtime))
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.host = 'http://127.0.0.1:' + str(cls.http.server_port)
        cls.project = cls.root / 'project'; cls.project.mkdir()
        cls.project_id = cls.runtime.store.register_project(str(cls.project))['id']
        (cls.project / 'shader.spv').write_bytes(b'\x03\x02\x23\x07' + bytes(48))
        (cls.project / 'test.bin').write_bytes(b'NSO0' + bytes(48))
        (cls.project / 'source.cs').write_text('public class Renderer { void DEPBAR() {} }\n', encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close()
        cls.runtime.kage.stop(); cls.runtime.companion.stop(); cls.runtime.bots.close(stop_bots=False)
        cls.temp.cleanup()

    def fetch(self, path, payload=None, auth=True):
        body = json.dumps(payload).encode('utf-8') if payload is not None else None
        headers = {'Content-Type': 'application/json'}
        if auth: headers['X-Takumi-Token'] = self.runtime.token
        req=urllib.request.Request(self.host + path, data=body, headers=headers,
                                   method='POST' if body is not None else 'GET')
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def test_inventory_returns_real_capability_state(self):
        data=self.fetch('/api/engineering/tools')
        self.assertIn('tools',data)
        self.assertIn('gpu_scheduler',data)
        self.assertTrue(any(x['id']=='spirv-val' for x in data['tools']))

    def test_symbol_search_source_bounded(self):
        out=self.fetch('/api/engineering/symbols',{'project_id':self.project_id,'query':'DEPBAR'})
        self.assertEqual(out['matches'][0]['file'],'source.cs')
        self.assertEqual(out['matches'][0]['line'],1)
        self.assertNotIn(str(self.project),json.dumps(out))

    def test_binary_and_shader_endpoints_no_fake_verification(self):
        out=self.fetch('/api/engineering/binary',{'project_id':self.project_id,'path':'test.bin'})
        self.assertEqual(out['format_hint'],'Nintendo Switch NSO')
        with mock.patch('takumi.server.disassemble_spirv',return_value={'available':False,'disassembled':False}):
            self.assertFalse(self.fetch('/api/engineering/disassemble',{'project_id':self.project_id,'path':'shader.spv'})['disassembled'])
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.fetch('/api/engineering/binary',{'project_id':self.project_id,'path':'../other.bin'})
        self.assertEqual(exc.exception.code,400)

    def test_ghidra_requires_explicit_confirmation_and_local_scope(self):
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.fetch('/api/engineering/ghidra',{'project_id':self.project_id,'path':'test.bin'})
        self.assertEqual(exc.exception.code,400)
        with mock.patch('takumi.server.analyze_ghidra',return_value={'available':False,'analyzed':False}):
            out=self.fetch('/api/engineering/ghidra',{'project_id':self.project_id,'path':'test.bin','confirmed':True})
        self.assertFalse(out['analyzed'])

    def test_update_flow_not_pretending_published(self):
        with mock.patch.object(self.runtime.updater,'check',return_value={'available':False,'reason':'Release not published'}):
            out=self.fetch('/api/update/check')
        self.assertFalse(out['available'])
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.fetch('/api/update/apply',{'confirmed':True})
        self.assertEqual(exc.exception.code,400)

    def test_new_write_endpoints_require_local_token(self):
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.fetch('/api/engineering/binary',{'project_id':self.project_id,'path':'test.bin'},auth=False)
        self.assertEqual(exc.exception.code,403)


if __name__=='__main__': unittest.main()
