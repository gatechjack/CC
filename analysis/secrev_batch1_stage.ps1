# secrev_batch1_stage.ps1 -- stage the Batch 1 ASCII/LF .sh scripts to /home/azureuser via scp (binary-exact),
# then verify each remote file's sha256 == local sha256. No root, no service change, no execution.
# scp does NOT transform bytes, so a clean LF/ASCII file round-trips identically (unlike Get-Content|ssh streaming).
param()
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$hostt = 'azureuser@trading.jacksumner.com'
$files = @(
  @{ local='C:\Users\AA Incorporado\cc\secrev_batch1.sh';          remote='/home/azureuser/secrev_batch1.sh' },
  @{ local='C:\Users\AA Incorporado\cc\secrev_batch1_rollback.sh'; remote='/home/azureuser/secrev_batch1_rollback.sh' }
)
function LocalSha([string]$path){
  $bytes = [IO.File]::ReadAllBytes($path)
  return (-join ([System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }))
}
# pre-flight: assert the local files are CR-free (LF only) so scp copies exactly what we hashed
foreach($f in $files){
  $b=[IO.File]::ReadAllBytes($f.local)
  $cr=($b | Where-Object {$_ -eq 13}).Count
  $na=($b | Where-Object {$_ -gt 127}).Count
  if($cr -ne 0 -or $na -ne 0){ Write-Output ("ABORT: " + (Split-Path $f.local -Leaf) + " has CR=$cr nonascii=$na (must be 0/0 for exact staging)"); exit 1 }
}
$allOk = $true
foreach($f in $files){
  $localSha = LocalSha $f.local
  Write-Output ("scp " + (Split-Path $f.local -Leaf) + " -> " + $f.remote)
  & scp -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new -- $f.local ($hostt + ':' + $f.remote) 2>&1 | ForEach-Object { Write-Output ("  scp: " + $_) }
  $remoteSha = (ssh -o BatchMode=yes -o ConnectTimeout=20 $hostt ("sha256sum " + $f.remote + " 2>/dev/null | cut -d' ' -f1")).Trim()
  Write-Output ("  local sha256: " + $localSha)
  Write-Output ("  box   sha256: " + $remoteSha)
  if($localSha -eq $remoteSha -and $localSha.Length -eq 64){ Write-Output "  MATCH" } else { Write-Output "  *** MISMATCH -- DO NOT RUN ***"; $allOk=$false }
}
Write-Output ""
if($allOk){ Write-Output "STAGE OK: both scripts present and sha256-verified on the box. Safe to run the root one-liner (STEP 2)." }
else { Write-Output "STAGE FAILED: fix before running anything as root." }
