"""Stable update and staged-source integrity checks. Never install or replace an app."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from takumi.updater import API, UpdateManager, _asset


class OtaSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.name = 'Takumi-v1.4.2-Sumi-1.0-Rei-1.0-Windows-Setup.zip'
        archive = io.BytesIO()
        with ZipFile(archive, 'w') as z:
            z.writestr('Takumi/scripts/Build-Windows-EXE.ps1', "Write-Output 'approved'\n")
            z.writestr('Takumi/desktop/Takumi.Desktop/Takumi.Desktop.csproj', '<Project/>')
            z.writestr('Takumi/takumi/core.py', '# verified source\n')
            z.writestr('Takumi/README.md', 'release metadata\n' * 100)
        self.raw = archive.getvalue()
        self.url = 'https://github.com/coye2/Takumi/releases/download/v1.4.2/' + self.name
        self.release = {'tag_name': 'v1.4.2', 'prerelease': False, 'draft': False,
                        'assets': [{'name': self.name, 'size': len(self.raw),
                                    'digest': 'sha256:' + hashlib.sha256(self.raw).hexdigest(),
                                    'browser_download_url': self.url}]}

    def manager(self):
        def fetch(url, max_bytes):
            if url == API:
                return json.dumps(self.release).encode('utf-8')
            if url == self.url:
                return self.raw
            raise AssertionError('Unexpected URL')
        return UpdateManager(self.root, '1.4.1', fetch=fetch)

    def test_valid_stable_source_stage_can_be_scheduled_without_running(self):
        updater = self.manager()
        self.assertTrue(updater.check()['available'])
        self.assertTrue(updater.prepare()['prepared'])
        install = self.root / 'installed'
        (install / 'scripts').mkdir(parents=True)
        (install / 'scripts' / 'Apply-Takumi-Update.ps1').write_text('# existing helper')
        commands = []
        status = updater.install_on_close(install, launcher=commands.append)
        self.assertTrue(status['scheduled'])
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], 'powershell.exe')

    def test_mutated_stage_does_not_launch_an_update(self):
        updater = self.manager()
        updater.prepare()
        stage = self.root / 'updates' / 'v1.4.2' / 'staged'
        (stage / 'takumi' / 'core.py').write_text('# replaced after archive verification')
        install = self.root / 'installed'
        (install / 'scripts').mkdir(parents=True)
        (install / 'scripts' / 'Apply-Takumi-Update.ps1').write_text('# existing helper')
        commands = []
        with self.assertRaisesRegex(ValueError, 'Staged update file'):
            updater.install_on_close(install, launcher=commands.append)
        self.assertFalse(commands)

    def test_extra_file_in_staged_update_is_rejected(self):
        updater = self.manager()
        updater.prepare()
        stage = self.root / 'updates' / 'v1.4.2' / 'staged'
        (stage / 'malicious.ps1').write_text('Write-Host unexpected')
        install = self.root / 'installed'
        (install / 'scripts').mkdir(parents=True)
        (install / 'scripts' / 'Apply-Takumi-Update.ps1').write_text('# existing helper')
        with self.assertRaisesRegex(ValueError, 'extra files'):
            updater.install_on_close(install, launcher=lambda argv: self.fail('launched'))

    def test_draft_and_prerelease_cannot_be_stable_ota(self):
        for key in ('draft', 'prerelease'):
            with self.subTest(key=key):
                release = dict(self.release)
                release[key] = True
                with self.assertRaisesRegex(ValueError, 'Preview and draft'):
                    _asset(release, '1.4.1')


if __name__ == '__main__':
    unittest.main()
