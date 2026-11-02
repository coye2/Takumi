# External transactional updater. Runs only after the user explicitly chooses Install.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$StagedDirectory,
    [Parameter(Mandatory=$true)][string]$InstalledDirectory,
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$Version
)
$ErrorActionPreference='Stop'
$logDir=Join-Path $env:LOCALAPPDATA 'Takumi\Logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log=Join-Path $logDir ('update-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
Start-Transcript -Path $log | Out-Null
try {
  if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid update version' }
  if (-not (Test-Path (Join-Path $StagedDirectory 'desktop\Takumi.Desktop\Takumi.Desktop.csproj'))) { throw 'Update source missing' }
  $expected=Join-Path $env:LOCALAPPDATA 'Programs\Takumi'
  if ([IO.Path]::GetFullPath($InstalledDirectory).TrimEnd('\') -ine [IO.Path]::GetFullPath($expected).TrimEnd('\')) {
    throw 'Updater only replaces the current-user Takumi installation'
  }
  Write-Host "Waiting for Takumi to close so v$Version can be installed..."
  $deadline=(Get-Date).AddMinutes(3)
  while (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
    if ((Get-Date) -ge $deadline) { throw 'Takumi did not close in three minutes; no files were changed' }
    Start-Sleep -Seconds 2
  }
  if (@(Get-Process -Name 'Takumi' -ErrorAction SilentlyContinue).Count -gt 0) {
    throw 'Other Takumi windows are still running. Close them and retry.'
  }
  # Compile the exact downloaded source into a separate output tree before
  # touching the running installation, preserving the previous build on error.
  & (Join-Path $StagedDirectory 'scripts\Build-Windows-EXE.ps1')
  if ($LASTEXITCODE -ne 0) { throw 'Native update build failed' }
  $built=Join-Path $StagedDirectory 'dist\Takumi-win-x64'
  if (-not (Test-Path (Join-Path $built 'Takumi.exe'))) { throw 'No Takumi.exe in the new build' }
  $parent=Split-Path -Parent $InstalledDirectory
  $stage=Join-Path $parent ('Takumi.updating-'+[guid]::NewGuid().ToString('N'))
  $backup=Join-Path $parent ('Takumi.previous-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
  Copy-Item -LiteralPath $built -Destination $stage -Recurse
  $hadOriginal=Test-Path $InstalledDirectory
  if ($hadOriginal) { Move-Item -LiteralPath $InstalledDirectory -Destination $backup -ErrorAction Stop }
  try { Move-Item -LiteralPath $stage -Destination $InstalledDirectory -ErrorAction Stop }
  catch {
    if ($hadOriginal -and -not (Test-Path $InstalledDirectory)) { Move-Item -LiteralPath $backup -Destination $InstalledDirectory }
    throw
  }
  Write-Host "Takumi v$Version installed. Previous version retained at $backup"
  Start-Process -FilePath (Join-Path $InstalledDirectory 'Takumi.exe')
} catch { Write-Host "Takumi update failed: $($_.Exception.Message)" -ForegroundColor Red; exit 1 }
finally { Stop-Transcript | Out-Null }
