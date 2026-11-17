echo "=== PVC GRAFT mode=$MODE : v2 (2 files) or v2+chase (6 files) -- drift-gated, rollback-all ==="
date -u +%FT%TZ
set -u
case "${MODE:-}" in v2|v2chase) ;; *) echo "ABORT: MODE must be v2 or v2chase (got '${MODE:-}')"; exit 1;; esac
ROOT=/home/azureuser/trading_corp
STAGE=/home/azureuser/pvc_graft_stage
TGZ=/home/azureuser/pvc_graft.tgz
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK=/home/azureuser/backups/pvc_${MODE}_$TS
sha() { tr -d '\r' < "$1" | sha256sum | cut -c1-16; }

# file list + BASE/TARGET shas (CR-stripped sha256-16). BASE == the live box 2026-11-17 == branch dbbc24bf.
FILES="trading_corp/data/polymarket_data_api_client.py trading_corp/prediction_markets/loss_grounding.py"
base_of() { case "$1" in
  */polymarket_data_api_client.py) echo ad9a31922becfbe1;;
  */loss_grounding.py) echo ce22b4d26de6faf4;;
  */execution.py) echo a8a03b59e422a8a2;;
  */live_driver.py) echo 4bbe3023610aaadc;;
  */heartbeat.py) echo 57afdcc6c61055e2;;
  */db.py) echo 49f4f6d8d53d8fc5;;
esac; }
tgt_of() { case "$1" in
  */polymarket_data_api_client.py) echo c88cfce6a16cc4a0;;
  */loss_grounding.py) echo 42d166575b2f39bd;;
  */execution.py) echo b4f75cc64793c3fb;;
  */live_driver.py) echo 69dc1040abe53bc9;;
  */heartbeat.py) echo 227eda723301c288;;
  */db.py) echo a4d8ddfd2254830a;;
esac; }
if [ "$MODE" = "v2chase" ]; then
  FILES="$FILES trading_corp/prediction_markets/execution.py trading_corp/prediction_markets/live_driver.py trading_corp/prediction_markets/heartbeat.py trading_corp/prediction_markets/db.py"
fi

echo "--- [0] df pre-guard ---"
AVAIL=$(df --output=avail -k / | tail -1 | tr -d ' ')
echo "avail_kb=$AVAIL"; [ "$AVAIL" -lt 1000000 ] && { echo "ABORT: <1G free"; exit 2; }

echo "--- [1] unpack + STAGE GATE (staged == TARGET) ---"
rm -rf "$STAGE"; mkdir -p "$STAGE"
tar xzf "$TGZ" -C "$STAGE" || { echo "ABORT: untar failed"; exit 3; }
fail=0
for f in $FILES; do
  s=$(sha "$STAGE/$f"); w=$(tgt_of "$f")
  echo "staged $f = $s (want $w)"
  [ "$s" = "$w" ] || fail=1
done
[ "$fail" = "0" ] || { echo "ABORT: stage gate failed -- box untouched"; exit 4; }

echo "--- [2] DRIFT GATE (box live == BASE) ---"
for f in $FILES; do
  l=$(sha "$ROOT/$f"); w=$(base_of "$f")
  echo "live $f = $l (want $w)"
  [ "$l" = "$w" ] || fail=1
done
[ "$fail" = "0" ] || { echo "ABORT: live tree DRIFTED from BASE -- box untouched; re-derive BASE first"; exit 5; }

echo "--- [3] BACKUP to $BK (left in place) ---"
mkdir -p "$BK"
for f in $FILES; do
  cp -p "$ROOT/$f" "$BK/$(basename "$f")" || { echo "ABORT: backup failed for $f"; exit 6; }
done
ls -la "$BK"

rollback() {
  echo "!!! ROLLBACK: restoring BASE from $BK"
  rfail=0
  for f in $FILES; do
    cp "$BK/$(basename "$f")" "$ROOT/$f"
    r=$(sha "$ROOT/$f"); w=$(base_of "$f")
    echo "rolled-back $f = $r (want $w)"; [ "$r" = "$w" ] || rfail=1
  done
  [ "$rfail" = "0" ] && echo "ROLLBACK VERIFIED == BASE" || echo "ROLLBACK VERIFY FAILED -- restore by hand from $BK"
}

echo "--- [4] APPLY ---"
for f in $FILES; do
  cp "$STAGE/$f" "$ROOT/$f" || { echo "ABORT: apply $f failed"; rollback; exit 7; }
done

echo "--- [5] VERIFY live == TARGET ---"
for f in $FILES; do
  v=$(sha "$ROOT/$f"); w=$(tgt_of "$f")
  echo "live $f = $v (want $w)"; [ "$v" = "$w" ] || fail=1
done
[ "$fail" = "0" ] || { echo "VERIFY FAILED"; rollback; exit 8; }

echo "--- [6] py_compile + import closure (venv) ---"
PYV=$ROOT/venv/bin/python
"$PYV" -m py_compile $(for f in $FILES; do echo "$ROOT/$f"; done) || { echo "py_compile FAILED"; rollback; exit 9; }
cd "$ROOT" && PYTHONPATH=. "$PYV" -c "
import trading_corp.data.polymarket_data_api_client as m
import trading_corp.prediction_markets.loss_grounding
import trading_corp.prediction_markets.execution as E
import trading_corp.prediction_markets.live_driver
import trading_corp.prediction_markets.heartbeat
import trading_corp.prediction_markets.db as D
print('import closure OK;', m._DATA_API_V2, '; schema head', D.SCHEMA_HEAD,
      '; chase defaults', E.CONFIG_DEFAULTS.get('max_chase_cents'), E.CONFIG_DEFAULTS.get('chase_upside_pct'))
" || { echo "import closure FAILED"; rollback; exit 10; }

echo ""
echo "=== GRAFT ($MODE) APPLIED + VERIFIED. Backup: $BK (kept). ==="
if [ "$MODE" = "v2chase" ]; then
  echo "NOTE: migration 025 (chase columns + heartbeat chase_skips) SELF-APPLIES via db.init_db from the"
  echo "pm_cli crons; until it lands the code runs on CONFIG_DEFAULTS (2.0c abs, relative off) and the"
  echo "heartbeat write falls back to the pre-025 shape (never dark). Postverify section [8] checks it."
fi
echo "NEXT (Jack, in order):"
echo "  1) restart engine:  powershell -ep bypass -f \"C:\\Users\\AA Incorporado\\Desktop\\restart_tc.ps1\""
echo "  2) restart pm_web (your reserved channel): systemctl restart prediction-markets-web  (via az run-command)"
echo "  3) verify:          powershell -ep bypass -f \"C:\\Users\\AA Incorporado\\cc-pm-polyv2-wt\\analysis\\pvc_postverify.ps1\" -Mode $MODE"
