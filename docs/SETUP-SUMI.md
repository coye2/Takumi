# Sumi 1.1 — team installation and troubleshooting

**New installations:** extract the *complete* Windows Setup ZIP, then double-click `Takumi-Setup.exe` or `REPAIR-Takumi.cmd`. The native desktop host is built from Takumi's own C# source. The installer also installs missing Python/.NET/WebView2 prerequisites via Windows Package Manager and provisions the **separate, official** local inference runtime, model and coding agent on the PC.

**Existing installations reporting “no model installed”:** double-click `SETUP-SUMI.cmd` inside the installed `Takumi` folder (`%LOCALAPPDATA%\Programs\Takumi`), or open Takumi → **Models → Set up Sumi 1.1**. This is a one-click guided retry, even if a previous model download was interrupted. The Models page shows live setup messages and reports the actual outcome; it does not pretend a failed download succeeded.

## What is downloaded

- The official Windows Ollama inference runtime through Microsoft WinGet (`Ollama.Ollama`). If WinGet is unavailable, setup tries the official installer **only after verifying its Authenticode signer**. The download runs on your PC, not in Takumi's published archive.
- The `bonsai-2:27b` base model through `ollama pull`. This is an approximately **8.3 GB** download. `ollama pull` can reuse/resume existing downloaded files.
- A Sumi 1.1 profile created with `ollama create sumi-1.0 -f models\Sumi-1.0.Modelfile`. This reuses the downloaded weights and does not retrain them.
- A version-pinned OpenCode command-line program from the upstream GitHub release, with a checked SHA-256 before installation **outside Takumi's distributable**. Existing installed coding engines are reused.

If the PC has less than 24 GB of system RAM or under 12 GB NVIDIA VRAM (when readable), the installer selects the separately labeled **Rei 1.1** (9B base) instead. It does not misleadingly call this the full 27B model. You can install Rei 1.1 directly using SETUP-REI.cmd, or select Rei on the Models page. Memory constraints and model quality differ across PCs.

For the recommended full model, reserve **at least 14 GB of additional free disk** before setup. The installer checks free space before downloading a base model and fails with a clear message if insufficient. The exact size varies with prior caches.

## First-run verification

The installer checks the inference API at `127.0.0.1:11434`, installs/resumes required model files, verifies the registered model appears in the local model list, then makes one real short generation request. If either verification fails, setup exits nonzero, preserves existing files, and logs the cause. Takumi's app itself can still be installed; model inference is **not** claimed to be ready until tested successfully.

The normal Windows desktop app also attempts to start an *already installed* local model service at login, without opening the model API to other devices. It does **not** silently download weights from a chat request.

## Logs and retry

Setup logs are saved under `%LOCALAPPDATA%\Takumi\Logs` (for example `sumi-setup-YYYYMMDD-HHMMSS.log`). Run `SETUP-SUMI.cmd` again after resolving internet, disk, policy or driver issues. Installed models and previously downloaded blobs are not deleted by this script. On tightly controlled or offline machines, install the officially licensed dependency packages through your IT deployment system and then rerun the setup; Takumi does not distribute them.

No sign-in to a hosted AI provider or paid API is required. Separate optional GitHub, Figma and Cloudflare integrations may need the developer's own accounts and credentials.

## Distributing Takumi

Takumi's source ZIP contains **no compiled binaries**. The Windows Setup ZIP contains only Takumi's own setup EXE and source; third-party engines and model weights are downloaded/installed on each developer's computer. Required attribution and model provenance are documented in `THIRD_PARTY.md`.

**Hardware verification still required:** Windows PowerShell/WinGet installation, signing verification, CUDA inference and the OpenCode CLI must be tested on the developer's actual PC. Automated Linux tests simulate setup command dispatch, not Windows execution.
