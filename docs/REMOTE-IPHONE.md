# Takumi on iPhone — private access from anywhere

Takumi's PC backend stays on `127.0.0.1:8765`, never a public network interface. The iPhone connects through **Tailscale Serve HTTPS** inside your own tailnet, plus a per-device Takumi pairing token. This setup does not require hosting, domain registration or a public forwarding port. A PC with power, internet access, and a signed-in Windows user session is required.

1. Install and sign into Tailscale on **Windows and iPhone**, using the same tailnet. Enable MagicDNS and HTTPS certificates for your tailnet if prompted.
2. On Windows, open PowerShell in the extracted Takumi directory and run:
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Enable-iPhone-PWA.ps1
   ```
   The script asks before creating the `Takumi Background` logon task and enabling `tailscale serve --bg 8765`. It does **not** enable `tailscale funnel` or expose a public website.
3. Confirm the `https://<your-pc>.<your-tailnet>.ts.net/` address printed by `tailscale serve status` is reachable on your iPhone while Tailscale is on.
4. In the Windows Takumi app, go to **Settings → Private iPhone access → Create pairing code**. Enter the 8-digit code on the iPhone. This creates a revocable device token stored in that phone's local browser storage.
5. In Safari, select **Share → Add to Home Screen**. Takumi will then launch in a standalone full-screen PWA. Source and game files remain on your Windows PC; the phone is a remote control, not a standalone model runtime.

## Security and limitations

- Access requires your tailnet account **and** Takumi's pairing code. Revoke a device in the PC settings if you lose it.
- **Do not turn on Tailscale Funnel**, reverse-proxy this service to the public Internet, or disable the private authentication check.
- The workstation must be powered and connected, with the background task running under the signed-in Windows user. The script creates a *logon* task, not an unattended system service.
- iPhone Safari's installable PWA requires HTTPS; a local IP or plain HTTP page will not work for secure remote features.
- The PWA caches only app-shell assets for offline display. Remote coding, chat, Discord management and PC actions require connectivity to your computer.
- Tailscale plans and supported features may change. The setup uses the currently documented `tailscale serve --bg` command; check [Tailscale Serve docs](https://tailscale.com/docs/reference/tailscale-cli/serve) if your version differs.
- Use a separate, non-administrator Windows account for automation if you plan to control the PC from remote devices. Takumi's agent permission rules are **not** an operating-system sandbox.
