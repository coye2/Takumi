import unittest
from pathlib import Path
from unittest import mock
from takumi import core, server
from scripts import package_release

class QualityTests(unittest.TestCase):
    def test_personality_reaches_agent_prompt(self):
        a=core.instruction_prompt('Inspect GPU path', personality='technical')
        b=core.instruction_prompt('Inspect GPU path', personality='friendly')
        self.assertIn('invariants',a)
        self.assertIn('collaborative',b)
        self.assertNotEqual(a,b)

    def test_no_unknown_native_thinking_variant_is_fabricated(self):
        from takumi.core import thinking_request_value
        with mock.patch('takumi.core.reasoning_profile',return_value={'verified':False,'levels':['default']}):
            with self.assertRaises(ValueError):
                thinking_request_value('low','local')

    def test_package_is_source_first(self):
        selected=package_release.selected('source')
        names={p.relative_to(package_release.ROOT).as_posix() for p in selected}
        self.assertNotIn('Takumi-Setup.exe',names)
        self.assertIn('desktop/Takumi.Desktop/Program.cs',names)
        self.assertIn('web/app.js',names)
        self.assertIn('installer/main.go',names)
        self.assertTrue(all(not p.name.endswith(('.dll','.exe','.gguf','.safetensors','.pyc')) for p in selected))

    def test_windows_setup_only_original_installer_binary(self):
        if not (package_release.ROOT / 'Takumi-Setup.exe').is_file():
            self.skipTest('This checkout has not compiled the original installer yet')
        selected=package_release.selected('windows-setup')
        binaries=[p.relative_to(package_release.ROOT).as_posix() for p in selected if p.suffix.lower() in {'.exe','.dll','.so','.bin'}]
        self.assertEqual(binaries,['Takumi-Setup.exe'])

    def test_static_frontend_links_reachable(self):
        path=Path(__file__).resolve().parents[1]
        markup=(path/'web/index.html').read_text()
        self.assertIn('id="newChat"',markup)
        for name in ('index.html','app.js','styles.css','mark.png','manifest.webmanifest','icon-192.png'):
            self.assertTrue((path/'web'/name).is_file())

if __name__=='__main__': unittest.main()
