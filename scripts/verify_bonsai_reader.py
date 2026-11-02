"""Differentially check the bounded reader against pinned llama.cpp GGUFReader."""
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
    from training.bonsai.reader import CompactGGUFReader
    started = time.monotonic()
    compact = CompactGGUFReader(args.gguf)
    compact_seconds = time.monotonic() - started
    reference = GGUFReader(args.gguf)
    assert compact.data_offset == reference.data_offset
    for key, field in reference.fields.items():
        if key.startswith(("GGUF.", "tokenizer.")):
            continue
        assert compact.fields[key].contents() == field.contents(), key
    assert len(compact.tensors) == len(reference.tensors)
    for actual, expected in zip(compact.tensors, reference.tensors):
        assert actual.name == expected.name
        for key in ("tensor_type", "data_offset", "n_bytes", "n_elements"):
            assert getattr(actual, key) == getattr(expected, key), (actual.name, key)
        assert np.array_equal(actual.shape, expected.shape)
        # Identical read-only file and exact offsets/sizes establish the same bytes.
        assert actual.data.view(np.uint8).reshape(-1)[:64].tobytes() == expected.data.view(np.uint8).reshape(-1)[:64].tobytes()
    report = {"passed": True, "tensor_count": len(compact.tensors),
              "compact_index_seconds": compact_seconds, "elapsed_seconds": time.monotonic() - started}
    args.out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
