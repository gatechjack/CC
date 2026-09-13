$ErrorActionPreference = "Stop"; $OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$py = Join-Path $PSScriptRoot "dump_kcv2.py"
$cands = @("$env:SystemRoot\System32\OpenSSH","$env:SystemRoot\Sysnative\OpenSSH","$env:ProgramFiles\Git\usr\bin","${env:ProgramFiles(x86)}\Git\usr\bin","$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($n){ $c=Get-Command $n -ErrorAction SilentlyContinue; if($c){return $c.Source}; foreach($d in $cands){$p=Join-Path $d "$n.exe"; if(Test-Path $p){return $p}}; throw "$n not found" }
$ssh = Find-Exe "ssh"
if ((([IO.File]::ReadAllBytes($py))|?{$_ -gt 127}).Count -gt 0){ throw "py non-ASCII" }
$legacy = "/home/azureuser/trading_corp/data/trading_corp.db"
$pm = "/home/azureuser/trading_corp/data/prediction_markets.db"
$out = "/home/azureuser/kcv2_archive_stage/kcv2_prod_tables.sql.gz"
$vpy = "/home/azureuser/trading_corp/venv/bin/python3"
$scr = "C:\Users\AA Incorporado\cc\_recon_scratch"
$ErrorActionPreference = "Continue"   # native ssh stderr (progress) must NOT halt the pipeline (PS 5.1 trap)
$res = Get-Content -Raw $py | & $ssh -o ConnectTimeout=25 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | $vpy - $legacy $pm $out"
($res -join "`n") | Out-File -FilePath "$scr\legpm_p5_dump.txt" -Encoding utf8
Write-Host "=== STDOUT (manifest JSON) ==="; $res | Write-Host
Write-Host "---- legpm_p5_dump exit ($LASTEXITCODE) ----"
