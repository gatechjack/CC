#!/usr/bin/env bash
# kill-paper-sol-xrp APPLY (prod). Drift-gate -> backup -> edit strategies.yaml
# (remove SOL+XRP from `symbols:` AND `symbol_modes:`) -> expire stuck SOL paper
# row -> verify. NO restart (config is inert until the kp_restart runner). All ops
# run as azureuser directly (config + DB are azureuser-owned rw; no sudo needed).
DB=/home/azureuser/trading_corp/data/trading_corp.db
Y=/home/azureuser/trading_corp/config/strategies.yaml
STAMP=2026-06-30
EXPECT_MD5=740d1a027da61322faa8a85c62173c78
SOL_ORDER=e450302a-a7b0-4181-9d06-eb722c201fbb

echo "=== [1] DRIFT-GATE: strategies.yaml md5 ==="
GOT=$(md5sum "$Y" | cut -d' ' -f1)
if [ "$GOT" != "$EXPECT_MD5" ]; then
  echo "DRIFT: got [$GOT] expected [$EXPECT_MD5] - ABORT (no changes made)"; exit 2
fi
echo "md5 OK ($GOT)"

echo "=== [2] BACKUP ==="
cp -p "$Y" "$Y.bak-pre-killpaper-$STAMP"
echo "backup -> $Y.bak-pre-killpaper-$STAMP"

echo "=== [3] EDIT strategies.yaml (python literal edit + assertions) ==="
python3 - "$Y" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read(); orig = s
# (a) drop the two symbol_modes entries (whitespace-insensitive per-line match)
out = []
for ln in s.split('\n'):
    st = ln.strip()
    if st.startswith('"SOL/USDT.P":') or st.startswith('"XRP/USDT.P":'):
        continue
    out.append(ln)
s = '\n'.join(out)
# (b) shrink the traded symbols list to BTC+ETH (exact 4-symbol CSV appears once)
s = s.replace('"BTC/USDT.P", "ETH/USDT.P", "SOL/USDT.P", "XRP/USDT.P"',
              '"BTC/USDT.P", "ETH/USDT.P"')
assert s != orig, "NO CHANGE - edit patterns did not match (drift?)"
assert '"SOL/USDT.P"' not in s, "SOL still present after edit"
assert '"XRP/USDT.P"' not in s, "XRP still present after edit"
assert 'symbols: ["BTC/USDT.P", "ETH/USDT.P"]' in s, "symbols line not as expected"
assert '"BTC/USDT.P": { bos_tf: "3m", arm: "trading" }' in s, "BTC mode entry missing"
assert '"ETH/USDT.P": { bos_tf: "3m", arm: "trading" }' in s, "ETH mode entry missing"
open(p, 'w').write(s)
print("edit applied + all assertions passed")
PY
RC=$?
if [ $RC -ne 0 ]; then
  echo "EDIT FAILED (rc=$RC) - restoring backup"
  cp -p "$Y.bak-pre-killpaper-$STAMP" "$Y"; exit 4
fi

echo "=== [4] NEW bitunix_sfp symbols + symbol_modes ==="
awk '/^[^ #]/ && f && !/^bitunix_sfp:/ {exit} /^bitunix_sfp:/{f=1} f{print}' "$Y" \
  | grep -E 'symbols:|symbol_modes:|USDT.P' || true

echo "=== [5] NEW md5 (record for next drift-gate) ==="
md5sum "$Y"

echo "=== [6] EXPIRE stuck SOL paper row (NULL-guarded, idempotent) ==="
sqlite3 "$DB" "PRAGMA busy_timeout=8000; UPDATE paper_trade_record SET result='expired', result_ts=strftime('%Y-%m-%dT%H:%M:%S+00:00','now') WHERE order_id='$SOL_ORDER' AND result IS NULL; SELECT 'rows_changed='||changes();"

echo "=== [7] VERIFY SOL row + no open bitunix_sfp rows ==="
sqlite3 -header -column "$DB" "SELECT substr(order_id,1,12) oid, symbol, result, result_ts FROM paper_trade_record WHERE order_id='$SOL_ORDER'"
echo -n "open (result IS NULL) bitunix_sfp rows remaining: "
sqlite3 "$DB" "SELECT COUNT(*) FROM paper_trade_record WHERE division='bitunix_sfp' AND result IS NULL"
echo "=== APPLY COMPLETE. Config edited + SOL row expired. Engine still on OLD config until kp_restart. ==="
