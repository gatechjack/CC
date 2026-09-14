set -u
ROOT=/home/azureuser/trading_corp
LIVE=$ROOT/data/prediction_markets.db
TCDB=$ROOT/data/trading_corp.db
PY=$ROOT/venv/bin/python3
NOW=$(date -u +%s)
echo "### PHASE-6 POST-RESTART GATE WATCH (READ-ONLY) $(date -u +%FT%TZ) now_epoch=$NOW ###"

echo "===ENGINE + SIBLINGS==="
systemctl show trading-corp -p MainPID,ActiveState,SubState,ExecMainStartTimestamp,NRestarts 2>/dev/null
EA=$(systemctl show trading-corp -p ExecMainStartTimestamp --value 2>/dev/null)
EAEPOCH=$(date -d "$EA" +%s 2>/dev/null || echo "")
if [ -n "$EAEPOCH" ]; then SINCE=$(( NOW - EAEPOCH )); echo "engine-active: $EA (epoch $EAEPOCH) seconds-since-active=$SINCE"; else SINCE=""; echo "engine-active: $EA (epoch parse FAILED)"; fi
echo "  [expect POST-RESTART: PID != 370246 and boot != 2026-09-12 21:00:28; if still those, restart did NOT happen]"
echo "SIBLINGS (must be UNCHANGED vs baseline pm_web=381803 sfp=656 kcv2=679):"
echo "  pm_web=$(systemctl show prediction-markets-web -p MainPID --value 2>/dev/null) sfp=$(systemctl show sfp-card-watcher -p MainPID --value 2>/dev/null) kcv2=$(pgrep -f kalshi_crypto_v2_observer | head -1)"
echo "===ENGINE_END==="

echo "===SIGNAL 1A: WIRING -- the direct 2026-09-04 detector (must be PRESENT) ==="
if [ -n "$EA" ]; then
  journalctl -u trading-corp --since "$EA" --no-pager 2>/dev/null | grep -E 'PM LIVE DRIVER WIRED|M3 shard-snapshot writer WIRED' | head -4 || echo "  !! 'PM LIVE DRIVER WIRED' ABSENT -- this is the 2026-09-04 signature (wiring gone) -> ROLL BACK"
else
  echo "  (no engine-active timestamp; cannot scope journal)"
fi
echo "===SIGNAL 1B: DRIVER CYCLING (polymarket-data-api positions) -- first cycle ~+5min on a healthy boot ==="
if [ -n "$EA" ]; then
  N=$(journalctl -u trading-corp --since "$EA" --no-pager 2>/dev/null | grep -c 'polymarket-data-api positions')
  echo "  cycle_line_count=$N"
  journalctl -u trading-corp --since "$EA" --no-pager 2>/dev/null | grep 'polymarket-data-api positions' | head -2
  journalctl -u trading-corp --since "$EA" --no-pager 2>/dev/null | grep 'polymarket-data-api positions' | tail -2
  echo "  -- pm_live_driver cycle lines (placed/latched) --"
  journalctl -u trading-corp --since "$EA" --no-pager 2>/dev/null | grep 'pm_live_driver cycle' | tail -4
  echo "  -- boot-reconcile (both accounts; latched should be False) --"
  journalctl -u trading-corp --since "$EA" --no-pager 2>/dev/null | grep 'boot-reconcile' | head -4
fi
echo "===SIGNAL1_END==="

echo "===BOOT: tracebacks / ERRORs since engine-active (ignore known-benign: fidelity playwright, EODHD/yfinance BTC/USD, kalshi_copy Apify FEED DOWN) ==="
journalctl -u trading-corp --since "$EA" --no-pager 2>/dev/null | grep -iE 'traceback|exception|critical|: error|ERROR ' | head -40 || echo "  (none matched)"
echo "===BOOT_END==="

echo "===SIGNAL 2: PLACEMENT (submitted_ts >= engine-active-epoch = POST-RESTART) + PM invariants ==="
BASE="${EAEPOCH:-0}"
echo "  placement baseline = engine-active-epoch = $BASE"
$PY - "$LIVE" "$BASE" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True); base=int(sys.argv[2])
def q(s,a=()):
  try: return list(c.execute(s,a))
  except Exception as e: return [("ERR",str(e)[:120])]
print("  armed active by account (want jack 23 / karen 21 = 44):", q("SELECT account_id,COUNT(*) FROM pm_subdivision WHERE active=1 GROUP BY account_id"))
print("  schema head (want 23):", q("SELECT MAX(version) FROM schema_version"))
print("  MAX submitted_ts now:", q("SELECT MAX(submitted_ts) FROM pm_subdivision_order"))
rows=q("SELECT id,submitted_ts,account_id,category,order_side,dry_run,fill_count,broker_order_id,outcome_status FROM pm_subdivision_order WHERE submitted_ts>=? AND dry_run=0 ORDER BY submitted_ts DESC LIMIT 12",(base,))
if rows and rows[0] and rows[0][0]!="ERR":
  print("  POST-RESTART real orders (submitted_ts>=%d): COUNT=%d"%(base,len(rows)))
  for r in rows: print("   ",r)
else:
  print("  POST-RESTART real orders (submitted_ts>=%d): NONE YET"%base)
PYEOF
echo "===SIGNAL2_END==="

echo "===SURVIVOR LIVENESS (trading_corp.db recent actors)==="
$PY - "$TCDB" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
try:
  for r in c.execute("SELECT actor,COUNT(*),MAX(ts) FROM audit_event WHERE actor IN ('bitunix_futures','bitunix_sfp','robinhood_mace','coinbase_btc_donchian','lord_otter','market_cypher','data_exec','scheduler','pmcc','pead') GROUP BY actor ORDER BY MAX(ts) DESC"):
    print("  ",r)
except Exception as e: print("  ERR",e)
PYEOF
echo "===SURVIVOR_END==="

echo "===HTTP CHECK (engine dashboard + survivor pages)==="
echo "  curl present: $(command -v curl >/dev/null 2>&1 && echo yes || echo NO)"
echo "  localhost listening ports: $(ss -ltnH 2>/dev/null | awk '{print $4}' | grep -oE '[0-9]+$' | sort -un | tr '\n' ' ')"
for P in 8000 8081 80; do
  code=$(curl -s -o /tmp/_pg -m 8 -w '%{http_code}' "http://127.0.0.1:$P/" 2>/dev/null || echo curlfail)
  ttl=$(grep -oiE '<title>[^<]*</title>' /tmp/_pg 2>/dev/null | head -1)
  bytes=$(wc -c < /tmp/_pg 2>/dev/null)
  echo "  port $P / -> HTTP $code bytes=$bytes $ttl"
  if [ "$P" = 8000 ] && [ "$code" = 200 ]; then
    for path in /division/mace /division/pmcc /division/pead /division/bitunix_futures /division/coinbase_btc_donchian; do
      c2=$(curl -s -o /tmp/_pg2 -m 8 -w '%{http_code}' "http://127.0.0.1:$P$path" 2>/dev/null || echo curlfail)
      t2=$(grep -oiE '<title>[^<]*</title>' /tmp/_pg2 2>/dev/null | head -1)
      echo "    $path -> HTTP $c2 $t2"
    done
  fi
done
echo "===HTTP_END==="
echo "### DONE ###"
