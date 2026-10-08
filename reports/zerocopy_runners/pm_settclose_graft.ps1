# pm_settclose_graft.ps1 - Phase 3: guarded graft of settlement.py (TARGET a0eb5a43) onto the box. NO restart.
# scp the file binary-exact (non-ASCII) -> stream the ASCII apply .sh (drift-gate box==BASE a1abe0e3 / staged==TARGET,
# backup, apply CR-stripped, re-verify live==TARGET, py_compile+import, rollback-all on any failure).
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h   = 'azureuser@trading.jacksumner.com'
$src = 'C:\Users\AA Incorporado\cc-settlement-close-wt\trading_corp\prediction_markets\settlement.py'
$sh  = 'C:\Users\AA Incorporado\cc\pm_settclose_graft.sh'
Write-Output '--- scp staged settlement.py -> box /tmp (binary-exact) ---'
scp -o ConnectTimeout=25 $src "$($h):/tmp/settclose_settlement.py"
Write-Output ("scp exit: " + $LASTEXITCODE)
if ($LASTEXITCODE -ne 0) { Write-Output 'scp FAILED -> aborting (box untouched)'; exit 1 }
Write-Output '--- guarded apply (box-side drift-gate/backup/apply/verify/rollback) ---'
Get-Content -LiteralPath $sh -Raw | ssh -o ConnectTimeout=25 $h "tr -d '\r\357\273\277' | bash"
