$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "pm_legaudit_classifier_parity_ro.sh"
$b64 = Join-Path $PSScriptRoot "_legaudit_new_leg_audit.b64"
$cands = @("$env:SystemRoot\System32\OpenSSH","$env:SystemRoot\Sysnative\OpenSSH","$env:ProgramFiles\Git\usr\bin","${env:ProgramFiles(x86)}\Git\usr\bin","$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($n){ $c=Get-Command $n -ErrorAction SilentlyContinue; if($c){return $c.Source}; foreach($d in $cands){$p=Join-Path $d "$n.exe"; if(Test-Path $p){return $p}}; throw "$n not found" }
$ssh = Find-Exe "ssh"
if ((([IO.File]::ReadAllBytes($sh))|?{$_ -gt 127}).Count -gt 0){ throw "sh non-ASCII" }
Write-Host "1/2 delivering new leg_audit.py (base64 -> /tmp/leg_audit_new.py) ..."
Get-Content -Raw $b64 | & $ssh -o ConnectTimeout=20 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | base64 -d > /tmp/leg_audit_new.py"
Write-Host "2/2 running classifier parity (READ-ONLY; box venv) ..."
Get-Content -Raw $sh | & $ssh -o ConnectTimeout=20 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | bash; rm -f /tmp/leg_audit_new.py"
Write-Host "---- pm_legaudit_classifier_parity_ro exit ($LASTEXITCODE) ----"
