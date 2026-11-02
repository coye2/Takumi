# Sumi 1.0: emulator and decomp engineering

Sumi is the locally configured engineering model in **Takumi**, built from already installed upstream Bonsai 2 27B weights. Sumi's specialization combines a system profile, relevant retrieval, source investigation and actual build/test workflows. **No new model weights, LoRA adapter, or foundation-model fine-tune were produced in this release.**

## Engineering coverage

The bundled `expertise/platforms.json` describes 25 platform families (console-generation architecture, expected observable failures and diagnostic probes). `expertise/playbooks.json` supplies 13 engineering investigation methods, including CPU/guest JIT, GPU state, synchronization, service contracts, binary decompilation, shader translation, regression harnesses and emulator performance. `expertise/sources.json` contains 26 reviewable reference leads. They are leads, not downloaded code and not guaranteed to represent the latest upstream implementation.

Takumi automatically scores the active task for matching platform names, architecture and subsystem terms. It injects only the highest-ranked limited context, which keeps VRAM and prompt processing controlled. In the **Knowledge** screen you can inspect platform/playbook matches; in **Research** you can browse a GitHub repository tree or individual source snippets and review its license. Never copy upstream code without checking the exact license and notice obligations for the relevant files. Private repos are accessible only through separately authorized tools.

## Practical emulator workflow

1. Read the active repository branch, dirty files, handoff, existing failing log and exact game/build identifiers. Never silently reset work.
2. Determine the *first causally relevant failure* rather than treating a shutdown exception as the cause.
3. Select an observation point (guest PC, instruction registers, service command, DMA state, GPU resource, barrier, shader compile or presentation) and reproduce it.
4. Compare current source with a matching, licensed reference implementation only after verifying the reference's commit and architecture.
5. Make one coherent code change, write an automated regression test when possible, rebuild the precise binary and rerun the same experiment.
6. Capture the real result and any remaining unknowns in a handoff. An application that exits normally or presents a black frame is **not** proven playable.

For matching decompilation, record binary provenance, executable/section/segment layout, toolchain, target ABI, relocation symbols and repeatable matching evidence; do not claim complete decomp from superficial disassembly. Keep proprietary keys, firmware, game files and protected source out of distribution.

## Trusted project memory

Use **Knowledge → Engineering lessons** to save concise facts backed by evidence you reviewed yourself. Takumi tags these lessons as user-reviewed, may detect them as relevant to subsequent tasks and will tell the model to verify their applicability. Removing an item deletes it from Takumi's local lesson library. These are explicit retrieval notes, **not neural training**.

## Measured benchmarks

Run `scripts/Benchmark-Sumi-1.0.ps1` from the installed or extracted source folder to measure local model first-response latency, completion throughput and answer content across four representative cases. Use `-Full` for 10 cases and `-CompareBase` for a same-hardware comparison with the base model. The report is written only to `%USERPROFILE%\.takumi\benchmark-sumi-1.0.json` unless you choose another path. No paid service or third-party credential is required.

Rubric coverage merely checks whether certain terms occur. It **does not** establish correctness, superior intelligence, or compatibility. Use manually reviewed code, regression tests and representative emulator workloads before publishing quality claims. Keep large competitive benchmarks off busy GPUs and avoid running the emulator simultaneously with model inference when comparing frame times.

## Live research and progress

Real research is best-effort: Bing RSS, two DuckDuckGo adapters, Wikipedia for general background, GitHub's public API and authenticated GitHub CLI where separately configured, with a local source fallback. Cached references are visibly labeled stale. Searches for proprietary logs or source can be disabled in Settings → Research. The progress tabs display **actual observed tool events and research status**, not a model's private internal reasoning transcript.

## Hardware bounds

Your target of an RTX 4060 Ti 16 GB + 32 GB RAM favors quantized ~27B weights and bounded context, one active coding model at a time, and measured GPU memory/temperature readings. Boost reduces context and output budgets but does **not** guarantee a 1.5× measured speedup. A local model's capacity cannot be made equivalent to GPT-6 or Claude Opus merely by renaming it. Track Sumi versions using evidence and model/workflow changes.
