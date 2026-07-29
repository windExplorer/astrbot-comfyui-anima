# One-click package AstrBot plugin into an uploadable/installable zip
# Usage: run .\build_zip.ps1 in the plugin root
# Output: dist/astrbot_plugin_comfyui_anima_vX.Y.Z.zip
#   Filename includes the version and is placed in dist/ so old packages are kept.

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot

# Output dir: all zips go into dist/ for centralized management (no overwrite)
$distDir = Join-Path $root "dist"
if (-not (Test-Path $distDir)) {
    New-Item -ItemType Directory -Path $distDir | Out-Null
}

# Auto-bump the patch version in metadata.yaml and use it in the filename
# Use .NET IO to avoid PowerShell default encoding/BOM issues
$metaPath = Join-Path $root "metadata.yaml"
$newVer = ""
if (Test-Path $metaPath) {
    $metaContent = [System.IO.File]::ReadAllText($metaPath)
    if ($metaContent -match 'version:\s*v(\d+)\.(\d+)\.(\d+)') {
        $maj = [int]$Matches[1]
        $min = [int]$Matches[2]
        $pat = [int]$Matches[3] + 1
        $newVer = "v$maj.$min.$pat"
        $metaContent = $metaContent -replace 'version:\s*v\d+\.\d+\.\d+', "version: $newVer"
        [System.IO.File]::WriteAllText($metaPath, $metaContent)
        Write-Host "Version bumped to $newVer"
    } else {
        Write-Warning "version field not found in metadata.yaml, using 'unknown' suffix"
        $newVer = "unknown"
    }
} else {
    Write-Error "metadata.yaml not found, cannot package"
    exit 1
}

# Filename with version, in dist/, keep old packages
$zipName = "astrbot_plugin_comfyui_anima_$newVer.zip"
$zipPath = Join-Path $distDir $zipName

# Only plugin runtime files (exclude docs/, tests/, .git, temp, this script)
$files = @(
    "_conf_schema.json",
    "main.py",
    "comfyui_client.py",
    "danbooru_client.py",
    "workflow_builder.py",
    "metadata.yaml",
    "requirements.txt",
    "README.md",
    "CHANGELOG.md",
    "LICENSE"
)

# Validate files exist
$missing = @()
foreach ($f in $files) {
    if (-not (Test-Path (Join-Path $root $f))) {
        $missing += $f
    }
}
if ($missing.Count -gt 0) {
    Write-Error "Missing files, cannot package: $($missing -join ', ')"
    exit 1
}

# Package (files at root, no outer folder)
Compress-Archive -Path $files -DestinationPath $zipPath

if (Test-Path $zipPath) {
    $size = (Get-Item $zipPath).Length
    Write-Host "Packaged: $zipPath  ($([math]::Round($size / 1KB, 1)) KB)"
} else {
    Write-Error "Packaging failed"
    exit 1
}
