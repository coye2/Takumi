"""A project that pins an exact SDK must get it from Takumi's tools folder."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from takumi import toolchains


class PinnedSdk(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'project'
        (self.root / 'src' / 'app').mkdir(parents=True)
        self.tools = Path(self.temp.name) / 'tools'
        home = self.tools / 'dotnet-10.0.402'
        (home / 'sdk' / '10.0.402').mkdir(parents=True)
        (home / ('dotnet.exe' if os.name == 'nt' else 'dotnet')).write_text('')
        patcher = mock.patch.dict(os.environ, {'TAKUMI_TOOLS_DIR': str(self.tools)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)

    def pin(self, where='src', **sdk):
        (self.root / where / 'global.json').write_text(json.dumps({'sdk': {'version': '10.0.402', 'rollForward': 'disable', **sdk}}))

    def env(self, cwd=None, installed=('10.0.403',)):
        with mock.patch.object(toolchains, 'installed_sdks', return_value=set(installed)):
            return toolchains.apply_project_toolchain({'PATH': 'base'}, self.root, cwd)

    def test_finds_a_pin_one_level_down_and_from_a_subfolder(self):
        self.pin()
        self.assertEqual(toolchains.pinned_dotnet_sdk(self.root), '10.0.402')
        self.assertEqual(toolchains.pinned_dotnet_sdk(self.root, self.root / 'src' / 'app'), '10.0.402')

    def test_uses_the_tools_copy_when_the_install_lacks_it(self):
        self.pin()
        env = self.env()
        self.assertTrue(env['PATH'].startswith(str(self.tools / 'dotnet-10.0.402')))
        self.assertEqual(env['DOTNET_MULTILEVEL_LOOKUP'], '0')
        self.assertEqual(env['DOTNET_ROOT'], str(self.tools / 'dotnet-10.0.402'))

    def test_leaves_the_environment_alone_otherwise(self):
        self.assertEqual(self.env(), {'PATH': 'base'})
        self.pin()
        self.assertEqual(self.env(installed=('10.0.402',)), {'PATH': 'base'})
        self.pin(rollForward='latestFeature')
        self.assertEqual(self.env(), {'PATH': 'base'})

    def test_a_feature_band_pin_needs_that_band(self):
        self.pin(version='10.0.100', rollForward='latestPatch')
        home = self.tools / 'dotnet-10.0.100'
        (home / 'sdk' / '10.0.100').mkdir(parents=True)
        (home / ('dotnet.exe' if os.name == 'nt' else 'dotnet')).write_text('')
        self.assertIn('dotnet-10.0.100', self.env()['PATH'])
        self.assertEqual(self.env(installed=('10.0.105',)), {'PATH': 'base'})

    def test_a_missing_tools_copy_changes_nothing(self):
        self.pin(version='9.9.9')
        self.assertEqual(self.env(), {'PATH': 'base'})


class WindowsProcessLookup(unittest.TestCase):
    def test_a_bare_program_is_resolved_with_the_project_path(self):
        """CreateProcess ignores the child's PATH when it looks up the program."""
        harness = (Path(__file__).resolve().parents[1] / 'takumi' / 'harness.py').read_text('utf-8')
        self.assertIn('shutil.which(first, path=apply_project_toolchain(', harness)
        platforms = (Path(__file__).resolve().parents[1] / 'takumi' / 'vibe' / 'platforms.py').read_text('utf-8')
        self.assertIn("shutil.which(name, path=env.get('PATH'))", platforms)


class WorkFolder(unittest.TestCase):
    def test_work_mode_tells_opencode_the_project_folder(self):
        core = (Path(__file__).resolve().parents[1] / 'takumi' / 'core.py').read_text('utf-8')
        self.assertIn('env["PWD"] = str(project)', core)


class FinishedWorkAfterCompaction(unittest.TestCase):
    def test_a_summary_does_not_erase_a_finished_status(self):
        from takumi.core import final_reply
        parts = ['It pins 10.0.402.\n\nTAKUMI_STATUS: COMPLETE', '## Objective\n- summary', 'Continue if you have next steps, or stop', 'The task is complete.']
        self.assertEqual(final_reply(parts), parts[0])

    def test_without_compaction_the_last_message_counts(self):
        from takumi.core import final_reply
        parts = ['TAKUMI_STATUS: COMPLETE', 'I found more to do.']
        self.assertEqual(final_reply(parts), parts[1])
        self.assertEqual(final_reply([]), '')


class PythonTasks(unittest.TestCase):
    def test_a_python_task_runs_real_python_not_the_store_stub(self):
        import sys
        from takumi.harness import Harness
        import takumi.harness as harness
        source = Path(harness.__file__).read_text('utf-8')
        self.assertIn("exe = sys.executable if first.lower() in ('python', 'python3', 'python.exe')", source)
        self.assertTrue(Path(sys.executable).is_file())


if __name__ == '__main__':
    unittest.main()
