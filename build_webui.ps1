# 构建 Vue3 WebUI（新版控制台）。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File ./build_webui.ps1
# 作用：
#   1) 进入 webui-src/ 执行 npm install（若缺依赖）
#   2) 执行 npm run build，产物输出到 ../pages/anima-console-vue/
# 前置条件：已安装 Node.js 18+ 与 npm。

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$src = Join-Path $root "webui-src"
$out = Join-Path $root "pages/anima-console-vue"

Write-Host "==> 构建 Vue3 WebUI (anima-console-vue)..." -ForegroundColor Cyan

if (-not (Test-Path (Join-Path $src "package.json"))) {
    Write-Host "错误：找不到 $src/package.json" -ForegroundColor Red
    exit 1
}

Push-Location $src
try {
    # 从 metadata.yaml 提取 version，注入前端版本常量（单一版本来源）。
    $metaPath = Join-Path $root "metadata.yaml"
    $pluginVersion = "dev"
    if (Test-Path $metaPath) {
        $metaRaw = Get-Content -Raw -Encoding UTF8 $metaPath
        if ($metaRaw -match '(?m)^\s*version:\s*"?([^"\r\n]+?)"?\s*$') {
            $pluginVersion = $Matches[1].Trim()
        }
    }
    $versionTs = "export const PLUGIN_VERSION = `"$pluginVersion`";`n"
    Set-Content -Path (Join-Path $src "src/version.ts") -Value $versionTs -Encoding UTF8
    Write-Host "==> 注入插件版本号：$pluginVersion" -ForegroundColor Green

    if (-not (Test-Path (Join-Path $src "node_modules"))) {
        Write-Host "==> 首次构建，安装依赖（npm install）..." -ForegroundColor Yellow
        npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install 失败" }
    }
    Write-Host "==> 执行 vite build..." -ForegroundColor Yellow
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "vite build 失败" }
} finally {
    Pop-Location
}

Write-Host "==> 构建完成，产物已输出到：$out" -ForegroundColor Green
