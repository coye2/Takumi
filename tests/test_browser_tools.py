import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from takumi import browser_tools
from takumi.core import Agent


class BrowserToolsTests(unittest.TestCase):
    def test_missing_or_changed_dependency_is_not_advertised(self):
        with tempfile.TemporaryDirectory() as td, patch('takumi.browser_tools.Path.home', return_value=Path(td)):
            self.assertIsNone(browser_tools.command())
            root = Path(td) / '.takumi/tools/browser/node_modules/@playwright/mcp'
            root.mkdir(parents=True)
            (root / 'package.json').write_text(json.dumps({'version':'unexpected'}))
            (root / 'cli.js').touch()
            self.assertIsNone(browser_tools.command())
            with self.assertRaises(RuntimeError):
                browser_tools.config()

    def test_browser_and_figma_coexist_without_personal_profile(self):
        agent = Agent.__new__(Agent)
        agent.cli_version = '1.19.5'
        self.assertNotIn('mcp', agent._config())
        with tempfile.TemporaryDirectory() as td, patch('takumi.browser_tools.Path.home', return_value=Path(td)), patch('takumi.browser_tools.shutil.which', return_value='node'):
            root = Path(td) / '.takumi/tools/browser/node_modules/@playwright/mcp'
            root.mkdir(parents=True)
            (root / 'package.json').write_text(json.dumps({'version':browser_tools.VERSION}))
            (root / 'cli.js').touch()
            cfg = agent._config(browser_enabled=True, figma_enabled=True)
            self.assertIn('figma', cfg['mcp'])
            argv = cfg['mcp']['browser']['command']
            self.assertIn('--isolated', argv)
            self.assertNotIn('--extension', argv)
            self.assertNotIn('--cdp-endpoint', argv)
            self.assertEqual(cfg['permission']['external_directory'], 'deny')
            agent.cli_version = '2.0.0'
            with self.assertRaises(RuntimeError):
                agent._config(browser_enabled=True)
