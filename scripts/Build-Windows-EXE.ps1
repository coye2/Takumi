# Build Takumi's own Windows host. No third-party AI executables or weights are included.
[CmdletBinding()]
param(
    [ValidateSet('win-x64','win-arm64')][string]$Runtime = 'win-x64',
    [string]$DotnetExe = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$project = Join-Path $root 'desktop\Takumi.Desktop\Takumi.Desktop.csproj'
$outputRoot = Join-Path $root 'dist'
$output = Join-Path $outputRoot ('Takumi-' + $Runtime)
$staging = Join-Path $outputRoot ('.takumi-building-' + [guid]::NewGuid().ToString('N'))
$backup = Join-Path $outputRoot ('.takumi-previous-' + [guid]::NewGuid().ToString('N'))
$allowedBuildRoot = [IO.Path]::GetFullPath($outputRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
foreach ($targetPath in @($output, $staging, $backup)) {
    if (-not [IO.Path]::GetFullPath($targetPath).StartsWith($allowedBuildRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Build output must stay inside the checkout dist directory.'
    }
}
if (-not (Test-Path -LiteralPath $project)) { throw "Missing C# project: $project. Extract the full ZIP." }
if (-not (Get-Command $DotnetExe -ErrorAction SilentlyContinue) -and -not (Test-Path -LiteralPath $DotnetExe)) {
    throw 'The .NET 10 SDK was not found. Install it from Microsoft and rerun the installer.'
}
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
$env:DOTNET_NOLOGO = '1'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = '1'
$createdBackup = $false
try {
    Write-Host 'Building native Takumi.exe with .NET...' -ForegroundColor Cyan
    & $DotnetExe publish $project --configuration Release --runtime $Runtime --self-contained true '-p:PublishSingleFile=true' '-p:IncludeNativeLibrariesForSelfExtract=true' '-p:DebugType=None' '-p:DebugSymbols=false' --output $staging
    if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed (exit code $LASTEXITCODE). Read the installation log for the compiler error." }
    foreach ($folder in @('web','takumi','assets','scripts','expertise','models','docs','licenses')) {
        $path = Join-Path $root $folder
        if (-not (Test-Path -LiteralPath $path)) { throw "Application source folder missing: $folder" }
        Copy-Item -LiteralPath $path -Destination (Join-Path $staging $folder) -Recurse -Force
    }
    foreach ($name in @('README.md','THIRD_PARTY.md','LICENSE','BRANDING.md','CHANGELOG.md','SETUP-SUMI.cmd','SETUP-REI.cmd')) {
        Copy-Item -LiteralPath (Join-Path $root $name) -Destination (Join-Path $staging $name) -Force
    }
    Get-ChildItem -LiteralPath $staging -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $staging -Recurse -File | Where-Object { $_.Extension -in @('.pyc','.pyo') } | Remove-Item -Force
    $stagedExe = Join-Path $staging 'Takumi.exe'
    if (-not (Test-Path -LiteralPath $stagedExe)) { throw 'dotnet publish completed without producing Takumi.exe.' }
    if (Test-Path -LiteralPath $output) {
        Move-Item -LiteralPath $output -Destination $backup -ErrorAction Stop
        $createdBackup = $true
    }
    try {
        Move-Item -LiteralPath $staging -Destination $output -ErrorAction Stop
    } catch {
        if ($createdBackup -and (Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $output)) {
            Move-Item -LiteralPath $backup -Destination $output -ErrorAction SilentlyContinue
            $createdBackup = $false
        }
        throw
    }
    if ($createdBackup -and (Test-Path -LiteralPath $backup)) { Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue }
    Write-Host "Native build succeeded: $(Join-Path $output 'Takumi.exe')" -ForegroundColor Green
} finally {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue }
}
