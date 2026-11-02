"""Lossless Q2_0/PQ2_0/PTQ1_0 to MLX affine 2-bit block transcoding.

Preserves folded weights. Runtime Hadamard metadata remains mandatory; this
module alone does not produce a loadable or validated model.
"""

import numpy as np


def transcode(raw, shape: tuple[int, int], source: str):
    """Return packed uint32 weights and FP16 scales/biases (Q2_0 g64; others g128)."""
    # Accept contiguous read-only mapped storage without a full tensor bytes copy.
    raw = memoryview(raw).cast("B")
    rows, width = shape
    if rows <= 0 or width <= 0 or width % 128:
        raise ValueError(
            "Expected positive [output, input] shape with input divisible by 128"
        )
    if source == "Q2_0":
        # llama.cpp type 42: one FP16 scale and 16 packed bytes per 64 values.
        # Keep the original group-64 scales; do not requantize or merge groups.
        if len(raw) != rows * width // 64 * 18:
            raise ValueError("Q2_0 byte length does not match shape")
        data = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 18)
        scales = data[:, :2].copy().view("<f2").reshape(rows, width // 64)
        if not np.isfinite(scales).all():
            raise ValueError("Non-finite Q2_0 scale")
        words = data[:, 2:].copy().view("<u4").reshape(rows, width // 16)
        return words, np.ascontiguousarray(scales), np.ascontiguousarray(-scales)
    sizes = {"PQ2_0": 34, "PTQ1_0": 28}
    if source not in sizes:
        raise ValueError(f"Unsupported format: {source}")
    blocks = rows * width // 128
    if len(raw) != blocks * sizes[source]:
        raise ValueError("Raw byte length does not match shape and format")
    data = np.frombuffer(raw, dtype=np.uint8).reshape(blocks, sizes[source])
    scale_bytes = data[:, :2] if source == "PQ2_0" else data[:, 26:28]
    scales = scale_bytes.copy().view("<f2").reshape(rows, width // 128)
    if not np.isfinite(scales).all():
        raise ValueError("Non-finite quantization scale")
    if source == "PQ2_0":
        words = data[:, 2:].copy().view("<u4").reshape(rows, width // 16)
        return words, np.ascontiguousarray(scales), np.ascontiguousarray(-scales)
    else:
        pieces = []
        # GGML PTQ stages consume 16 bytes, then 8, then the two-byte tail.
        for lo, hi, count in [(0, 16, 5), (16, 24, 5), (24, 26, 4)]:
            packed = data[:, lo:hi].astype(np.uint16)
            for trit in range(count):
                remainder = (packed * (3**trit)) & 255
                pieces.append(((remainder * 3) >> 8).astype(np.uint8))
        codes = np.concatenate(pieces, axis=1)
    words = np.bitwise_or.reduce(
        codes.astype(np.uint32).reshape(rows, width // 16, 16)
        << (2 * np.arange(16, dtype=np.uint32)),
        axis=-1,
    ).astype("<u4")
    return (
        np.ascontiguousarray(words),
        np.ascontiguousarray(scales),
        np.ascontiguousarray(-scales),
    )


def unpack(weight, scales, biases):
    """Independent affine reconstruction in float32 for packing validation."""
    rows, words = weight.shape
    values = np.empty((rows, words * 16), dtype=np.float32)
    for lane in range(16):
        values[:, lane::16] = (weight >> (2 * lane)) & 3
    group_size = words * 16 // scales.shape[1]
    groups = values.reshape(rows, -1, group_size)
    return (
        groups * scales.astype(np.float32)[..., None]
        + biases.astype(np.float32)[..., None]
    ).reshape(rows, -1)
