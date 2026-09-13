$ErrorActionPreference = "Continue"; $OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "legpm_p6_baseline2_ro.sh"
$cands = @("$env:SystemRoot\System32\OpenSSH","$env:SystemRoot\Sysnative\OpenSSH","$env:ProgramFiles\Git\usr\bin","${env:ProgramFiles(x86)}\Git\usr\bin","$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($n){ $c=Get-Command $n -ErrorAction SilentlyContinue; if($c){return $c.Source}; foreach($d in $cands){$p=Join-Path $d "$n.exe"; if(Test-Path $p){return $p}}; throw "$n not found" }
$ssh = Find-Exe "ssh"
if ((([IO.File]::ReadAllBytes($sh))|?{$_ -gt 127}).Count -gt 0){ throw "sh non-ASCII" }
$out = "C:\Users\AA Incorporado\cc\_recon_scratch"
if (-not (Test-Path $out)) { New-Item -ItemType Directory -Force $out | Out-Null }
$res = Get-Content -Raw $sh | & $ssh -o ConnectTimeout=25 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | bash"
$res | Out-File -FilePath "$out\legpm_p6_baseline2.txt" -Encoding utf8; $res | Write-Host
Write-Host "---- legpm_p6_baseline2_ro exit ($LASTEXITCODE) ----"
