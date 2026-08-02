# One-click package AstrBot plugin into an uploadable zip
# Usage: .\build_zip.ps1 in plugin root
# Output: dist/astrbot_plugin_comfyui_anima_vX.Y.Z.zip
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$distDir = Join-Path $root "dist"
if (-not (Test-Path $distDir)) {
    New-Item -ItemType Directory -Path $distDir | Out-Null
}
$metaPath = Join-Path $root "metadata.yaml"
$newVer = ""
if (Test-Path $metaPath) {
    $metaContent = [System.IO.File]::ReadAllText($metaPath)
    if ($metaContent -match 'version:\s*(v\d+\.\d+\.\d+)') {
        $newVer = $Matches[1]
        Write-Host "Using version $newVer from metadata.yaml"
    } else {
        Write-Warning "version field not found in metadata.yaml"
        $newVer = "unknown"
    }
} else {
    Write-Host "metadata.yaml not found, cannot package" -ForegroundColor Red
    exit 1
}
$zipName = "astrbot_plugin_comfyui_anima_$newVer.zip"
$zipPath = Join-Path $distDir $zipName
if (Test-Path $zipPath) {
    $errMsg = "Version " + $newVer + " already packaged. Bump version in metadata.yaml and add a CHANGELOG entry before repacking."
    Write-Host $errMsg -ForegroundColor Red
    exit 1
}
$relativeFiles = @(
    "_conf_schema.json",
    "main.py",
    "webui_api.py",
    "comfyui_client.py",
    "danbooru_client.py",
    "workflow_builder.py",
    "image_store.py",
    "metadata.yaml",
    "requirements.txt",
    "README.md",
    "CHANGELOG.md",
    "LICENSE"
)
$dirFiles = @(
    "pages",
    "workflow"
)
$files = @()
foreach ($f in $relativeFiles) {
    $p = Join-Path $root $f
    if (Test-Path $p) { $files += $p } else { Write-Host "Missing file: $p" -ForegroundColor Red; exit 1 }
}
foreach ($d in $dirFiles) {
    $p = Join-Path $root $d
    if (Test-Path $p) { $files += $p } else { Write-Host "Missing dir: $p" -ForegroundColor Red; exit 1 }
}
# Use -LiteralPath with absolute paths to avoid CWD-dependent resolution
Compress-Archive -LiteralPath $files -DestinationPath $zipPath -Force
if (Test-Path $zipPath) {
    $size = (Get-Item $zipPath).Length
    $kb = [math]::Round($size / 1024, 1)
    $okMsg = "Packaged: " + $zipPath + "  size " + $kb + " KB"
    Write-Host $okMsg
} else {
    Write-Host "Packaging failed" -ForegroundColor Red
    exit 1
}
