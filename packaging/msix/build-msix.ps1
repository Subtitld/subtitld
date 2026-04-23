#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Packages the PyInstaller output in dist/Subtitld/ into an MSIX for the
    Microsoft Store.

.DESCRIPTION
    Requires MSStore Publisher values as environment variables (set by the CI
    job from GitHub Secrets):
        MSIX_PUBLISHER_ID        e.g. 12345SubtitldStudio.Subtitld
        MSIX_PUBLISHER_CN        e.g. CN=12345678-...
        MSIX_PUBLISHER_DISPLAY   e.g. Subtitld Studio
        MSIX_VERSION             four-part version (26.3.0.0)

    Produces: Subtitld.msix in the current directory.
#>

$ErrorActionPreference = 'Stop'

# Resolve paths relative to this script
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir '..\..')
$DistDir = Join-Path $RepoRoot 'dist\Subtitld'
$StagingDir = Join-Path $RepoRoot 'build\msix-staging'
$OutputMsix = Join-Path $RepoRoot 'Subtitld.msix'

if (-not (Test-Path $DistDir)) {
    throw "PyInstaller output not found at $DistDir. Run pyinstaller first."
}

foreach ($var in @('MSIX_PUBLISHER_ID', 'MSIX_PUBLISHER_CN', 'MSIX_PUBLISHER_DISPLAY', 'MSIX_VERSION')) {
    if (-not [Environment]::GetEnvironmentVariable($var)) {
        throw "Environment variable $var is required."
    }
}

# MSIX requires a four-part version with no leading zeros in any component
# (pattern: (0|[1-9][0-9]{0,3}|...)). Normalize '26.03.0.0' -> '26.3.0.0'.
$parts = $env:MSIX_VERSION.Split('.') | ForEach-Object { [int]$_ }
while ($parts.Count -lt 4) { $parts += 0 }
$MsixVersion = ($parts[0..3] -join '.')
Write-Host "Normalized MSIX version: $env:MSIX_VERSION -> $MsixVersion"

# Fresh staging dir
if (Test-Path $StagingDir) { Remove-Item -Recurse -Force $StagingDir }
New-Item -ItemType Directory -Path $StagingDir | Out-Null

# Copy PyInstaller bundle contents into staging root
Copy-Item -Path (Join-Path $DistDir '*') -Destination $StagingDir -Recurse -Force

# Remove files/directories MakeAppx rejects (build metadata, pycache, editable
# install artifacts). Not needed at runtime.
Get-ChildItem -Path $StagingDir -Recurse -Force -Directory -Include '__pycache__', '*.dist-info', '*.egg-info' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -Recurse -Force -LiteralPath $_.FullName }
# Flag any filenames with characters MSIX rejects (backslash/colon in name,
# trailing dot/space, or Windows reserved names).
$bad = Get-ChildItem -Path $StagingDir -Recurse -Force -File | Where-Object {
    $n = $_.Name
    $n -match '[<>:"/\\|?*]' -or $n -match '[. ]$' -or $n -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)'
}
if ($bad) {
    Write-Warning "Problematic filenames detected (MSIX may reject):"
    $bad | ForEach-Object { Write-Warning "  $($_.FullName)" }
}

# Copy icon assets
$AssetsSrc = Join-Path $ScriptDir 'assets'
$AssetsDst = Join-Path $StagingDir 'Assets'
if (Test-Path $AssetsSrc) {
    Copy-Item -Path $AssetsSrc -Destination $AssetsDst -Recurse -Force
} else {
    Write-Warning "No Assets/ directory found at $AssetsSrc. Store tiles will be missing."
    New-Item -ItemType Directory -Path $AssetsDst | Out-Null
}

# Render AppxManifest.xml with substitutions
$Manifest = Get-Content (Join-Path $ScriptDir 'AppxManifest.xml') -Raw
$Manifest = $Manifest.Replace('PLACEHOLDER_PUBLISHER_ID',      $env:MSIX_PUBLISHER_ID)
$Manifest = $Manifest.Replace('PLACEHOLDER_PUBLISHER_CN',      $env:MSIX_PUBLISHER_CN)
$Manifest = $Manifest.Replace('PLACEHOLDER_PUBLISHER_DISPLAY', $env:MSIX_PUBLISHER_DISPLAY)
$Manifest = $Manifest.Replace('PLACEHOLDER_VERSION',           $MsixVersion)
Set-Content -Path (Join-Path $StagingDir 'AppxManifest.xml') -Value $Manifest -NoNewline

# Locate makeappx.exe from Windows SDK
$MakeAppx = (Get-Command makeappx.exe -ErrorAction SilentlyContinue).Source
if (-not $MakeAppx) {
    $SdkRoot = 'C:\Program Files (x86)\Windows Kits\10\bin'
    $MakeAppx = Get-ChildItem -Path $SdkRoot -Filter 'makeappx.exe' -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match 'x64' } |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $MakeAppx) {
    throw 'makeappx.exe not found. Install the Windows 10/11 SDK.'
}

Write-Host "Using makeappx: $MakeAppx"
Write-Host "Packaging $StagingDir -> $OutputMsix"

& $MakeAppx pack /v /d $StagingDir /p $OutputMsix /o
if ($LASTEXITCODE -ne 0) {
    throw "makeappx failed with exit code $LASTEXITCODE"
}

Write-Host "Created $OutputMsix"
