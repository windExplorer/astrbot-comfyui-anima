# 一键打包 AstrBot 插件为可上传安装的 zip
# 用法：在插件根目录执行  .\build_zip.ps1
# 产物：astrbot_plugin_comfyui_anima.zip（根目录即插件文件，可直接在 AstrBot WebUI 上传安装）

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$zipName = "astrbot_plugin_comfyui_anima.zip"
$zipPath = Join-Path $root $zipName

# 自动递增 metadata.yaml 中的小版本号（patch +1）
# 用 .NET 读写以避免 PowerShell 默认编码/BOM 问题
$metaPath = Join-Path $root "metadata.yaml"
if (Test-Path $metaPath) {
    $metaContent = [System.IO.File]::ReadAllText($metaPath)
    if ($metaContent -match 'version:\s*v(\d+)\.(\d+)\.(\d+)') {
        $maj = [int]$Matches[1]
        $min = [int]$Matches[2]
        $pat = [int]$Matches[3] + 1
        $newVer = "v$maj.$min.$pat"
        $metaContent = $metaContent -replace 'version:\s*v\d+\.\d+\.\d+', "version: $newVer"
        [System.IO.File]::WriteAllText($metaPath, $metaContent)
        Write-Host "版本号已递增为 $newVer"
    } else {
        Write-Warning "未在 metadata.yaml 中找到 version 字段，跳过版本递增"
    }
}

# 仅包含插件运行必需的文件（排除 docs/、tests/、.git、临时文件、本脚本自身）
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

# 校验文件存在
$missing = @()
foreach ($f in $files) {
    if (-not (Test-Path (Join-Path $root $f))) {
        $missing += $f
    }
}
if ($missing.Count -gt 0) {
    Write-Error "以下文件缺失，无法打包：$($missing -join ', ')"
    exit 1
}

# 删除旧 zip
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

# 打包（文件放根目录，不带外层文件夹）
Compress-Archive -Path $files -DestinationPath $zipPath

if (Test-Path $zipPath) {
    $size = (Get-Item $zipPath).Length
    Write-Host "打包成功：$zipPath  ($([math]::Round($size / 1KB, 1)) KB)"
} else {
    Write-Error "打包失败"
    exit 1
}
