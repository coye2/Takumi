
import unittest
from pathlib import Path
from takumi.core import APP_VERSION
from takumi.server import build_stamp

ROOT=Path(__file__).resolve().parents[1]

class RepairTests(unittest.TestCase):
    def test_build_version(self):
        self.assertEqual(APP_VERSION,'1.5.0')
    def test_compiler_bug_is_absent(self):
        text=(ROOT/'desktop/Takumi.Desktop/TakumiTitleBar.cs').read_text()
        self.assertIn('internal CaptionSymbol Symbol;',text)
        self.assertNotIn('CaptionSymbol Symbol { get; set; }',text)
    def test_backend_and_desktop_compare_source(self):
        self.assertEqual(len(build_stamp(ROOT)),64)
        host=(ROOT/'desktop/Takumi.Desktop/Program.cs').read_text()
        self.assertIn('api/runtime-identity',host)
        self.assertIn('BuildStamp(root)',host)
        backend=(ROOT/'takumi/server.py').read_text()
        self.assertIn('if route == "/api/runtime-identity"',backend)
    def test_locked_app_has_isolated_install(self):
        s=(ROOT/'scripts/Install-Takumi.ps1').read_text()
        self.assertIn('Takumi-Repaired-1.5.0-',s)
        self.assertNotIn("throw 'Takumi is currently running.",s)
        self.assertIn('Get-FileHash -LiteralPath $installedNative',s)
    def test_repair_launcher_present(self):
        self.assertTrue((ROOT/'scripts/OneClick-Repair.ps1').is_file())

if __name__=='__main__':unittest.main()
