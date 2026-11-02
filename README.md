<p align="center">
  <img src="web/mark-clean.png" width="104" alt="Takumi logo">
</p>

<h1 align="center">Takumi 匠</h1>

<p align="center"><strong>A local AI workspace for building things.</strong></p>
<p align="center">Chat, write code, explore a project, and keep the work on your machine.</p>

<p align="center">
  <a href="https://github.com/coye2/Takumi/releases"><img alt="Public preview" src="https://img.shields.io/badge/release-1.5.1_preview.1-c48e96"></a>
  <a href="LICENSE"><img alt="Code license: MIT" src="https://img.shields.io/badge/code-MIT-52637a"></a>
  <a href="https://github.com/coye2/Takumi/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/coye2/Takumi/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <img alt="Windows x64" src="https://img.shields.io/badge/desktop-Windows_x64-52637a">
</p>

<p align="center">
  <a href="https://github.com/coye2/Takumi/releases/tag/v1.5.1-preview.1"><strong>Download Takumi</strong></a> ·
  <a href="docs/GETTING-STARTED.md">Getting started</a> ·
  <a href="CONTRIBUTING.md">Contribute</a> ·
  <a href="https://github.com/coye2/Takumi/issues">Issues</a>
</p>

![Takumi desktop workspace](docs/images/workspace.jpg)

## Meet Takumi

Takumi is an open-source Windows workspace for local AI chat and coding. It brings your model, project files, coding tools and research into a quiet desktop interface, with saved conversations and visible task progress.

**Sumi 1.0** is the full local model profile. **Rei 1.0** offers a smaller profile for machines with less memory. **Kage** adds a companion panel with task coordination and selectable ninja characters, including Akane, the crimson ninja.

## What you can do

- **Talk through an idea.** Get clear explanations, keep local notes, and return to saved conversations.
- **Work in a real project.** Use the separately installed OpenCode engine for edits and commands, with live activity, cancellation and saved run history.
- **Explore source.** Search local symbols and inspect bounded, commit-pinned GitHub excerpts using your existing GitHub CLI login.
- **Investigate engineering problems.** Use optional compiler, shader and binary-analysis tools. Review evidence and test results alongside the answer.
- **Choose your working style.** Change themes, language, model settings and Kage companions. Code blocks and references stay accessible.
- **Connect optional services.** Configure research providers, Figma MCP, a project Discord bot or private phone access when you need them.

![Kage companions](docs/images/kage.jpg)

## Get started

Download the **Windows x64** ZIP from [Releases](https://github.com/coye2/Takumi/releases/tag/v1.5.1-preview.1), extract it completely, and open `Takumi.exe`. The portable desktop requires **Windows 10/11 x64, Python 3.11+ and Microsoft Edge WebView2**. The .NET runtime is included in the desktop executable.

Use **Set up Sumi 1.0** in the app, or `SETUP-SUMI.cmd`, to configure local inference. Setup downloads the separate inference engine and model when needed. For a smaller model, use `SETUP-REI.cmd`. Coding mode also needs the separately installed OpenCode CLI.

For a source-based installation that installs missing prerequisites and builds the desktop, download the **Source** ZIP and run `REPAIR-Takumi.cmd`. Read the [setup guide](docs/GETTING-STARTED.md) for downloads, requirements and troubleshooting.

| Profile | Intended hardware | Base-model download |
| --- | --- | --- |
| Sumi 1.0 | 16 GB GPU memory and 32 GB system RAM recommended | About 8.3 GB |
| Rei 1.0 | Smaller-memory systems; intended for 8 GB GPUs | About 3.2 GB |

Actual memory use varies with context and workload. Sumi was tested on an RTX 4060 Ti 16 GB; the 8 GB Rei target still needs physical-device qualification. Sumi and Rei are application profiles over separately downloaded Bonsai models, not newly trained foundation models.

## Local by default

Conversations, settings and project notes are stored under `~/.takumi`. Model inference uses your local Ollama service. Web research, GitHub, optional integrations, dependency downloads and some browser voice services can contact external services. Their own authentication and terms apply. See [privacy and security](SECURITY.md).

This first public release is a **preview**. Model answers can be wrong; review edits and run relevant tests. Phone, integration and extended autonomous-run qualification is incomplete. The [release status](docs/RELEASE-STATUS.md) separates tested behavior from remaining work.

## Build and contribute

The backend uses the Python standard library; the UI uses plain HTML, CSS and JavaScript. The Windows host uses .NET 10, WinForms and WebView2.

```sh
git clone https://github.com/coye2/Takumi.git
cd Takumi
python -m takumi --no-browser --port 8765
```

Open `http://127.0.0.1:8765` for the browser UI. On Windows with the .NET 10 SDK, build the native app with `scripts/Build-Windows-EXE.ps1`. See [CONTRIBUTING.md](CONTRIBUTING.md) for checks and project structure.

## Documentation

- [Getting started and troubleshooting](docs/GETTING-STARTED.md)
- [Release status and known limitations](docs/RELEASE-STATUS.md)
- [Engineering tools](docs/ENGINEERING-TOOLS.md)
- [Sumi setup](docs/SETUP-SUMI.md) and [engineering guide](docs/SUMI-ENGINEERING.md)
- [Private iPhone access](docs/REMOTE-IPHONE.md)
- [Changelog](CHANGELOG.md)

## License

Takumi application code is available under the [MIT License](LICENSE). The logo and character artwork have separate [branding terms](BRANDING.md). Third-party components and model profiles retain their own licenses; see [THIRD_PARTY.md](THIRD_PARTY.md). No model weights, AI engine executables, credentials or game content are bundled.
