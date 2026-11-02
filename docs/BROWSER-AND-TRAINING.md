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
are not bundled. Actions include a forward check, two diagnostic adapter steps,
fresh-process checkpoint verification and cooperative Stop. Diagnostic samples
are not an engineering curriculum. Training does not automatically replace Sumi.

The Windows guard requires 12 GiB available RAM before loading and stops below
2 GiB. MLX allocations are capped at 12 GiB and each diagnostic run at 15 minutes.
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
