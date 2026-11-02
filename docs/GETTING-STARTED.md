# Getting started

## Portable Windows desktop

1. Download `Takumi-v1.5.0-preview.1-Windows-x64.zip` from the [official release](https://github.com/coye2/Takumi/releases/tag/v1.5.0-preview.1).
2. Extract the complete ZIP to a folder you control. Keep `Takumi.exe` and its accompanying folders together.
3. Install Python 3.11+ and Microsoft Edge WebView2 if missing. The .NET runtime is included; the .NET SDK is only needed for source builds and source-based updates.
4. Open `Takumi.exe`. Use the app's model setup, `SETUP-SUMI.cmd` or `SETUP-REI.cmd` to configure inference.
5. Start a chat. Choose a project folder when you want to work on code.

Official dependency sources: [Python](https://www.python.org/downloads/windows/), [WebView2](https://developer.microsoft.com/en-us/microsoft-edge/webview2/), [Ollama](https://ollama.com/download/windows), [.NET](https://dotnet.microsoft.com/download/dotnet/10.0), and [OpenCode](https://github.com/anomalyco/opencode).

The preview executables are not Authenticode-signed. Verify the download and published SHA-256; a checksum confirms file integrity, not publisher identity or software safety. Do not disable operating-system protections to install the app.

## Source-based setup

Download and extract `Takumi-v1.5.0-preview.1-Source.zip`, then run `REPAIR-Takumi.cmd`. This invokes the supplied installer, installs missing prerequisites through WinGet, builds Takumi from its C# source, and provisions separately downloaded model tools. It requires internet access and may show installer permission prompts. Read the visible installer output before continuing.

The portable download avoids compilation; source-based repair needs the .NET 10 SDK. Setup logs are written to `%LOCALAPPDATA%/Takumi/Logs`. Previous installation folders are retained for recovery where supported; back up your data before upgrades.

## Choose a model

Sumi 1.0 uses a separately downloaded Bonsai 2 27B model, approximately 8.3 GB on disk. A 16 GB GPU and 32 GB RAM are recommended for the full profile. Rei 1.0 uses the smaller 9B base, approximately 3.2 GB, and is intended for lower-memory systems. Context, other applications and driver behavior affect memory use. Physical 8 GB GPU qualification is pending.

Allow at least 14 GB additional free space for full-model setup. Runtime and model downloads are separate from Takumi's ZIP; their licenses apply independently. See [Sumi setup](SETUP-SUMI.md) for recovery and details.

## Optional tools

- **Coding:** OpenCode must be separately available. Takumi launches it in the chosen project and displays its real events.
- **GitHub:** install GitHub CLI and sign in with `gh auth login`. Use explicit repository wording, such as “review my owner/repository repo on GitHub.” Bounded source reading does not build or test that project.
- **Research:** public providers can be blocked or return irrelevant results. Configure a supported provider or supply a source URL. Do not treat a search result as verification.
- **Phone:** follow the [private iPhone guide](REMOTE-IPHONE.md). The PC must remain available; the phone does not run the local model itself.
- **Engineering tools:** see the [workbench guide](ENGINEERING-TOOLS.md) for optional compilers, SPIR-V tools and Ghidra.

## Troubleshooting and updates

If the window fails to open, check Python/WebView2 and the desktop log under `%LOCALAPPDATA%/Takumi/Logs`. If the model is missing, retry model setup and inspect its error before downloading again. If a task hangs, use Stop; restarting the app preserves saved conversations and run records.

Keep one installation folder per version and close its window before replacing files. User data normally lives in `~/.takumi`, separately from the application. Retain the previous folder to roll back.

This preview is downloaded manually from Releases. The in-app updater follows the stable release channel and does not offer GitHub prereleases. End-to-end automatic upgrade qualification is pending.
