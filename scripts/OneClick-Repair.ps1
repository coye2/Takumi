# Repairs a complete, bundled Takumi source tree and installs the native desktop app.
# Does not search for, delete, or forcibly stop existing Takumi installations.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'Continue'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$local = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE 'AppData\Local' }
$logs = Join-Path $local 'Takumi\Logs'
$log = Join-Path $logs ('oneclick-repair-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
$runningTranscript = $false
$failed = $false
try {
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    Start-Transcript -Path $log -Force -ErrorAction Stop | Out-Null
    $runningTranscript = $true
} catch {
    Write-Host "Warning: could not open the log: $($_.Exception.Message)" -ForegroundColor Yellow
}
function Step([string]$label) { Write-Host "`n[TAKUMI REPAIR] $label" -ForegroundColor Cyan }
try {
    Step 'Checking included source (no old-folder lookup required)'
    $title = Join-Path $root 'desktop\Takumi.Desktop\TakumiTitleBar.cs'
    $project = Join-Path $root 'desktop\Takumi.Desktop\Takumi.Desktop.csproj'
    $installer = Join-Path $root 'scripts\Install-Takumi.ps1'
    foreach ($file in @($title, $project, $installer)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "Repair package is incomplete: $file. Use Extract All on the ZIP, then double-click ONE-CLICK-REPAIR.cmd."
        }
    }
    # Validate all PowerShell setup scripts before editing or launching anything.
    foreach ($file in @($PSCommandPath, $installer, (Join-Path $root 'scripts\Build-Windows-EXE.ps1'),
                          (Join-Path $root 'scripts\Ensure-Sumi-1.0.ps1'))) {
        $tokens = $null
        $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($file,[ref]$tokens,[ref]$errors)
        if ($null -ne $errors -and $errors.Count -gt 0) { throw "Syntax error in $file : $($errors[0].Message)" }
    }
    Step 'Checking the custom Windows title bar compiler fix'
    $source = [System.IO.File]::ReadAllText($title)
    $field = '(?m)^\s*internal\s+CaptionSymbol\s+Symbol\s*;\s*$'
    $property = '(?m)^(?<indent>[ \t]*)internal\s+CaptionSymbol\s+Symbol\s*\{\s*get\s*;\s*set\s*;\s*\}[ \t]*$'
    if ([regex]::IsMatch($source,$field)) {
        Write-Host 'Title bar already corrected; no patch needed.' -ForegroundColor Green
    } else {
        $matches = [regex]::Matches($source,$property)
        if ($matches.Count -ne 1 -or $source -notmatch 'class\s+CaptionButton\s*:\s*Control') {
            throw 'Unknown title-bar source layout. No changes were made to the custom control.'
        }
        $match = $matches[0]
        $replacement = $match.Groups['indent'].Value + 'internal CaptionSymbol Symbol;'
        $patched = $source.Remove($match.Index,$match.Length).Insert($match.Index,$replacement)
        if (-not [regex]::IsMatch($patched,$field)) { throw 'Corrected field verification failed; source preserved.' }
        $backup = $title + '.before-repair.bak'
        if (-not (Test-Path -LiteralPath $backup)) { [IO.File]::Copy($title,$backup) }
        $tmp = $title + '.new-' + [guid]::NewGuid().ToString('N')
        try {
            [IO.File]::WriteAllText($tmp,$patched,(New-Object System.Text.UTF8Encoding($false)))
            Move-Item -LiteralPath $tmp -Destination $title -Force -ErrorAction Stop
        } finally {
            if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
        }
        Write-Host "Fixed WFO1000; original source backed up at: $backup" -ForegroundColor Green
    }
    Step 'Compiling and installing Takumi; old app data and model files are preserved'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -Unattended
    $rc = $LASTEXITCODE
    if ($rc -ne 0) { throw "Windows setup returned exit code $rc. See the installer log above and $logs" }
    Step 'Checking installed application shortcut'
    $link = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Takumi (Repaired).lnk'
    if (Test-Path -LiteralPath $link -PathType Leaf) {
        $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
        if (-not (Test-Path -LiteralPath $shortcut.TargetPath -PathType Leaf)) {
            throw 'The new shortcut target is missing. Inspect setup logs; the previous installation was preserved.'
        }
        Write-Host "Verified new desktop shortcut: $link" -ForegroundColor Green
        Write-Host "New native executable: $($shortcut.TargetPath)" -ForegroundColor Green
    } else {
        Write-Host 'Installer completed; repaired desktop shortcut could not be verified. Open the installed EXE from the logged path.' -ForegroundColor Yellow
    }
    Step 'Completed'
    Write-Host 'Use the new Takumi (Repaired) desktop icon, not an old pinned taskbar icon.' -ForegroundColor Green
    Write-Host 'Old locked app folders were kept as backups; chats, models and project data were not deleted.'
} catch {
    $failed = $true
    Write-Host "`nTAKUMI REPAIR FAILED: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray }
    Write-Host 'Existing installation, user data and downloaded models were not intentionally deleted.' -ForegroundColor Yellow
} finally {
    if ($runningTranscript) { try { Stop-Transcript | Out-Null } catch { } }
    Write-Host "`nDiagnostic log: $log" -ForegroundColor Cyan
}
if ($failed) { exit 1 }
exit 0
