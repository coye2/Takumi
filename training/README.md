# Exact Sumi training investigation

## Latest measured result (2026-11-02)

Historical blocked/zero-step reports below are superseded. Exact Sumi 27B LoRA
training now executes on this PC with other apps open. Layers 62/63, rank 4:
180,224 trainable parameters. The 64-step diagnostic-v2 fit reduced losses from
4.597659/4.812826 to 0.004205/0.005114 with unchanged frozen hashes and exact
zero/disabled-adapter parity. A separate saved two-step checkpoint reloaded in a
fresh process with zero output difference. The old copy diagnostic worsened
already-low losses; its old threshold-only pass must not be read as improvement.

See evidence/2026-11-02/learning-milestones.json. The eight-fixture coding pilot
has independent validators and held-out families, but broader engineering,
optimizer-state resume, serving/export and deployment remain unqualified.
No frontier parity is claimed. Keep local checkpoints out of source control.


Owner request: actual parameter updates on the installed Bonsai 2 27B base,
then independently verified improvement and deployment qualification.
**Exact-model forward passed; no training or engineering improvement is established.**
No model profile has been changed, no paid compute used, and no weights uploaded.

## Verified on 2026-11-02

- Repository baseline `46dec8b0c0c1121779d5260aaebcee25cdbfec74`, clean before
  creating `training/sumi-exact-base-audit`. Remote main remains `8fef1c3`.
- RTX 4060 Ti 16,380 MiB, driver 610.88; Windows Python 3.12.10, Ollama 0.40.13.
  Installed Ubuntu WSL2 has Python 3.14.4 and access to the same GPU.
- Sumi and `bonsai-2:27b` have identical language/projector blob hashes.
  Language SHA-256: `4f99aed01b8a877e153f9aa6569a4440fe17c59701cc0953e36d7f460549e70e`.
  It matches `Ternary-Bonsai-2-27B-Q2_0.gguf` in upstream revision
  `94dd51cdbbec5309ed8b76a12f3268bc4fdceb93` (7,626,008,928 bytes).
  This pins a revision containing the identical artifact, not its original creation revision.
- Rei and `bonsai-2:9b` likewise share language/projector blobs. No substitution.
- The language file contains 26,895,998,464 parameters in 851 tensors:
  402 Q2_0, 353 F32, 96 BF16. Architecture `qwen35`: 64 hybrid-attention blocks,
  full attention every fourth block. Actual Q2_0 tensors use GGML type 42.
- Tokenizer is GPT-2/BPE with `qwen35` pre-tokenization, 248,320 vocabulary entries,
  247,587 merges; BOS/pad 248044, EOS 248046. Embedded Jinja template has
  ChatML boundaries and XML function/parameter tool calls. Ollama exposes
  `{{ .Prompt }}` separately; it must not replace the embedded template for training.
- Transformers 5.22.1's real header reader rejects type 42 before weight allocation.
  CPU PyTorch was installed solely for this metadata probe; this is not a CUDA
  training environment qualification. The first probe had an incorrect helper
  import; the corrected invocation produced the recorded unsupported-type error.
- MLX 0.32.4 CUDA passed group-128, 2-bit matmul/Hadamard gradient checks at
  widths 5120, 6144, 17408. Maximum gradient error against dense MLX matmul
  reference: 4.39e-5; peak MLX allocation 19,431,536 bytes. These are synthetic
  primitive checks, not model training, nor an independent Hadamard oracle.
- Experimental Q2_0-to-MLX storage conversion preserves all 7,557,120,000
  encoded bytes across all 402 Q2_0 tensors. First/last rows of each tensor
  exactly match llama.cpp's independent decoder. No scales are merged or
  weights requantized. This is storage equivalence, not full-model equivalence.
- The extended six-case CUDA probe passed for both g64 and g128 at all three
  widths (maximum gradient error 3.82e-5, peak MLX allocation 19,775,656 bytes).
- Compact reader matched all 851 official tensor entries and retained metadata;
  index creation took 0.297 seconds. The tokenizer export preserves every one
  of 248,320 vocabulary IDs and passes three text round trips. Ollama tokenizer
  parity is still untested.
- Seven dedicated storage tests pass: independent g64 scale/code arithmetic,
  malformed magic, truncated header/tensor, out-of-bounds offsets, nonfinite
  scales and incorrect packed lengths. Python compilation and diff checks pass.

## Measured loading constraint

Three full-model attempts ended before a forward pass. The first, using only
Linux memory checks, reached 5.45 GB process RSS while Windows available RAM fell
to 68,548 KiB; the owned process was terminated immediately. WSL's available-RAM
counter did not describe Windows headroom. The second was stopped automatically
by the new Windows guard around 300/851 transferred tensors. The third, using
explicit CUDA materialization, reached 700/851 transferred tensors and reduced
sampled peak process RSS to 1.68 GB, but still hit the 2 GiB Windows headroom guard.
This does **not** establish that the model cannot fit on an otherwise free 32 GB
machine. Current concurrent applications materially constrain the trial.

