import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from training.bonsai.codec import transcode, unpack
from training.bonsai.reader import CompactGGUFReader


def string(value):
    raw = value.encode()
    return struct.pack("<Q", len(raw)) + raw


def fixture(offset=0):
    metadata = string("general.architecture") + struct.pack("<I", 8) + string("qwen35")
    header = b"GGUF" + struct.pack("<IQQ", 3, 1, 1) + metadata
    header += string("test.weight") + struct.pack("<IQQIQ", 2, 128, 1, 42, offset)
    header += b"\0" * (-len(header) % 32)
    raw = struct.pack("<e", 1.0) + bytes([228] * 16)
    raw += struct.pack("<e", 2.0) + bytes([228] * 16)
    return header + raw


class StorageTests(unittest.TestCase):
    def read(self, content):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "fixture.gguf"
        path.write_bytes(content)
        reader = CompactGGUFReader(path)
        # Drop tensor memoryviews before closing the mapping on Windows.
        self.addCleanup(lambda: (reader.tensors.clear(), reader.data.close()))
        return reader

    def test_distinct_group64_scales_and_all_four_codes(self):
        reader = self.read(fixture())
        self.assertEqual(reader.fields["general.architecture"].contents(), "qwen35")
        tensor = reader.tensors[0]
        self.assertEqual(tensor.n_bytes, 36)
        actual = unpack(*transcode(tensor.data.tobytes(), (1, 128), "Q2_0"))
        expected = np.tile(np.array([-1, 0, 1, 2], dtype=np.float32), 32).reshape(1, 128)
        expected[:, 64:] *= 2
        np.testing.assert_array_equal(actual, expected)

    def test_mapped_input_matches_bytes_and_returns_owned_arrays(self):
        reader = self.read(fixture())
        tensor = reader.tensors[0]
        mapped = transcode(tensor.data, (1, 128), "Q2_0")
        copied = transcode(tensor.data.tobytes(), (1, 128), "Q2_0")
        for actual, expected in zip(mapped, copied):
            np.testing.assert_array_equal(actual, expected)
            self.assertFalse(np.shares_memory(actual, tensor.data))

    def test_noncontiguous_input_rejected(self):
        with self.assertRaises(TypeError):
            transcode(np.zeros(72, dtype=np.uint8)[::2], (1, 128), "Q2_0")

    def test_truncated_tensor_rejected(self):
        with self.assertRaisesRegex(ValueError, "out-of-bounds"):
            self.read(fixture()[:-1])

    def test_out_of_bounds_offset_rejected(self):
        with self.assertRaisesRegex(ValueError, "out-of-bounds"):
            self.read(fixture(offset=4096))

    def test_bad_magic_rejected(self):
        with self.assertRaisesRegex(ValueError, "magic"):
            self.read(b"NOPE" + fixture()[4:])

    def test_truncated_header_rejected(self):
        with self.assertRaisesRegex(ValueError, "Truncated"):
            self.read(b"GGUF")

    def test_nonfinite_scale_rejected(self):
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            transcode((struct.pack("<e", float("nan")) + bytes(16)) * 2, (1, 128), "Q2_0")

    def test_bad_packed_length_rejected(self):
        with self.assertRaisesRegex(ValueError, "byte length"):
            transcode(bytes(35), (1, 128), "Q2_0")


if __name__ == "__main__":
    unittest.main()
