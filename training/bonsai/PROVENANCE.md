# Experimental Bonsai loader adaptation

`runtime.py`, `codec.py`, and `LICENSE` originate from Prism ML's
`prism-ml/Ternary-Bonsai-2-27B-mlx-2bit`, revision
`fcba37d2117a7077eac6b613b2668d14d9779edd`, `runtime/` directory.
Source: https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-mlx-2bit/tree/fcba37d2117a7077eac6b613b2668d14d9779edd/runtime

Modifications by Takumi contributors, 2026-11-02:

- Relative codec import for isolated training tooling.
- Lossless Q2_0 group-64 packing conversion into MLX affine storage, retaining
  each original FP16 scale and every 2-bit code; no NF4 or requantization.
- Derive group size from array shapes for both lookup and matrix multiplication.
- Decode BF16 auxiliary tensors through llama.cpp's GGUF implementation.
- Bounded index reader, independently compared against all 851 entries in the
  upstream reader; skip tokenizer arrays during weight loading and release
  clean mapped pages between transfers.
- Explicit CUDA identity operations when importing NumPy arrays, to materialize
  weights on the GPU instead of retaining the whole model in host storage.
- Transcode contiguous mapped buffers directly, eliminating the full raw-tensor
  bytes copy, and release NumPy staging references after each GPU transfer.

Q2_0 format oracle: `ggml-quants.c::dequantize_row_q2_0` and
`gguf-py/gguf/quants.py::Q2_0` in ggml-org/llama.cpp at
`1b6935b227b9f9aa89deea8016c0e208f547446f` (MIT).

This is an experimental loader adaptation, not a supported Prism ML CUDA
training release. It uses MLX operations, not newly written low-bit CUDA kernels.
Primitive gradient checks do not validate full-model mappings, tokenizer,
hybrid attention, training, serialization, or export. Those gates are separate.
