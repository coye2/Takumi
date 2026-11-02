"""Regression coverage for source evidence, GPU isolation, and verified OTA staging."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock
import zipfile

from takumi.engineering_tools import inspect_binary, inventory, symbol_search, validate_spirv, disassemble_spirv, analyze_ghidra
from takumi.gpu_scheduler import GpuScheduler
from takumi.updater import API, UpdateManager, _asset, _url_allowed
from takumi.harness import Harness
from takumi.core import Storage
from takumi.intelligence import profile_for


class EngineerToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.project=Path(self.tmp.name)/'project'
        self.project.mkdir()

    def tearDown(self): self.tmp.cleanup()

    def test_binary_signatures_are_evidence_not_content_leaks(self):
        data=b'\x7fELF'+bytes(range(256))*6
        (self.project/'test.elf').write_bytes(data)
        report=inspect_binary(self.project,'test.elf')
        self.assertIn('ELF',report['format_hint'])
        self.assertEqual(report['sha256'],hashlib.sha256(data).hexdigest())
        self.assertNotIn('strings',report)
        with self.assertRaises(ValueError):inspect_binary(self.project,'../secrets.bin')

    def test_symbol_search_project_bound_and_case_insensitive(self):
        (self.project/'GpuBarrierState.cs').write_text('class GPUBarrier {\nvoid DEPBAR() {}\n}\n')
        got=symbol_search(self.project,'depbar')
        self.assertEqual(got['matches'][0]['line'],2)
        self.assertEqual(got['matches'][0]['file'],'GpuBarrierState.cs')
        with self.assertRaises(ValueError):symbol_search(self.project,'')

    def test_symlink_escape_denied(self):
        with tempfile.TemporaryDirectory() as outside:
            target=Path(outside)/'sensitive.elf';target.write_bytes(b'\x7fELF')
            try:(self.project/'link.elf').symlink_to(target)
            except OSError:self.skipTest('Symlink unavailable')
            with self.assertRaises(ValueError):inspect_binary(self.project,'link.elf')

    def test_spirv_validator_unavailable_is_not_success(self):
        (self.project/'shader.spv').write_bytes(b'\x03\x02\x23\x07'+bytes(40))
        result=validate_spirv(self.project,'shader.spv',which=lambda _:None)
        self.assertFalse(result['verified'])
        with self.assertRaises(ValueError):validate_spirv(self.project,'missing.spv')

    def test_tool_inventory_reports_installed_versus_missing(self):
        result=inventory(which=lambda name:'/usr/bin/'+name if name=='clangd' else None)
        self.assertTrue(next(x for x in result if x['id']=='clangd')['installed'])
        self.assertFalse(next(x for x in result if x['id']=='renderdoc')['installed'])


class GpuSchedulerTests(unittest.TestCase):
    def test_reservation_blocks_model_and_releases(self):
        called=[]
        busy={'v':False}
        scheduler=GpuScheduler(is_model_busy=lambda:busy['v'],unload=lambda:called.append(1))
        busy['v']=True
        with self.assertRaisesRegex(ValueError,'inference'):scheduler.acquire('capture')
        busy['v']=False
        scheduler.acquire('capture')
        self.assertTrue(scheduler.is_reserved())
        self.assertEqual(called,[1])
        with self.assertRaisesRegex(ValueError,'already running'):scheduler.acquire('other')
        self.assertFalse(scheduler.release('other'))
        self.assertTrue(scheduler.release('capture'))
        self.assertFalse(scheduler.is_reserved())

    def test_exclusive_harness_task_cannot_claim_busy_gpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp)/'prj';project.mkdir()
            (project/'takumi.tasks.json').write_text(json.dumps({'version':1,'tasks':[
                {'id':'gpu-capture','argv':['python','-c','print("capture")'],
                 'gpu_exclusive':True,'cwd':'.'}]}))
            store=Storage(Path(tmp)/'data');store.register_project(str(project));pid=store.data['projects'][0]['id']
            busy={'v':True};gpu=GpuScheduler(is_model_busy=lambda:busy['v'])
            harness=Harness(store,gpu_scheduler=gpu)
            with self.assertRaisesRegex(ValueError,'inference'):harness.launch(pid,'gpu-capture')
            self.assertFalse(gpu.is_reserved())
            busy['v']=False
            with mock.patch('takumi.harness.shutil.which', return_value=sys.executable):
                run=harness.launch(pid,'gpu-capture')
            self.assertTrue(run['gpu_exclusive'])
            limit=time.monotonic()+5
            while gpu.is_reserved() and time.monotonic()<limit:time.sleep(.025)
            self.assertFalse(gpu.is_reserved())
            self.assertEqual(harness.inspect()[0]['status'],'passed')


class UpdateTests(unittest.TestCase):
    @staticmethod
    def archive(*, malicious=False):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            for name in ('scripts/Build-Windows-EXE.ps1','desktop/Takumi.Desktop/Takumi.Desktop.csproj','takumi/core.py'):
                z.writestr('Takumi/'+name,'ok')
            z.writestr('Takumi/docs/random-fixture.bin',os.urandom(2400))
            if malicious:z.writestr('Takumi/../../escaped.txt','no')
        return buf.getvalue()

    def manager(self,tmp, data, *, digest=None):
        name='Takumi-v1.4.0-Sumi-1.0-Rei-1.0-Windows-Setup.zip'
        asset={'name':name,'size':len(data),'digest':'sha256:'+(digest or hashlib.sha256(data).hexdigest()),
               'browser_download_url':'https://github.com/coye2/Takumi/releases/download/v1.4.0/'+name}
        release={'tag_name':'v1.4.0','assets':[asset],'body':'Changes'}
        def fetch(url,max_bytes):
            if url==API:return json.dumps(release).encode()
            return data
        return UpdateManager(Path(tmp),'1.3.5',fetch=fetch)

    def test_update_staging_and_digest(self):
        with tempfile.TemporaryDirectory() as t:
            m=self.manager(t,self.archive())
            self.assertTrue(m.check()['available'])
            self.assertTrue(m.prepare()['prepared'])
            self.assertTrue((Path(t)/'updates'/'v1.4.0'/'staged'/'takumi'/'core.py').is_file())
            self.assertFalse((Path(t)/'updates'/'v1.4.0'/'staged'/'Takumi-Setup.exe').exists())
            self.assertTrue(m.check()['verified_digest'])

    def test_tampered_digest_refused_and_existing_data_preserved(self):
        with tempfile.TemporaryDirectory() as t:
            m=self.manager(t,self.archive(),digest='0'*64)
            self.assertTrue(m.check()['available'])
            with self.assertRaisesRegex(ValueError,'SHA-256'):m.prepare()
            self.assertFalse((Path(t)/'updates'/'v1.4.0'/'staged').exists())

    def test_zip_traversal_denied(self):
        with tempfile.TemporaryDirectory() as t:
            m=self.manager(t,self.archive(malicious=True))
            self.assertTrue(m.check()['available'])
            with self.assertRaisesRegex(ValueError,'Unsafe path'):m.prepare()
            self.assertFalse((Path(t)/'escaped.txt').exists())

    def test_release_missing_digest_or_unsupported_host_refused(self):
        asset={'name':'Takumi-v1.4.0-Sumi-Rei-Windows-Setup.zip','size':2200,
               'digest':'','browser_download_url':'https://github.com/coye2/Takumi/releases/test'}
        with self.assertRaisesRegex(ValueError,'SHA-256'):_asset({'tag_name':'v1.4.0','assets':[asset]},'1.3.5')
        self.assertFalse(_url_allowed('https://127.0.0.1/download.zip'))
        self.assertFalse(_url_allowed('http://github.com/coye2/Takumi/test'))



class ExpandedHardwareTests(unittest.TestCase):
    def test_default_4060_ti_and_rei_are_conservative(self):
        self.assertEqual(profile_for('standard','maximum','sumi-1.0',vram_mb=16384).context,32768)
        self.assertEqual(profile_for('standard','balanced','rei-1.0',vram_mb=8192).context,8192)
        self.assertEqual(profile_for('standard','maximum','rei-1.0',vram_mb=32768).context,16384)
        self.assertLessEqual(profile_for('standard','maximum','sumi-1.0',vram_mb=8192).context,8192)

    def test_high_vram_enables_more_context_without_unbounded_allocation(self):
        self.assertEqual(profile_for('standard','high','sumi-1.0',vram_mb=24576).context,32768)
        self.assertEqual(profile_for('standard','maximum','sumi-1.0',vram_mb=32768).context,49152)
        self.assertEqual(profile_for('standard','maximum','sumi-1.0',vram_mb=49152).context,65536)
        self.assertEqual(profile_for('boost','balanced','sumi-1.0',vram_mb=49152).context,8192)
        self.assertEqual(profile_for('standard','balanced','sumi-1.0',vram_mb=None).context,16384)


class BoundedAnalyzerTests(unittest.TestCase):
    def test_spirv_disassembler_marks_missing_and_parses_real_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'shader.spv').write_bytes(b'\x03\x02\x23\x07'+bytes(36))
            self.assertFalse(disassemble_spirv(root,'shader.spv',which=lambda _:None)['disassembled'])
            args=[]
            def runner(argv,**kwargs):
                args.append((argv,kwargs))
                return __import__('subprocess').CompletedProcess(argv,0,'OpCapability Shader\nOpMemoryModel Logical GLSL450','')
            out=disassemble_spirv(root,'shader.spv',which=lambda _: '/tools/spirv-dis',runner=runner)
            self.assertTrue(out['disassembled'])
            self.assertIn('OpCapability Shader',out['assembly'])
            self.assertFalse(args[0][1]['shell'])
            with self.assertRaises(ValueError):disassemble_spirv(root,'../shader.spv')

    def test_ghidra_analysis_is_explicit_bounded_and_not_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'rom.bin').write_bytes(b'\x7fELF' + bytes(80))
            self.assertFalse(analyze_ghidra(root,'rom.bin',which=lambda _:None)['analyzed'])
            executions=[]
            def runner(argv,**kwargs):
                executions.append((argv,kwargs))
                return __import__('subprocess').CompletedProcess(argv,0,
                    'TAKUMI_FUNCTION|00100000|main\nTAKUMI_FUNCTION|00100040|sub_100040\n','')
            result=analyze_ghidra(root,'rom.bin',which=lambda _:'/tools/analyzeHeadless',runner=runner)
            self.assertTrue(result['analyzed'])
            self.assertEqual(result['functions'][0]['name'],'main')
            self.assertEqual(result['function_count_reported'],2)
            argv,kw=executions[0]
            self.assertIn('-readOnly',argv)
            self.assertIn('-postScript',argv)
            self.assertEqual(kw['timeout'],220)
            self.assertEqual(kw['env']['GHIDRA_HEADLESS_MAXMEM'],'2G')
            self.assertFalse(kw['shell'])
            self.assertNotIn('Ghidra completed analysis',str(result))
            with self.assertRaises(ValueError):analyze_ghidra(root,'../../etc/passwd',which=lambda _:'/tools/analyzeHeadless')

    def test_ghidra_failure_does_not_imply_code_was_decompiled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'test.bin').write_bytes(b'NSO0'+bytes(36))
            def failed(argv,**kw):return __import__('subprocess').CompletedProcess(argv,1,'','Analyzer rejected input')
            out=analyze_ghidra(root,'test.bin',runner=failed,which=lambda _:'/tools/analyzeHeadless')
            self.assertFalse(out['analyzed'])
            self.assertEqual(out['exit_code'],1)


class UpdateLaunchTests(unittest.TestCase):
    def test_staged_verified_update_requires_explicit_install_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=UpdateTests.archive()
            mgr=UpdateTests().manager(tmp,data)
            self.assertTrue(mgr.check()['available'])
            self.assertTrue(mgr.prepare()['prepared'])
            commands=[]
            root=Path(__file__).resolve().parents[1]
            scheduled=mgr.install_on_close(root,launcher=lambda cmd:commands.append(cmd))
            self.assertTrue(scheduled['scheduled'])
            self.assertEqual(len(commands),1)
            self.assertIn('Apply-Takumi-Update.ps1',' '.join(commands[0]))
            self.assertIn('-ParentPid',commands[0])

if __name__=='__main__':unittest.main()
