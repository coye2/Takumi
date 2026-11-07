# Takumi native installer with durable diagnostic logs and visible errors.
# Downloads separately installed official inference/coding engines when needed; never bundles them.
[CmdletBinding()]
param([switch]$NoLaunch, [switch]$SkipModelSetup, [switch]$Unattended)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$local = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE 'AppData\Local' }
$logDir = Join-Path $local 'Takumi\Logs'
$logPath = Join-Path $logDir ('setup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
$transcribing = $false
$failed = $false
try {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Start-Transcript -Path $logPath -Force -ErrorAction Stop | Out-Null
    $transcribing = $true
} catch {
    Write-Host "Could not start the log transcript: $($_.Exception.Message)" -ForegroundColor Yellow
}
function Banner([string]$label) { Write-Host "`n=== $label ===" -ForegroundColor Cyan }
function Resolve-Python {
    $options = @('py.exe','python.exe','python3.exe',
        (Join-Path $local 'Programs\Python\Python313\python.exe'),
        (Join-Path $local 'Programs\Python\Python312\python.exe'),
        (Join-Path $local 'Programs\Python\Python311\python.exe'),
        (Join-Path $env:ProgramFiles 'Python313\python.exe'))
    foreach ($candidate in $options) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $command -and -not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            if ($candidate -eq 'py.exe') {
                $version = & $candidate -3 -c 'import sys;print(sys.version_info.major * 100 + sys.version_info.minor)' 2>$null
            } else {
                $version = & $candidate -c 'import sys;print(sys.version_info.major * 100 + sys.version_info.minor)' 2>$null
            }
            if ($LASTEXITCODE -eq 0 -and [int]$version -ge 311) { return $candidate }
        } catch { }
    }
    return $null
}
function Resolve-Dotnet {
    $programFiles = [Environment]::GetFolderPath('ProgramFiles')
    $options = @('dotnet.exe', (Join-Path $programFiles 'dotnet\dotnet.exe'))
    foreach ($candidate in $options) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $command -and -not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            $versions = @(& $candidate --list-sdks)
            if ($LASTEXITCODE -eq 0 -and @($versions -match '^10\.').Count -gt 0) { return $candidate }
        } catch { }
    }
    return $null
}
function Request-Install([string]$name, [string]$package) {
    $winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "$name is missing. Windows Package Manager (App Installer) is required for automatic setup. Install it from Microsoft, then rerun."
    }
    Write-Host "Automatically installing $name using Microsoft's WinGet catalog..."
    & $winget.Source install --exact --id $package --source winget --silent --disable-interactivity --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "WinGet failed to install $name (exit code $LASTEXITCODE). Inspect the setup log and retry." }
}
function WebView2-Installed {
    # Official Microsoft Edge WebView2 runtime client GUID; check per-machine and per-user locations.
    $id = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    foreach ($key in @("HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$id",
                      "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$id",
                      "HKCU:\Software\Microsoft\EdgeUpdate\Clients\$id")) {
        try {
            $version = (Get-ItemProperty -LiteralPath $key -Name 'pv' -ErrorAction Stop).pv
            if ($version -and $version -ne '0.0.0.0') { return $true }
        } catch { }
    }
    return $false
}
function Ensure-WebView2 {
    if (WebView2-Installed) { Write-Host 'WebView2 desktop runtime already available.'; return }
    Request-Install 'Microsoft Edge WebView2 Runtime' 'Microsoft.EdgeWebView2Runtime'
    if (-not (WebView2-Installed)) {
        throw 'WebView2 runtime installation could not be verified. Takumi.exe requires WebView2; inspect the setup log.'
    }
    Write-Host 'WebView2 runtime installed and verified.' -ForegroundColor Green
}

