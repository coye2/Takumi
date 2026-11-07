"""Rei model, GPU budget, and multilingual prompts; no online API required."""
import json
import subprocess
import threading
import unittest
from pathlib import Path
from unittest import mock

from takumi import models
from takumi.core import ui_event_stream, instruction_prompt, web_design_context
from takumi.intelligence import profile_for
from takumi.language import resolve_language
from takumi.server import resilient_chat_events

ROOT = Path(__file__).resolve().parents[1]


class ReiTests(unittest.TestCase):
    def test_rei_profile_and_upstream_are_honest(self):
        meta = models.rei_manifest()
        self.assertEqual(meta['name'], 'Rei 1.1')
        self.assertEqual(meta['base_model'], 'bonsai-2:9b')
        self.assertIn('No additional weights', meta['training'])
        self.assertIn('FROM bonsai-2:9b', (ROOT/'models/Rei-1.0.Modelfile').read_text())
        self.assertIn('Rei-1.0.Modelfile', (ROOT/'scripts/Ensure-Sumi-1.0.ps1').read_text())
        self.assertEqual(models.display_name('rei-1.0:latest'), 'Rei 1.1')

    def test_rei_registration_never_downloads_on_its_own(self):
        with self.assertRaisesRegex(ValueError, 'base weights'):
            models.register_rei([])
        commands = []
        def runner(argv, **kwargs):
            commands.append(argv)
            return subprocess.CompletedProcess(argv, 0)
        result = models.register_rei(['bonsai-2:9b'], engine='ollama', runner=runner)
        self.assertTrue(result['registered'])
        self.assertEqual(commands[0][1:3], ['create', 'rei-1.0'])
        self.assertNotIn('pull', commands[0])
        self.assertEqual(models.default_model(['rei-1.0:latest']), 'rei-1.0:latest')

    def test_rei_8gb_memory_budgets(self):
        for effort, expected in [('quick', 4096), ('balanced', 8192), ('high', 12288), ('maximum', 16384)]:
            p = profile_for('standard', effort, model='rei-1.0:latest')
            self.assertLessEqual(p.context, expected)
            self.assertLessEqual(p.output_tokens, 2600)
        self.assertLess(profile_for('boost','balanced',model='rei-1.0').context,
                        profile_for('standard','balanced',model='sumi-1.0').context)
        self.assertEqual(profile_for('standard','maximum',model='sumi-1.0').context, 32768)

    def test_rei_failover_never_loads_sumi_on_8gb(self):
        attempts=[]
        def offline(*args, **kwargs):
            attempts.append(args[2])
            raise OSError('service unavailable')
            yield None
        with self.assertRaises(OSError):
            list(resilient_chat_events(offline,'http://127.0.0.1:11434',[],
                'rei-1.0:latest','default',threading.Event(),{},
                ['rei-1.0:latest','sumi-1.0:latest','bonsai-2:27b']))
        self.assertEqual(attempts, ['rei-1.0:latest', 'rei-1.0:latest'])

    def test_rei_selection_survives_desktop_relaunch(self):
        ui = (ROOT/'web/app.js').read_text(encoding='utf-8')
        self.assertIn("takumi-selected-model", ui)
        self.assertIn("rememberModelChoice()", ui)
        self.assertIn('models.find(m=>/^rei-', ui)

    def test_installer_does_not_claim_sumi_when_rei_is_selected(self):
        install = (ROOT/'scripts/Install-Takumi.ps1').read_text(encoding='utf-8')
        self.assertIn('Selected Takumi model installed and inference verified', install)
        self.assertNotIn("Write-Host 'Sumi 1.0 installed and inference verified.'", install)

    def test_italian_language_detected_without_translating_code(self):
        self.assertEqual(resolve_language('Puoi spiegarmi perché questo codice non funziona?'), 'it')
        self.assertEqual(resolve_language('Come posso migliorare questo programma?'), 'it')
        self.assertEqual(resolve_language('Please fix the shader translation.'), 'en')
        self.assertEqual(resolve_language('Hi', preference='it'), 'it')
        self.assertEqual(resolve_language('Ciao'), 'it')
        prompt = instruction_prompt('Puoi correggere questo shader e testare?', language='it', model='rei-1.0')
        self.assertIn('Rei 1.1', prompt)
        self.assertIn('professional Italian', prompt)
        self.assertIn('Keep code', prompt)

    def test_chat_payload_stays_italian(self):
        seen={}
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def __iter__(self):return iter([b'{"message":{"content":"Certo, esaminiamo il codice."},"done":true}'])
        def open_stub(req, cancel, timeout):
            seen.update(json.loads(req.data))
            return FakeResponse()
        with mock.patch('takumi.core.open_stream',side_effect=open_stub),mock.patch(
                'takumi.core.reasoning_profile',return_value={'verified':False,'levels':['default']}):
            list(ui_event_stream('http://127.0.0.1:11434',
                [{'role':'user','content':'Puoi spiegare perché il programma è lento?'}],
                'rei-1.0:latest','default',threading.Event(),language='auto'))
        system=seen['messages'][0]['content']
        self.assertIn('Rei 1.1',system)
        self.assertIn('professional Italian',system)
        self.assertEqual(seen['options']['num_ctx'],8192)

    def test_site_design_prompt_uses_real_web_design_guidance(self):
        self.assertIn('responsive',web_design_context('Build a modern HTML website'))
        self.assertNotIn('WEBSITE DESIGN SKILL',web_design_context('GPU semaphore failure'))
        prompt=instruction_prompt('Create a responsive web page in HTML',model='rei-1.0:latest')
        self.assertIn('generic AI-generated',prompt)


if __name__=='__main__': unittest.main()

class ItalianWorkflowTests(unittest.TestCase):
    def test_italian_work_routes_to_project_agent(self):
        from takumi.routing import choose_mode
        self.assertEqual(choose_mode('auto','Correggi il bug nel renderer Vulkan', True),'agent')
        self.assertEqual(choose_mode('auto','Puoi implementare un test JIT?',True),'agent')
        self.assertEqual(choose_mode('auto','Come funziona la GPU?',True),'chat')
        self.assertEqual(choose_mode('auto','Correggi il bug nel renderer',False),'chat')

    def test_italian_web_research_trigger(self):
        from takumi.research import should_research
        self.assertTrue(should_research('Cerca online una recente implementazione del renderer.'))
        self.assertFalse(should_research('Ciao!'))
