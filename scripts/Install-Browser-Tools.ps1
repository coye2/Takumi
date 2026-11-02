# Install the optional Microsoft browser connector outside the Takumi release.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Install Node.js 18 or newer first.' }
$destination = Join-Path $env:USERPROFILE '.takumi\tools\browser'
New-Item -ItemType Directory -Force -Path $destination | Out-Null
& npm.cmd install --prefix $destination --save-exact --ignore-scripts '@playwright/mcp@0.0.84'
if ($LASTEXITCODE -ne 0) { throw 'Browser tool installation failed.' }
Write-Host 'Browser tools installed. Enable Browser in Takumi Tools & connections. Microsoft Edge must be installed.'
