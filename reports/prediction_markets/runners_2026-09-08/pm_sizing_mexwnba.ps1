$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "pm_sizing_mexwnba.sh"
$cands = @("$env:SystemRoot\System32\OpenSSH", "$env:SystemRoot\Sysnative\OpenSSH",
           "$env:ProgramFiles\Git\usr\bin", "${env:ProgramFiles(x86)}\Git\usr\bin",
           "$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($n) {
  $c = Get-Command $n -ErrorAction SilentlyContinue; if ($c) { return $c.Source }
  foreach ($d in $cands) { $p = Join-Path $d "$n.exe"; if (Test-Path $p) { return $p } }
  throw "$n not found."
}
$ssh = Find-Exe "ssh"; Write-Host "Using ssh=$ssh"
Get-Content -Raw $sh | & $ssh -o ConnectTimeout=15 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | bash"
Write-Host "---- pm_sizing_mexwnba exit ----"
