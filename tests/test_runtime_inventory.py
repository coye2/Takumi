import unittest
from unittest.mock import patch
from takumi import core


class CapabilityIdentityTests(unittest.TestCase):
    def test_tool_log_redacts_credentials_without_rewriting_answers(self):
        event = core.readable_tool_event({'type': 'tool_use', 'part': {'tool': 'bash', 'state': {
            'input': {'command': 'run password=example-secret'},
            'output': 'Authorization: Bearer example-token\napi_key=another-secret'}}})
        self.assertNotIn('example-secret', event['detail'])
        self.assertNotIn('example-token', event['output'])
        self.assertNotIn('another-secret', event['output'])
        self.assertIn('[redacted]', event['output'])

    def test_changed_digest_invalidates_capabilities_and_booleans_roundtrip(self):
        core._model_cache.clear()
        identity = {'digest': 'first', 'values': [False, True]}
        def request(path, data=None, timeout=4):
            if path == '/api/tags':
                return {'models': [{'name': 'local', 'digest': identity['digest']}]}
            return {'thinking': {'values': identity['values'], 'default': False}}
        with patch('takumi.core.json_request', side_effect=request):
            self.assertEqual(core.reasoning_profile('local')['levels'], ['default', 'off', 'on'])
            self.assertIs(core.thinking_request_value('on', 'local'), True)
            identity.update(digest='replaced', values=[False])
            self.assertEqual(core.reasoning_profile('local')['levels'], ['default', 'off'])
            with self.assertRaises(ValueError):
                core.thinking_request_value('on', 'local')
            self.assertIs(core.thinking_request_value('off', 'local'), False)


if __name__ == '__main__':
    unittest.main()
