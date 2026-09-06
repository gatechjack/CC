$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "pm_m3_errcheck_ro.sh"
$cands = @("$env:SystemRoot\System32\OpenSSH", "$env:SystemRoot\Sysnative\OpenSSH",
           "$env:ProgramFiles\Git\usr\bin", "${env:ProgramFiles(x86)}\Git\usr\bin",
           "$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($name) {
  $c = Get-Command $name -ErrorAction SilentlyContinue; if ($c) { return $c.Source }
  foreach ($d in $cands) { $p = Join-Path $d "$name.exe"; if (Test-Path $p) { return $p } }
  throw "$name not found."
}
$ssh = Find-Exe "ssh"; Write-Host "Using ssh=$ssh"
Write-Host "M3 error breakdown + Jack retry (read-only) on $h ..."
Get-Content -Raw $sh | & $ssh -o ConnectTimeout=15 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | bash"
Write-Host "---- m3 errcheck exit ----"
