# Used by the optional Windows logon task. Runs the local API and bot supervisor.
$ErrorActionPreference='Stop'
$root=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
$env:PYTHONPATH = $root
$env:PYTHONUTF8 = '1'
$env:TAKUMI_TAILSCALE_SERVE = '1'
foreach($launcher in @('py','python')) {
  if (Get-Command $launcher -ErrorAction SilentlyContinue) {
    if ($launcher -eq 'py') { & py -3 -m takumi --port 8765 --no-browser }
    else { & python -m takumi --port 8765 --no-browser }
    exit $LASTEXITCODE
  }
}
throw 'Python 3.11+ is required for Takumi background service.'
