# pk_realizedpnl_probe_ro.ps1 -- READ-ONLY realizedPnl-semantics probe for Prediction Markets P1
# (is /closed-positions realizedPnl PER-LEG REAL or MIRRORED across negRisk legs?). Ships the
# committed sibling pm_realizedpnl_probe.py to the box via az run-command (base64-chunked), pipes it
# to the venv python3 with cwd=repo-root so 'from trading_corp...' resolves; reuses
# PolymarketDataAPIClient (no hand-rolled HTTP). Public no-auth API, NO writes, NO DB, NO engine.
# Requires pm_realizedpnl_probe.py in the same folder. Run: powershell -ep bypass -f .\pk_realizedpnl_probe_ro.ps1
$ErrorActionPreference = 'Stop'
$pyPath = Join-Path $PSScriptRoot 'pm_realizedpnl_probe.py'
if (-not (Test-Path $pyPath)) { throw "missing sibling probe: $pyPath" }
$py  = [IO.File]::ReadAllText($pyPath) -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_rpp_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pm_probe.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
$run = "base64 -d /tmp/pm_probe.b64 | (cd /home/azureuser/trading_corp && PYTHONPATH=. venv/bin/python3 -); rm -f /tmp/pm_probe.b64"
[IO.File]::WriteAllText($tf, $run + "`n", $enc)
Write-Host "== PREDICTION MARKETS realizedPnl SEMANTICS PROBE (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
