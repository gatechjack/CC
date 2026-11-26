# secrev_batch2b_stage.ps1 -- stage deploy + rollback .sh (scp, byte-exact) + sha256 verify. No root.
param()
$ErrorActionPreference='Continue'; $OutputEncoding=New-Object System.Text.UTF8Encoding $false
$hostt='azureuser@trading.jacksumner.com'
$items=@(
  @{ local='C:\Users\AA Incorporado\cc\secrev_batch2b_deploy.sh';   remote='/home/azureuser/secrev_batch2b_deploy.sh' },
  @{ local='C:\Users\AA Incorporado\cc\secrev_batch2b_rollback.sh'; remote='/home/azureuser/secrev_batch2b_rollback.sh' }
)
$allOk=$true
foreach($it in $items){ $b=[IO.File]::ReadAllBytes($it.local); if((($b|?{$_ -eq 13}).Count) -ne 0 -or (($b|?{$_ -gt 127}).Count) -ne 0){ Write-Output ("ABORT: "+(Split-Path $it.local -Leaf)+" not LF/ASCII"); exit 1 }
  $loc=(-join ([Security.Cryptography.SHA256]::Create().ComputeHash($b) | ForEach-Object { $_.ToString('x2') }))
  & scp -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new -- $it.local ($hostt+':'+$it.remote) 2>&1 | ForEach-Object { Write-Output ("  scp: "+$_) }
  $box=(ssh -o BatchMode=yes -o ConnectTimeout=20 $hostt ("sha256sum "+$it.remote+" 2>/dev/null | cut -d' ' -f1")).Trim()
  Write-Output ("  "+(Split-Path $it.local -Leaf)+" local="+$loc.Substring(0,16)+" box="+$box.Substring(0,16)+" "+$(if($loc -eq $box){'MATCH'}else{'MISMATCH'; $allOk=$false})) }
Write-Output ""; if($allOk){ Write-Output "STAGE OK." } else { Write-Output "STAGE FAILED." }
