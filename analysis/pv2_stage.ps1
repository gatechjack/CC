# PV2 stage: tar the worktree (trading_corp + tests + pyproject) -> scp to box -> stream pv2_run1.sh.
# Box-scratch only: the live tree is READ (BASE copies + venv python), never written. No creds.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$wt = 'C:\Users\AA Incorporado\cc-pm-polyv2-wt'
$tgz = Join-Path $env:TEMP 'pv2_stage.tgz'
$h = 'azureuser@trading.jacksumner.com'
if (Test-Path $tgz) { Remove-Item -Force $tgz }
tar -czf $tgz -C $wt trading_corp tests scripts pyproject.toml
if ($LASTEXITCODE -ne 0) { throw "tar failed: $LASTEXITCODE" }
$mb = [math]::Round((Get-Item $tgz).Length / 1MB, 1)
Write-Output ("tarball: {0} MB" -f $mb)
scp -o ConnectTimeout=20 -o BatchMode=yes $tgz ("{0}:/home/azureuser/pv2_stage.tgz" -f $h)
if ($LASTEXITCODE -ne 0) { throw "scp failed: $LASTEXITCODE" }
Get-Content -LiteralPath 'C:\Users\AA Incorporado\cc\pv2_run1.sh' -Raw | ssh -o ConnectTimeout=20 -o BatchMode=yes $h "tr -d '\r\357\273\277' | bash"
