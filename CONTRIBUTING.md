# Contributing to Takumi

Bug reports, documentation improvements and focused pull requests are welcome. For substantial behavior changes, open an issue describing the problem and intended result first.

## Development

Use Python 3.11+ and Node.js 22+ for checks. Windows desktop builds need the .NET 10 SDK. Local inference and coding require separately installed Ollama/models and OpenCode; ordinary unit tests use fixtures and do not download models.

```sh
python -m unittest discover -s tests -v
python -m compileall -q takumi
node --check web/app.js
node --check web/kage.js
node scripts/test-markdown.cjs
node tests/test_run_stream.cjs
```

Run `python -m takumi --no-browser --port 8765` and open the local URL to use the browser UI. On Windows, run `./scripts/Build-Windows-EXE.ps1` from PowerShell to build `dist/Takumi-win-x64/Takumi.exe`.

## Project layout

| Directory | Purpose |
| --- | --- |
| `takumi/` | Local server, model transport, runs, projects and integrations |
| `web/` | Desktop/browser interface and Kage assets |
| `desktop/` | Native WinForms/WebView2 host |
| `models/`, `expertise/` | Model profiles, engineering references and playbooks |
| `scripts/`, `installer/` | Build, setup, repair and packaging tools |
| `tests/` | Backend and frontend regressions |

## Pull requests

Explain the concrete problem, resulting behavior and relevant checks. Preserve existing conversations, model identities, cancellation, permission boundaries and recovery behavior. Avoid broad unrelated refactors.

Use focused tests for meaningful failure cases. Report mocked checks, actual local inference, native Windows behavior and hardware tests separately. A syntax pass or model-generated claim is not a runtime test. Document skipped checks and known limitations.

Never commit credentials, chat databases, private project contents, model weights or third-party AI executables. Use synthetic fixtures. Do not include full private prompts or logs in issues; remove identifying data and secrets first.

Code contributions are made under the repository's MIT license. Artwork and third-party code require appropriate rights and notices. Follow [BRANDING.md](BRANDING.md) and [THIRD_PARTY.md](THIRD_PARTY.md).
