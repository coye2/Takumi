import hashlib
from pathlib import Path
import tempfile
import unittest

from takumi.evidence import audit_answer, response_contract, structure_report


class EvidenceTests(unittest.TestCase):
    def test_twenty_citation_cases_and_four_unsupported_locations(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'fence.cpp'
            source.write_text('int fence(unsigned done, unsigned target) {\n    return done >= target;\n}\n')
            snapshot = {'hashes': {'fence.cpp': hashlib.sha256(source.read_bytes()).hexdigest()}, 'commit': 'test-commit'}
            cases = [f'Claim {n} [source:fence.cpp:L2]' for n in range(16)] + [
                'Unknown [source:missing.cpp:L1]', 'Unknown [source:fence.cpp:L99]',
                'Unknown [source:../outside.cpp:L1]', 'No evidence provided.']
            report = audit_answer('\n'.join(cases), root, snapshot)
            self.assertEqual([c['category'] for c in report['claims']], ['source_located']*16 + ['unverified']*4)
            self.assertTrue(all(not c['behavior_verified'] for c in report['claims']))
            explicit = audit_answer('[unverified] Possible fence cause [source:fence.cpp:L2]', root, snapshot)
            self.assertEqual(explicit['claims'][0]['category'], 'unverified')
            self.assertEqual(explicit['claims'][0]['citations'][0]['status'], 'source_located')
            source.write_text('int changed;\n')
            stale = audit_answer(cases[0], root, snapshot)
            self.assertEqual(stale['claims'][0]['category'], 'unverified')

    def test_structure_contracts_and_inference_not_promoted(self):
        self.assertIn('(review)', response_contract('Review the GPU code'))
        self.assertIn('shared representation', response_contract('Explain CPU and GPU descriptors'))
        report = audit_answer('[inferred] A likely data hazard.\n[analogy] Similar to a pipeline stall.', None, None)
        self.assertEqual([c['category'] for c in report['claims']], ['inferred', 'analogy'])
        self.assertEqual(structure_report('# A\n### B')['issues'], ['Skipped heading level'])
        paragraph = 'A repeated caveat should be detected without deleting important technical information from the response.'
        self.assertIn('Repeated paragraph', structure_report(paragraph+'\n\n'+paragraph)['issues'])
