import copy
import json
from pathlib import Path
import unittest

from takumi.gpu_scheduler import GpuScheduler
from takumi.trainer import Trainer
from takumi.vibe import edits
from training.bonsai.guard import continue_steps, gpu_streak, prior_report, request_yield, yield_requested
from training.vibe.dataset import KEPT_LOGIT_POSITIONS, REPAIRS, VERSION, audit_splits, materialize, prompt_for, prove_repair, render_repair, score_project, validate_dataset, validator_sha256
from training.vibe.holdout3 import HOLDOUT3
from training.vibe.holdout4 import HOLDOUT4
from training.vibe.tasks import TASKS
from training.vibe.evaluate import summarize

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / 'training/vibe/vibe-coding-v1.json'


class Suite(unittest.TestCase):
    def test_families_do_not_cross_splits(self):
        audit_splits()

    def test_every_platform_is_held_out(self):
        self.assertEqual({t.platform for t in TASKS if t.split == 'test'}, {'python', 'web', 'pwa', 'dotnet', 'cpp', 'android', 'ios'})

    def test_hidden_tests_never_reach_the_prompt(self):
        repairs = {case.task_id: json.dumps(render_repair(case)) for case in REPAIRS}
        for task in TASKS:
            shown = [json.dumps(prompt_for(task))]
            if task.id in repairs:
                shown.append(repairs[task.id])
            for name, body in task.hidden.items():
                for blob in shown:
                    self.assertNotIn(name, blob, task.id)
                    self.assertNotIn(body.strip(), blob, task.id)

    def test_references_parse_in_the_served_edit_format(self):
        for task in TASKS:
            self.assertTrue(edits.parse(task.solution), task.id)


class NewHeldOut(unittest.TestCase):
    def test_no_family_is_shared_across_any_split(self):
        seen = {}
        for task in TASKS:
            self.assertEqual(seen.setdefault(task.family, task.split), task.split, task.family)

    def test_holdout2_covers_every_platform_and_never_trains(self):
        chosen = [t for t in TASKS if t.split == 'holdout2']
        self.assertEqual({t.platform for t in chosen}, {'python', 'web', 'pwa', 'dotnet', 'cpp', 'android', 'ios'})
        examples = json.loads(DATASET.read_text('utf-8'))['examples']
        trained = {e['id'].split('#')[0] for e in examples}
        self.assertFalse(trained & {t.id for t in chosen})

    def test_scoring_a_saved_adapter_checks_its_hash_and_trains_nothing(self):
        smoke = (ROOT / 'training/bonsai/smoke.py').read_text('utf-8')
        block = smoke[smoke.index("if config.get('score_adapter'):"):smoke.index('    if not reload_from:' + chr(10) + '        layer_type')]
        self.assertIn("config['score_adapter_sha256']", block)
        self.assertIn('steps = 0', block)
        self.assertIn('if vibe and steps:', smoke)

    def test_training_config_cannot_leak_into_older_suites(self):
        smoke = (ROOT / 'training/bonsai/smoke.py').read_text('utf-8')
        self.assertIn('A training config applies only to vibe-coding-v1', smoke)


class Features(unittest.TestCase):
    def test_each_feature_spans_a_large_project_and_is_winnable(self):
        from training.vibe.features import FEATURES, check, project
        self.assertGreaterEqual(len(project()), 50)
        self.assertGreaterEqual(sum(1 for f in FEATURES.values() if f['split'] == 'unseen'), 1)
        for row in check():
            with self.subTest(feature=row['id']):
                self.assertTrue(row['visible_before'])
                self.assertTrue(row['visible_after'])
                self.assertEqual(row['hidden_before'], 0)
                self.assertEqual(row['hidden_after'], row['hidden_total'])
                self.assertGreaterEqual(row['solution_files'], 3)


class Holdout4(unittest.TestCase):
    def test_stays_out_of_training_and_shares_no_family(self):
        used = {t.family for t in [*TASKS, *HOLDOUT3]}
        self.assertFalse(used & {t.family for t in HOLDOUT4})
        self.assertFalse({t.id for t in [*TASKS, *HOLDOUT3]} & {t.id for t in HOLDOUT4})
        self.assertEqual({t.split for t in HOLDOUT4}, {'holdout4'})
        self.assertEqual({t.platform for t in HOLDOUT4}, {'python', 'web', 'pwa', 'dotnet', 'cpp', 'android', 'ios'})
        examples = json.loads(DATASET.read_text('utf-8'))['examples']
        self.assertFalse({e['id'].split('#')[0] for e in examples} & {t.id for t in HOLDOUT4})


