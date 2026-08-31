$d63='C:\Users\Noow\AppData\Local\Temp\companion63\astrbot_plugin_private_companion-6.3.3'
$d64='C:\Users\Noow\AppData\Local\Temp\companion634\astrbot_plugin_private_companion'
$h63=@{}; $h64=@{}
Get-ChildItem $d63 -Recurse -File | Get-FileHash -Algorithm MD5 | ForEach-Object { $h63[$_.Path.Substring($d63.Length+1)]=$_.Hash }
Get-ChildItem $d64 -Recurse -File | Get-FileHash -Algorithm MD5 | ForEach-Object { $h64[$_.Path.Substring($d64.Length+1)]=$_.Hash }
$h63.GetEnumerator() | ForEach-Object { if($h64.ContainsKey($_.Key) -and $h63[$_.Key]-ne $h64[$_.Key]){ Write-Output ('DIFF: '+$_.Key) } }
$h64.GetEnumerator() | ForEach-Object { if(-not $h63.ContainsKey($_.Key)){ Write-Output ('ONLY64: '+$_.Key) } }
