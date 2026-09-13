$ErrorActionPreference = "Stop"; $OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$cands = @("$env:SystemRoot\System32\OpenSSH","$env:SystemRoot\Sysnative\OpenSSH","$env:ProgramFiles\Git\usr\bin","${env:ProgramFiles(x86)}\Git\usr\bin","$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($n){ $c=Get-Command $n -ErrorAction SilentlyContinue; if($c){return $c.Source}; foreach($d in $cands){$p=Join-Path $d "$n.exe"; if(Test-Path $p){return $p}}; throw "$n not found" }
$scp = Find-Exe "scp"
$dir = "C:\Users\AA Incorporado\kcv2_archive_2026-09-13"
$dst = "$dir\kcv2_prod_tables.sql.gz"
$boxsha = "a4eef50f7a5913b9cf4b2336aa35e63b8d5bf9feb6e3f6ed019f72a305e6f9f3"
Write-Host "scp pull (278 MB) ..."
& $scp -o ConnectTimeout=25 -o ServerAliveInterval=20 "${h}:/home/azureuser/kcv2_archive_stage/kcv2_prod_tables.sql.gz" $dst
if (-not (Test-Path $dst)) { throw "pull produced no file" }
$fi = Get-Item $dst
$localsha = (Get-FileHash $dst -Algorithm SHA256).Hash.ToLower()
Write-Host ("local bytes={0} ({1:N1} MB)" -f $fi.Length, ($fi.Length/1MB))
Write-Host "box   sha256=$boxsha"
Write-Host "local sha256=$localsha"
Write-Host ("TRANSFER_INTEGRITY=" + ($(if ($localsha -eq $boxsha) {"PASS (byte-identical)"} else {"FAIL"})))