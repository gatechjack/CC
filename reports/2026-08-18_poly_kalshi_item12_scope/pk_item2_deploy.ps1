# ============================================================================
# ITEM 2 DEPLOY -- brokers/kalshi.py mark quote() fix (single file, one restart).
# Abort-safe: aborts WITHOUT restart on drift or install md5 mismatch (backup
# restored on install mismatch). LF-normalized md5 throughout.
# Run:  powershell -ep bypass -f .\pk_item2_deploy.ps1
# ============================================================================
$ErrorActionPreference = 'Stop'
$LOCAL   = 'C:\Users\AA Incorporado\cc\pk_kalshi_new.py'
$EXPECT  = '7fb2688f39b9fa3d425e1e0136ee6c3c'   # NEW brokers/kalshi.py (LF-md5)
$PREV    = '18626cf0ddcdf6c3663be7d9602abbba'   # pre-fix baseline the box MUST have (Stage 1)
$RG='RG-SHARED-PROD'; $VM='tc-prod-vm'

$h = (Get-FileHash -Algorithm MD5 $LOCAL).Hash.ToLower()   # local file is pure-LF -> raw md5 == LF-md5
if ($h -ne $EXPECT) { Write-Host "ABORT local md5 mismatch: $h != $EXPECT"; exit 1 }
Write-Host "local brokers/kalshi.py md5 OK ($h)"

$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($LOCAL))
Write-Host "base64 length $($b64.Length) -- uploading in chunks (via @file)"
$size = 50000; $n = 0; $first = $true
$tf = Join-Path $env:TEMP 'pk_item2_chunk.sh'
$enc = New-Object Text.UTF8Encoding($false)
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_kalshi.b64`n", $enc)
    az vm run-command invoke -g $RG -n $VM --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $n++; $first = $false
    Write-Host "  chunk $n uploaded ($($chunk.Length) chars)"
}
Remove-Item $tf -ErrorAction SilentlyContinue

$applyBash = @'
set -e
B=/tmp/pk_kalshi.b64; NEW=/tmp/pk_kalshi_new.py
base64 -d $B > $NEW
NEWMD5=$(tr -d '\r' < $NEW | md5sum | cut -d" " -f1); echo "TRANSFER_LFMD5 $NEWMD5"
[ "$NEWMD5" = "7fb2688f39b9fa3d425e1e0136ee6c3c" ] || { echo ABORT_BAD_TRANSFER; exit 3; }
F=/home/azureuser/trading_corp/trading_corp/brokers/kalshi.py
CUR=$(tr -d '\r' < $F | md5sum | cut -d" " -f1); echo "CUR_BEFORE_LFMD5 $CUR"
[ "$CUR" = "18626cf0ddcdf6c3663be7d9602abbba" ] || { echo ABORT_DRIFT; exit 4; }
TS=$(date -u +%Y%m%d_%H%M%S); BK=${F}.bak_item2_$TS
cp $F $BK; echo "BACKUP $BK"
cp $NEW $F
INST=$(tr -d '\r' < $F | md5sum | cut -d" " -f1); echo "INSTALLED_LFMD5 $INST"
[ "$INST" = "7fb2688f39b9fa3d425e1e0136ee6c3c" ] || { cp $BK $F; echo ABORT_INSTALL_RESTORED; exit 5; }
RS=$(date -u +"%Y-%m-%d %H:%M:%S"); echo "RESTART_AT_UTC $RS"
systemctl restart trading-corp
for i in $(seq 1 66); do
  sleep 5
  if journalctl -u trading-corp --since "$RS" --no-pager 2>/dev/null | grep -q "Poly->Kalshi MLB copy WIRED"; then
    echo "WIRED_SECONDS_FROM_RESTART $((i*5))"; break; fi
done
echo "PID $(systemctl show trading-corp -p MainPID --value)"
echo "=== armed WIRED + roster invariant (since restart) ==="
journalctl -u trading-corp --since "$RS" --no-pager 2>/dev/null | grep -E "Poly->Kalshi MLB copy WIRED|roster invariant"
echo "TRACEBACKS_SINCE_RESTART $(journalctl -u trading-corp --since "$RS" --no-pager 2>/dev/null | grep -c 'Traceback (most recent call last)')"
'@
$applyBash = $applyBash -replace "`r", ""
$ab64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($applyBash))
$applyCmd = "printf %s '$ab64' | base64 -d | bash"
Write-Host "== ITEM 2 APPLY (abort-safe) + RESTART + BOOT PROOF (waits ~3-5 min) =="
az vm run-command invoke -g $RG -n $VM --command-id RunShellScript --scripts $applyCmd --query "value[0].message" -o tsv
