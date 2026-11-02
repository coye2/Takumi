# Privacy and security

Takumi is a local developer tool. Its backend runs on loopback by default, and its standard data directory is `~/.takumi`. Project tasks and the optional coding engine run with the permissions of your Windows account. Takumi is not an operating-system sandbox.

Local chat inference is sent to your local Ollama service. Public research, GitHub, optional MCP integrations, setup downloads and browser voice features can use external services. Disable research when a prompt contains information that must remain local. Explicit owner-repository reads use your existing GitHub CLI authentication, but vague project references can still be routed to public search; use an explicit repository name and review sensitive prompts.

Use a normal, non-administrator account for everyday work. Inspect commands and proposed changes. Do not expose the local server directly to the internet. Optional phone access is designed for private Tailscale transport with device pairing; public exposure is not supported.

Profiles and logs can contain private material. Review them before exporting or sharing. Never post tokens, full chat databases, pairing codes or private project files in a public issue.

## Reporting a vulnerability

Use [GitHub's private vulnerability reporting](https://github.com/coye2/Takumi/security/advisories/new). Include affected version, a minimal reproduction and the likely impact. Please do not disclose credentials or another person's data. Ordinary bugs belong in [Issues](https://github.com/coye2/Takumi/issues).

This project is in public preview. Security fixes target the current preview; no response-time or long-term-support guarantee is offered.
