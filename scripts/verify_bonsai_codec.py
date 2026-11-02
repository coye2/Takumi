"""Check every installed Q2_0 byte and sampled rows against upstream decoding."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gguf", required=True)
    parser.add_argument("--gguf-python", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, args.gguf_python)
    import numpy as np
    from gguf import GGUFReader
    from gguf.quants import dequantize
    from training.bonsai.codec import transcode
    start = time.monotonic()
    result = {"training_performed": False, "checks": [], "all_encoded_bytes_checked": 0}
    reader = GGUFReader(args.gguf)
    for t in reader.tensors:
        if t.tensor_type.name != "Q2_0":
            continue
        rows, width = map(int, t.shape[::-1])
        raw = t.data.reshape(rows, -1)
        for lo in range(0, rows, 64):
            chunk = raw[lo:lo + 64]
            words, scales, biases = transcode(chunk.tobytes(), (len(chunk), width), "Q2_0")
            # Reverse the storage transformation: all scales and codes, not a sample.
            restored = np.empty((len(chunk) * width // 64, 18), dtype=np.uint8)
            restored[:, :2] = scales.reshape(-1, 1).view(np.uint8)
            restored[:, 2:] = words.view(np.uint8).reshape(-1, 16)
            if not np.array_equal(restored.reshape(chunk.shape), chunk):
                raise AssertionError(f"Encoded bytes changed: {t.name} row {lo}")
            if not np.array_equal(biases, -scales):
                raise AssertionError("Affine bias mismatch")
            result["all_encoded_bytes_checked"] += int(chunk.nbytes)
        # Independent upstream dequantizer vs affine expression for boundary rows.
        indices = sorted(set([0, rows - 1]))
        for row in indices:
            chunk = raw[row:row + 1]
            words, scales, biases = transcode(chunk.tobytes(), (1, width), "Q2_0")
            codes = ((words[..., None] >> (2 * np.arange(16, dtype=np.uint32))) & 3).reshape(1, -1, 64)
            actual = codes.astype(np.float32) * scales.astype(np.float32)[..., None] + biases.astype(np.float32)[..., None]
            expected = dequantize(chunk, t.tensor_type)
            if not np.array_equal(actual.reshape(expected.shape), expected):
                raise AssertionError(f"Decoded values differ: {t.name} row {row}")
        result["checks"].append({"tensor": t.name, "rows": rows, "width": width, "decoded_rows_checked": indices})
    if not result["checks"]:
        raise AssertionError("No Q2_0 tensors checked")
    result.update(passed=True, elapsed_seconds=time.monotonic() - start, tensor_count=len(result["checks"]))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}))


if __name__ == "__main__":
    main()
