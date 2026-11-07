"""Nonzero OpenCode exits must explain an observed category without guessing results."""
import unittest
from takumi.core import classify_opencode_failure


class OpenCodeFailureDiagnosticsTests(unittest.TestCase):
    def test_common_model_provider_failure_is_distinguished_from_windows_shell(self):
        cases = [
            (['Error: connect ECONNREFUSED 127.0.0.1:11434'], 'local model service'),
            (['Error: model ollama/sumi-1.0 not found'], 'selected Sumi/Rei model'),
            (['error: unknown option --foo'], 'rejected an argument'),
            (['Invalid configuration: provider'], 'generated configuration'),
            (['EACCES reading project file'], 'permission error'),
            (['Session abc not found'], 'coding session'),
        ]
        for stderr, evidence in cases:
            with self.subTest(stderr=stderr):
                self.assertIn(evidence, classify_opencode_failure(stderr))

    def test_unknown_exit_never_claims_power_shell_results_or_release(self):
        msg = classify_opencode_failure(['ERROR: unsupported provider exception'])
        self.assertIn('without verified completion', msg)
        self.assertIn('no requested PowerShell result is confirmed', msg)
        self.assertNotIn('release created', msg)

    def test_credentials_never_echo_back_to_mobile(self):
        token = 'github_pat_SUPER_PRIVATE_TOKEN_VALUE'
        for line in (f'GitHub token: {token}', f'Error: {token}'):
            msg = classify_opencode_failure([line])
            self.assertNotIn(token, msg)
            self.assertNotIn('SUPER_PRIVATE', msg)

    def test_unrelated_diagnostic_is_not_falsely_called_a_shell_failure(self):
        msg = classify_opencode_failure(['Application stopped with exit 1'])
        self.assertIn('coding engine', msg.lower())


if __name__ == '__main__':
    unittest.main()