class Holdout3(unittest.TestCase):
    def test_stays_out_of_training_and_shares_no_family(self):
        known = {t.family for t in TASKS}
        self.assertFalse(known & {t.family for t in HOLDOUT3})
        self.assertFalse({t.id for t in TASKS} & {t.id for t in HOLDOUT3})
        self.assertEqual({t.split for t in HOLDOUT3}, {'holdout3'})
        self.assertEqual({t.platform for t in HOLDOUT3}, {'python', 'web', 'pwa', 'dotnet', 'cpp', 'android', 'ios'})
        examples = json.loads(DATASET.read_text('utf-8'))['examples']
        self.assertFalse({e['id'].split('#')[0] for e in examples} & {t.id for t in HOLDOUT3})

    def test_score_ignores_what_the_run_did_to_tests(self):
        import tempfile
        task = next(t for t in HOLDOUT3 if t.id == 'py-roman')
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
            folder = Path(raw) / 'p'
            folder.mkdir()
            materialize(task, folder)
            edits.apply(folder, edits.parse(task.solution), edits.Journal(folder))
            (folder / 'test_visible.py').write_text('import nothing_here\n', encoding='utf-8')
            (folder / 'test_mine.py').write_text('raise SystemExit(1)\n', encoding='utf-8')
            self.assertTrue(score_project(task, folder, drop=('test_mine.py',))['passed'])
            self.assertFalse(score_project(task, folder)['passed'])


