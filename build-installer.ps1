#Requires -Version 5.1
<#
.SYNOPSIS
    Builds MyWorkDay and packages it into a Windows installer (MyWorkDay-Setup-<version>.exe).

.DESCRIPTION
    Two steps:
      1. `dotnet publish` - self-contained, single-file, win-x64 (see MyWorkDay.csproj's
         publish-related properties). No separate .NET runtime install needed on the target
         machine, matching the old PyInstaller build's "just works" experience.
      2. Inno Setup (ISCC.exe) packages that publish output using installer\myworkday.iss -
         same installer shape (per-user install under %LOCALAPPDATA%\Programs, optional
         desktop shortcut, launch-on-finish) as the original Python build's installer.

.PARAMETER Version
    Version string stamped into the exe and the installer filename (e.g. "1.0.0").
    Defaults to the version already in MyWorkDay.csproj.
#>
param(
    [string]$Version
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$project = Join-Path $root "src\MyWorkDay\MyWorkDay.csproj"
$publishDir = Join-Path $root "publish"
$issFile = Join-Path $root "installer\myworkday.iss"

if (-not $Version) {
    $csproj = [xml](Get-Content $project)
    $Version = $csproj.Project.PropertyGroup.Version | Select-Object -First 1
    if (-not $Version) { $Version = "1.0.0" }
}
Write-Host "==> Building MyWorkDay v$Version" -ForegroundColor Cyan

Write-Host "==> Publishing (self-contained, single-file, win-x64)..." -ForegroundColor Cyan
if (Test-Path $publishDir) { Remove-Item $publishDir -Recurse -Force }
dotnet publish $project -c Release -r win-x64 --self-contained true `
    -p:PublishSingleFile=true -p:Version=$Version -o $publishDir
if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed" }

# Inno Setup isn't always on PATH - probe the common install locations.
$isccPath = $null
$onPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($onPath) { $isccPath = $onPath.Source }
if (-not $isccPath) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $isccPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $isccPath) {
    throw "ISCC.exe (Inno Setup) not found. Install it (winget install JRSoftware.InnoSetup) or add it to PATH."
}

Write-Host "==> Packaging installer with Inno Setup..." -ForegroundColor Cyan
& $isccPath "/DMyAppVersion=$Version" $issFile
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }

$outputExe = Join-Path $root "installer\output\MyWorkDay-Setup-$Version.exe"
if (Test-Path $outputExe) {
    Write-Host "==> Done: $outputExe" -ForegroundColor Green
} else {
    Write-Warning "Expected installer not found at $outputExe - check the Inno Setup output above."
}
