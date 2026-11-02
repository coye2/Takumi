# Enables a private tailnet-only connection. Do not use public Tailscale Funnel.
# Requires Tailscale logged in on both Windows and iPhone, and HTTPS enabled.
[CmdletBinding()]
param([switch]$Confirmed)
$ErrorActionPreference='Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
  throw 'Tailscale is not installed. Install it from https://tailscale.com/download, sign in on Windows and your iPhone, then rerun.'
}
Write-Host 'Takumi iPhone setup: private tailnet only.' -ForegroundColor Cyan
Write-Host 'This will create a logon task for Takumi, run the local service on port 8765, and use HTTPS Tailscale Serve.'
if (-not $Confirmed) {
  $answer=Read-Host 'Continue? [y/N]'
  if ($answer -notin @('y','Y','yes','YES')) { exit 0 }
}
$taskName='Takumi Background'
$script=Join-Path $PSScriptRoot 'Launch-Background.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$script+'"')
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Takumi private phone workspace; starts after sign-in and recovers after failure' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
try {
  & tailscale serve --bg 8765
  if ($LASTEXITCODE -ne 0) { throw 'Tailscale Serve failed; inspect tailscale serve status.' }
} catch { Write-Warning "Tailscale Serve was not activated: $_"; exit 1 }
Write-Host 'Private HTTPS endpoint:' -ForegroundColor Green
& tailscale serve status
Write-Host 'On your iPhone: connect to Tailscale, open the displayed HTTPS .ts.net address in Safari, and Add to Home Screen.'
Write-Host 'On your PC: open Takumi settings > iPhone access > Create pairing code.'
Write-Host 'Do NOT turn on Funnel. Remote devices have significant authority over the selected workspace.'
