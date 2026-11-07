"""PC diagnostics must use fixed read-only commands and an authenticated API."""
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest import mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from takumi import core, host_diagnostics, server


class HostDiagnosticsTests(unittest.TestCase):
    def test_real_host_probe_never_claims_remote_execution(self):
        report = host_diagnostics.host_readiness()
        self.assertEqual(report['executed_on'], 'Takumi backend host, not the phone')
        self.assertFalse(report['source_or_installed_build_modified'])
        self.assertFalse(report['github_release_published'])
        self.assertIn('github_auth', report['checks'])
        self.assertIn('dotnet', report['checks'])

    def test_windows_checks_use_only_fixed_commands(self):
        commands = []
        def fake_run(argv, **kwargs):
            commands.append(argv)
            return {'status': 'passed', 'detail': 'ok'}
        with mock.patch.object(host_diagnostics, 'os', types.SimpleNamespace(name='nt')), \
             mock.patch('takumi.host_diagnostics.shutil.which', side_effect=lambda name: 'C:/tools/' + name), \
             mock.patch('takumi.host_diagnostics._probe', side_effect=fake_run):
            report = host_diagnostics.host_readiness()
        self.assertEqual(report['host'], 'Windows')
        self.assertTrue(any('powershell.exe' in row[0] for row in commands))
        self.assertTrue(any(row[1:3] == ['auth', 'status'] for row in commands))
        self.assertTrue(any(row[1:3] == ['api', 'user'] for row in commands))
        self.assertFalse(any('push' in row or 'release' in row for row in commands))
        self.assertFalse(any('github_token' in str(row).lower() for row in commands))

    def test_missing_github_cli_is_not_misreported_as_connected(self):
        with mock.patch('takumi.host_diagnostics.shutil.which', return_value=None):
            report = host_diagnostics.host_readiness()
        self.assertEqual(report['checks']['github_auth']['status'], 'unavailable')
        self.assertEqual(report['checks']['github_login']['status'], 'unavailable')
        self.assertEqual(report['checks']['dotnet']['status'], 'unavailable')


class HostReadinessHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.rt = server.Runtime(core.Storage(Path(self.temp.name)))
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.handler_factory(self.rt))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.rt.companion.stop()
        self.rt.kage.stop()
        self.rt.bots.close(stop_bots=True)
        self.temp.cleanup()

    def fetch(self, token):
        req = urllib.request.Request(self.url + '/api/host/readiness',
                                     headers={'X-Takumi-Token': token})
        with urllib.request.urlopen(req, timeout=12) as response:
            return json.loads(response.read())

    def test_token_required_and_fixed_result_is_returned(self):
        with mock.patch('takumi.server.host_readiness', return_value={
            'scope': 'fixed_read_only_host_checks', 'checks': {'powershell': {'status': 'passed'}}}) as run:
            with self.assertRaises(urllib.error.HTTPError) as err:
                self.fetch('incorrect-token')
            self.assertEqual(err.exception.code, 403)
            self.assertEqual(self.fetch(self.rt.token)['checks']['powershell']['status'], 'passed')
            run.assert_called_once_with(None)


if __name__ == '__main__':
    unittest.main()
