echo "=== PV2 GRAFT: Data API v2 client migration (2 files) -- drift-gated, rollback-all ==="
date -u +%FT%TZ
set -u
ROOT=/home/azureuser/trading_corp
STAGE=/home/azureuser/pv2_graft_stage
TGZ=/home/azureuser/pv2_graft.tgz
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK=/home/azureuser/backups/pv2_$TS
F1=trading_corp/data/polymarket_data_api_client.py
F2=trading_corp/prediction_markets/loss_grounding.py
B1=ad9a31922becfbe1; T1=c88cfce6a16cc4a0
B2=ce22b4d26de6faf4; T2=42d166575b2f39bd
sha() { tr -d '\r' < "$1" | sha256sum | cut -c1-16; }

echo "--- [0] df pre-guard ---"
AVAIL=$(df --output=avail -k / | tail -1 | tr -d ' ')
echo "avail_kb=$AVAIL"; [ "$AVAIL" -lt 1000000 ] && { echo "ABORT: <1G free"; exit 2; }

echo "--- [1] unpack + STAGE GATE (staged == TARGET) ---"
rm -rf "$STAGE"; mkdir -p "$STAGE"
tar xzf "$TGZ" -C "$STAGE" || { echo "ABORT: untar failed"; exit 3; }
s1=$(sha "$STAGE/$F1"); s2=$(sha "$STAGE/$F2")
echo "staged client=$s1 (want $T1) loss_grounding=$s2 (want $T2)"
{ [ "$s1" = "$T1" ] && [ "$s2" = "$T2" ]; } || { echo "ABORT: stage gate failed -- box untouched"; exit 4; }

echo "--- [2] DRIFT GATE (box live == BASE) ---"
l1=$(sha "$ROOT/$F1"); l2=$(sha "$ROOT/$F2")
echo "live client=$l1 (want $B1) loss_grounding=$l2 (want $B2)"
{ [ "$l1" = "$B1" ] && [ "$l2" = "$B2" ]; } || { echo "ABORT: live tree DRIFTED from BASE -- box untouched; re-derive BASE first"; exit 5; }

echo "--- [3] BACKUP to $BK (left in place) ---"
mkdir -p "$BK"
cp -p "$ROOT/$F1" "$BK/polymarket_data_api_client.py" || { echo "ABORT: backup failed"; exit 6; }
cp -p "$ROOT/$F2" "$BK/loss_grounding.py" || { echo "ABORT: backup failed"; exit 6; }
ls -la "$BK"

rollback() {
  echo "!!! ROLLBACK: restoring BASE from $BK"
  cp "$BK/polymarket_data_api_client.py" "$ROOT/$F1"
  cp "$BK/loss_grounding.py" "$ROOT/$F2"
  r1=$(sha "$ROOT/$F1"); r2=$(sha "$ROOT/$F2")
  echo "rolled-back client=$r1 (want $B1) loss_grounding=$r2 (want $B2)"
  { [ "$r1" = "$B1" ] && [ "$r2" = "$B2" ]; } && echo "ROLLBACK VERIFIED == BASE" || echo "ROLLBACK VERIFY FAILED -- restore by hand from $BK"
}

echo "--- [4] APPLY ---"
cp "$STAGE/$F1" "$ROOT/$F1" || { echo "ABORT: apply F1 failed"; rollback; exit 7; }
cp "$STAGE/$F2" "$ROOT/$F2" || { echo "ABORT: apply F2 failed"; rollback; exit 7; }

echo "--- [5] VERIFY live == TARGET ---"
v1=$(sha "$ROOT/$F1"); v2=$(sha "$ROOT/$F2")
echo "live client=$v1 (want $T1) loss_grounding=$v2 (want $T2)"
{ [ "$v1" = "$T1" ] && [ "$v2" = "$T2" ]; } || { echo "VERIFY FAILED"; rollback; exit 8; }

echo "--- [6] py_compile + import closure (venv) ---"
PYV=$ROOT/venv/bin/python
"$PYV" -m py_compile "$ROOT/$F1" "$ROOT/$F2" || { echo "py_compile FAILED"; rollback; exit 9; }
cd "$ROOT" && PYTHONPATH=. "$PYV" -c "import trading_corp.data.polymarket_data_api_client as m; import trading_corp.prediction_markets.loss_grounding; import trading_corp.prediction_markets.live_driver; print('import closure OK;', m._DATA_API_V2)" || { echo "import closure FAILED"; rollback; exit 10; }

echo ""
echo "=== GRAFT APPLIED + VERIFIED. Backup: $BK (kept). ==="
echo "NEXT (Jack, in order):"
echo "  1) restart engine:  powershell -ep bypass -f \"C:\\Users\\AA Incorporado\\Desktop\\restart_tc.ps1\""
echo "  2) restart pm_web (your reserved channel): systemctl restart prediction-markets-web  (via az run-command)"
echo "  3) verify:          powershell -ep bypass -f \"C:\\Users\\AA Incorporado\\cc\\pv2_postverify.ps1\""
