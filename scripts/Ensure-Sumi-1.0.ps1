# Installs Takumi's external local inference and coding dependencies ON THIS PC.
# No third-party executable or model weights are included in the redistributed ZIP.
# Re-running is safe: existing models are reused; interrupted Ollama pulls resume.
[CmdletBinding()]
param(
    [switch]$SkipCodingAgent,
    [switch]$SkipInferenceTest,
    [switch]$CheckOnly,
    [switch]$PreferSmallModel
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'Continue'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$local = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE 'AppData\Local' }
$logDir = Join-Path $local 'Takumi\Logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ('sumi-setup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
$startedTranscript = $false
try { Start-Transcript -Path $log -Force | Out-Null; $startedTranscript = $true } catch {
    Write-Warning ('Could not save transcript: ' + $_.Exception.Message)
}
function Step([string]$value) { Write-Host ("`n[TAKUMI] " + $value) -ForegroundColor Cyan }
function Find-Ollama {
    $candidates = @('ollama.exe', (Join-Path $local 'Programs\Ollama\ollama.exe'),
                    (Join-Path $env:ProgramFiles 'Ollama\ollama.exe'))
    foreach ($file in $candidates) {
        $cmd = Get-Command $file -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -and (Test-Path -LiteralPath $cmd.Source)) { return $cmd.Source }
        if (Test-Path -LiteralPath $file -PathType Leaf) { return $file }
    }
    return $null
}
function Is-ServerReady {
    try {
        $r = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 3
        return [bool]$r.version
    } catch { return $false }
}
function Get-InstalledModelNames {
    $response = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 12
    return @($response.models | ForEach-Object { $_.name })
}
function Assert-EngineVersion([string]$exe) {
    $output = (& $exe --version 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot query local inference runtime version.' }
    $m = [regex]::Match($output, '\d+\.\d+\.\d+')
    if (-not $m.Success) { throw ('Cannot verify the installed local inference runtime version: ' + $output) }
    $actual = [version]$m.Value
    if ($actual -lt [version]'0.40.6') { throw 'This Sumi model requires Ollama 0.40.6 or newer. Upgrade the installed engine and retry.' }
    Write-Host "Local runtime version: $actual"
}
function Use-LighterModelForHardware {
    if ($PreferSmallModel) { return $true }
    try {
        $ram = (Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory
        if ($ram -and $ram -lt 24GB) {
            Write-Warning 'This PC has less than 24 GB of RAM. Selecting Rei 1.0 to avoid excessive paging.'
            return $true
        }
    } catch { Write-Host 'System RAM detection unavailable; leaving full model selected.' }
    $smi = Get-Command 'nvidia-smi.exe' -ErrorAction SilentlyContinue
    if (-not $smi) {
        $candidate = Join-Path $env:ProgramFiles 'NVIDIA Corporation\NVSMI\nvidia-smi.exe'
        if (Test-Path $candidate) { $smi = @{ Source = $candidate } }
    }
    if ($smi) {
        try {
            $result = (& $smi.Source '--query-gpu=memory.total' '--format=csv,noheader,nounits' 2>$null | Select-Object -First 1)
            $match = [regex]::Match([string]$result, '\d+')
            if ($match.Success -and [int]$match.Value -lt 12000) {
                Write-Warning 'This GPU has less than 12 GB of VRAM. Selecting Rei 1.0 rather than overcommitting memory.'
                return $true
            }
        } catch { Write-Host 'GPU memory detection unavailable; leaving full model selected.' }
    }
    return $false
}
function Check-FreeDisk([string]$model) {
    try {
        $drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($local))
        $required = if ($model -eq 'bonsai-2:9b') { 6GB } else { 14GB }
        if ($drive.AvailableFreeSpace -lt $required) {
            throw "Not enough free disk space: Sumi setup needs approximately $([math]::Round($required / 1GB)) GB free before its download. Available: $([math]::Round($drive.AvailableFreeSpace / 1GB, 1)) GB."
        }
    } catch [System.IO.IOException] {
        Write-Warning ('Could not check remaining disk space: ' + $_.Exception.Message)
    }
}
function Try-WinGetEngine {
    $cmd = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
    if (-not $cmd) { Write-Warning 'Windows Package Manager is unavailable. Trying verified official installer.'; return $false }
    Write-Host 'Installing or updating the local runtime using Microsoft WinGet package verification…'
    & $cmd.Source install --id 'Ollama.Ollama' --exact --source winget --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -eq 0) { return $true }
    # WinGet can return nonzero when the package is already installed. Check again.
    if (Find-Ollama) { return $true }
    Write-Warning "WinGet returned $LASTEXITCODE. Trying the signed vendor installer."
    return $false
}
function Install-SignedVendorEngine {
    $downloadUrl = 'https://ollama.com/download/OllamaSetup.exe'
    $temp = Join-Path $env:TEMP ('takumi-ollama-' + [guid]::NewGuid().ToString('N') + '.exe')
    try {
        Step 'Downloading the official signed local runtime installer'
        Invoke-WebRequest -Uri $downloadUrl -OutFile $temp -UseBasicParsing -TimeoutSec 180
        if (-not (Test-Path $temp) -or (Get-Item $temp).Length -lt 1000000) {
            throw 'Vendor installer download was missing or incomplete.'
        }
        $signature = Get-AuthenticodeSignature -FilePath $temp
        if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or
            $signature.SignerCertificate.Subject -notmatch '(?i)ollama') {
            throw 'The official installer did not pass its Ollama Authenticode signature check. No installer was run.'
        }
        $p = Start-Process -FilePath $temp -ArgumentList @('/SILENT','/NORESTART') -PassThru -Wait
        if ($p.ExitCode -ne 0) { throw "Official installer failed with exit code $($p.ExitCode)." }
    } finally { Remove-Item -LiteralPath $temp -ErrorAction SilentlyContinue }
}
function Ensure-Server([string]$engine) {
    if (Is-ServerReady) { return }
    Step 'Starting local inference service'
    # Bind locally. Never publish an unauthenticated model API to the network.
    $env:OLLAMA_HOST = '127.0.0.1:11434'
    $env:OLLAMA_NO_CLOUD = '1'
    $env:OLLAMA_NUM_PARALLEL = '1'
    $env:OLLAMA_MAX_LOADED_MODELS = '1'
    $env:OLLAMA_FLASH_ATTENTION = '1'
    $env:OLLAMA_KV_CACHE_TYPE = 'q8_0'
    Start-Process -FilePath $engine -ArgumentList @('serve') -WindowStyle Hidden | Out-Null
    for ($i = 0; $i -lt 25; $i++) {
        if (Is-ServerReady) { return }
        Start-Sleep -Seconds 1
    }
    throw 'The local inference service did not start on 127.0.0.1:11434. Inspect the Ollama server log and retry.'
}
function Install-CodingAgent {
    # Pinned official upstream binary, installed separately from Takumi's release.
    # Version and hash are from the official GitHub v1.19.5 release asset metadata.
    $destination = Join-Path $local 'Takumi\Tools\opencode.exe'
    if (Test-Path $destination -PathType Leaf) {
        Write-Host 'Coding engine already provisioned for Takumi.'
        return
    }
    if (Get-Command 'opencode.exe' -ErrorAction SilentlyContinue) {
        Write-Host 'An existing coding engine is already available on PATH.'
        return
    }
    $url = 'https://github.com/anomalyco/opencode/releases/download/v1.19.5/opencode-windows-x64.zip'
    $sha = '09a6e693157f0887e504f2cf4357a8cbddc5ead5d0dffb9a269dcc47c48640f5'
    $tempdir = Join-Path $env:TEMP ('takumi-agent-' + [guid]::NewGuid().ToString('N'))
    $archive = Join-Path $tempdir 'opencode.zip'
    $extracted = Join-Path $tempdir 'unpacked'
    New-Item -ItemType Directory -Path $tempdir -Force | Out-Null
    try {
        Step 'Installing the separate local coding engine from verified official release'
        Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing -TimeoutSec 180
        $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $sha) { throw 'Official coding engine archive digest mismatch. Nothing was installed.' }
        Expand-Archive -LiteralPath $archive -DestinationPath $extracted -Force
        $candidates = @(Get-ChildItem -LiteralPath $extracted -Filter 'opencode.exe' -Recurse -File)
        if ($candidates.Count -ne 1) { throw 'Expected exactly one coding engine executable in the verified archive.' }
        $folder = Split-Path -Parent $destination
        New-Item -ItemType Directory -Force -Path $folder | Out-Null
        $staging = Join-Path $folder ('opencode.' + [guid]::NewGuid().ToString('N') + '.exe')
        Copy-Item -LiteralPath $candidates[0].FullName -Destination $staging
        try { Move-Item -LiteralPath $staging -Destination $destination -ErrorAction Stop }
        finally { Remove-Item -LiteralPath $staging -ErrorAction SilentlyContinue }
        Write-Host 'Local coding engine ready.'
    } finally { Remove-Item -LiteralPath $tempdir -Recurse -Force -ErrorAction SilentlyContinue }
}
$failed = $false
try {
    Step 'Sumi 1.0 · complete first-run provisioning'
    Write-Host 'This installs official external dependencies ON THIS PC. No API subscription is required.'
    Write-Host 'Model downloads: Sumi 1.0 approximately 8.3 GB, or Rei 1.0 approximately 3.2 GB on lower-memory PCs.'
    Write-Host "Setup log: $log"
    if ($CheckOnly) {
        $engine = Find-Ollama
        if (-not $engine) { throw 'Local inference engine not installed.' }
        Assert-EngineVersion $engine
        if (-not (Is-ServerReady)) { throw 'Local inference service is not running.' }
        $available = @(Get-InstalledModelNames)
        if ($available -contains 'sumi-1.0:latest') {
            Write-Host 'Sumi 1.0 is ready.' -ForegroundColor Green
        } elseif ($available -contains 'rei-1.0:latest') {
            Write-Host 'Rei 1.0 is ready on this lower-memory PC.' -ForegroundColor Green
        } else { throw 'Neither Sumi 1.0 nor Rei 1.0 is installed.' }
    } else {
        $engine = Find-Ollama
        if (-not $engine) {
            if (-not (Try-WinGetEngine)) { Install-SignedVendorEngine }
            $engine = Find-Ollama
        }
        if (-not $engine) { throw 'Local inference runtime could not be found after installation. Sign out/in or rerun setup.' }
        try { Assert-EngineVersion $engine }
        catch {
            if ($_.Exception.Message -notmatch '0\.40\.6 or newer') { throw }
            Step 'Upgrading an older local runtime for Sumi compatibility'
            $winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
            if (-not $winget) { throw 'An older local inference runtime needs an update. Install the current version from https://ollama.com/download/windows then retry.' }
            & $winget.Source upgrade --id 'Ollama.Ollama' --exact --silent --accept-source-agreements --accept-package-agreements --disable-interactivity
            if ($LASTEXITCODE -ne 0) { throw 'The installed runtime is too old and its automatic update did not succeed.' }
            $engine = Find-Ollama
            Assert-EngineVersion $engine
        }
        Ensure-Server $engine
        if (Use-LighterModelForHardware) { $PreferSmallModel = $true }
        $available = @(Get-InstalledModelNames)
        $targetTag = if ($PreferSmallModel) { 'rei-1.0:latest' } else { 'sumi-1.0:latest' }
        if ($available -notcontains $targetTag) {
            $base = if ($PreferSmallModel) { 'bonsai-2:9b' } else { 'bonsai-2:27b' }
            if ($available -notcontains $base) {
                Check-FreeDisk $base
                Step "Downloading $base (the model runtime resumes interrupted downloads)"
                # One built-in retry; repeat the full setup to resume an interrupted download.
                $worked = $false
                for ($try = 1; $try -le 2; $try++) {
                    & $engine pull $base
                    if ($LASTEXITCODE -eq 0) { $worked = $true; break }
                    Write-Warning "Download attempt $try failed. Retrying if connectivity allows."
                    Start-Sleep -Seconds 2
                }
                if (-not $worked) { throw "Model download failed; retry Setup-Sumi.cmd when online. Existing model files were retained." }
            }
            $modelfile = Join-Path $root 'models\Sumi-1.0.Modelfile'
            if (-not (Test-Path $modelfile)) { throw 'Sumi model profile is missing from the extracted Takumi package.' }
            Step 'Registering the selected Takumi model (shared upstream weights)'
            if ($PreferSmallModel) {
                $modelfile = Join-Path $root 'models\Rei-1.0.Modelfile'
                if (-not (Test-Path -LiteralPath $modelfile)) { throw 'Rei 1.0 profile is missing from this installation.' }
                & $engine create 'rei-1.0' -f $modelfile
                if ($LASTEXITCODE -ne 0) { throw 'Rei 1.0 registration failed.' }
            } else {
                & $engine create 'sumi-1.0' -f $modelfile
                if ($LASTEXITCODE -ne 0) { throw 'Sumi 1.0 registration failed.' }
            }
        }
        $available = @(Get-InstalledModelNames)
        $registeredName = $targetTag
        if ($available -notcontains $registeredName) { throw 'The selected Takumi model registration was not visible in the local model list.' }
        if (-not $SkipInferenceTest) {
            Step 'Testing a real response from the selected model'
            $body = @{model=$registeredName;messages=@(@{role='user';content='Reply with READY.'});stream=$false;options=@{num_predict=24;num_ctx=1024}} | ConvertTo-Json -Depth 7 -Compress
            $result = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:11434/api/chat' -Body $body -ContentType 'application/json' -TimeoutSec 180
            if (-not $result.message -or -not $result.message.content -and -not $result.message.thinking) {
                throw 'Sumi is registered, but a local inference response could not be verified.'
            }
            Write-Host 'Local response verified.' -ForegroundColor Green
        }
        if (-not $SkipCodingAgent) {
            Install-CodingAgent
        }
        Step 'Setup complete'
        Write-Host ('Ready: ' + $registeredName) -ForegroundColor Green
        Write-Host 'All model inference remains on the local PC. Third-party engine binaries are not shipped in the Takumi ZIP.'
    }
} catch {
    $failed = $true
    Write-Host ('SETUP FAILED: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Your existing Takumi files and any previously downloaded model blobs were preserved.' -ForegroundColor Yellow
    Write-Host 'Rerun Setup-Sumi.cmd or consult the setup log.' -ForegroundColor Yellow
} finally {
    if ($startedTranscript) { try { Stop-Transcript | Out-Null } catch { } }
    Write-Host "Setup log: $log"
}
if ($failed) { exit 1 }
exit 0
