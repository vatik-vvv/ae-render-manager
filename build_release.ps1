# Build AERenderManager.exe, ZIP, and optional Inno Setup installer.
# Usage: .\build_release.ps1 [-Version 1.1.1] [-SkipInstaller]

param(
    [string]$Version = "1.1.1",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

Write-Host "==> Icon"
python build_icon.py

Write-Host "==> PyInstaller"
Get-Process AERenderManager -ErrorAction SilentlyContinue | Stop-Process -Force
pyinstaller main.spec --noconfirm

$exe = Join-Path $Root "dist\AERenderManager.exe"
if (-not (Test-Path $exe)) {
    throw "Build failed: $exe not found"
}

Write-Host "==> ZIP"
python -c "from publish_release import build_zip; build_zip('$Version')"

if ($SkipInstaller) {
    Write-Host "Skip installer (-SkipInstaller)."
    exit 0
}

$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

Write-Host "==> Installer"
python -c "from publish_release import build_installer; build_installer('$Version')"
