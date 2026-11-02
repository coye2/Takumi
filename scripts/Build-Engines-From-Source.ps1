# Optional. Compiles the third-party engines on the user's OWN Windows PC.
# The Takumi release contains no third-party binary and does not redistribute these builds.
[CmdletBinding()]
param(
  [ValidateSet('OpenCode','Ollama','Both')][string] $Engine = 'OpenCode',
  [string] $OpenCodeRef = 'v1.19.5',
  [string] $OllamaRef = 'v0.40.6',
  [ValidateSet('cuda_v12','cuda_v13','vulkan','cpu')][string] $Backend = 'cuda_v13'
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:USERPROFILE '.takumi\upstream-src'
New-Item -Force -ItemType Directory -Path $root | Out-Null
function Require-Command([string] $name, [string] $help) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { throw "Missing $name. $help" }
}
function Run-External([string] $name, [string[]] $arguments) {
  & $name @arguments
  if ($LASTEXITCODE -ne 0) { throw "$name failed (exit code $LASTEXITCODE). See the command output above." }
}
Require-Command git 'Install Git for Windows first.'
if ($Engine -eq 'Both' -or $Engine -eq 'OpenCode') {
  Require-Command bun 'Install Bun 1.3+ from https://bun.sh first. This is a build-time prerequisite, not shipped with Takumi.'
  $checkout = Join-Path $root 'opencode'
  if (-not (Test-Path $checkout)) {
    Run-External git @('clone','--depth','1','--branch',$OpenCodeRef,'https://github.com/anomalyco/opencode.git',$checkout)
  } else {
    Write-Host "Reusing existing OpenCode source at $checkout (not resetting it)."
  }
  Push-Location $checkout
  try {
    Run-External bun @('install')
    Run-External bun @('run','packages/opencode/script/build.ts','--single')
    $executables = @(Get-ChildItem -Path (Join-Path $checkout 'packages/opencode/dist') -Filter 'opencode.exe' -File -Recurse -ErrorAction SilentlyContinue)
    if ($executables.Count -eq 0) { throw 'The source build returned without an opencode.exe. Check the upstream build output.' }
    $exe = $executables | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
    $settingsPath = Join-Path $env:USERPROFILE '.takumi\tools.json'
    $settings = @{}
    if (Test-Path $settingsPath) {
      try { $old = Get-Content $settingsPath -Raw | ConvertFrom-Json; if ($old.opencode) { $settings.opencode = $old.opencode } } catch {}
    }
    $settings.opencode = $exe
    $settings | ConvertTo-Json | Set-Content -Path $settingsPath -Encoding UTF8
    Write-Host "OpenCode built from source. Takumi will use: $exe"
  } finally { Pop-Location }
}
if ($Engine -eq 'Both' -or $Engine -eq 'Ollama') {
  Require-Command cmake 'Install CMake 3.24+, Visual Studio 2022 C++ build tools and (for CUDA) NVIDIA CUDA SDK.'
  $checkout = Join-Path $root 'ollama'
  if (-not (Test-Path $checkout)) {
    Run-External git @('clone','--depth','1','--branch',$OllamaRef,'https://github.com/ollama/ollama.git',$checkout)
  } else { Write-Host "Reusing existing Ollama source at $checkout (not resetting it)." }
  Push-Location $checkout
  try {
    $args = @('-S','.','-B','build')
    if ($Backend -ne 'cpu') {
      $args += "-DOLLAMA_LLAMA_BACKENDS=$Backend"
      if ($Backend -like 'cuda_*') { $args += '-DCMAKE_CUDA_ARCHITECTURES=89' } # RTX 4060 Ti = Ada sm_89
    }
    Run-External cmake $args
    Run-External cmake @('--build','build','--parallel','8','--config','Release')
    Write-Host "Ollama source build is at $checkout\build."
    Write-Host 'Do not run two Ollama servers on port 11434. Stop the existing server before using a source-built instance.'
    Write-Host 'See https://github.com/ollama/ollama/blob/main/docs/development.md for GPU runtime details.'
  } finally { Pop-Location }
}
Write-Host 'Third-party binaries remain in your local source build directories. None are copied into Takumi.'
