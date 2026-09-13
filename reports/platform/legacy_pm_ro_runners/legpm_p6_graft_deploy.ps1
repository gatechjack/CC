param([ValidateSet("verify","graft")]$Mode="verify")
$ErrorActionPreference = "Continue"; $OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "legpm_p6_graft_deploy.sh"
$localMain = "C:\Users\AA Incorporado\cc-legpm-removal-wt\trading_corp\main.py"
$localCfg  = "C:\Users\AA Incorporado\cc-legpm-removal-wt\config\strategies.yaml"
$EXP_MAIN = "c15b4de64ad19b814580cb6ed311262f"
$EXP_CFG  = "1657119b9a872472a53328ac57e0b5ae"

$cands = @("$env:SystemRoot\System32\OpenSSH","$env:SystemRoot\Sysnative\OpenSSH","$env:ProgramFiles\Git\usr\bin","${env:ProgramFiles(x86)}\Git\usr\bin","$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($n){ $c=Get-Command $n -ErrorAction SilentlyContinue; if($c){return $c.Source}; foreach($d in $cands){$p=Join-Path $d "$n.exe"; if(Test-Path $p){return $p}}; throw "$n not found" }
$ssh = Find-Exe "ssh"; $scp = Find-Exe "scp"

function Md5CrStripped($path){
  $b=[IO.File]::ReadAllBytes($path); $ms=New-Object IO.MemoryStream
  foreach($x in $b){ if($x -ne 13){ $ms.WriteByte($x) } }
  $md5=[Security.Cryptography.MD5]::Create(); ($md5.ComputeHash($ms.ToArray())|%{ $_.ToString("x2") }) -join ""
}

# ---- local guards (do not transfer the wrong bytes) ----
if ((([IO.File]::ReadAllBytes($sh))|?{$_ -gt 127}).Count -gt 0){ throw "sh non-ASCII" }
$lm = Md5CrStripped $localMain; $lc = Md5CrStripped $localCfg
Write-Host "local grafted main.py CR-md5 = $lm  (expect $EXP_MAIN)"
Write-Host "local grafted config  CR-md5 = $lc  (expect $EXP_CFG)"
if ($lm -ne $EXP_MAIN){ throw "LOCAL main.py md5 mismatch -- refusing to transfer" }
if ($lc -ne $EXP_CFG){ throw "LOCAL config md5 mismatch -- refusing to transfer" }
Write-Host "MODE=$Mode"

# ---- stage: scp both files to a box scratch dir (NOT the live path) ----
& $ssh -o ConnectTimeout=25 $h "mkdir -p /home/azureuser/legpm_deploy_scratch"
& $scp -o ConnectTimeout=25 $localMain "${h}:/home/azureuser/legpm_deploy_scratch/main.py.staged"
& $scp -o ConnectTimeout=25 $localCfg  "${h}:/home/azureuser/legpm_deploy_scratch/strategies.yaml.staged"

# ---- run verify/graft on the box ----
$out = "C:\Users\AA Incorporado\cc\_recon_scratch"; if (-not (Test-Path $out)) { New-Item -ItemType Directory -Force $out | Out-Null }
$res = Get-Content -Raw $sh | & $ssh -o ConnectTimeout=25 -o ServerAliveInterval=20 $h "tr -d '\r\357\273\277' | MODE=$Mode bash"
$res | Out-File -FilePath "$out\legpm_p6_graft_deploy_$Mode.txt" -Encoding utf8; $res | Write-Host
Write-Host "---- legpm_p6_graft_deploy ($Mode) exit ($LASTEXITCODE) ----"
