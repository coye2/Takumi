"""Probe the installed GGUF header with Transformers, without allocating weights.

Exit 2 means the loader probe failed. Exit 0 means header parsing ONLY;
neither status establishes differentiable loading or numerical equivalence.
"""
import argparse
from collections import Counter
import importlib.metadata
import json
from pathlib import Path
import time
import traceback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gguf", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    start = time.monotonic()
    result = {"probe": "transformers_gguf_header", "path": str(args.gguf.resolve()),
              "training_performed": False, "optimizer_steps": 0,
              "versions": {name: importlib.metadata.version(name) for name in ("torch", "transformers", "gguf", "numpy")}}
    try:
        from transformers.integrations.gguf.reader import read_gguf_metadata, GgufHeader
        metadata, names = read_gguf_metadata(str(args.gguf))
        result["architecture"] = metadata["general.architecture"]
        result["tensor_count"] = len(names)
        result["rotation"] = {k: v for k, v in metadata.items() if k.startswith("prism.hadamard.")
                              and k not in ("prism.hadamard.sign_values", "prism.hadamard.weight_names")}
        header = GgufHeader.from_file(str(args.gguf))
        result["tensor_types"] = dict(Counter(str(t.ggml_type) for t in header.tensors))
        result["header_parsed"] = True
    except Exception:
        result.update(header_parsed=False, traceback=traceback.format_exc())
    result["elapsed_seconds"] = time.monotonic() - start
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["header_parsed"] else 2)


if __name__ == "__main__":
    main()
