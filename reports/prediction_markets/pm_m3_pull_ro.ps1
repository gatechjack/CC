$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "pm_m3_pull_ro.sh"
$out = Join-Path $PSScriptRoot "_m3stage\box_main_b64.txt"
$cands = @("$env:SystemRoot\System32\OpenSSH", "$env:SystemRoot\Sysnative\OpenSSH",
           "$env:ProgramFiles\Git\usr\bin", "${env:ProgramFiles(x86)}\Git\usr\bin",
           "$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($name) {
  $c = Get-Command $name -ErrorAction SilentlyContinue; if ($c) { return $c.Source }
  foreach ($d in $cands) { $p = Join-Path $d "$name.exe"; if (Test-Path $p) { return $p } }
  throw "$name not found."
}
$ssh = Find-Exe "ssh"; Write-Host "Using ssh=$ssh"
Write-Host "Pulling box main.py (base64) -> $out"
Get-Content -Raw $sh | & $ssh -o ConnectTimeout=15 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | bash" | Set-Content -Encoding ascii $out
Write-Host "---- pull done ----"
