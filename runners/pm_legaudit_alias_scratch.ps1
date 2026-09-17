$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "pm_legaudit_alias_scratch.sh"
$b64 = Join-Path $PSScriptRoot "_legaudit_overlay.b64"
$cands = @("$env:SystemRoot\System32\OpenSSH","$env:SystemRoot\Sysnative\OpenSSH","$env:ProgramFiles\Git\usr\bin","${env:ProgramFiles(x86)}\Git\usr\bin","$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($n){ $c=Get-Command $n -ErrorAction SilentlyContinue; if($c){return $c.Source}; foreach($d in $cands){$p=Join-Path $d "$n.exe"; if(Test-Path $p){return $p}}; throw "$n not found" }
$ssh = Find-Exe "ssh"; Write-Host "Using ssh=$ssh"
if ((([IO.File]::ReadAllBytes($sh))|?{$_ -gt 127}).Count -gt 0){ throw "sh non-ASCII" }
Write-Host "1/2 delivering overlay (base64 -> /tmp/legaudit_overlay.tgz) ..."
Get-Content -Raw $b64 | & $ssh -o ConnectTimeout=20 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | base64 -d > /tmp/legaudit_overlay.tgz"
Write-Host "2/2 running box-scratch differential (box venv; live tree/engine untouched) ..."
Get-Content -Raw $sh | & $ssh -o ConnectTimeout=20 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | bash"
Write-Host "---- pm_legaudit_alias_scratch exit ($LASTEXITCODE) ----"