class Dataset(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(DATASET.read_text('utf-8'))

    def test_committed_dataset_matches_tasks_and_prompt(self):
        validate_dataset(self.data)
        self.assertEqual(self.data['validator_sha256'], validator_sha256())

    def test_only_train_split_reaches_training(self):
        split = {t.id: t.split for t in TASKS}
        self.assertTrue(self.data['examples'])
        self.assertEqual({split[e['id'].split('#')[0]] for e in self.data['examples']}, {'train'})

    def test_every_example_was_verified_by_a_real_run(self):
        verified = {a['id'] for a in self.data['audit'] if a['status'] == 'verified'}
        self.assertLessEqual({e['id'].split('#')[0] for e in self.data['examples']}, verified)

    def test_tampering_fails_closed(self):
        changed = copy.deepcopy(self.data)
        changed['examples'][0]['messages'][-1]['content'] += '\nextra'
        with self.assertRaisesRegex(ValueError, 'differs'):
            validate_dataset(changed)
        changed = copy.deepcopy(self.data)
        changed['validator_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'rebuild'):
            validate_dataset(changed)
        changed = copy.deepcopy(self.data)
        changed['audit'] = [a for a in changed['audit'] if a['id'] != changed['examples'][0]['id']]
        with self.assertRaisesRegex(ValueError, 'verified'):
            validate_dataset(changed)

    def test_completions_follow_the_trainer_row_shape(self):
        repairs = {case.task_id: case for case in REPAIRS}
        for example in self.data['examples']:
            self.assertEqual(example['license'], 'MIT')
            if example['id'].endswith('#repair'):
                case = repairs[example['id'].split('#')[0]]
                self.assertEqual([m['role'] for m in example['messages']], ['system', 'user', 'assistant', 'user', 'assistant'])
                self.assertEqual(example['messages'][2]['content'], case.wrong)
                self.assertEqual(example['messages'][-1]['content'], case.fix)
                self.assertEqual(example['messages'], render_repair(case))
                self.assertIn('The checks failed', example['messages'][3]['content'])
            else:
                task = next(t for t in TASKS if t.id == example['id'])
                self.assertEqual([m['role'] for m in example['messages']], ['system', 'user', 'assistant'])
                self.assertEqual(example['messages'], prompt_for(task) + [example['messages'][-1]])

    def test_repair_rows_fail_the_visible_check_and_then_pass_hidden_tests(self):
        self.assertEqual([case.task_id for case in REPAIRS], ['js-has', 'py-channel', 'ios-app-group'])
        for case in REPAIRS:
            prove_repair(case)


class Trainer_(unittest.TestCase):
    def test_vibe_is_a_known_bounded_mode(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            trainer = Trainer(raw, ROOT, GpuScheduler())
            with self.assertRaisesRegex(ValueError, 'Configure'):
                trainer.start('vibe', 128)
            with self.assertRaisesRegex(ValueError, '1–128'):
                trainer.start('vibe', 500)
            with self.assertRaisesRegex(ValueError, 'Unknown'):
                trainer.start('other', 2)
            with self.assertRaisesRegex(ValueError, 'Configure'):
                trainer.start('continue', 32)

    def test_existing_suites_are_not_redirected(self):
        source = (ROOT / 'training/pilot/evaluate.py').read_text('utf-8')
        self.assertIn("suite == 'verified-specialist-pilot-v1'", source)
        smoke = (ROOT / 'training/bonsai/smoke.py').read_text('utf-8')
        self.assertIn('max_length=640 if vibe else 192 if specialist else 96', smoke)
        self.assertEqual(VERSION, 'vibe-coding-v1')


class Guard(unittest.TestCase):
    def test_one_gpu_dip_does_not_throw_the_run_away(self):
        streak, trip = gpu_streak(500, 0)
        self.assertEqual((streak, trip), (1, False))
        streak, trip = gpu_streak(2000, streak)
        self.assertEqual((streak, trip), (0, False))
        streak = 0
        for _ in range(2):
            streak, trip = gpu_streak(100, streak)
            self.assertFalse(trip)
        streak, trip = gpu_streak(100, streak)
        self.assertTrue(trip)

    def test_continue_accepts_the_same_suite_after_repair_rows_were_added(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            (folder / 'smoke-progress.json').write_text(json.dumps({
                'dataset_version': 'vibe-coding-v1', 'dataset_sha256': 'a' * 64, 'optimizer_steps': 19,
                'adapter_sha256': 'b' * 64}), encoding='utf-8')
            self.assertEqual(continue_steps(prior_report(folder), 'vibe-coding-v1'), 19)
            with self.assertRaisesRegex(RuntimeError, 'different training set'):
                continue_steps(prior_report(folder), 'verified-coding-pilot-v1')
            request_yield(folder)
            self.assertTrue(yield_requested(folder))


class HostMemory(unittest.TestCase):
    VOCABULARY = 248320

    def test_kept_logits_fit_well_inside_the_host_guard(self):
        rows = len(json.loads(DATASET.read_text('utf-8'))['examples'])
        one_copy = rows * KEPT_LOGIT_POSITIONS * self.VOCABULARY * 4
        self.assertLess(one_copy, 1024**3)

    def test_frozen_layers_are_cached_outside_the_gradient_for_this_suite_only(self):
        smoke = (ROOT / 'training/bonsai/smoke.py').read_text('utf-8')
        start = smoke.index('# Only the target layers train')
        block = smoke[start:smoke.index('        model.train()')]
        self.assertIn('if vibe and steps:', smoke[:start].rsplit(chr(10), 2)[-2])
        self.assertIn("model.model.layers[:min(report['target_layers'])]", block)
        self.assertNotIn('stop_gradient', block)
        self.assertIn('Cached frozen layers changed the base loss', smoke)
        self.assertIn('layer_type.__call__ = original_layer_call', smoke)

    def test_only_this_suite_slices_so_older_runs_stay_reproducible(self):
        smoke = (ROOT / 'training/bonsai/smoke.py').read_text('utf-8')
        self.assertIn('logits[0, -KEPT_LOGIT_POSITIONS:] if vibe else logits[0]', smoke)


class Scoring(unittest.TestCase):
    def test_excluded_tasks_are_not_counted_as_pass_or_fail(self):
        rows = [{'id': 'a', 'platform': 'web', 'state': 'verified', 'hidden_passed': True, 'one_shot_passed': False, 'seconds': 3},
                {'id': 'b', 'platform': 'ios', 'state': 'excluded', 'hidden_passed': None, 'one_shot_passed': None, 'seconds': 0},
                {'id': 'c', 'platform': 'web', 'state': 'verified', 'hidden_passed': False, 'one_shot_passed': False, 'seconds': 5}]
        summary = summarize(rows)
        self.assertEqual((summary['scored'], summary['hidden_passed'], summary['excluded']), (2, 1, ['b']))
        self.assertEqual(summary['false_verified'], ['c'])
