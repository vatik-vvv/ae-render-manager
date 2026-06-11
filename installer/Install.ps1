# AE Render Manager — per-user install (no admin required).
# Usage: Right-click → Run with PowerShell, or: powershell -ExecutionPolicy Bypass -File Install.ps1

$ErrorActionPreference = "Stop"
$Source = $PSScriptRoot
$Target = Join-Path $env:LOCALAPPDATA "Programs\AE Render Manager"
$ExeName = "AERenderManager.exe"

Write-Host "Installing AE Render Manager to:`n  $Target"

New-Item -ItemType Directory -Force -Path $Target | Out-Null
Copy-Item (Join-Path $Source $ExeName) $Target -Force
Copy-Item (Join-Path $Source "config.example.json") $Target -Force
Copy-Item (Join-Path $Source "RELEASE_INSTALL.txt") $Target -Force

$config = Join-Path $Target "config.json"
$example = Join-Path $Target "config.example.json"
if (-not (Test-Path $config)) {
    Copy-Item $example $config
    Write-Host "Created config.json from config.example.json"
}

$startMenu = [Environment]::GetFolderPath("Programs")
$shortcutDir = Join-Path $startMenu "AE Render Manager"
New-Item -ItemType Directory -Force -Path $shortcutDir | Out-Null
$wsh = New-Object -ComObject WScript.Shell
$lnk = $wsh.CreateShortcut((Join-Path $shortcutDir "AE Render Manager.lnk"))
$lnk.TargetPath = Join-Path $Target $ExeName
$lnk.WorkingDirectory = $Target
$lnk.Description = "After Effects Render Manager"
$lnk.Save()

Write-Host "`nInstalled. Start Menu → AE Render Manager"
$launch = Read-Host "Launch now? [Y/n]"
if ($launch -eq "" -or $launch -match "^[Yy]") {
    Start-Process (Join-Path $Target $ExeName)
}
