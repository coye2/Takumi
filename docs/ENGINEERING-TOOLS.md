# Takumi engineering workbench — operator guide

Only registered projects are eligible. You must own or have permission to analyze the selected files. No model weights, ROMs, keys, firmware, binary payloads or external executable are uploaded by Takumi's local analyzer endpoints.

## Read-only local primitives

- **Source symbols:** bounded case-insensitive exact matches with project-relative paths and line numbers; not semantic understanding or a substitute for actual compiler/LSP navigation.
- **Binary inspection:** bounded streaming SHA-256, format-header hints and entropy; no binary bytes returned.
- **SPIR-V validation:** invokes locally installed `spirv-val` with a timeout, reports its actual exit code and diagnostics; missing `spirv-val` means **not verified**.
- **SPIR-V disassembly:** invokes separately installed `spirv-dis`, returns bounded output; disassembly alone does not establish shader behavior.
- **Ghidra:** after user confirmation, invokes separately installed `analyzeHeadless` on one project file, with `-readOnly`, `-max-cpu 2`, `-analysisTimeoutPerFile 160` and the fixed `scripts/ghidra/TakumiFunctionSummary.py` postscript. Outputs up to 60 function names/addresses, plus a hash of the analyzed input. Heap is capped at 2 GB; the process times out after 220 seconds. Binary analysis is heuristic: decompiler accuracy and matching-decomp results require separate tests.

Ghidra has its own license, install process and security implications; do not analyze untrusted files as an administrator. The [official Ghidra Headless Analyzer guide](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/RuntimeScripts/support/analyzeHeadlessREADME.md) documents the supported flags.

## Project-controlled analyzers and benchmarks

RenderDoc capture/replay, objdiff comparisons, compiler differentials, and emulator gameplay harnesses depend on tool version, build options and local project assets. Declare those commands explicitly in a project-owned `takumi.tasks.json`. For example:

```json
{
  "version": 1,
  "tasks": [
    {
      "id": "jit-tests",
      "name": "CPU interpreter/JIT parity",
      "argv": ["dotnet", "test", "tests/NxInfinity.Core.Regression.Tests", "--configuration", "Release"],
      "cwd": "."
    },
    {
      "id": "gpu-capture",
      "name": "GPU performance capture",
      "description": "Replace the command with your reviewed capture script before using",
      "argv": ["python", "tools/capture_gpu.py"],
      "cwd": ".",
      "gpu_exclusive": true
    }
  ]
}
```

The example capture script is not shipped; add and review your own before running. Registered harness jobs are **not a sandbox** and have the current user's permissions. Use a non-admin account and review project task definitions. GPU-exclusive jobs reserve Takumi's own inference scheduler only; they cannot prevent other Windows programs using the GPU.

## Genuine proficiency requires measured evidence

For a hard emulator or decomp problem, collect the exact Git commit and repro, faulting guest state, relevant code and specification, run controlled differential experiments, patch one causal issue, and rerun tests. A locally hosted model cannot be made infallible by system prompts alone. See `docs/SUMI-ENGINEERING.md` and the opt-in benchmark script for documented, reproducible measurements.
