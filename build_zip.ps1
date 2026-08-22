# One-click package AstrBot plugin into an uploadable zip
# Usage: .\build_zip.ps1 in plugin root
# Output: dist/astrbot_plugin_comfyui_anima_vX.Y.Z.zip
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$distDir = Join-Path $root "dist"

# Build Vue3 WebUI first so the frontend version constant is auto-injected from
# metadata.yaml (via build_webui.ps1), keeping the WebUI version in sync.
$webuiScript = Join-Path $root "build_webui.ps1"
if (Test-Path $webuiScript) {
    Write-Host "==> Building WebUI (auto inject version)..." -ForegroundColor Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File $webuiScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WebUI build failed, abort packaging" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Warning "build_webui.ps1 not found, skip WebUI build (frontend version may be stale)"
}
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
# Files and directories to include (relative to plugin root)
$includeList = @(
    "_conf_schema.json",
    "main.py",
    "webui_api.py",
    "comfyui_client.py",
    "danbooru_client.py",
    "translate_client.py",
    "workflow_builder.py",
    "image_store.py",
    "quota_store.py",
    "oplog_store.py",
    "standalone_webui.py",
    "token_store.py",
    "nsfw_detector.py",
    "logo.png",
    "metadata.yaml",
    "requirements.txt",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "pages",
    "skills",
    "assets"
    # Note: do NOT package the workflow/ directory.
    # Root workflow/*.json are just default/reference samples; at runtime the
    # plugin reads workflows from data_dir/workflow/, so repo samples should not
    # be bundled to avoid polluting users' data_dir/workflow.
)

# Build zip with forward-slash paths so that Python zipfile on Linux
# sees proper directory entries instead of backslash-in-filename.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($zipPath, 1)  # 1 = Create

function Add-ItemToZip($fsPath, $zipEntryPath) {
    if (Test-Path -PathType Container $fsPath) {
        # directory entry must end with /
        $null = $zip.CreateEntry($zipEntryPath + "/")
        # recurse
        Get-ChildItem $fsPath | ForEach-Object {
            Add-ItemToZip $_.FullName ($zipEntryPath + "/" + $_.Name)
        }
    } else {
        $entry = $zip.CreateEntry($zipEntryPath)
        $stream = $entry.Open()
        # Version pages' static assets with a query string so browsers pull the
        # fresh files after each release, avoiding stale-cache symptoms.
        if ($zipEntryPath -eq "pages/anima-console-vue-legacy/index.html" -and $newVer -ne "") {
            $html = [System.IO.File]::ReadAllText($fsPath)
            $html = $html -replace '\./app\.js', ("./app.js?v=" + $newVer)
            $html = $html -replace '\./styles\.css', ("./styles.css?v=" + $newVer)
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($html)
        } else {
            $bytes = [System.IO.File]::ReadAllBytes($fsPath)
        }
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Dispose()
    }
}

foreach ($name in $includeList) {
    $fsPath = Join-Path $root $name
    if (Test-Path $fsPath) {
        Add-ItemToZip $fsPath $name
    } else {
        Write-Host "Missing: $name" -ForegroundColor Red
        $zip.Dispose()
        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
        exit 1
    }
}
$zip.Dispose()
if (Test-Path $zipPath) {
    $size = (Get-Item $zipPath).Length
    $kb = [math]::Round($size / 1024, 1)
    $okMsg = "Packaged: " + $zipPath + "  size " + $kb + " KB"
    Write-Host $okMsg
} else {
    Write-Host "Packaging failed" -ForegroundColor Red
    exit 1
}
