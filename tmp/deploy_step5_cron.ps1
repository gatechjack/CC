# Step 5: redeploy updated reconciler (now respects audit_corrected) +
# install systemd timer + service unit for daily reality reconciliation.

$ErrorActionPreference = "Stop"
$REPO = "C:\Users\AA Incorporado\CC"
$VM = "tc-prod-vm"
$RG = "rg-shared-prod"

function Encode-LF-Base64($path) {
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $lf = [byte[]]@($bytes | Where-Object { $_ -ne 13 })
    return [Convert]::ToBase64String($lf)
}

function MD5-LF($path) {
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $lf = [byte[]]@($bytes | Where-Object { $_ -ne 13 })
    $md5 = [System.Security.Cryptography.MD5]::Create()
    $hash = $md5.ComputeHash($lf)
    return ($hash | ForEach-Object { $_.ToString("x2") }) -join ""
}

$recPath = Join-Path $REPO "scripts\audit_reality_reconciler.py"
$recB64 = Encode-LF-Base64 $recPath
$recMD5 = MD5-LF $recPath
Write-Host "Reconciler new md5: $recMD5"

$deployScript = @"
#!/bin/bash
set -e
BASE=/home/azureuser/trading_corp

# Redeploy updated reconciler
echo "$recB64" | base64 -d > /tmp/audit_reality_reconciler.py.new
REC_NEW_MD5=`$(md5sum /tmp/audit_reality_reconciler.py.new | awk '{print `$1}')
if [ "`$REC_NEW_MD5" != "$recMD5" ]; then
  echo "FAIL: uploaded reconciler md5 mismatch: `$REC_NEW_MD5 != $recMD5"
  exit 2
fi
mv /tmp/audit_reality_reconciler.py.new `$BASE/scripts/audit_reality_reconciler.py
chown azureuser:azureuser `$BASE/scripts/audit_reality_reconciler.py
chmod 644 `$BASE/scripts/audit_reality_reconciler.py
echo "OK: updated reconciler landed (md5 `$REC_NEW_MD5)"

# Install systemd service unit
cat > /etc/systemd/system/tc-audit-reality.service <<'EOF'
[Unit]
Description=Trading Corp audit-vs-reality reconciler (catches silent v2 lifecycle audit-log failures)
Documentation=https://github.com/gatechjack/CC/blob/main/reports/bitunix_v2_fix_2026-05-20.md

[Service]
Type=oneshot
User=azureuser
Group=azureuser
WorkingDirectory=/home/azureuser/trading_corp
ExecStart=/home/azureuser/trading_corp/venv/bin/python scripts/audit_reality_reconciler.py --db sqlite:////home/azureuser/trading_corp/data/trading_corp.db
StandardOutput=journal
StandardError=journal
# Exit code 1 from reconciler (any mismatch) propagates as service failure -> journal WARN
SuccessExitStatus=0
EOF
chmod 644 /etc/systemd/system/tc-audit-reality.service
echo "OK: tc-audit-reality.service installed"

# Install systemd timer (daily, with random jitter)
cat > /etc/systemd/system/tc-audit-reality.timer <<'EOF'
[Unit]
Description=Daily run of trading-corp audit-vs-reality reconciler

[Timer]
OnCalendar=*-*-* 06:00:00
Persistent=true
RandomizedDelaySec=600
Unit=tc-audit-reality.service

[Install]
WantedBy=timers.target
EOF
chmod 644 /etc/systemd/system/tc-audit-reality.timer
echo "OK: tc-audit-reality.timer installed"

systemctl daemon-reload
systemctl enable tc-audit-reality.timer
systemctl start tc-audit-reality.timer
echo "=== timer status ==="
systemctl status tc-audit-reality.timer --no-pager | head -10 || true
echo ""
echo "=== triggering service once (first clean run) ==="
systemctl start tc-audit-reality.service
sleep 5
echo "--- service exit status ---"
systemctl status tc-audit-reality.service --no-pager | head -15 || true
echo ""
echo "--- service journal output (last run) ---"
journalctl -u tc-audit-reality.service --since "1 minute ago" --no-pager | tail -40

echo ""
echo "=== DONE ==="
"@

$deployFile = "$env:TEMP\deploy_step5_combined.sh"
[System.IO.File]::WriteAllText($deployFile, $deployScript.Replace("`r`n","`n"))
Write-Host "Deploy script size: $((Get-Item $deployFile).Length)"
az vm run-command invoke -n $VM -g $RG --command-id RunShellScript --scripts "@$deployFile" --query "value[0].message" -o tsv | Out-File -FilePath "$env:TEMP\deploy_step5_output.txt" -Encoding utf8
Get-Content "$env:TEMP\deploy_step5_output.txt" -Encoding utf8
