"""Cross-platform hash and training fixture regression tests."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from training.pilot import specialist


class SpecialistSourcePortabilityTests(unittest.TestCase):
    def test_validator_hash_is_identical_for_lf_and_crlf_checkout(self):
        raw = Path(specialist.__file__).read_bytes()
        lf = raw.replace(b'\r\n', b'\n')
        crlf = lf.replace(b'\n', b'\r\n')
        expected = hashlib.sha256(lf).hexdigest()
        self.assertEqual(specialist.validator_source_sha256(), expected)
        with patch.object(Path, 'read_bytes', return_value=crlf):
            self.assertEqual(specialist.validator_source_sha256(), expected)

    def test_committed_training_dataset_uses_canonical_validator_hash(self):
        path = Path(specialist.__file__).resolve().parents[1] / 'pilot' / 'specialist-v1.json'
        dataset = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(dataset['validator_sha256'], specialist.validator_source_sha256())


if __name__ == '__main__':
    unittest.main()
