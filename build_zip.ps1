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

# Read the version from metadata.yaml (version is managed manually in metadata.yaml,
# kept in sync with CHANGELOG.md). No auto-bump to avoid version drift.
# Use .NET IO to avoid PowerShell default encoding/BOM issues
$metaPath = Join-Path $root "metadata.yaml"
$newVer = ""
if (Test-Path $metaPath) {
    $metaContent = [System.IO.File]::ReadAllText($metaPath)
    if ($metaContent -match 'version:\s*(v\d+\.\d+\.\d+)') {
        $newVer = $Matches[1]
        Write-Host "Using version $newVer from metadata.yaml"
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
# -Force: 若目标 zip 已存在则直接覆盖，避免手动删除旧包（保留历史版本包不被误删）。
Compress-Archive -Path $files -DestinationPath $zipPath -Force

if (Test-Path $zipPath) {
    $size = (Get-Item $zipPath).Length
    Write-Host "Packaged: $zipPath  ($([math]::Round($size / 1KB, 1)) KB)"
} else {
    Write-Error "Packaging failed"
    exit 1
}
