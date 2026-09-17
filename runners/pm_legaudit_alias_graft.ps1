# RESERVED (Jack runs): graft 2 leg-audit files onto the box (azureuser; drift-gated base d9468361).
# Delivers a staging tar via base64/STDIN then runs the drift-gated graft. Live tree written; engine
# NOT restarted here (restart is the separate restart_pmweb.ps1 + restart_tc.ps1 steps). Rolls back on
# any mismatch. After graft: (1) restart_pmweb.ps1  (2) restart_tc.ps1  (3) FF push to prod-live.
$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "pm_legaudit_alias_graft.sh"
$b64 = Join-Path $PSScriptRoot "_legaudit_graft.b64"
$cands = @("$env:SystemRoot\System32\OpenSSH","$env:SystemRoot\Sysnative\OpenSSH","$env:ProgramFiles\Git\usr\bin","${env:ProgramFiles(x86)}\Git\usr\bin","$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($n){ $c=Get-Command $n -ErrorAction SilentlyContinue; if($c){return $c.Source}; foreach($d in $cands){$p=Join-Path $d "$n.exe"; if(Test-Path $p){return $p}}; throw "$n not found" }
$ssh = Find-Exe "ssh"; Write-Host "Using ssh=$ssh"
if ((([IO.File]::ReadAllBytes($sh))|?{$_ -gt 127}).Count -gt 0){ throw "sh non-ASCII" }
Write-Host "1/2 delivering staging tar (base64 -> /tmp/legaudit_graft.tgz) ..."
Get-Content -Raw $b64 | & $ssh -o ConnectTimeout=20 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | base64 -d > /tmp/legaudit_graft.tgz"
Write-Host "2/2 running drift-gated graft (azureuser; rolls back on mismatch) ..."
Get-Content -Raw $sh | & $ssh -o ConnectTimeout=20 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | bash"
Write-Host "---- pm_legaudit_alias_graft exit ($LASTEXITCODE) ----"
