$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'Takumi.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $root 'START-Takumi.cmd'
$shortcut.WorkingDirectory = $root
$shortcut.Description = 'Takumi - local AI coding workspace'
$icon = Join-Path $root 'assets\takumi.ico'
if (Test-Path $icon) { $shortcut.IconLocation = $icon }
$shortcut.Save()
Write-Host "Created $shortcutPath"
