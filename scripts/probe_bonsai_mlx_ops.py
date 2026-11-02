"""Bounded CUDA primitive probe, not model training or checkpoint validation.

Exercises group-64/128 2-bit affine matmul and normalized block-1024
Hadamard operations required by the official packed MLX runtime. A pass
does not establish full hybrid-attention model/backward/export support.
"""
import argparse
import json
import math
from itertools import product
from pathlib import Path
import time
import traceback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    report = {"probe": "mlx_cuda_primitives", "training_performed": False,
              "optimizer_steps": 0, "cases": []}
    start = time.monotonic()
    try:
        import mlx.core as mx
        report["cuda_available"] = mx.cuda.is_available()
        if not report["cuda_available"]:
            raise RuntimeError("MLX CUDA backend unavailable")
        mx.set_default_device(mx.gpu)
        mx.random.seed(1701)
        for group_size, width in product((64, 128), (5120, 6144, 17408)):
            # Small row count bounds allocations; representative actual activation widths.
            case = {"width": width, "rows": 128, "bits": 2, "group_size": group_size}
            report["cases"].append(case)
            try:
                x = mx.random.normal((1, width)).astype(mx.float32)
                signs = mx.where(mx.arange(width) % 2 == 0, 1.0, -1.0)
                weight = mx.random.normal((128, width)).astype(mx.float32)
                packed, scales, biases = mx.quantize(weight, group_size=group_size, bits=2)
                dense = mx.dequantize(packed, scales, biases, group_size=group_size, bits=2)
                def rotate(value):
                    return mx.hadamard_transform((value * signs).reshape(-1, 1024),
                                                 scale=1 / math.sqrt(1024)).reshape(value.shape)
                def packed_loss(value):
                    y = mx.quantized_matmul(rotate(value), packed, scales, biases,
                                             transpose=True, group_size=group_size, bits=2)
                    return mx.mean(y * y)
                def reference_loss(value):
                    y = rotate(value) @ dense.T
                    return mx.mean(y * y)
                loss, grad = mx.value_and_grad(packed_loss)(x)
                expected, expected_grad = mx.value_and_grad(reference_loss)(x)
                mx.eval(loss, grad, expected, expected_grad)
                case.update(loss=float(loss.item()), reference_loss=float(expected.item()),
                            grad_norm=float(mx.linalg.norm(grad).item()),
                            max_grad_error=float(mx.max(mx.abs(grad - expected_grad)).item()),
                            finite=bool(mx.all(mx.isfinite(grad)).item()),
                            close=bool(mx.allclose(grad, expected_grad, rtol=1e-3, atol=1e-3).item()))
                case["passed"] = case["finite"] and case["close"] and case["grad_norm"] > 0
            except Exception:
                case.update(passed=False, traceback=traceback.format_exc())
        report["peak_mlx_memory_bytes"] = mx.get_peak_memory()
        report["passed"] = all(c["passed"] for c in report["cases"])
    except Exception:
        report.update(passed=False, traceback=traceback.format_exc())
    report["elapsed_seconds"] = time.monotonic() - start
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] else 2)


if __name__ == "__main__":
    main()
