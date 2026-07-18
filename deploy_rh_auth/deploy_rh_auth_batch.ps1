# RH-auth batch deploy runner (ITEMS 1/2/3). Operator paste (ONE line):
#   DRY-RUN (default, no writes):  powershell -ep bypass -f .\deploy_rh_auth\deploy_rh_auth_batch.ps1
#   APPLY to disk (NO restart):    powershell -ep bypass -f .\deploy_rh_auth\deploy_rh_auth_batch.ps1 -Apply
# Streams the patcher (+ template on -Apply) to prod LF-clean, runs the drift-gated patcher.
# Apply = .bak_rhauth backups + py_compile + an import pre-flight. Does NOT restart the engine
# (schedule the restart separately: Bitunix flat -> restart -> bootsmoke_rh_auth.sh).
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$h    = 'azureuser@trading.jacksumner.com'
$base = 'C:\Users\AA Incorporado\cc\deploy_rh_auth'

function Send-LF([string]$src, [string]$dest) {
  $L1 = [Text.Encoding]::GetEncoding('iso-8859-1')
  $lf = $L1.GetBytes($L1.GetString([IO.File]::ReadAllBytes($src)).Replace("`r`n","`n"))
  $tmp = Join-Path $env:TEMP ([IO.Path]::GetFileName($dest))
  [IO.File]::WriteAllBytes($tmp, $lf)
  scp "$tmp" "${h}:$dest"
}

Send-LF "$base\apply_rh_auth_batch.py" 'apply_rh_auth_batch.py'

if ($Apply) {
  Send-LF "$base\templates\rh_session_panel.html" 'trading_corp/trading_corp/web/templates/rh_session_panel.html'
  Write-Host '=== APPLY (with .bak_rhauth backups) ==='
  $c = 'cd /home/azureuser/trading_corp && PYTHONPATH=/home/azureuser/trading_corp venv/bin/python ~/apply_rh_auth_batch.py --apply'
  $c | ssh $h "tr -d '\r'|bash"
  Write-Host '=== IMPORT PRE-FLIGHT (no restart) ==='
  $p = 'cd /home/azureuser/trading_corp && PYTHONPATH=/home/azureuser/trading_corp venv/bin/python -c ''import trading_corp.web.routes, trading_corp.brokers.robinhood, trading_corp.agents.data_exec, trading_corp.utils.secrets; print("IMPORT OK")'''
  $p | ssh $h "tr -d '\r'|bash"
  Write-Host 'Applied to DISK, NOT restarted. Next: schedule the restart window, then run bootsmoke_rh_auth.sh.'
} else {
  Write-Host '=== DRY-RUN (no writes) -- unified diff of every change ==='
  $c = 'cd /home/azureuser/trading_corp && PYTHONPATH=/home/azureuser/trading_corp venv/bin/python ~/apply_rh_auth_batch.py'
  $c | ssh $h "tr -d '\r'|bash"
  Write-Host 'Review the diffs above. When satisfied, re-run with -Apply.'
}
