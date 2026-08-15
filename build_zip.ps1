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
    "token_store.py",
    "logo.png",
    "metadata.yaml",
    "requirements.txt",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "pages",
    "skills"
    # 注意：不要打包 workflow/ 目录。
    # 仓库根 workflow/*.json 只是默认/参考工作流样例；插件运行时工作流来自
    # data_dir/workflow/（main.py 的 self.workflow_dir.mkdir 自建），从 data_dir 读取，
    # 不应把仓库样例打进插件包，避免污染用户 data_dir/workflow 或造成路径混淆。
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
        # 给 pages 的静态资源加版本化 query，强制浏览器在每次发布后拉取新文件，
        # 避免旧缓存导致「统计有数但图库空白 / 回收站点不动」等典型缓存症状。
        if ($zipEntryPath -eq "pages/anima-console/index.html" -and $newVer -ne "") {
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
