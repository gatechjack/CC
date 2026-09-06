$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "pm_mainpy_postcheck.sh"
$cands = @("$env:SystemRoot\System32\OpenSSH", "$env:SystemRoot\Sysnative\OpenSSH",
           "$env:ProgramFiles\Git\usr\bin", "${env:ProgramFiles(x86)}\Git\usr\bin",
           "$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($name) {
  $c = Get-Command $name -ErrorAction SilentlyContinue; if ($c) { return $c.Source }
  foreach ($d in $cands) { $p = Join-Path $d "$name.exe"; if (Test-Path $p) { return $p } }
  throw "$name not found (try a 64-bit shell)."
}
$ssh = Find-Exe "ssh"
Write-Host "Streaming READ-ONLY PM-driver-restore POST-CHECK from $h ..."
Get-Content -Raw $sh | & $ssh -o ConnectTimeout=20 -o ServerAliveInterval=15 $h "tr -d '\r\357\273\277' | bash"
Write-Host "---- post-check exit ----"
