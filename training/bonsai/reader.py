"""Bounded GGUF index reader for training; skips unneeded tokenizer arrays.

Uses pinned llama.cpp type/block definitions. Tensor bytes remain read-only
memory maps. Never dequantizes or changes tensor coordinates.
"""
import mmap
from pathlib import Path
import struct
from types import SimpleNamespace

import numpy as np
from gguf.constants import GGMLQuantizationType, GGML_QUANT_SIZES


class Field:
    def __init__(self, value):
        self.value = value

    def contents(self):
        return self.value


class CompactGGUFReader:
    def __init__(self, path):
        self.path = str(Path(path))
        with open(path, "rb") as f:
            self.data = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        self.pos = 0
        self.fields = {}
        self.tensors = []
        if self.take(4) != b"GGUF":
            raise ValueError("Invalid little-endian GGUF magic")
        version, count, kv = self.unpack("<IQQ")
        if version != 3 or count > 10000 or kv > 10000:
            raise ValueError("Unsupported GGUF version or unreasonable index size")
        for _ in range(kv):
            key = self.string()
            kind = self.unpack("<I")[0]
            # Tokenizer export is separate. Do not materialize ~500k numpy views.
            keep = not key.startswith("tokenizer.")
            value = self.value(kind, keep)
            if keep:
                if key in self.fields:
                    raise ValueError("Duplicate metadata key")
                self.fields[key] = Field(value)
        entries = []
        for _ in range(count):
            name = self.string()
            ndim = self.unpack("<I")[0]
            if not 1 <= ndim <= 4:
                raise ValueError("Invalid tensor rank")
            shape = self.unpack("<" + "Q" * ndim)
            if any(dim == 0 for dim in shape):
                raise ValueError("Empty tensor")
            kind, offset = self.unpack("<IQ")
            tensor_type = GGMLQuantizationType(kind)
            block, size = GGML_QUANT_SIZES[tensor_type]
            elements = 1
            for dim in shape:
                elements *= dim
            if elements % block:
                raise ValueError("Tensor block size mismatch")
            entries.append((name, shape, tensor_type, offset, elements // block * size, elements))
        alignment = self.fields.get("general.alignment", Field(32)).contents()
        if not isinstance(alignment, int) or alignment <= 0 or alignment > 65536 or alignment & (alignment - 1):
            raise ValueError("Invalid alignment")
        self.data_offset = (self.pos + alignment - 1) // alignment * alignment
        seen = set()
        for name, shape, tensor_type, offset, size, elements in entries:
            absolute = self.data_offset + offset
            if name in seen or absolute + size > len(self.data):
                raise ValueError("Duplicate or out-of-bounds tensor")
            seen.add(name)
            dtype = {"F32": np.float32, "F16": np.float16}.get(tensor_type.name, np.uint8)
            array = np.ndarray((size // np.dtype(dtype).itemsize,), dtype=dtype,
                               buffer=self.data, offset=absolute)
            if tensor_type.name in ("F32", "F16"):
                array = array.reshape(shape[::-1])
            self.tensors.append(SimpleNamespace(name=name, shape=np.array(shape), tensor_type=tensor_type,
                                               data=array, data_offset=absolute, n_bytes=size, n_elements=elements))

    def take(self, count):
        if count < 0 or self.pos + count > len(self.data):
            raise ValueError("Truncated GGUF")
        result = self.data[self.pos:self.pos + count]
        self.pos += count
        return result

    def skip(self, count):
        if count < 0 or self.pos + count > len(self.data):
            raise ValueError("Truncated GGUF")
        self.pos += count

    def unpack(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))

    def string(self, keep=True):
        size = self.unpack("<Q")[0]
        if keep:
            if size > 16 * 1024**2:
                raise ValueError("Oversized metadata string")
            return self.take(size).decode("utf-8")
        self.skip(size)

    def value(self, kind, keep=True, depth=0):
        formats = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f",
                   7: "?", 10: "Q", 11: "q", 12: "d"}
        if kind in formats:
            return self.unpack("<" + formats[kind])[0]
        if kind == 8:
            return self.string(keep)
        if kind == 9 and depth == 0:
            subtype, size = self.unpack("<IQ")
            if size > 2_000_000 or subtype == 9:
                raise ValueError("Oversized or nested metadata array")
            if not keep and subtype in formats:
                self.skip(size * struct.calcsize("<" + formats[subtype]))
                return None
            if keep:
                return [self.value(subtype, True, depth + 1) for _ in range(size)]
            for _ in range(size):
                self.value(subtype, False, depth + 1)
            return None
        raise ValueError(f"Unsupported metadata type {kind}")

    def release_file_pages(self):
        # Linux drops clean file-backed pages after each transferred tensor.
        if hasattr(self.data, "madvise"):
            self.data.madvise(mmap.MADV_DONTNEED)
