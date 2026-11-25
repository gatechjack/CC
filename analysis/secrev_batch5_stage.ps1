# secrev_batch5_stage.ps1 -- stage secrev_batch5.sh to the box (scp, byte-exact) + sha256 verify. No root.
param()
$ErrorActionPreference='Continue'
$OutputEncoding=New-Object System.Text.UTF8Encoding $false
$hostt='azureuser@trading.jacksumner.com'
$local='C:\Users\AA Incorporado\cc\secrev_batch5.sh'; $remote='/home/azureuser/secrev_batch5.sh'
$b=[IO.File]::ReadAllBytes($local); $cr=($b|Where-Object{$_ -eq 13}).Count; $na=($b|Where-Object{$_ -gt 127}).Count
if($cr -ne 0 -or $na -ne 0){ Write-Output "ABORT: CR=$cr nonascii=$na (must be 0/0)"; exit 1 }
$localSha=(-join ([System.Security.Cryptography.SHA256]::Create().ComputeHash($b) | ForEach-Object { $_.ToString('x2') }))
& scp -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new -- $local ($hostt+':'+$remote) 2>&1 | ForEach-Object { Write-Output ("  scp: "+$_) }
$remoteSha=(ssh -o BatchMode=yes -o ConnectTimeout=20 $hostt ("sha256sum "+$remote+" 2>/dev/null | cut -d' ' -f1")).Trim()
Write-Output ("  local sha256: "+$localSha)
Write-Output ("  box   sha256: "+$remoteSha)
if($localSha -eq $remoteSha -and $localSha.Length -eq 64){ Write-Output "STAGE OK: secrev_batch5.sh verified on box." } else { Write-Output "STAGE FAILED -- do not run." }
