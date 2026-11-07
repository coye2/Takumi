"""Regressions for owner-repo and subreddit requests accidentally sent as generic search."""
import base64
import hashlib
import json
import threading
import unittest
import tempfile
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse, unquote
from takumi.github_reader import GitHubReader, requested_repository
from takumi.repository_review import collect_repository_review
from takumi import reddit_reader, research
from takumi.routing import choose_mode
from takumi.grounding import research_correction

PROMPT = 'review my current switch 2 work in my nxinfinity developer repo on github'
REDDIT = 'HOW ARE people liking the latest NX Infinity reddit post in r/yuzu'
A, B = 'a'*40, 'b'*40


class Reader:
    def __init__(self, ambiguous=False): self.paths=[]; self.ambiguous=ambiguous
    def get(self,path):
        self.paths.append(path)
        if path=='/user': return {'login':'owner'}
        if path.startswith('/user/repos'):
            rows=[{'name':'NX-Infinity-Development','full_name':'owner/NX-Infinity-Development','private':True,'default_branch':'main'}]
            if self.ambiguous: rows.append({**rows[0],'full_name':'other/NX-Infinity-Development'})
            return rows
        if '/branches?' in path:
            return [{'name':'main','commit':{'sha':'c'*40}},
                    {'name':'work/switch2-integration','commit':{'sha':A}},
                    {'name':'work/switch2-core','commit':{'sha':A}},
                    {'name':'work/switch2-gpu','commit':{'sha':B}}]
        if '/git/trees/' in path:
            return {'truncated':False,'tree':[{'type':'blob','path':name,'size':120} for name in ('docs/switch2/STATUS.md','core/Switch2Gpu.cs','tests/Switch2Tests.cs')]}
        if '/contents/' in path:
            raw=b'// actual source from the chosen commit\n'
            return {'type':'file','encoding':'base64','size':len(raw),'content':base64.b64encode(raw).decode(),
                    'sha':hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()}
        if '/commits/' in path: return {'sha':'c'*40}
        return {'default_branch':'main','private':True}
    def fetch(self,url):
        p=urlparse(url)
        return json.dumps(self.get(p.path+('?' + p.query if p.query else ''))).encode()


