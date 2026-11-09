# Windows build and command safety — release candidate

This source change prepares a **candidate** on a separate branch. It does not install software, change an existing Takumi process, tag a version, or create/publish a GitHub Release.

## Project commands

The optional, project-owned `takumi.tasks.json` declares reviewable command arrays. The shipped commands inspect Git status/history, execute Python tests, check JavaScript syntax, or compile an isolated Windows x64 `dist/Takumi-win-x64` build. They do **not** merge branches, push Git, create a release, or install over a running executable. OpenCode remains a separately installed coding agent and its permissions should be reviewed for every project.

PowerShell tasks accept explicit project-local `.ps1` files via `-File`. Inline `-Command` or encoded commands are rejected in the registered task parser. This is **not a security sandbox**: a reviewed project script still runs with the operator's Windows account privileges. Use a non-admin account and only register trusted projects.

GitHub reads and reviewed pushes require the separately installed Git/GitHub CLI and that Windows account's authorization. They are not automatically authenticated by any ChatGPT connection.

## Self-update

Stable updates are retrieved only from the official `coye2/Takumi` GitHub stable release feed. Draft/prerelease assets are refused for stable OTA. The updater verifies the archive's GitHub-published SHA-256 and verifies every staged file against that archive again when scheduling installation. The external update helper waits for Takumi to close, rebuilds in isolation and retains the previous directory for rollback. There is no silent update or live-process replacement. An authenticated paired phone cannot invoke local software installation.

## Release gate

GitHub Actions produces a versioned-source **candidate** ZIP, a checksum file and an explicit candidate manifest only after Python tests on Windows/Ubuntu, frontend checks, a successful native .NET Windows build and the package exclusion scan. It is not an official release and has not been qualified on a clean Windows installation.

Before creating any new public release, separately verify: actual native Windows launch and closing without data loss, Sumi/Rei generation on installed hardware, connected-phone access, concurrent-process safety, upgrade/rollback and exact package digests. Preserve the current released preview until these gates pass.