The MLX CUDA allocator's WSL path uses pinned host storage when concurrent managed
access is unavailable. Source inspection supports investigating host allocation
as well as GPU allocation; it is not a complete attribution of Windows memory.
No unrelated applications were closed, no swap settings changed, and no clocks
or security settings changed. The owner has been asked to free RAM voluntarily.
Further full-model retries require 12 GiB **available Windows RAM** as a conservative
starting target, still unproven sufficient. The guard stops below 2 GiB and after
15 minutes, and signals only the PID whose command matches the isolated probe.

See [audit evidence](evidence/2026-11-02/audit.json). There are zero optimizer
steps, no adapter, no dataset, no trained checkpoint, and no candidate to promote.

## Paths under investigation

The public release includes a 53.8 GB F16 GGUF reference, but no matching
unpacked HF checkpoint in the publisher listing inspected. Dense 16-bit language
weights alone require about 50.1 GiB, exceeding combined physical RAM and VRAM
before OS, activations or training. No dense allocation or paging trial was run.
The generic Transformers reader also lacks the installed Q2_0 decoder and the
inspected GGUF integration does not implement Prism's activation rotations.
PEFT/TRL/Unsloth wrappers do not establish those missing numerical contracts.

The promising local experiment uses the exact installed Q2_0 file with MLX
CUDA in existing WSL. The adapted Prism loader preserves rotations, inverse
embedding transforms and GDN grouping; it still needs full-model validation.
The supplied Prism MLX pack is an alternative representation, **not assumed
identical** to the installed file. No dense-ancestor substitution is permitted.

Current llama.cpp `1b6935b227b9f9aa89deea8016c0e208f547446f` source explicitly
handles LoRA on folded matmul weights using primal inputs and rejects inverse
lookup targets. This is source evidence only: installed Ollama adapter loading,
tensor naming, coordinate basis, quantization/export and behavior are unverified.
Do not merge adapters back into the ternary representation without validation.

## Reproduce the bounded probes

Run from the repository. Set `$Artifacts` to a local directory outside Git.
The inventory requires a new output directory, reads loopback Ollama metadata,
fetches the pinned public model metadata, and hashes installed weight blobs.

```powershell
py -3.12 scripts/audit_sumi_training.py --out "$Artifacts/inventory"
py -3.12 -m venv "$Artifacts/audit-env"
& "$Artifacts/audit-env/Scripts/python.exe" -m pip install -r training/requirements-audit-windows.lock.txt
& "$Artifacts/audit-env/Scripts/python.exe" scripts/probe_bonsai_transformers.py --gguf $BaseBlob --out "$Artifacts/transformers-probe.json"
```

The last command is expected to reject unsupported type 42 with the pinned stack.
Clone `ggml-org/llama.cpp`, check out the commit above and supply its `gguf-py`
directory to `scripts/verify_bonsai_codec.py --gguf $BaseBlob --gguf-python $GgufPython --out $Report`.
The same module is needed by the experimental model probe.

In the isolated WSL environment, install `mlx[cuda13]==0.32.4`; run
`scripts/probe_bonsai_mlx_ops.py --out PATH`. Full-model probing additionally
needs `mlx-lm==0.32.0`, `transformers==5.22.1`, `numpy==2.5.4`, and `psutil==7.2.2`.
On Windows, use `scripts/run_bonsai_wsl_probe.py --python WINDOWS_PATH_TO_WSL_ENV_BIN_PYTHON
--gguf WINDOWS_BLOB_PATH --gguf-python WINDOWS_GGUF_PY_PATH --out NEW_WINDOWS_DIRECTORY`.
Use `training/requirements-mlx-wsl.lock.txt` for the exact installed WSL environment.
The wrapper invokes `scripts/probe_bonsai_model.py`, monitors Windows headroom,
and limits the probe to 15 minutes. The child limits MLX allocations to 12 GiB
and also monitors Linux process/RAM use; it performs only a
four-token forward pass, saves logits, and exits. The probe does not train.
Do not run the child directly on this WSL host: its Linux-only memory counter
misses the Windows memory pressure demonstrated by the first attempt.

## Next gates

### Diagnostic sample preflight

`training/smoke/diagnostic-v1.json` contains two locally authored MIT-licensed
copy-exactly fixtures, excluded from engineering curriculum and capability
evaluation. The exact exported tokenizer produces 57 tokens, of which 14 final
answer/EOS tokens are supervised. No optimizer has consumed these samples.
`scripts/prepare_bonsai_smoke.py --tokenizer LOCAL_TOKENIZER --out NEW_REPORT`
records source/tokenizer hashes. Prefix and token boundaries must agree; embedded
EOS, empty answers and overlong samples are rejected. There is no packing or
silent truncation. Padding and previous tool/assistant/user history are masked.
The eventual loss must shift these labels once for next-token prediction and
honor the padding mask; that loss integration is not yet implemented or tested.

