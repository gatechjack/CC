$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$grafted = "C:\Users\AA Incorporado\cc\_grafted_main.py"
$sh = Join-Path $PSScriptRoot "pm_mainpy_apply.sh"
$cands = @("$env:SystemRoot\System32\OpenSSH", "$env:SystemRoot\Sysnative\OpenSSH",
           "$env:ProgramFiles\Git\usr\bin", "${env:ProgramFiles(x86)}\Git\usr\bin",
           "$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($name) {
  $c = Get-Command $name -ErrorAction SilentlyContinue; if ($c) { return $c.Source }
  foreach ($d in $cands) { $p = Join-Path $d "$name.exe"; if (Test-Path $p) { return $p } }
  throw "$name not found (try a 64-bit shell)."
}
$ssh = Find-Exe "ssh"; $scp = Find-Exe "scp"
if (-not (Test-Path $grafted)) { throw "grafted main.py missing: $grafted" }
& $ssh -o ConnectTimeout=20 $h "rm -rf /home/azureuser/pm_mainpy_stage; mkdir -p /home/azureuser/pm_mainpy_stage"
& $scp -o ConnectTimeout=20 $grafted ("{0}:/home/azureuser/pm_mainpy_stage/grafted_main.py" -f $h)
Write-Host "==== APPLY (drift-check 3f3f3df8 -> backup -> graft -> verify MACE survives + PM present -> Gate-A; auto-restore on fail; NO restart) ===="
Get-Content -Raw $sh | & $ssh -o ConnectTimeout=25 -o ServerAliveInterval=15 $h "tr -d '\r\357\273\277' | bash"
Write-Host "---- apply runner exit ----"
