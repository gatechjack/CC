# PVC GRAFT (JACK RUNS THIS -- it WRITES the live tree): deploys EITHER
#   -Mode v2       : the Data API v2 migration alone (2 files -- byte-identical to pv2_graft.ps1), OR
#   -Mode v2chase  : v2 + the maximum-chase rule / gate C (6 files)
# from the pm-polyv2-migration-2026-11-17 worktree. Drift-gated (box==BASE), stage-gated (staged==TARGET),
# backup kept, verify live==TARGET, py_compile + import closure, ROLLBACK-ALL on any failure.
# After APPLIED+VERIFIED: restart trading-corp (restart_tc.ps1) + prediction-markets-web, then run
#   powershell -ep bypass -f "<worktree>\analysis\pvc_postverify.ps1" -Mode <same mode>
param(
    [Parameter(Mandatory = $true)][ValidateSet('v2', 'v2chase')][string]$Mode
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$wt = 'C:\Users\AA Incorporado\cc-pm-polyv2-wt'
$tgz = Join-Path $env:TEMP 'pvc_graft.tgz'
$h = 'azureuser@trading.jacksumner.com'
$files = @('trading_corp/data/polymarket_data_api_client.py', 'trading_corp/prediction_markets/loss_grounding.py')
if ($Mode -eq 'v2chase') {
    $files += @('trading_corp/prediction_markets/execution.py', 'trading_corp/prediction_markets/live_driver.py',
                'trading_corp/prediction_markets/heartbeat.py', 'trading_corp/prediction_markets/db.py')
}
if (Test-Path $tgz) { Remove-Item -Force $tgz }
tar -czf $tgz -C $wt @files
if ($LASTEXITCODE -ne 0) { throw "tar failed: $LASTEXITCODE" }
scp -o ConnectTimeout=20 -o BatchMode=yes $tgz ("{0}:/home/azureuser/pvc_graft.tgz" -f $h)
if ($LASTEXITCODE -ne 0) { throw "scp failed: $LASTEXITCODE" }
Get-Content -LiteralPath (Join-Path $wt 'analysis\pvc_graft.sh') -Raw |
    ssh -o ConnectTimeout=20 -o BatchMode=yes $h ("tr -d '\r\357\273\277' | MODE={0} bash" -f $Mode)
