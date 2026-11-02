# Only publishes after YOU explicitly run this file and approve the public/private selection.
[CmdletBinding()]
param([string] $RepositoryName = 'Takumi',[ValidateSet('public','private')][string] $Visibility = 'public', [string] $ReleaseTag = 'v1.5.0')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
foreach ($exe in @('git','gh')) {
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { throw "Missing $exe. Install Git and the GitHub CLI first." }
}
& gh auth status
if ($LASTEXITCODE -ne 0) { throw 'First run gh auth login and sign in to GitHub.' }
Write-Host "About to publish source folder: $root"
Write-Host "Repository: $RepositoryName. Visibility: $Visibility. No third-party binaries or model weights are included."
Write-Host "Conversations are stored in ~/.takumi and are not in this source folder."
$reply = Read-Host 'Type PUBLISH to continue'
if ($reply -cne 'PUBLISH') { Write-Host 'Canceled.'; exit 0 }
Push-Location $root
try {
  if (-not (Test-Path '.git')) {
    & git init -b main
    if ($LASTEXITCODE -ne 0) { throw 'Git init failed.' }
    & git add .
    if ($LASTEXITCODE -ne 0) { throw 'Git stage failed.' }
    & git commit -m 'Initial Takumi source release'
    if ($LASTEXITCODE -ne 0) { throw 'Git commit failed; configure git user.name/user.email first.' }
  } else {
    Write-Host 'Existing Git repository detected. No automatic commit or force-push will occur.'
    $pending = & git status --porcelain=v1
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect working tree status' }
    if ($pending) { throw 'Local changes are not committed. Review and commit the intended release before publishing.' }
    $hasCommit = & git rev-parse --verify HEAD 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $hasCommit) { throw 'Existing Git checkout has no valid commit to publish.' }
  }
  & gh repo create $RepositoryName "--$Visibility" --source . --remote origin --push
  if ($LASTEXITCODE -ne 0) { throw 'GitHub publish failed. Inspect your local repository and retry manually.' }
  Write-Host 'Source published. Checking official release tag...'
  if ($ReleaseTag -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$') { throw 'Release tag must be vMAJOR.MINOR.PATCH' }
  & git tag --list $ReleaseTag
  if ($LASTEXITCODE -ne 0) { throw 'Could not inspect Git tags' }
  $tagExists = (& git tag --list $ReleaseTag) -eq $ReleaseTag
  if (-not $tagExists) {
    & git tag -a $ReleaseTag -m "Takumi $ReleaseTag source release"
    if ($LASTEXITCODE -ne 0) { throw 'Could not create release tag' }
  }
  & git push origin $ReleaseTag
  if ($LASTEXITCODE -ne 0) { throw 'Source is published but release tag push failed; check Git authentication' }
  Write-Host "Published source and tag $ReleaseTag. GitHub Actions can now build the Windows release."
} finally { Pop-Location }
