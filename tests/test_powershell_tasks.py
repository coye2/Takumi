"""Validate bounded local PowerShell tasks without executing arbitrary commands."""
import json
from pathlib import Path
import tempfile
import unittest

from takumi.harness import task_definitions


class PowerShellTasksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts' / 'build.ps1').write_text("Write-Output 'build'\n")

    def declare(self, argv):
        (self.root / 'takumi.tasks.json').write_text(
            json.dumps({'version': 1, 'tasks': [
                {'id': 'windows-build', 'name': 'Compile Takumi', 'argv': argv, 'cwd': '.'}
            ]}), encoding='utf-8')
        return task_definitions(self.root)

    def test_project_powershell_file_is_recognized(self):
        tasks = self.declare(['powershell.exe', '-NoProfile', '-NonInteractive',
                              '-File', 'scripts/build.ps1'])
        self.assertEqual(tasks[0]['id'], 'windows-build')

    def test_explicit_execution_policy_and_script_arguments(self):
        tasks = self.declare(['pwsh', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                              '-File', 'scripts/build.ps1', '-Configuration', 'Release'])
        self.assertEqual(tasks[0]['argv'][-1], 'Release')

    def test_arbitrary_inline_powershell_is_rejected(self):
        for args in (
            ['powershell.exe', '-Command', 'Write-Output injected'],
            ['powershell.exe', '-EncodedCommand', 'SQBFAFgA'],
            ['pwsh', '-NoProfile', '-Command', 'pwd'],
            ['pwsh', '-NoProfile', '-File'],
            ['pwsh', '-NoProfile', '-File', 'scripts/build.ps1', '-EncodedCommand', 'bad'],
        ):
            with self.subTest(argv=args), self.assertRaisesRegex(ValueError, 'PowerShell'):
                self.declare(args)

    def test_file_must_exist_inside_project(self):
        for relative in ('missing.ps1', '../outside.ps1', '/tmp/outside.ps1', 'scripts/build.py'):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                self.declare(['pwsh', '-NoProfile', '-File', relative])


if __name__ == '__main__':
    unittest.main()
