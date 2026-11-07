import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from takumi import models, expertise, source_miner, core
from scripts import package_release

ROOT=Path(__file__).resolve().parents[1]

class SumiReleaseTests(unittest.TestCase):
    def test_identity_is_not_false_training(self):
        m=models.manifest()
        self.assertEqual(m['name'],'Sumi 1.1')
        self.assertEqual(m['base_model'],'bonsai-2:27b')
        self.assertIn('No new training',m['training'])
        self.assertTrue((ROOT/'models/Sumi-1.0.Modelfile').read_text().startswith('# Sumi 1.1'))
        self.assertEqual(models.display_name('sumi-1.0:latest'),'Sumi 1.1')
        self.assertEqual(models.display_name('bonsai-2:27b'),'Bonsai 27B')
        self.assertNotIn('Sumi', models.display_name('bonsai-2:27b'))

    def test_prefer_sumi_when_installed(self):
        found=['bonsai-2:27b','sumi-1.0:latest','qwen3:4b']
        self.assertEqual(models.default_model(found),'sumi-1.0:latest')
        self.assertEqual(models.catalog(found)[0]['label'],'Sumi 1.1')
        self.assertTrue(models.state(found)['installed'])

    def test_model_menu_names_are_unique_and_do_not_call_base_weights_sumi(self):
        found=['rei-1.0:latest','bonsai-2:9b','sumi-1.0:latest','bonsai2-local:latest','bonsai-2:27b','qwen3:4b']
        rows=models.catalog(found)
        labels=[row['label'] for row in rows]
        self.assertEqual(labels[0],'Sumi 1.1')
        self.assertEqual(labels[1],'Rei 1.1')
        self.assertEqual(len(labels),len(set(labels)))
        by_id={row['id']:row for row in rows}
        self.assertEqual(by_id['bonsai-2:27b']['label'],'Bonsai 27B')
        self.assertEqual(by_id['bonsai-2:9b']['label'],'Bonsai 9B')
        self.assertIn('not the Sumi profile', by_id['bonsai-2:27b']['note'])
        self.assertIn('not the Rei profile', by_id['bonsai-2:9b']['note'])
        self.assertNotIn(':', by_id['qwen3:4b']['label'])
        ui=(ROOT/'web/app.js').read_text(encoding='utf-8')
        self.assertIn('modelMenuList', ui)
        self.assertIn('Takumi profiles', ui)
        self.assertIn("ordered.push(row)", ui)

    def test_model_activation_no_download(self):
        with self.assertRaisesRegex(ValueError, 'base weights'):
            models.register_sumi([])
        expected=[]
        def fake(cmd,**kw):
            expected.extend(cmd)
            return subprocess.CompletedProcess(cmd,0,'','')
        result=models.register_sumi(['bonsai-2:27b'],engine='ollama',runner=fake)
        self.assertTrue(result['registered'])
        self.assertEqual(expected[1:3],['create','sumi-1.0'])
        self.assertNotIn('pull',expected)

    def test_release_includes_knowledge_and_model_identity(self):
        names={p.relative_to(ROOT).as_posix() for p in package_release.selected('source')}
        for name in ('models/Sumi-1.0.Modelfile','models/sumi-1.0.json',
                     'expertise/platforms.json','expertise/playbooks.json','expertise/sources.json',
                     'scripts/Register-Sumi-1.0.ps1','takumi/source_miner.py'):
            self.assertIn(name,names)
        build=(ROOT/'scripts/Build-Windows-EXE.ps1').read_text()
        self.assertIn("'expertise'",build)
        self.assertIn("'models'",build)

    def test_specialization_recognizes_ps3_and_decomp(self):
        p=expertise.retrieve('PS3 SPU DMA MFC interrupts and barrier ordering')
        self.assertTrue(p['relevant'])
        self.assertEqual(p['platforms'][0]['id'],'ps3')
        self.assertTrue(p['sources'])
        n=expertise.retrieve('N64 MIPS matching decompilation symbol relocation')
        self.assertIn('n64',[x['id'] for x in n['platforms']])
        self.assertIn('decomp',[x['id'] for x in n['playbooks']])

    def test_specialist_context_enters_actual_chat_payload(self):
        from takumi.core import ui_event_stream
        seen={}
        class FakeResponse:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def __iter__(self):return iter([b'{"message":{"content":"Done"},"done":true,"eval_count":1,"eval_duration":1000000000}'])
        def open_stub(request,cancel,timeout):
            seen.update(json.loads(request.data))
            return FakeResponse()
        import threading
        with mock.patch('takumi.core.open_stream',side_effect=open_stub),mock.patch('takumi.core.reasoning_profile',return_value={'verified':False,'levels':['default']}):
            list(ui_event_stream('http://127.0.0.1:11434',[{'role':'user','content':'GPU issue'}],
                 'sumi-1.0:latest','default',threading.Event(),expertise_context='PLATFORM: PS3 / SPU'))
        self.assertIn('PLATFORM: PS3 / SPU',seen['messages'][0]['content'])
        self.assertEqual(seen['model'],'sumi-1.0:latest')

    def test_representation_playbooks_route_without_a_console_name(self):
        for query, book in [('completion counter wraps at its bit width', 'serial-arithmetic'),
                            ('texture row pitch and padded rows', 'layout-strides'),
                            ('float bit patterns and signed zero', 'bit-representation'),
                            ('GPU completion and mapped bytes', 'completion-visibility')]:
            with self.subTest(query=query):
                result = expertise.retrieve(query)
                self.assertTrue(result['relevant'])
                self.assertIn(book, [row['id'] for row in result['playbooks']])
                self.assertFalse(result['truncated'])
        self.assertFalse(expertise.retrieve('Write a friendly birthday greeting')['relevant'])

