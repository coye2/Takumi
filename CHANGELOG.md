# Changelog

## 1.6.0 — 2026-11-07

- **Verified build** (opt-in, Tools → Verified build): Sumi edits a project, runs its real build and tests, and repairs from the errors. Results are labeled `verified`, `partially_verified` or `unverified`; failed, stuck or cancelled runs put your files back. Repairs that rewrite tests, call methods a class does not define or rename exports are rejected. Python, JavaScript/Node, Go, Rust, .NET and C++ projects are covered. See [Verified build](docs/VIBE-CODING.md).
- Sumi runs an edge-case test step, with reasoning, before calling a change `verified`. It was adopted under a rule fixed before scoring: on 17 unseen tasks it passed hidden tests on 14 with none falsely verified, against 10 with two falsely verified for the previous setup.
- **Sumi 1.1 and Rei 1.1** profiles. Installed runtime tags stay `sumi-1.0:latest` and `rei-1.0:latest`, so existing weights keep working. No new foundation-model training is implied.
- Reviewable project tasks in `takumi.tasks.json` for Git, Python, JavaScript and an isolated Windows build. PowerShell tasks must run a project script with `-File`; inline commands are rejected. Tasks use the project's real Python and pinned .NET SDK.
- Paired phones get read-only PC, PowerShell, GitHub and .NET readiness checks and project task controls. Windows diagnostics go to Work instead of chat.
- Coding-engine failures are classified with bounded diagnostics instead of a generic exit 1, with GitHub tokens redacted, including on iPhone.
- Work mode narrates progress more naturally, knows today's date, opens files in the chosen project, keeps a finished task finished after context compaction and shows fewer false reconnect warnings.
- More color themes, and Settings is easier to find.
- Stable updates re-verify the staged source against the official release ZIP before installing and reject previews and tampered archives.
- Fixes: reviewing a pull request stays in chat; a missing model is reported as the selected Sumi/Rei model; early error replies no longer reset connections on Windows; Node 24 test failures point at the assertion with expected and actual values.
- Experimental vibe-coding trainer with held-out evaluation. The latest adapter showed no held-out gain and was not promoted. No weights or adapters are bundled.

## 1.5.1-preview.2 — 2026-11-02

- Startup fix: a copy launched from another folder reuses an identical running background service instead of hitting the shared-data lock.

## 1.5.1-preview.1 — 2026-11-02

- Fixed phone shell with independently scrolling content, keyboard viewport handling, larger touch controls and reconnect recovery.
- Sage Green theme, matching page/system theme metadata and appearance restoration before first paint; reduced-motion-aware panel transitions.
- Desktop experimental exact-Sumi trainer with memory guards, GPU reservation, cancellation and diagnostic checkpoint verification. No trained model is bundled or promoted.
- Optional separately installed browser tools for OpenCode 1.x coding runs, with temporary isolated browser sessions.
- Private phone setup diagnostics, persistent background task and automatic idle-sleep prevention. Manual sleep, sign-out and network loss remain interruptions.

## 1.5.0-preview.1 — 2026-11-02

First public open-source preview of Takumi.

- Native Windows desktop with a local Python backend and saved conversations.
- Sumi 1.0 and lightweight Rei 1.0 model profiles, with guided setup.
- Persistent coding runs, live activity, cancellation and reconnect recovery.
- Kage chat, coordination and five companion characters, including Akane.
- Local source navigation, optional compiler checks and evidence inspection.
- Authenticated, bounded GitHub source reads and scoped subreddit lookup.
- Clearer conversational defaults and readable source links.
- Theme and language controls, optional voice, private phone access and integration configuration.
- Public contribution guidance, issue templates, CI and source/native release archives.

This release incorporates earlier private application development. It is a preview: model accuracy, some research routes, external integrations, 8 GB hardware and extended unattended operation have remaining qualification gaps. See [release status](docs/RELEASE-STATUS.md).