class RepositoryRoutingTests(unittest.TestCase):
    def test_owned_review_and_capability_followup_reuse_request(self):
        self.assertEqual(requested_repository(PROMPT),PROMPT)
        self.assertEqual(requested_repository('you have github access',[{'role':'user','content':PROMPT}]),PROMPT)
        self.assertIsNone(requested_repository('find public github shader reference implementations'))
        self.assertEqual(choose_mode('auto',PROMPT,True),'chat')
        self.assertEqual(choose_mode('agent',PROMPT,True),'agent')
        self.assertEqual(choose_mode('auto','fix my GitHub repo',True),'agent')

    def test_private_repo_distinct_branch_commits_and_real_bytes(self):
        reader=Reader();packet=collect_repository_review(PROMPT,reader=reader)
        self.assertEqual(packet['repository'],'owner/NX-Infinity-Development')
        self.assertTrue(packet['private'])
        self.assertEqual([s['commit'] for s in packet['snapshots']],[A,B])
        self.assertTrue(all('actual source' in f['excerpt'] for s in packet['snapshots'] for f in s['files']))
        self.assertTrue(all('ref='+A in p or 'ref='+B in p for p in reader.paths if '/contents/' in p))
        self.assertNotIn('/search/',str(reader.paths))
        self.assertIn('GITHUB READ SERVICE',research.context(packet))
        self.assertIsNotNone(research_correction('No GitHub access is available.',packet))
        self.assertIn('were not checked',research_correction('The code is ready.',packet))

    def test_slash_branch_is_not_mistaken_for_repo(self):
        packet=collect_repository_review(PROMPT+' branch work/switch2-gpu',reader=Reader())
        self.assertEqual(packet['repository'],'owner/NX-Infinity-Development')
        self.assertEqual([s['commit'] for s in packet['snapshots']],[B])

    def test_ambiguous_repository_reads_no_source(self):
        reader=Reader(True);packet=collect_repository_review(PROMPT,reader=reader)
        self.assertFalse(packet['snapshots'])
        self.assertEqual(packet['providers'][0]['status'],'connected')
        self.assertFalse(any('/contents/' in p for p in reader.paths))

    def test_permission_failure_is_not_public_search(self):
        reader=mock.Mock();reader.get.side_effect=ValueError('GitHub read 403')
        with mock.patch('takumi.research._fetch',side_effect=AssertionError('private query leaked')):
            packet=collect_repository_review(PROMPT,reader=reader)
        self.assertEqual(packet['freshness'],'unavailable')
        self.assertIn('403',packet['errors'][0])

    def test_runtime_research_endpoint_also_keeps_owned_requests_private(self):
        from takumi.server import Runtime, Storage
        with tempfile.TemporaryDirectory() as temp:
            runtime=Runtime(Storage(Path(temp)))
            with mock.patch('takumi.server.collect_repository_review',return_value={'kind':'github_repository'}) as read, mock.patch('takumi.server.conduct_research',side_effect=AssertionError('private query leaked')):
                self.assertEqual(runtime.get_research(PROMPT),{'kind':'github_repository'})
            read.assert_called_once_with(PROMPT)
            self.assertFalse(runtime.research_cache)

    def test_cancel_does_not_start_cli_or_follow_arbitrary_host(self):
        cancel=threading.Event();cancel.set();reader=GitHubReader(cancel);reader.executable='gh'
        with mock.patch('takumi.github_reader.subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError,'cancelled'): reader.get('/user')
            with self.assertRaises(ValueError): reader.fetch('https://other.example/repos/me/repo')
        launch.assert_not_called()


class RedditRoutingTests(unittest.TestCase):
    def test_query_keeps_subject_not_conversation_fillers(self):
        self.assertEqual(reddit_reader.request_scope(REDDIT),{'subreddit':'yuzu','topic':'NX Infinity','post_id':None})
        self.assertTrue(research.should_research('How are people liking NX Infinity in r/yuzu'))
        self.assertFalse(research.should_research(REDDIT,'off'))
        self.assertFalse(research.should_research('Fix the source of the startup error in the local backend.'))
        self.assertFalse(research.should_research('Implement the settings page and run the project tests.'))
        self.assertTrue(research.should_research('Fix the crash, then look up the current OAuth documentation.'))
        self.assertTrue(research.should_research('find public github shader reference implementations'))

    def test_newest_matching_post_comment_sample_and_links(self):
        urls=[]
        def fetch(url):
            urls.append(url)
            if '/search.json' in url:
                return json.dumps({'data':{'children':[{'data':{'id':i,'title':title,'created_utc':n,'subreddit':'yuzu'}}
                    for i,title,n in [('aaa','NX Infinity release',1),('bbb','NX Infinity update',2),('ccc','Unrelated post',3)]]}}).encode()
            post={'id':'bbb','subreddit':'yuzu','title':'NX Infinity update','score':42,'selftext':'Release notes','num_comments':500}
            return json.dumps([{'data':{'children':[{'data':post}]}},{'data':{'children':[
                {'kind':'t1','data':{'id':'d1','body':'I like the UI','score':4}},
                {'kind':'t1','data':{'id':'d2','body':'Scanning is slow','score':3}},
                {'kind':'t1','data':{'id':'d3','body':'[deleted]'}}, {'kind':'more','data':{}}]}}]).encode()
        packet=reddit_reader.collect(REDDIT,fetch=fetch)
        self.assertEqual(packet['freshness'],'live_comments')
        self.assertEqual(len(packet['comments']),2)
        self.assertEqual(packet['post']['num_comments'],500)
        self.assertIn('/bbb.json',urls[1])
        self.assertIn('q=NX%20Infinity',urls[0])
        self.assertFalse(packet['latest_verified'])
        self.assertIn('not unique users',research.context(packet))

    def test_blocked_read_rejects_dictionary_and_wrong_subreddit(self):
        query=[]
        def fetch(url): raise OSError('403 Blocked')
        def search(q,limit):
            query.append(q)
            return {'providers':[],'results':[{'url':'https://dictionary.example/people','title':'People'},
                {'url':'https://reddit.com/r/other/comments/abc','title':'NX Infinity'},
                {'url':'https://reddit.com/r/yuzu/comments/abc','title':'NX Infinity release','snippet':'Release announcement'}]}
        packet=reddit_reader.collect(REDDIT,fetch=fetch,search=search)
        self.assertEqual(len(packet['results']),1)
        self.assertEqual(packet['freshness'],'live_leads')
        self.assertFalse(packet['comments'])
        self.assertIn('not a sentiment sample',packet['scope'])
        self.assertEqual(query,['site:reddit.com/r/yuzu/comments/ NX Infinity'])

    def test_irrelevant_fast_provider_cannot_win_before_valid_provider(self):
        def provider(name,query,fetch):
            if name=='bing-rss': return [{'url':'https://dictionary.example/people','title':'People'}]*10
            if name=='duckduckgo-html': return [{'url':'https://www.reddit.com/r/yuzu/comments/abc/','title':'NX Infinity'}]
            return []
        with mock.patch('takumi.research._web_provider',side_effect=provider):
            result=research.web_search_detailed('site:reddit.com/r/yuzu/comments/ NX Infinity',limit=1,fetch=lambda u:b'')
        self.assertEqual(len(result['results']),1)
        self.assertIn('reddit.com',result['results'][0]['url'])

    def test_configured_provider_is_used_for_reddit_fallback(self):
        hit={'url':'https://reddit.com/r/yuzu/comments/abc/','title':'NX Infinity','snippet':'Announcement'}
        provider=mock.Mock(return_value=[hit])
        with mock.patch('takumi.research._fetch',side_effect=OSError('blocked')), mock.patch('takumi.research.web_search_detailed',return_value={'results':[],'providers':[]}), mock.patch('takumi.search_connectors.configured_providers',return_value=[('fixture',provider,'secret')]):
            packet=research.research(REDDIT,credentials_dir='fixture')
        self.assertEqual(packet['results'],[hit])
        self.assertNotIn('secret',json.dumps(packet))
        self.assertIn('site:reddit.com/r/yuzu/comments/',provider.call_args.args[0])


if __name__=='__main__': unittest.main()
