$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "pm_mainpy_pull.sh"
$boxmain = "C:\Users\AA Incorporado\cc\_box_main_current.py"
$cands = @("$env:SystemRoot\System32\OpenSSH", "$env:SystemRoot\Sysnative\OpenSSH",
           "$env:ProgramFiles\Git\usr\bin", "${env:ProgramFiles(x86)}\Git\usr\bin",
           "$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($name) {
  $c = Get-Command $name -ErrorAction SilentlyContinue; if ($c) { return $c.Source }
  foreach ($d in $cands) { $p = Join-Path $d "$name.exe"; if (Test-Path $p) { return $p } }
  throw "$name not found (try a 64-bit shell)."
}
$ssh = Find-Exe "ssh"; $scp = Find-Exe "scp"
& $scp -o ConnectTimeout=20 ("{0}:/home/azureuser/trading_corp/trading_corp/main.py" -f $h) $boxmain
Write-Host ("box main.py -> {0} ({1} bytes)" -f $boxmain, (Get-Item $boxmain).Length)
Get-Content -Raw $sh | & $ssh -o ConnectTimeout=20 -o ServerAliveInterval=15 $h "tr -d '\r\357\273\277' | bash"
Write-Host "---- pull exit ----"
