Add-Type -AssemblyName System.IO.Compression.FileSystem
$old = "X:\_WorkSpace\Mine\astrbot-comfyui-anima\_References\astrbot_plugin_private_companion\dist\astrbot_plugin_private_companion-6.3.4.zip"
$new = "X:\_WorkSpace\Mine\astrbot-comfyui-anima\_References\astrbot_plugin_private_companion\dist\astrbot_plugin_private_companion_6.3.4_fix.zip"
$rootO = "astrbot_plugin_private_companion-6.3.4/"
$rootN = "astrbot_plugin_private_companion/"

function Get-Rel([string]$full, [string]$root) {
    if ($full.StartsWith($root)) { return $full.Substring($root.Length) }
    return $full
}

$zo = [System.IO.Compression.ZipFile]::OpenRead($old)
$zn = [System.IO.Compression.ZipFile]::OpenRead($new)

$setO = @{}
foreach ($e in $zo.Entries) {
    if (-not $e.FullName.EndsWith("/")) {
        $rel = Get-Rel $e.FullName $rootO
        $setO[$rel] = $e.Length
    }
}
$setN = @{}
foreach ($e in $zn.Entries) {
    if (-not $e.FullName.EndsWith("/")) {
        $rel = Get-Rel $e.FullName $rootN
        $setN[$rel] = $e.Length
    }
}

$onlyOld = @()
foreach ($k in $setO.Keys) { if (-not $setN.ContainsKey($k)) { $onlyOld += $k } }
$onlyNew = @()
foreach ($k in $setN.Keys) { if (-not $setO.ContainsKey($k)) { $onlyNew += $k } }
$sizeDiff = @()
foreach ($k in $setO.Keys) { if ($setN.ContainsKey($k) -and $setN[$k] -ne $setO[$k]) { $sizeDiff += $k } }

Write-Host "=== only in OLD (orig): $($onlyOld.Count) ==="
$onlyOld | Sort-Object | Select-Object -First 25
Write-Host "=== only in NEW (fix): $($onlyNew.Count) ==="
$onlyNew | Sort-Object | Select-Object -First 25
Write-Host "=== size-diff: $($sizeDiff.Count) ==="
$sizeDiff | Sort-Object | Select-Object -First 15

$zo.Dispose()
$zn.Dispose()
