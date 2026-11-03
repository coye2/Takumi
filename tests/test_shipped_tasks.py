"""Self-hosted project task catalog never mutates live Takumi or publishes."""
import json
from pathlib import Path
import unittest
from takumi.harness import task_definitions

ROOT = Path(__file__).resolve().parents[1]

class ShippedTaskCatalogTests(unittest.TestCase):
    def test_registered_project_tasks_are_valid(self):
        tasks = task_definitions(ROOT)
        self.assertEqual({t['id'] for t in tasks},
                         {'git-status', 'git-log', 'python-tests', 'js-syntax', 'windows-build'})

    def test_build_does_not_install_or_publish(self):
        tasks = {t['id']: t for t in task_definitions(ROOT)}
        build = tasks['windows-build']
        self.assertEqual(build['argv'][0].lower(), 'powershell.exe')
        self.assertEqual(build['argv'][build['argv'].index('-File') + 1],
                         'scripts/Build-Windows-EXE.ps1')
        for task in tasks.values():
            full = ' '.join(task['argv']).lower()
            self.assertNotIn('install-takumi.ps1', full)
            self.assertNotIn('apply-takumi-update.ps1', full)
            self.assertNotIn('publish-to-github.ps1', full)
            self.assertNotIn('git push', full)
            self.assertNotIn('git clean', full)
            self.assertNotIn('git reset', full)
            self.assertNotIn('gh release', full)

if __name__ == '__main__':
    unittest.main()
