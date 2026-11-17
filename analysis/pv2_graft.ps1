# PV2 GRAFT (JACK RUNS THIS -- it WRITES the live tree): deploys the Data API v2 migration (2 files)
# from the pm-polyv2-migration-2026-11-17 worktree. Drift-gated (box==BASE), stage-gated (staged==TARGET),
# backup kept, verify live==TARGET, py_compile + import closure, ROLLBACK-ALL on any failure.
# After it reports APPLIED+VERIFIED: restart trading-corp (restart_tc.ps1) + prediction-markets-web,
# then run pv2_postverify.ps1.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$wt = 'C:\Users\AA Incorporado\cc-pm-polyv2-wt'
$tgz = Join-Path $env:TEMP 'pv2_graft.tgz'
$h = 'azureuser@trading.jacksumner.com'
if (Test-Path $tgz) { Remove-Item -Force $tgz }
tar -czf $tgz -C $wt trading_corp/data/polymarket_data_api_client.py trading_corp/prediction_markets/loss_grounding.py
if ($LASTEXITCODE -ne 0) { throw "tar failed: $LASTEXITCODE" }
scp -o ConnectTimeout=20 -o BatchMode=yes $tgz ("{0}:/home/azureuser/pv2_graft.tgz" -f $h)
if ($LASTEXITCODE -ne 0) { throw "scp failed: $LASTEXITCODE" }
Get-Content -LiteralPath 'C:\Users\AA Incorporado\cc\pv2_graft.sh' -Raw | ssh -o ConnectTimeout=20 -o BatchMode=yes $h "tr -d '\r\357\273\277' | bash"
