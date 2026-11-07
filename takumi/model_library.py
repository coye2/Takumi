"""Fixed list of extra local models Takumi may download.

These are registry tags for the already installed local runtime. They are not
Sumi or Rei, and a request cannot substitute its own tag or shell command.
Sizes are described by fit on a 16 GB GPU, not by a measured byte count.
"""
from __future__ import annotations

from .models import canonical_tag

LIBRARY = (
    {'id': 'qwen3:4b', 'label': 'Qwen3 4B', 'fit': 'Small',
     'note': 'Faster general Qwen. Less capable than Sumi.'},
    {'id': 'qwen3:8b', 'label': 'Qwen3 8B', 'fit': 'Fits a 16 GB GPU',
     'note': 'General Qwen3. Not a Takumi profile.'},
    {'id': 'qwen2.5-coder:7b', 'label': 'Qwen2.5 Coder 7B', 'fit': 'Fits a 16 GB GPU',
     'note': 'Coding-focused Qwen. Not Sumi or Rei.'},
    {'id': 'qwen2.5-coder:14b', 'label': 'Qwen2.5 Coder 14B', 'fit': 'Tight on 16 GB',
     'note': 'Larger coding Qwen. It may crowd the GPU.'},
    {'id': 'qwen3:14b', 'label': 'Qwen3 14B', 'fit': 'Tight on 16 GB',
     'note': 'Larger general Qwen. It may crowd the GPU.'},
    {'id': 'llama3.1:8b', 'label': 'Llama 3.1 8B', 'fit': 'Fits a 16 GB GPU',
     'note': 'General local model. Not a Takumi profile.'},
    {'id': 'gemma3:4b', 'label': 'Gemma 3 4B', 'fit': 'Small',
     'note': 'Small Gemma model from the local registry.'},
    {'id': 'deepseek-r1:8b', 'label': 'DeepSeek R1 8B', 'fit': 'Fits a 16 GB GPU',
     'note': 'Small reasoning model. It was not measured as Sumi.'},
    {'id': 'phi4-mini', 'label': 'Phi-4 mini', 'fit': 'Small',
     'note': 'Small Phi model from the local registry.'},
)

_BY_ID = {row['id']: row for row in LIBRARY}


def library_row(tag: str) -> dict | None:
    return _BY_ID.get(str(tag or '').strip())


def library_catalog(installed: list[str]) -> list[dict]:
    have = {canonical_tag(item) for item in installed if isinstance(item, str)}
    rows = []
    for row in LIBRARY:
        rows.append({**row, 'installed': canonical_tag(row['id']) in have})
    return rows
