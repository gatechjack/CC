# Deploy step 2: ship the fixed paper_trade_replay.py + new audit_reality_reconciler.py to prod.
# CRLF -> LF normalize, base64 upload via az vm run-command, restart service.

$ErrorActionPreference = "Stop"
$REPO = "C:\Users\AA Incorporado\CC"
$VM = "tc-prod-vm"
$RG = "rg-shared-prod"

function Encode-LF-Base64($path) {
    $bytes = [System.IO.File]::ReadAllBytes($path)
    # Strip CR bytes (0x0D) -> LF-only
    $lf = [byte[]]@($bytes | Where-Object { $_ -ne 13 })
    return [Convert]::ToBase64String($lf)
}

$ptrPath = Join-Path $REPO "trading_corp\agents\paper_trade_replay.py"
$recPath = Join-Path $REPO "scripts\audit_reality_reconciler.py"

$ptrB64 = Encode-LF-Base64 $ptrPath
$recB64 = Encode-LF-Base64 $recPath

Write-Host "paper_trade_replay.py LF-b64 len: $($ptrB64.Length)"
Write-Host "audit_reality_reconciler.py LF-b64 len: $($recB64.Length)"

# Compute expected LF md5 for post-deploy verification
function MD5-LF($path) {
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $lf = [byte[]]@($bytes | Where-Object { $_ -ne 13 })
    $md5 = [System.Security.Cryptography.MD5]::Create()
    $hash = $md5.ComputeHash($lf)
    return ($hash | ForEach-Object { $_.ToString("x2") }) -join ""
}
$ptrExpectedMD5 = MD5-LF $ptrPath
$recExpectedMD5 = MD5-LF $recPath
Write-Host "Expected prod md5 (paper_trade_replay.py): $ptrExpectedMD5"
Write-Host "Expected prod md5 (audit_reality_reconciler.py): $recExpectedMD5"

# Build the deploy shell script — write both files, chown, then restart.
$deployScript = @"
#!/bin/bash
set -e
BASE=/home/azureuser/trading_corp

# Upload paper_trade_replay.py (running as root via az run-command)
echo "$ptrB64" | base64 -d > /tmp/paper_trade_replay.py.new
PTR_MD5_NEW=`$(md5sum /tmp/paper_trade_replay.py.new | awk '{print `$1}')
if [ "`$PTR_MD5_NEW" != "$ptrExpectedMD5" ]; then
  echo "FAIL: uploaded paper_trade_replay.py md5 mismatch: `$PTR_MD5_NEW != $ptrExpectedMD5"
  exit 2
fi
mv /tmp/paper_trade_replay.py.new `$BASE/trading_corp/agents/paper_trade_replay.py
chown azureuser:azureuser `$BASE/trading_corp/agents/paper_trade_replay.py
chmod 644 `$BASE/trading_corp/agents/paper_trade_replay.py
echo "OK: paper_trade_replay.py landed (md5 `$PTR_MD5_NEW)"

# Upload audit_reality_reconciler.py (new file)
echo "$recB64" | base64 -d > /tmp/audit_reality_reconciler.py.new
REC_MD5_NEW=`$(md5sum /tmp/audit_reality_reconciler.py.new | awk '{print `$1}')
if [ "`$REC_MD5_NEW" != "$recExpectedMD5" ]; then
  echo "FAIL: uploaded audit_reality_reconciler.py md5 mismatch: `$REC_MD5_NEW != $recExpectedMD5"
  exit 2
fi
mv /tmp/audit_reality_reconciler.py.new `$BASE/scripts/audit_reality_reconciler.py
chown azureuser:azureuser `$BASE/scripts/audit_reality_reconciler.py
chmod 644 `$BASE/scripts/audit_reality_reconciler.py
echo "OK: audit_reality_reconciler.py landed (md5 `$REC_MD5_NEW)"

# Verify import path before restart
sudo -u azureuser python3 -c "import sys; sys.path.insert(0,'/home/azureuser/trading_corp'); from trading_corp.agents.paper_trade_replay import _bitunix_kline_fetcher; print('import OK')"

# Restart service
echo "=== restarting trading-corp ==="
PID_BEFORE=`$(sudo -u azureuser cat /home/azureuser/trading_corp/data/trading_corp.pid 2>/dev/null || echo "none")
echo "pid before: `$PID_BEFORE"
sudo systemctl restart trading-corp
sleep 6
PID_AFTER=`$(sudo -u azureuser cat /home/azureuser/trading_corp/data/trading_corp.pid 2>/dev/null || echo "none")
echo "pid after:  `$PID_AFTER"
systemctl is-active trading-corp
curl -sS -o /dev/null -w "healthz HTTP %{http_code}\n" http://127.0.0.1:8000/healthz || echo "healthz probe failed"

# Sanity: confirm boot wiring still reads as expected
sudo journalctl -u trading-corp --since "30 seconds ago" 2>/dev/null | grep -E "BitUnix observer wiring|paper_trade_replay" | head -5

echo "=== DEPLOY OK ==="
"@

$deployFile = "$env:TEMP\deploy_step2_combined.sh"
[System.IO.File]::WriteAllText($deployFile, $deployScript.Replace("`r`n","`n"))
Write-Host "Deploy script size: $((Get-Item $deployFile).Length)"
Write-Host "Invoking az vm run-command (this may take 60-90s)..."

az vm run-command invoke -n $VM -g $RG --command-id RunShellScript --scripts "@$deployFile" --query "value[0].message" -o tsv | Out-File -FilePath "$env:TEMP\deploy_step2_output.txt" -Encoding utf8
Get-Content "$env:TEMP\deploy_step2_output.txt" -Encoding utf8
