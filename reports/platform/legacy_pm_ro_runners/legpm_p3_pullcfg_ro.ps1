$ErrorActionPreference = "Stop"; $OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "legpm_p3_pullcfg_ro.sh"
$cands = @("$env:SystemRoot\System32\OpenSSH","$env:SystemRoot\Sysnative\OpenSSH","$env:ProgramFiles\Git\usr\bin","${env:ProgramFiles(x86)}\Git\usr\bin","$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($n){ $c=Get-Command $n -ErrorAction SilentlyContinue; if($c){return $c.Source}; foreach($d in $cands){$p=Join-Path $d "$n.exe"; if(Test-Path $p){return $p}}; throw "$n not found" }
$ssh = Find-Exe "ssh"
if ((([IO.File]::ReadAllBytes($sh))|?{$_ -gt 127}).Count -gt 0){ throw "sh non-ASCII" }
$out = "C:\Users\AA Incorporado\cc\_recon_scratch"; New-Item -ItemType Directory -Force -Path $out | Out-Null
$res = Get-Content -Raw $sh | & $ssh -o ConnectTimeout=25 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | bash"
$raw = ($res -join "`n")
# md5 section
$md5 = ($raw -split "===MD5===")[1]; $md5 = ($md5 -split "===B64BEGIN===")[0]
$md5.Trim() | Out-File "$out\box_config_md5.txt" -Encoding utf8
Write-Host "=== box config/*.yaml md5 (CR-stripped) ==="; Write-Host $md5.Trim()
# base64 section -> decode to box_strategies.yaml
$b64 = ($raw -split "===B64BEGIN===")[1]; $b64 = ($b64 -split "===B64END===")[0]
$b64 = ($b64 -replace "\s","")
[IO.File]::WriteAllBytes("$out\box_strategies.yaml", [Convert]::FromBase64String($b64))
$fi = Get-Item "$out\box_strategies.yaml"
Write-Host "box_strategies.yaml written: $($fi.Length) bytes"
Write-Host "---- legpm_p3_pullcfg_ro exit ($LASTEXITCODE) ----"
