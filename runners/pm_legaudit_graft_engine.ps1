# RESERVED (Jack runs): PHASE 2 = graft live_driver.py (engine writer) only. Drift-gated base d9468361.
# PRECONDITION: aborts unless the pm_web half (leg_audit.py) is already on the box at target -> enforces
# pm_web-first. Does NOT restart anything. After this (timed clear of the open): restart_tc.ps1 (bounces
# ALL divisions), then boot-verify + FF push. STOP: if this lands but pm_web is not on new code, HOLD.
$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "pm_legaudit_graft_engine.sh"
$b64 = Join-Path $PSScriptRoot "_legaudit_new_live_driver.b64"
$cands = @("$env:SystemRoot\System32\OpenSSH","$env:SystemRoot\Sysnative\OpenSSH","$env:ProgramFiles\Git\usr\bin","${env:ProgramFiles(x86)}\Git\usr\bin","$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($n){ $c=Get-Command $n -ErrorAction SilentlyContinue; if($c){return $c.Source}; foreach($d in $cands){$p=Join-Path $d "$n.exe"; if(Test-Path $p){return $p}}; throw "$n not found" }
$ssh = Find-Exe "ssh"; Write-Host "Using ssh=$ssh"
if ((([IO.File]::ReadAllBytes($sh))|?{$_ -gt 127}).Count -gt 0){ throw "sh non-ASCII" }
Write-Host "1/2 delivering live_driver.py (base64 -> /tmp/live_driver_new.py) ..."
Get-Content -Raw $b64 | & $ssh -o ConnectTimeout=20 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | base64 -d > /tmp/live_driver_new.py"
Write-Host "2/2 running Phase-2 drift-gated graft (engine; precondition=pm_web half present; rolls back on mismatch) ..."
Get-Content -Raw $sh | & $ssh -o ConnectTimeout=20 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | bash"
Write-Host "---- pm_legaudit_graft_engine exit ($LASTEXITCODE) ----"
