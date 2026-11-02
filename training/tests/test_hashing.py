"""Run in the pinned WSL CUDA environment; no model weights required."""
import hashlib
import unittest

try:
    import mlx.core as mx
    import numpy as np
    from training.bonsai.smoke import tensor_hashes
except ImportError:
    mx = None


@unittest.skipIf(mx is None, 'Requires the optional MLX environment')
class TensorHashTests(unittest.TestCase):
    def test_chunked_hash_matches_storage_bytes(self):
        mx.set_default_device(mx.gpu)
        for dtype in [np.uint32, np.float16]:
            with self.subTest(dtype=dtype):
                a = (np.arange(600000, dtype=np.int32) % 1024).astype(dtype).reshape(1000, 600)
                self.assertEqual(tensor_hashes([('sample', mx.array(a))])['sample'],
                                 hashlib.sha256(a.tobytes()).hexdigest())

    def test_slice_hash_does_not_retain_whole_host_mirror(self):
        import psutil
        mx.set_default_device(mx.gpu)
        source = mx.arange(64 * 1024**2, dtype=mx.uint32)
        mx.eval(source)
        process = psutil.Process()
        before = process.memory_info().rss
        tensor_hashes([('large', source)])
        mx.clear_cache()
        # A view-only implementation grew RSS by 258 MiB for this 256 MiB
        # tensor. Independent chunks measured 15 MiB; allow allocator variance.
        self.assertLess(process.memory_info().rss - before, 128 * 1024**2)
