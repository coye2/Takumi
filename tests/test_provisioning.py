"""First-run installation regression coverage without network or Windows dependencies.

The real Windows installer, model download, GPU inference, and native WebView2
execution must still be verified on a Windows machine.
"""
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from takumi.provisioning import Provisioner
from takumi import core, models, server

ROOT = Path(__file__).resolve().parents[1]


class FakeChild:
    def __init__(self, output='[TAKUMI] Downloading\nSumi setup complete.\n', code=0):
        self.stdout = io.StringIO(output)
        self.code = code

    def wait(self):
        return self.code


class ProvisioningTests(unittest.TestCase):
    def test_missing_windows_dependency_does_not_fake_success(self):
        p = Provisioner(ROOT, platform='posix', executable='powershell.exe')
        with self.assertRaisesRegex(RuntimeError, 'Windows'):
            p.start()
        self.assertEqual(p.status()['state'], 'idle')

    def test_real_command_is_pinned_and_no_user_shell_is_injected(self):
        p = Provisioner(ROOT, platform='nt', executable='powershell.exe')
        with mock.patch('takumi.provisioning.subprocess.Popen', return_value=FakeChild()) as launch:
            state = p.start()
            self.assertTrue(state['running'] or p.status()['state'] == 'ready')
            for i in range(100):
                if p.status()['state'] == 'ready':
                    break
                time.sleep(.005)
            self.assertEqual(p.status()['state'], 'ready')
            args = launch.call_args.args[0]
            self.assertEqual(args[:4], ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass'])
            self.assertEqual(Path(args[5]).name, 'Ensure-Sumi-1.0.ps1')
            self.assertNotIn('bash', args)
            self.assertEqual(launch.call_args.kwargs['stdin'], __import__('subprocess').DEVNULL)
            self.assertFalse(launch.call_args.kwargs.get('shell', False))

    def test_failed_external_setup_reports_failure_and_retains_error(self):
        p = Provisioner(ROOT, platform='nt', executable='powershell.exe')
        with mock.patch('takumi.provisioning.subprocess.Popen', return_value=FakeChild('Download failed\n', 3)):
            p.start(lightweight=True)
            for i in range(100):
                if p.status()['state'] == 'failed':
                    break
                time.sleep(.005)
            st = p.status()
            self.assertEqual(st['state'], 'failed')
            self.assertEqual(st['exit_code'], 3)
            self.assertTrue(any('download failed' in x.lower() for x in st['messages']))

    def test_logs_redact_authentication_secrets(self):
        p = Provisioner(ROOT, platform='nt', executable='powershell.exe')
        with mock.patch('takumi.provisioning.subprocess.Popen', return_value=FakeChild('token=abcdefghi\nOK\n')):
            p.start()
            for i in range(100):
                if p.status()['state'] == 'ready':
                    break
                time.sleep(.005)
            self.assertNotIn('abcdefghi', repr(p.status()))

    def test_model_catalog_uses_rei_identity_with_legacy_compatibility(self):
        values = ['rei-1.0:latest', 'sumi-1.0-lite:latest', 'bonsai-2:9b']
        self.assertEqual(models.default_model(values), 'rei-1.0:latest')
        self.assertEqual(models.display_name('rei-1.0:latest'), 'Rei 1.1')
        self.assertEqual(models.display_name('sumi-1.0-lite:latest'), 'Rei 1.1 (legacy)')
        status = models.state(values)
        self.assertFalse(status['installed'])
        self.assertTrue(status['installed_rei'])
        self.assertTrue(status['installed_lite'])

    def test_installer_auto_provisions_and_does_not_bundle_engines(self):
        install = (ROOT / 'scripts' / 'Install-Takumi.ps1').read_text()
        ensure = (ROOT / 'scripts' / 'Ensure-Sumi-1.0.ps1').read_text()
        builder = (ROOT / 'scripts' / 'Build-Windows-EXE.ps1').read_text()
        self.assertIn('Ensure-Sumi-1.0.ps1', install)
        self.assertIn("if ($LASTEXITCODE -ne 0)", install)
        self.assertIn("'SETUP-SUMI.cmd'", builder)
        self.assertIn("winget.exe", ensure.lower())
        self.assertIn('Get-AuthenticodeSignature', ensure)
        self.assertIn("pull $base", ensure)
        self.assertIn("create 'sumi-1.0'", ensure)
        self.assertIn("create 'rei-1.0'", ensure)
        self.assertIn('Get-FileHash', ensure)
        self.assertIn('/api/chat', ensure)
        self.assertIn('The selected Takumi model registration was not visible', ensure)
        self.assertNotIn('iex', ensure.lower())
        from scripts.package_release import selected
        for candidate in selected('source'):
            self.assertFalse(candidate.name.lower().endswith(('.exe', '.dll', '.gguf', '.safetensors')))

    def test_prerequisites_are_automatic_and_include_webview2(self):
        installer = (ROOT / 'scripts' / 'Install-Takumi.ps1').read_text()
        self.assertIn('Microsoft.EdgeWebView2Runtime', installer)
        self.assertIn('WebView2-Installed', installer)
        self.assertIn('Ensure-WebView2', installer)
        self.assertIn('Get-ItemProperty', installer)
        self.assertIn('--silent', installer)
        self.assertIn('--disable-interactivity', installer)
        self.assertNotIn('Read-Host "$name is missing.', installer)
        self.assertIn("Request-Install 'Python 3.11+'", installer)
        self.assertIn("Request-Install '.NET 10 SDK'", installer)

    def test_repair_and_model_setup_scripts_are_reusable_and_visible(self):
        repair = (ROOT / 'REPAIR-Takumi.cmd').read_text()
        setup = (ROOT / 'SETUP-SUMI.cmd').read_text()
        self.assertIn('Install-Takumi.ps1', repair)
        self.assertIn('Ensure-Sumi-1.0.ps1', setup)
        self.assertIn('pause', setup.lower())
        self.assertIn('pause', repair.lower())


    def test_qwen_install_pulls_only_the_allowlisted_tag(self):
        from takumi.model_library import library_catalog
        rows = {row['id']: row for row in library_catalog(['qwen3:8b', 'sumi-1.0:latest'])}
        self.assertTrue(rows['qwen3:8b']['installed'])
        self.assertFalse(rows['qwen3:4b']['installed'])
        self.assertNotIn('Sumi', rows['qwen3:8b']['label'])
        p = Provisioner(ROOT, platform='nt', executable='powershell.exe')
        with mock.patch('takumi.provisioning.subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError, 'install list'):
                p.install_library('qwen3:8b; calc.exe', engine='ollama')
            launch.assert_not_called()
        with mock.patch('takumi.provisioning.subprocess.Popen', return_value=FakeChild('pulling manifest\n')) as launch:
            p.install_library('qwen3:8b', engine='ollama')
            for _ in range(100):
                if p.status()['state'] == 'ready':
                    break
                time.sleep(.005)
            self.assertEqual(p.status()['state'], 'ready')
            self.assertEqual(launch.call_args.args[0], ['ollama', 'pull', 'qwen3:8b'])
            self.assertFalse(launch.call_args.kwargs.get('shell', False))
            self.assertIn('does not change Sumi', p.status()['messages'][0])

    def test_cline_install_is_the_npm_package_and_does_not_run_without_npm(self):
        p = Provisioner(ROOT, platform='nt', executable='powershell.exe')
        with mock.patch('takumi.provisioning.shutil.which', return_value=None), \
             mock.patch('takumi.provisioning.subprocess.Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, 'npm'):
                p.install_cline()
            launch.assert_not_called()
        with mock.patch('takumi.provisioning.subprocess.Popen', return_value=FakeChild('added 1 package\n')) as launch:
            p.install_cline(npm='npm')
            for _ in range(100):
                if p.status()['state'] == 'ready':
                    break
                time.sleep(.005)
            self.assertEqual(launch.call_args.args[0], ['npm', 'install', '-g', 'cline'])
            self.assertFalse(launch.call_args.kwargs.get('shell', False))
            self.assertTrue(any('cline auth' in line for line in p.status()['messages']))


class SetupHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = server.Runtime(core.Storage(Path(self.temp.name)))
        self.runtime.provisioner = Provisioner(ROOT, platform='nt', executable='powershell.exe')
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.handler_factory(self.runtime))
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.runtime.companion.stop()
        self.runtime.bots.close(stop_bots=True)
        self.temp.cleanup()

    def req(self, route, body=None, token=True):
        headers={'Content-Type': 'application/json'}
        if token:
            headers['X-Takumi-Token'] = self.runtime.token
        req=urllib.request.Request(self.url + route, data=json.dumps(body).encode() if body is not None else None,
                                   headers=headers, method='POST' if body is not None else 'GET')
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode())

    def test_setup_requires_authorization_for_start(self):
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.req('/api/setup/start', {}, token=False)
        self.assertEqual(err.exception.code, 403)

    def test_setup_status_and_start_report_actual_outcome(self):
        code, info = self.req('/api/setup/status')
        self.assertEqual(code, 200)
        self.assertEqual(info['state'], 'idle')
        with mock.patch('takumi.provisioning.subprocess.Popen', return_value=FakeChild()):
            code, info = self.req('/api/setup/start', {'lightweight': False, 'skip_coding_agent': False})
        self.assertEqual(code, 202)
        for i in range(100):
            st=self.req('/api/setup/status')[1]
            if st['state'] != 'running':
                break
            time.sleep(.005)
        self.assertEqual(st['state'], 'ready')
        self.assertEqual(st['exit_code'], 0)

    def test_unknown_library_model_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.req('/api/models/install', {'tag': 'qwen3:72b'})
        self.assertEqual(err.exception.code, 400)
        with mock.patch('takumi.provisioning.subprocess.Popen', return_value=FakeChild('pulling\n')) as launch:
            code, info = self.req('/api/models/install', {'tag': 'qwen2.5-coder:7b'})
        self.assertEqual(code, 202)
        self.assertEqual(launch.call_args.args[0][-2:], ['pull', 'qwen2.5-coder:7b'])
        self.assertFalse(launch.call_args.kwargs.get('shell', False))


if __name__ == '__main__':
    unittest.main()
