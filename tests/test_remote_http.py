"""Exercise reverse-proxy host rewriting and device pairing."""
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
from takumi.server import Runtime, handler_factory
from takumi.core import Storage

class RemoteAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.runtime=Runtime(Storage(Path(cls.temp.name)))
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),handler_factory(cls.runtime))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close()
        cls.runtime.companion.stop();cls.runtime.bots.close(stop_bots=False);cls.temp.cleanup()
    def request(self,path,headers=None,body=None):
        data=json.dumps(body).encode() if body is not None else None
        req=urllib.request.Request(self.base+path,data=data,headers=headers or {},method='POST' if data is not None else 'GET')
        with urllib.request.urlopen(req,timeout=5) as r:return json.load(r)
    def test_remote_requires_tls_and_pairing_even_when_proxy_rewrites_host(self):
        headers={'Host':'127.0.0.1','X-Forwarded-Host':'pc.tailnet.ts.net','X-Forwarded-Proto':'https'}
        with mock.patch.dict(os.environ,{'TAKUMI_TAILSCALE_SERVE':'1'}):
            with self.assertRaises(urllib.error.HTTPError) as unauthorized:
                self.request('/api/bootstrap',headers)
            self.assertEqual(unauthorized.exception.code,401)
            with self.assertRaises(urllib.error.HTTPError) as incomplete:
                self.request('/api/bootstrap',{'Host':'127.0.0.1','X-Forwarded-For':'100.40.20.1'})
            self.assertEqual(incomplete.exception.code,403)
            issue=self.request('/api/remote/pair-code',{'X-Takumi-Token':self.runtime.token}, {})
            paired=self.request('/api/pair',headers,{'code':issue['code'],'label':'Test phone'})
            authorized=self.request('/api/bootstrap',{**headers,'X-Takumi-Token':paired['token']})
            self.assertEqual(authorized['token'],paired['token'])
            self.assertTrue(authorized['remote'])
    def test_pwa_files_have_correct_mime(self):
        req=urllib.request.Request(self.base+'/manifest.webmanifest')
        with urllib.request.urlopen(req) as r:
            self.assertIn('application/manifest+json',r.headers['Content-Type'])
            self.assertIn('Takumi',r.read().decode())