Set `TAKUMI_TEST_TOKENIZER` to the exact local export and `PYTHONPATH` to the
pinned llama.cpp `gguf-py`, then run `python -m unittest discover -s training/tests -v`
in the Windows audit environment. All 16 storage/sample tests passed, including
the seven actual-tokenizer cases (which otherwise explicitly skip).

The WSL `scripts/probe_mlx_placement.py --out NEW_REPORT` diagnostic holds 128 MiB
using the loader's explicit CUDA multiply operation. It observed 134,217,728
active MLX bytes and a 64,581,632-byte RSS increase, with the expected checksum;
after release MLX active memory returned to zero. This supports the transfer
mechanism, but does not establish total Windows memory attribution or full-model
feasibility. The model load still requires the guarded 12 GiB starting headroom.

A second run (`--chunks 32`) held 512 MiB while process RSS remained
461,893,632 bytes from the 128 MiB through 512 MiB stages; released MLX memory
again returned to zero. Concurrent Windows available RAM fell from 5,528,817,664
to a sampled minimum 3,488,120,832 bytes. Other applications remained active;
these observations cannot attribute that decline to the probe. No full-model
retry was launched. The owner requested that all apps remain open.

The loader now transcodes directly from contiguous mapped buffers, removing a
raw-tensor bytes copy, and deletes staging references after transfer. Mapped and
copied inputs produce identical arrays in the storage fixture, independent of
the original mapping. Full-model peak-memory savings remain unmeasured.

### Remaining acceptance gates

1. Verify group-64 CUDA gradients, hybrid attention, exact-model forward and
   tokenizer/template parity with Ollama under controlled deterministic inputs.
2. Tiny adapter overfit: finite gradients, changed tensor hashes/norms, unchanged
   frozen weights, optimizer steps, fresh-process reload and disabled-adapter parity.
3. Only after that: provenance-tracked, pre-split verified curriculum; completion
   masking/packing tests; measured pilot; SFT; locked held-out evaluation.
4. Predeclared promotion gates, export/runtime comparison and rollback before
   registering or promoting an experimental candidate. DPO/RL are conditional.

## Primary references

- https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/tree/94dd51cdbbec5309ed8b76a12f3268bc4fdceb93
- https://docs.prismml.com/download/formats
- https://github.com/PrismML-Eng/Bonsai-demo/issues/76 (older unpacked-model guidance;
  not proof of Bonsai 2 packed backward support)
- https://huggingface.co/docs/transformers/v5.22.1/quantization/gguf
- https://huggingface.co/docs/peft/developer_guides/quantization
- https://ml-explore.github.io/mlx/build/html/install.html
- [Vendored loader provenance and license](bonsai/PROVENANCE.md)


## Successful exact-model forward, subsequent smoke preparation

The guarded `model-probe-004` loaded all 851 tensors and produced finite logits
of shape [1, 4, 248320] for token IDs [1, 2, 3, 4]; next-token argmax was 5.
Load: 175.807 s; first forward: 90.323 s; total: 266.447 s. Peak MLX allocation:
8,845,349,008 bytes; sampled peak process RSS: 2,965,753,856 bytes; minimum Windows
available RAM: 3,778,228,224 bytes. Exit 0; no host guard stop and no observed swap
use. See `evidence/2026-11-02/model-forward-004.json` and raw artifact hashes.
This establishes execution, not serving parity, backward support or learning.

The guarded probe now accepts `--smoke-steps 1` through `16` and `--tokenizer`
(exact local exported directory), or `--reload-from` (a prior successful smoke
output directory) with the tokenizer. Reload and training run in separate
processes. The 12 GiB startup, 2 GiB runtime, 12 GiB MLX, and 15-minute bounds
remain in force. The experimental smoke implementation freezes the base and
adds rank-4 original-activation-basis LoRA to layers 0 and 63 `mlp.down_proj`
(180,224 adapter parameters), with block activation checkpointing. It records
losses, per-tensor gradient norms, hashes, deltas and base/disabled parity;
it saves adapter weights after each step. This smoke is not a resumable SFT
pipeline, a deployment export, or a supported runtime integration.

**The adapter smoke has not executed on the exact model.** A separate synthetic
wrapper check was stopped during initialization after Windows free RAM fell
below 1 GiB; it produced no result. No app was closed. The new smoke code has
only passed Python compilation; the earlier 16 storage/sample tests do not
validate it. Tiny overfit, full backward, optimizer updates, save/reload parity,
serving equivalence, curriculum SFT and deployment remain outstanding.
