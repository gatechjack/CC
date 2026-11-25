# secrev_housekeeping_stage.ps1 -- stage secrev_housekeeping.sh (scp, byte-exact) + sha256 verify. No root.
param()
$ErrorActionPreference='Continue'; $OutputEncoding=New-Object System.Text.UTF8Encoding $false
$hostt='azureuser@trading.jacksumner.com'
$local='C:\Users\AA Incorporado\cc\secrev_housekeeping.sh'; $remote='/home/azureuser/secrev_housekeeping.sh'
$b=[IO.File]::ReadAllBytes($local); if((($b|?{$_ -eq 13}).Count) -ne 0 -or (($b|?{$_ -gt 127}).Count) -ne 0){ Write-Output "ABORT: not LF/ASCII"; exit 1 }
$localSha=(-join ([Security.Cryptography.SHA256]::Create().ComputeHash($b) | ForEach-Object { $_.ToString('x2') }))
& scp -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new -- $local ($hostt+':'+$remote) 2>&1 | ForEach-Object { Write-Output ("  scp: "+$_) }
$remoteSha=(ssh -o BatchMode=yes -o ConnectTimeout=20 $hostt ("sha256sum "+$remote+" 2>/dev/null | cut -d' ' -f1")).Trim()
Write-Output ("  local sha256: "+$localSha); Write-Output ("  box   sha256: "+$remoteSha)
if($localSha -eq $remoteSha -and $localSha.Length -eq 64){ Write-Output "STAGE OK: secrev_housekeeping.sh verified." } else { Write-Output "STAGE FAILED -- do not run." }
