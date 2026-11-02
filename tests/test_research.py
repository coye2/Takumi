"""Fallback guarantees: degraded answers must be honest, bounded, recoverable."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from takumi import research as r
from takumi.server import Runtime, Storage


RSS = b'''<?xml version="1.0"?><rss><channel><item><title>Project release</title><link>https://example.org/release</link><description>Version 2.1</description></item></channel></rss>'''
LITE = b'''<html><a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.net%2Ffix">Project fix</a></html>'''
HTML = b'''<a class="result__a" href="https://example.com/upstream">Upstream implementation</a>'''
WIKI = json.dumps({'query': {'search':[{'title':'Graphics processing unit','snippet':'A programmable processor'}]}}).encode()


def fake_fetch(url):
    if 'bing.com' in url:
        return RSS
    if 'lite.duckduckgo.com' in url:
        return LITE
    if 'html.duckduckgo.com' in url:
        return HTML
    if 'wikipedia.org' in url:
        return WIKI
    raise OSError('deliberate outage')


class ResearchFallbackTests(unittest.TestCase):
    def setUp(self):
        r._FAILURES.clear()

    def tearDown(self):
        r._FAILURES.clear()

    def test_scrubs_secrets_and_local_paths(self):
        q = r.public_query('Find github foo password=abc123 C:\\Users\\Someone\\secret.py ```api_key=123```')
        self.assertNotIn('abc123', q)
        self.assertNotIn('Someone', q)
        self.assertNotIn('api_key=123', q)
        self.assertIn('[local path]', q)

    def test_rejects_loopback_and_private_urls(self):
        for url in ['http://localhost/', 'http://127.0.0.1/', 'http://192.168.1.1/',
                    'https://169.254.169.254/', 'http://[::1]/', 'file:///etc/passwd',
                    'https://example.com:8765']:
            self.assertIsNone(r._public_url(url), url)
        self.assertEqual(r._public_url('https://github.com/user/repo'),'https://github.com/user/repo')
        req = mock.Mock()
        with self.assertRaises(ValueError):
            r._RejectPrivateRedirects().redirect_request(req,None,302,'Found',{},'http://127.0.0.1/private')

    def test_multiple_independent_search_sources(self):
        packet = r.web_search_detailed('gpu driver', limit=5, fetch=fake_fetch)
        self.assertEqual(len(packet['results']), 4)
        self.assertTrue(any(x['provider']=='bing-rss' for x in packet['results']))
        self.assertTrue(any(x['provider']=='duckduckgo-html' for x in packet['results']))
        self.assertTrue(any(x['provider']=='wikipedia' for x in packet['results']))

    def test_ddg_lite_fallback_on_bing_outage(self):
        def fetch(url):
            if 'lite.duckduckgo.com' in url:
                return LITE
            raise OSError('simulated outage')
        packet = r.web_search_detailed('pokopia renderer', fetch=fetch)
        self.assertEqual(packet['results'][0]['url'],'https://example.net/fix')
        self.assertEqual(packet['results'][0]['provider'],'duckduckgo-lite')
        self.assertEqual(next(x['status'] for x in packet['providers'] if x['name']=='bing-rss'),'unavailable')

    def test_total_outage_returns_no_fake_facts(self):
        def fetch(url): raise OSError('all offline')
        data = r.web_search_detailed('reference implementation', fetch=fetch)
        self.assertEqual(data['results'],[])
        self.assertTrue(data['offline'])
        self.assertTrue(all(x['status']=='unavailable' for x in data['providers']))

    def test_parser_crash_is_isolated(self):
        with mock.patch.object(r,'_web_provider',side_effect=RuntimeError('provider bug')):
            data=r.web_search_detailed('who is',fetch=fake_fetch)
        self.assertEqual(data['results'],[])
        self.assertTrue(all(x.get('reason')=='RuntimeError' for x in data['providers']))

    def test_circuit_breaker_bypasses_repeated_failure(self):
        for _ in range(2):
            r._record('bing-rss',False)
        self.assertFalse(r._available('bing-rss'))
        with mock.patch('takumi.research.time.monotonic',return_value=r._FAILURES['bing-rss'][1]+1):
            self.assertTrue(r._available('bing-rss'))
        r._record('bing-rss',True)
        self.assertTrue(r._available('bing-rss'))

    def test_github_api_and_cli_fallback(self):
        def broken(url):
            raise OSError('github rate limited')
        def cli(args,**kwargs):
            return subprocess.CompletedProcess(args,0,json.dumps({'items':[{'full_name':'test/emu',
                    'html_url':'https://github.com/test/emu','description':'reference implementation','license':{'spdx_id':'MIT'}}]}),'')
        packet = r.github_repo_search_detailed('emulator',fetch=broken,runner=cli)
        self.assertEqual(packet['results'][0]['provider'],'github-cli')
        self.assertEqual(packet['providers'][0]['status'],'unavailable')
        self.assertEqual(packet['results'][0]['license'],'MIT')

    def test_github_public_api(self):
        def fetch(url):
            return json.dumps({'items':[{'full_name':'test/pokopia','html_url':'https://github.com/test/pokopia',
                      'description':'gpu implementation', 'license':{'spdx_id':'Apache-2.0'}}]}).encode()
        data = r.github_repo_search_detailed('GPU',fetch=fetch)
        self.assertEqual(data['results'][0]['source'],'github')
        self.assertEqual(data['results'][0]['license'],'Apache-2.0')

    def test_local_repository_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'Renderer.cs').write_text('public void DEPBAR() { }\n')
            (p/'node_modules').mkdir()
            (p/'node_modules'/'Renderer.cs').write_text('DEPBAR garbage')
            data=r.local_code_search('investigate DEPBAR implementation',p)
            self.assertEqual(len(data),1)
            self.assertIn('Renderer.cs:1',data[0]['title'])
            self.assertEqual(data[0]['provider'],'local-project')

    def test_cache_remains_stale_and_disallows_private_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=r.EvidenceCache(Path(tmp)/'cache.json')
            cache.put('web:test',{'query':'test','results':[{'url':'https://github.com/org/private/blob/code',
                'provider':'github-cli-code','source':'github-code','snippet':'private source'},
                {'url':'https://example.com/doc','source':'web','provider':'bing-rss','title':'Documentation'}]})
            got=cache.get('web:test')
            self.assertEqual(len(got[0]['results']),1)
            self.assertNotIn('private',str(got))
            self.assertLess(got[1],30)

    def test_disk_cache_recovers_when_public_search_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=r.EvidenceCache(Path(tmp)/'cache.json')
            online={'results':[{'title':'Doc','url':'https://example.com/doc', 'snippet':'example',
                                'source':'web','provider':'bing-rss'}], 'providers':[]}
            offline={'results':[], 'providers':[{'name':'bing-rss','status':'unavailable'}]}
            with mock.patch('takumi.research.web_search_detailed',return_value=online):
                first=r.research('What is example',github=False,cache=cache)
            self.assertEqual(first['freshness'],'live_leads')
            self.assertFalse(first['verified'])
            with mock.patch('takumi.research.web_search_detailed',return_value=offline):
                second=r.research('What is example',github=False,cache=cache)
            self.assertEqual(second['freshness'],'stale_cache')
            self.assertTrue(second['results'][0]['stale'])
            self.assertIn('STALE CACHED',r.context(second))

    def test_local_only_does_not_claim_internet_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'shader.wgsl').write_text('fn HADD2() {}')
            with mock.patch('takumi.research.github_repo_search_detailed',return_value={'results':[],'providers':[]}):
                with mock.patch('takumi.research.github_code_search',return_value=[]):
                    result=r.research('Find HADD2 implementation in emulator',web=False,code=True,project=p)
            self.assertEqual(result['freshness'],'local_only')
            self.assertFalse(result['verified'])
            self.assertEqual(result['results'][0]['source'],'local-code')

    def test_runtime_reuses_results_but_labels_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime=Runtime(Storage(Path(tmp)))
            packet={'results':[{'url':'https://example.net'}], 'freshness':'live_leads','note':'leads'}
            with mock.patch('takumi.server.conduct_research',return_value=packet) as called:
                self.assertIsNotNone(runtime.get_research('how does it work'))
                got=runtime.get_research('how does it work')
            self.assertEqual(called.call_count,1)
            self.assertEqual(got['cache_kind'],'memory')
            self.assertTrue(got['cached'])

if __name__=='__main__':unittest.main()
