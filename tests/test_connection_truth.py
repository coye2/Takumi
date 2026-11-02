import subprocess
import unittest
from unittest.mock import patch

from takumi.companion import connections_status
from takumi.plugins import status


class ConnectionTruthTests(unittest.TestCase):
    def test_figma_negative_states_are_not_connections(self):
        for text, code, expected in [('figma connected',0,True),('figma online',0,True),
                ('figma disconnected',0,False),('figma not connected',0,False),
                ('figma offline',0,False),('figma connected',1,False)]:
            with self.subTest(text=text, code=code), patch('takumi.companion.shutil.which',return_value=None), patch('takumi.core.open_code_path',return_value='fixture'), patch('takumi.companion._run',return_value=subprocess.CompletedProcess([],code,text,'')):
                self.assertEqual(connections_status()['figma']['connected'],expected)

    def test_discord_feature_presence_is_not_remote_authorization(self):
        with patch('takumi.plugins.shutil.which',return_value=None), patch('takumi.core.open_code_path',return_value=None):
            discord=next(p for p in status() if p['id']=='discord')
        self.assertTrue(discord['installed'])
        self.assertFalse(discord['connected'])
