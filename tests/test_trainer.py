import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from takumi.gpu_scheduler import GpuScheduler
from takumi.trainer import Trainer, BASE_SHA


class TrainerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.scheduler = GpuScheduler()
        self.trainer = Trainer(self.base, self.base, self.scheduler)

    def test_missing_configuration_and_bad_action_fail_before_gpu_reservation(self):
        for mode in ('smoke', 'unknown'):
            with self.assertRaises(ValueError):
                self.trainer.start(mode)
        self.assertFalse(self.scheduler.is_reserved())

    def test_config_rejects_another_model(self):
        model = self.base / 'other.gguf'; model.touch()
        with self.assertRaisesRegex(ValueError, 'exact Sumi'):
            self.trainer.configure(dict(python=str(model), gguf=str(model), gguf_python=str(self.base), tokenizer=str(self.base)))

    def test_hash_mismatch_fails_before_launch_and_releases_gpu(self):
        model = self.base / ('sha256-' + BASE_SHA); model.write_bytes(b'not the exact weights')
        self.trainer.configure(dict(python=str(model), gguf=str(model), gguf_python=str(self.base), tokenizer=str(self.base)))
        job_id = 'a'*32
        self.trainer.current = {'id': job_id, 'status': 'checking'}
        self.scheduler.acquire('training-' + job_id)
        with patch('takumi.trainer.subprocess.Popen') as launch:
            self.trainer._run(job_id, self.trainer.configuration(), 'forward', 2, None)
        launch.assert_not_called()
        self.assertEqual(self.trainer.current['status'], 'failed')
        self.assertFalse(self.scheduler.is_reserved())

    def test_recovery_cancels_owned_wrapper_and_prevents_overlapping_run(self):
        self.trainer.current = {'id': 'a'*32, 'status': 'running'}
        self.trainer._save()
        recovered = Trainer(self.base, self.base, self.scheduler)
        self.assertEqual(recovered.status()['job']['status'], 'recovering')
        self.assertTrue((recovered.directory / ('a'*32 + '.cancel')).is_file())
        with self.assertRaisesRegex(ValueError, 'already active'):
            recovered.start('smoke')

    def test_stop_requests_cancellation_without_signalling_other_processes(self):
        self.trainer.current = {'id': 'b'*32, 'status': 'running'}
        self.assertEqual(self.trainer.stop()['job']['status'], 'stopping')
        self.assertTrue((self.trainer.directory / ('b'*32 + '.cancel')).is_file())

    def test_completed_report_does_not_invent_optimizer_steps(self):
        self.trainer.current = {'id': 'c'*32, 'status': 'finished'}
        out = self.trainer.directory / ('c'*32); out.mkdir()
        (out / 'result.json').write_text(json.dumps({'status': 'forward_passed', 'optimizer_steps': 0}))
        self.assertEqual(self.trainer.status()['job']['result']['optimizer_steps'], 0)

if __name__ == '__main__':
    unittest.main()
