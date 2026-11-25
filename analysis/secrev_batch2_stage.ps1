# secrev_batch2_stage.ps1 -- stage the modified main.py + deploy/rollback .sh to the box (scp, byte-exact) + verify.
# main.py verified by CR-stripped md5 (== 6e52c9bd...); .sh by sha256. No root, no service change, no execution.
param()
$ErrorActionPreference='Continue'
$OutputEncoding=New-Object System.Text.UTF8Encoding $false
$hostt='azureuser@trading.jacksumner.com'
$items=@(
  @{ local='C:\Users\AA Incorporado\cc-batch2-2026-11-24-wt\trading_corp\main.py'; remote='/home/azureuser/secrev_batch2_main.py'; kind='md5cr'; expect='6e52c9bd1a36eb1bc801c5772f4178c0' },
  @{ local='C:\Users\AA Incorporado\cc\secrev_batch2_deploy.sh';    remote='/home/azureuser/secrev_batch2_deploy.sh';    kind='sha' },
  @{ local='C:\Users\AA Incorporado\cc\secrev_batch2_rollback.sh';  remote='/home/azureuser/secrev_batch2_rollback.sh';  kind='sha' }
)
function LocalShaFile([string]$p){ (-join ([Security.Cryptography.SHA256]::Create().ComputeHash([IO.File]::ReadAllBytes($p)) | ForEach-Object { $_.ToString('x2') })) }
function LocalMd5Cr([string]$p){ $b=[IO.File]::ReadAllBytes($p); $ms=New-Object IO.MemoryStream; foreach($x in $b){ if($x -ne 13){ $ms.WriteByte($x) } }; (-join ([Security.Cryptography.MD5]::Create().ComputeHash($ms.ToArray()) | ForEach-Object { $_.ToString('x2') })) }
# .sh must be LF/ASCII
foreach($it in $items){ if($it.kind -eq 'sha'){ $b=[IO.File]::ReadAllBytes($it.local); if((($b|?{$_ -eq 13}).Count) -ne 0 -or (($b|?{$_ -gt 127}).Count) -ne 0){ Write-Output ("ABORT: "+(Split-Path $it.local -Leaf)+" not LF/ASCII"); exit 1 } } }
$allOk=$true
foreach($it in $items){
  Write-Output ("scp " + (Split-Path $it.local -Leaf) + " -> " + $it.remote)
  & scp -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new -- $it.local ($hostt+':'+$it.remote) 2>&1 | ForEach-Object { Write-Output ("  scp: "+$_) }
  if($it.kind -eq 'md5cr'){
    $loc=LocalMd5Cr $it.local
    $box=(ssh -o BatchMode=yes -o ConnectTimeout=20 $hostt ("tr -d '\r' < "+$it.remote+" | md5sum | cut -c1-32")).Trim()
    Write-Output ("  local md5(CR-stripped): "+$loc); Write-Output ("  box   md5(CR-stripped): "+$box); Write-Output ("  expect: "+$it.expect)
    if($loc -eq $box -and $box -eq $it.expect){ Write-Output "  MATCH" } else { Write-Output "  *** MISMATCH ***"; $allOk=$false }
  } else {
    $loc=LocalShaFile $it.local
    $box=(ssh -o BatchMode=yes -o ConnectTimeout=20 $hostt ("sha256sum "+$it.remote+" 2>/dev/null | cut -d' ' -f1")).Trim()
    Write-Output ("  local sha256: "+$loc); Write-Output ("  box   sha256: "+$box)
    if($loc -eq $box -and $loc.Length -eq 64){ Write-Output "  MATCH" } else { Write-Output "  *** MISMATCH ***"; $allOk=$false }
  }
}
Write-Output ""
if($allOk){ Write-Output "STAGE OK: main.py + deploy + rollback staged and verified. Ready for the root deploy." } else { Write-Output "STAGE FAILED -- do not run the deploy." }
