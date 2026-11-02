# Browser, training and phone tools

## Browser for coding tasks

Run `scripts/Install-Browser-Tools.ps1` on Windows, then enable **Browser** in
**Tools & connections**. Node.js 18+ and Microsoft Edge must already be installed.
The separately installed Microsoft Playwright MCP connector is pinned to 0.0.84.
Takumi passes it to the OpenCode 1.x coding engine for Agent-mode tasks. The
connector provides page navigation, interaction, accessibility snapshots and
screenshots. It starts a temporary headless browser and does not attach to your
personal browser or reuse its logins. Disable it to remove its tools from new
coding runs and conserve context. OpenCode 2.x is not qualified for this connector.

This is a tool integration, not model training or general Windows UI control.
It uses the current user's permissions and is not a security sandbox. Use it on
authorized tasks; websites and tool outputs are untrusted task data. Browser
availability does not prove that a local model will use it correctly.

Official interfaces: [OpenCode MCP](https://opencode.ai/docs/mcp-servers/) and
[Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp).

## Experimental exact-model training

The desktop trainer checks the exact Sumi Bonsai 2 27B blob hash and coordinates
GPU access with Takumi. Configure the existing WSL MLX Python, GGUF reader,
exact tokenizer and model paths in the trainer. These dependencies and weights
are not bundled. Actions include a forward check, a bounded 64-step learning check, a 64-step
coding pilot, fresh-process checkpoint verification and cooperative Stop. The
learning check memorizes two artificial associations. The pilot trains on eight
MIT-authored engineering fixtures checked against independent integer oracles,
then compares four fixed held-out tasks using bounded, non-executing AST checks.
This small synthetic suite does not establish whole-project coding capability. Training does not automatically replace Sumi.

The Windows guard requires 12 GiB available RAM before loading and stops below
2 GiB. The wrapper also stops when observed free GPU memory falls below 1 GiB.
MLX has a 12 GiB requested allocation limit; this is not a hard cap on all driver
memory. Each diagnostic run is bounded to 15 minutes; the coding pilot to 30 minutes.
The frozen 27B base remains exact. Rank-4 adapters target the last two MLP down
projections (180,224 trainable parameters). Saved adapters can be reloaded for
verification; optimizer-state resume and a serving/export path are not implemented.
Keep the base, adapters and local run artifacts outside source control. A saved
adapter needs numerical checks, independent engineering evaluation and a verified
serving/export path before deployment. No Claude parity is claimed.

## iPhone

Use the private HTTPS address shown in **Connect iPhone**, with Tailscale
connected on both devices. Pair with a fresh code, then Safari **Share → Add to
Home Screen**. Open that icon for the standalone app presentation. The header and
composer stay fixed while conversation content scrolls. Reconnection preserves
the request identity and replay cursor so an interrupted stream can recover.

The background task starts at Windows sign-in and prevents automatic idle sleep
while private serving is active. Shutdown, manual sleep, signing out, missing
network connectivity and iOS suspension still interrupt live access. Saved
pairings survive PC-service restart; unused pairing codes do not. An offline
shell does not provide offline model inference. Physical iPhone keyboard,
lock/unlock and cellular handoff still require device qualification.

**Sage Green** is available in Appearance and preserves the original logo.
Theme selection updates the page theme-color and light/dark hints, paints the
safe-area background and restores the saved appearance before first paint.
Phone OS/browser versions control the final status-bar and launch-screen
appearance. Panels respect the device Reduce Motion preference.

Chrome theme-color and Apple standalone behavior follow their supported browser interfaces: [Chrome documentation](https://developer.chrome.com/docs/lighthouse/pwa/themed-omnibox) and [Apple web-app configuration](https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/SafariWebContent/ConfiguringWebApplications/ConfiguringWebApplications.html).
