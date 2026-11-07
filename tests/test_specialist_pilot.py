import copy
import json
from pathlib import Path
import tempfile
import unittest
from training.pilot.specialist import TASKS, verify, validate_dataset, write_dataset


class SpecialistPilotTests(unittest.TestCase):
    def test_independent_oracles_and_train_only_export(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'dataset.json'
            audit = write_dataset(path)
            self.assertEqual(len(audit), 22)
            self.assertEqual(sum(r['checked'] for r in audit), 8*256+14*512)
            data = json.loads(path.read_text())
            self.assertEqual(len(data['examples']),16)
            self.assertEqual(data, json.loads(Path('training/pilot/specialist-v1.json').read_text()))
            self.assertFalse({r['id'] for r in data['examples']} & {t[0] for t in TASKS if t[1]=='test'})
            altered = copy.deepcopy(data)
            altered['examples'][0]['messages'][1]['content']='def f(a,b):\n return 0'
            with self.assertRaises(ValueError): validate_dataset(altered)
            altered = copy.deepcopy(data)
            altered['examples'][0]['id']='ring-count'
            with self.assertRaises(ValueError): validate_dataset(altered)
            altered = copy.deepcopy(data)
            altered['validator_sha256']='0'*64
            with self.assertRaises(ValueError): validate_dataset(altered)

    def test_wrong_kernel_is_rejected_in_every_family(self):
        for task in TASKS:
            with self.subTest(task=task[0]):
                self.assertFalse(verify('def f('+task[4]+'):\n return 999999999999',task)['passed'])

    def test_boundary_bug_mutants_fail(self):
        mutations = {'pc-relative':'pc+4+4*d', 'align-up':'x&-a',
            'page-address':'p+x', 'relocation':'s+a-p',
            'mips-jump':'(pc&4026531840)|(t<<2)', 'packed-s24':'a+(b<<8)+(c<<16)',
            'remaining-bytes':'n-o', 'stack-slot':'n//16'}
        for task in TASKS:
            if task[0] in mutations:
                with self.subTest(task=task[0]):
                    self.assertFalse(verify('def f('+task[4]+'):\n return '+mutations[task[0]],task)['passed'])