try {
    Banner 'Takumi - Native Windows installation / repair'
    Write-Host "Source directory: $root"
    Write-Host "Setup log: $logPath"
    Write-Host 'The application is built locally from its own source. No third-party AI binaries are bundled.'
    if (-not (Test-Path -LiteralPath (Join-Path $root 'desktop\Takumi.Desktop\Takumi.Desktop.csproj'))) {
        throw 'The source files are missing. Extract the COMPLETE Takumi ZIP before running setup.'
    }
    $python = Resolve-Python
    if (-not $python) { Request-Install 'Python 3.11+' 'Python.Python.3.13'; $python = Resolve-Python }
    if (-not $python) { throw 'Python was installed but is not detected. Sign out and back in, or reopen the installer.' }
    Write-Host "Python ready: $python"

    $dotnet = Resolve-Dotnet
    if (-not $dotnet) { Request-Install '.NET 10 SDK' 'Microsoft.DotNet.SDK.10'; $dotnet = Resolve-Dotnet }
    if (-not $dotnet) { throw '.NET 10 SDK was installed but is not detectable. Reopen the installer or repair the official SDK installation.' }
    $foundDotnet = Get-Command $dotnet -ErrorAction SilentlyContinue
    $dotnetPath = if ($foundDotnet) { $foundDotnet.Source } elseif (Test-Path -LiteralPath $dotnet) { (Resolve-Path -LiteralPath $dotnet).Path } else { $null }
    if ($dotnetPath) { $env:PATH = "$(Split-Path -Parent $dotnetPath);$env:PATH" }
    Write-Host ".NET SDK ready: $dotnet"
    Ensure-WebView2

    $running = @(Get-Process -Name 'Takumi' -ErrorAction SilentlyContinue)
    if ($running.Count -gt 0) {
        Write-Host 'A prior Takumi.exe is running. Preserving it and preparing an isolated install if the old folder is locked.' -ForegroundColor Yellow
    }
    Banner 'Compiling Takumi.exe'
    & (Join-Path $PSScriptRoot 'Build-Windows-EXE.ps1') -DotnetExe $dotnet
    if (-not $?) { throw 'Native Windows build script did not complete successfully.' }
    $src = Join-Path $root 'dist\Takumi-win-x64'
    $native = Join-Path $src 'Takumi.exe'
    if (-not (Test-Path -LiteralPath $native)) { throw 'Compilation did not produce the native executable.' }

    Banner 'Installing application'
    $installParent = Join-Path $local 'Programs'
    $canonical = Join-Path $installParent 'Takumi'
    $dest = $canonical
    $staging = Join-Path $installParent ('Takumi.new-' + [guid]::NewGuid().ToString('N'))
    $backup = Join-Path $installParent ('Takumi.previous-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,6))
    New-Item -ItemType Directory -Force -Path $installParent | Out-Null
    $savedPrevious = $false
    try {
        Copy-Item -LiteralPath $src -Destination $staging -Recurse -Force
        if (-not (Test-Path -LiteralPath (Join-Path $staging 'Takumi.exe') -PathType Leaf)) {
            throw 'Staging installation is missing Takumi.exe. Existing installation untouched.'
        }
        # Verify staged content before retiring any current executable.
        if ((Get-FileHash -LiteralPath (Join-Path $staging 'Takumi.exe') -Algorithm SHA256).Hash -ne
            (Get-FileHash -LiteralPath $native -Algorithm SHA256).Hash) {
            throw 'Staged Takumi.exe does not match the compiled executable.'
        }
        foreach ($relative in @('takumi\server.py','takumi\core.py','web\app.js','web\styles.css')) {
            if ((Get-FileHash -LiteralPath (Join-Path $src $relative) -Algorithm SHA256).Hash -ne
                (Get-FileHash -LiteralPath (Join-Path $staging $relative) -Algorithm SHA256).Hash) {
                throw "Staged source checksum mismatch: $relative"
            }
        }
        $oldLocked = $false
        if (Test-Path -LiteralPath $canonical) {
            try {
                $oldItem = Get-Item -LiteralPath $canonical -Force -ErrorAction Stop
                if (($oldItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw 'Existing path is a link or junction; preserve it and install side by side.'
                }
                Move-Item -LiteralPath $canonical -Destination $backup -ErrorAction Stop
                $savedPrevious = $true
            } catch {
                # Never kill Discord/Python agents or delete locked application directories.
                $oldLocked = $true
                Write-Host 'Previous Takumi folder is in use; leaving it untouched.' -ForegroundColor Yellow
            }
        }
        if ($oldLocked) {
            $dest = Join-Path $installParent ('Takumi-Repaired-1.6.0-' + [guid]::NewGuid().ToString('N').Substring(0,8))
            Write-Host "Installing corrected app beside the locked installation: $dest"
        }
        try {
            Move-Item -LiteralPath $staging -Destination $dest -ErrorAction Stop
        } catch {
            if ($savedPrevious -and (Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $canonical)) {
                Move-Item -LiteralPath $backup -Destination $canonical -ErrorAction SilentlyContinue
            }
            throw
        }
        try {
            $installedNative = Join-Path $dest 'Takumi.exe'
            $expectedHash = (Get-FileHash -LiteralPath $native -Algorithm SHA256).Hash
            $actualHash = (Get-FileHash -LiteralPath $installedNative -Algorithm SHA256).Hash
            if ($expectedHash -ne $actualHash) { throw 'Installed executable checksum does not match compiled executable.' }
            foreach ($relative in @('takumi\server.py','takumi\core.py','web\app.js','web\styles.css')) {
                $sourceFile = Join-Path $src $relative
                $installedFile = Join-Path $dest $relative
                if ((Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash -ne
                    (Get-FileHash -LiteralPath $installedFile -Algorithm SHA256).Hash) {
                    throw "Installed source checksum mismatch: $relative"
                }
            }
        } catch {
            if ($savedPrevious -and $dest -eq $canonical -and (Test-Path -LiteralPath $backup)) {
                $failedCopy = Join-Path $installParent ('Takumi.failed-' + [guid]::NewGuid().ToString('N'))
                try {
                    if (Test-Path -LiteralPath $dest) {
                        Move-Item -LiteralPath $dest -Destination $failedCopy -ErrorAction Stop
                    }
                    Move-Item -LiteralPath $backup -Destination $canonical -ErrorAction Stop
                    Write-Host 'Previous application restored after verification failure.' -ForegroundColor Yellow
                } catch {
                    Write-Host 'Automatic rollback failed; the previous app remains in the logged backup folder.' -ForegroundColor Yellow
                }
            }
            throw
        }
        Write-Host 'Native EXE and application source files verified.' -ForegroundColor Green
        if ($savedPrevious) { Write-Host "Old Takumi backup retained: $backup" -ForegroundColor Yellow }
    } finally {
        if (Test-Path -LiteralPath $staging) {
            try { Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction Stop } catch {
                Write-Host 'A temporary staging directory could not be cleaned; it is safe to leave it for now.' -ForegroundColor Yellow
            }
        }
    }
    $exe = Join-Path $dest 'Takumi.exe'
    Banner 'Creating app shortcuts'
    try {
        $shell = New-Object -ComObject WScript.Shell
        foreach ($shortcutPath in @((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Takumi.lnk'),
                                  (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Takumi.lnk'))) {
            $shortcut = $shell.CreateShortcut($shortcutPath)
            $shortcut.TargetPath = $exe
            $shortcut.WorkingDirectory = $dest
            $shortcut.IconLocation = "$exe,0"
            $shortcut.Description = 'Takumi - Native AI workspace'
            $shortcut.Save()
        }
    } catch { Write-Host "Shortcut warning: $($_.Exception.Message)" -ForegroundColor Yellow }
    # If Windows kept an old pinned taskbar icon, provide a distinct verified entry.
    try {
        $shell = New-Object -ComObject WScript.Shell
        $fixedLink = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Takumi (Repaired).lnk'
        $shortcut = $shell.CreateShortcut($fixedLink)
        $shortcut.TargetPath = $exe
        $shortcut.WorkingDirectory = $dest
        $shortcut.IconLocation = "$exe,0"
        $shortcut.Save()
    } catch { Write-Host "Could not create the extra repaired shortcut: $($_.Exception.Message)" -ForegroundColor Yellow }
    Write-Host "`nInstalled Takumi.exe: $exe" -ForegroundColor Green
    Write-Host 'Use the Takumi desktop shortcut next time. It opens a native window, not a browser tab.'
    if (-not $SkipModelSetup) {
        Banner 'Provisioning Sumi 1.1 or Rei 1.1 and local coding dependencies'
        Write-Host 'The setup selects Sumi 1.1 (~8.3 GB) or Rei 1.1 (~3.2 GB) based on detected hardware.'
        Write-Host 'The setup never uses a paid model API, and external dependencies are installed separately.'
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dest 'scripts\Ensure-Sumi-1.0.ps1')
        if ($LASTEXITCODE -ne 0) {
            throw ('Takumi.exe was installed successfully, but Sumi 1.1 is NOT ready. ' +
                   'The model setup error is in the log above. Rerun SETUP-SUMI.cmd from the installed Takumi folder ' +
                   'once the connection or dependency issue has been corrected.')
        }
        Write-Host 'Selected Takumi model installed and inference verified. Open Models to see its name.' -ForegroundColor Green
    } else {
        Write-Host 'Model provisioning explicitly skipped. Setup can be resumed using SETUP-SUMI.cmd.' -ForegroundColor Yellow
    }
    if (-not $NoLaunch) {
        $started = Start-Process -FilePath $exe -WorkingDirectory $dest -PassThru -ErrorAction Stop
        Start-Sleep -Seconds 2
        $started.Refresh()
        if ($started.HasExited -and $started.ExitCode -ne 0) {
            throw "Takumi.exe exited immediately (code $($started.ExitCode)). Inspect the new desktop log under $logDir."
        }
        Write-Host "Launched repaired Takumi.exe (PID $($started.Id)). Desktop errors are recorded under $logDir."
    }
} catch {
    $failed = $true
    Banner 'INSTALLATION FAILED'
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($_.ScriptStackTrace) { Write-Host "At: $($_.ScriptStackTrace)" -ForegroundColor DarkGray }
    Write-Host 'Your existing installation has been preserved where possible.' -ForegroundColor Yellow
} finally {
    if ($transcribing) { try { Stop-Transcript | Out-Null } catch { } }
    Write-Host "`nSetup log: $logPath" -ForegroundColor Cyan
    if ($failed) {
        Write-Host 'The window will stay open so you can read the error.' -ForegroundColor Yellow
    } else {
        Write-Host 'Setup completed. You can close this window.' -ForegroundColor Green
    }
    if (-not $Unattended) { try { Read-Host 'Press Enter to close this installer' | Out-Null } catch { } }
}
if ($failed) { exit 1 }
