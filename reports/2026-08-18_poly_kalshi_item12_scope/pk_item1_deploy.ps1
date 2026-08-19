# ============================================================================
# ITEM 1 DEPLOY -- ATOMIC two-file (poly_kalshi_executor.py + mlb_poly_kalshi_match.py).
# The executor calls game_key_and_side in the matcher -> the box must NEVER run a
# mismatched pair. BOTH-OR-NEITHER: drift-gate both, install both, verify both; abort
# WITHOUT restart if EITHER fails (restoring both to the pre-fix pair on a partial
# install). One restart picks up both. LF-normalized md5 throughout.
# Run:  powershell -ep bypass -f .\pk_item1_deploy.ps1
# ============================================================================
$ErrorActionPreference = 'Stop'
$RG='RG-SHARED-PROD'; $VM='tc-prod-vm'
$files = @(
  @{ Local='C:\Users\AA Incorporado\cc\pk_exec_new.py';  Remote='/tmp/pk_exec.b64';  Md5='257f6433b4e7d5144cfc6eaae88a7552' },
  @{ Local='C:\Users\AA Incorporado\cc\pk_match_new.py'; Remote='/tmp/pk_match.b64'; Md5='7c191e830b7222cfc59f51cf8c871c97' }
)
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_item1_chunk.sh'
foreach ($f in $files) {
    $h = (Get-FileHash -Algorithm MD5 $f.Local).Hash.ToLower()   # local files are pure-LF -> raw==LF md5
    if ($h -ne $f.Md5) { Write-Host "ABORT local md5 mismatch $($f.Local): $h != $($f.Md5)"; exit 1 }
    Write-Host "local $($f.Local) md5 OK ($h)"
    $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($f.Local))
    $size = 50000; $n = 0; $first = $true
    for ($i = 0; $i -lt $b64.Length; $i += $size) {
        $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
        $op = if ($first) { '>' } else { '>>' }
        [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op $($f.Remote)`n", $enc)
        az vm run-command invoke -g $RG -n $VM --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
        $n++; $first = $false
        Write-Host "  $($f.Remote) chunk $n uploaded ($($chunk.Length) chars)"
    }
}
Remove-Item $tf -ErrorAction SilentlyContinue

$applyBash = @'
set -e
NE=/tmp/pk_exec_new.py; NM=/tmp/pk_match_new.py
base64 -d /tmp/pk_exec.b64 > $NE
base64 -d /tmp/pk_match.b64 > $NM
EMD=$(tr -d '\r' < $NE | md5sum | cut -d" " -f1); echo "TRANSFER_EXEC $EMD"
MMD=$(tr -d '\r' < $NM | md5sum | cut -d" " -f1); echo "TRANSFER_MATCH $MMD"
[ "$EMD" = "257f6433b4e7d5144cfc6eaae88a7552" ] || { echo ABORT_BAD_TRANSFER_EXEC; exit 3; }
[ "$MMD" = "7c191e830b7222cfc59f51cf8c871c97" ] || { echo ABORT_BAD_TRANSFER_MATCH; exit 3; }
FE=/home/azureuser/trading_corp/trading_corp/agents/strategies/poly_kalshi_executor.py
FM=/home/azureuser/trading_corp/trading_corp/data/mlb_poly_kalshi_match.py
CE=$(tr -d '\r' < $FE | md5sum | cut -d" " -f1); echo "CUR_EXEC $CE"
CM=$(tr -d '\r' < $FM | md5sum | cut -d" " -f1); echo "CUR_MATCH $CM"
[ "$CE" = "d1f871f9c3e83530dc6fba3bd58c2eae" ] || { echo ABORT_DRIFT_EXEC; exit 4; }
[ "$CM" = "4b2a5c49fb737d54d5a964868a4cd9fa" ] || { echo ABORT_DRIFT_MATCH; exit 4; }
TS=$(date -u +%Y%m%d_%H%M%S); BKE=${FE}.bak_item1_$TS; BKM=${FM}.bak_item1_$TS
cp $FE $BKE; cp $FM $BKM; echo "BACKUP_EXEC $BKE"; echo "BACKUP_MATCH $BKM"
cp $NE $FE; cp $NM $FM
IE=$(tr -d '\r' < $FE | md5sum | cut -d" " -f1); echo "INSTALLED_EXEC $IE"
IM=$(tr -d '\r' < $FM | md5sum | cut -d" " -f1); echo "INSTALLED_MATCH $IM"
if [ "$IE" != "257f6433b4e7d5144cfc6eaae88a7552" ] || [ "$IM" != "7c191e830b7222cfc59f51cf8c871c97" ]; then
  cp $BKE $FE; cp $BKM $FM; echo ABORT_INSTALL_RESTORED_BOTH_TO_PREFIX; exit 5
fi
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
echo "POST_EXEC_LFMD5 $(tr -d '\r' < $FE | md5sum | cut -d' ' -f1)"
echo "POST_MATCH_LFMD5 $(tr -d '\r' < $FM | md5sum | cut -d' ' -f1)"
echo "TRACEBACKS_SINCE_RESTART $(journalctl -u trading-corp --since "$RS" --no-pager 2>/dev/null | grep -c 'Traceback (most recent call last)')"
'@
$applyBash = $applyBash -replace "`r", ""
$ab64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($applyBash))
$applyCmd = "printf %s '$ab64' | base64 -d | bash"
Write-Host "== ITEM 1 ATOMIC APPLY (both-or-neither, abort-safe) + RESTART + BOOT PROOF (waits ~3-5 min) =="
az vm run-command invoke -g $RG -n $VM --command-id RunShellScript --scripts $applyCmd --query "value[0].message" -o tsv