class SourceMinerTests(unittest.TestCase):
    def test_reject_invalid_repositories_and_paths(self):
        for value in ('http://127.0.0.1/a','../../a','owner/repo/extra','owner/.git/secret'):
            with self.assertRaises(ValueError): source_miner.normalize_repo(value)
        for value in ('../foo.cpp','C:\\foo.cpp','/etc/a.py','bin/rom.nsp'):
            with self.assertRaises(ValueError): source_miner.normalize_path(value)

    def test_bounded_source_map_and_read(self):
        repo={'default_branch':'main','html_url':'https://github.com/test/emu','description':'sample',
              'license':{'spdx_id':'GPL-2.0-or-later'}}
        tree={'tree':[{'type':'blob','path':'src/video/barrier.cpp','size':123},
                      {'type':'blob','path':'src/video/dma.cpp','size':77},
                      {'type':'blob','path':'assets/game.nsp','size':34}]}
        code={'type':'file','encoding':'base64','size':20,'sha':__import__('hashlib').sha1(b'blob '+str(len(b'// demo\nint barrier=1;\n')).encode()+b'\0// demo\nint barrier=1;\n').hexdigest(),
              'html_url':'https://github.com/test/emu/blob/main/src/video/barrier.cpp',
              'content':base64.b64encode(b'// demo\nint barrier=1;\n').decode()}
        urls=[]
        def fetch(url):
            urls.append(url)
            return json.dumps({'sha':'a'*40} if '/commits/' in url else tree if '/git/trees/' in url else code if '/contents/' in url else repo).encode()
        m=source_miner.map_repository('https://github.com/test/emu','barrier',fetch=fetch)
        self.assertEqual(m['license'],'GPL-2.0-or-later')
        self.assertEqual(m['files'][0]['path'],'src/video/barrier.cpp')
        self.assertEqual(len(m['files']),2)
        f=source_miner.inspect_file('test/emu','src/video/barrier.cpp','main',fetch=fetch)
        self.assertIn('int barrier=1',f['excerpt'])
        self.assertEqual(f['total_lines'],2)
        self.assertEqual(f['commit'],'a'*40)
        self.assertIn('/blob/'+'a'*40+'/',f['url'])
        code['sha']='b'*40
        with self.assertRaisesRegex(ValueError,'Git blob'):
            source_miner.inspect_file('test/emu','src/video/barrier.cpp','main',fetch=fetch)
        self.assertTrue(all(u.startswith('https://api.github.com/repos/test/emu') for u in urls))

    def test_reject_binary_and_oversized_content(self):
        def fake(_): return json.dumps({'type':'file','encoding':'base64','size':48100,'content':'AA=='}).encode()
        with self.assertRaisesRegex(ValueError,'48 KB'):
            source_miner.inspect_file('test/emu','renderer.cpp','a'*40,fetch=fake)

if __name__=='__main__':unittest.main()
