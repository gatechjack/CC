set -u
ROOT=/home/azureuser/trading_corp
PM=$ROOT/data/prediction_markets.db
LEG=$ROOT/data/trading_corp.db
V=$ROOT/venv/bin/python
date -u +"### OPTION-C FIRE PROBE (READ-ONLY; no writes, touches nothing) %Y-%m-%dT%H:%M:%SZ ###"
echo "box_hostname: $(hostname)"

echo
echo "### [1] ENGINE RESTART CHECK -- did the shared service bounce overnight (nobody asked)? ###"
echo "  wrap baseline: engine MainPID=171106 NRestarts=0 ExecMainStart=2026-09-02 23:10:02 UTC ; pmweb MainPID=170400"
(systemctl show -p MainPID -p NRestarts -p ActiveState -p ExecMainStartTimestamp -p StateChangeTimestamp trading-corp 2>/dev/null) | sed 's/^/  engine  /'
(systemctl show -p MainPID -p NRestarts -p ActiveState -p ExecMainStartTimestamp prediction-markets-web 2>/dev/null) | sed 's/^/  pmweb   /'

echo
echo "### [2] PM CRONS (four: paper-poll */30, refresh 05:00, adjudicate 05:40, rollup 05:50 UTC) ###"
crontab -l 2>/dev/null | grep -iE "pm_cli|prediction|refresh|adjudicate|rollup|paper-poll|paper_poll" || echo "  (crontab -l empty/denied)"

RESTART=$(date -u -d "2026-09-02 23:10:02" +%s 2>/dev/null || echo 1756854602)
echo
echo "### [3] DB STATE (mode=ro); placements/settlements counted since RESTART epoch=$RESTART ###"
PM="$PM" LEG="$LEG" RESTART="$RESTART" PYTHONPATH="$ROOT" "$V" - <<'PY'
import os, sqlite3, time, json
now=int(time.time()); R=int(os.environ["RESTART"])
def ts(x):
    try:
        x=int(x); return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(x)) if x else "None"
    except Exception: return str(x)
pm=sqlite3.connect("file:%s?mode=ro"%os.path.abspath(os.environ["PM"]),uri=True); pm.row_factory=sqlite3.Row
leg=sqlite3.connect("file:%s?mode=ro"%os.path.abspath(os.environ["LEG"]),uri=True)
try: print("  schema_head =", pm.execute("SELECT MAX(version) FROM schema_version").fetchone()[0], "(expect 17)")
except Exception as e: print("  schema err", e)
print("  -- ARM (persisted rows + ts; NOT a status call -- the mode=ro fail-safe reads a false disarm) --")
print("     baseline: global 2026-08-31T02:35:38 / jack 2026-08-31T21:49:39 / karen 2026-09-02T12:53:23 (all armed latched=False)")
for k in ("arm:global","arm:kalshi_jack:mlb","arm:kalshi_karen:mlb"):
    r=leg.execute("SELECT value_json FROM agent_state WHERE agent='pm_live' AND key=?", (k,)).fetchone()
    if r:
        v=json.loads(r[0]); print("     %-24s armed=%s latched=%s ts=%s by=%s"%(k, v.get("armed"), v.get("latched"), v.get("ts"), v.get("by")))
    else: print("     %-24s ABSENT"%k)
WRAP={"kalshi_jack":114,"kalshi_karen":115}
print("  -- ORDERS per account (dry_run=0). wrap: jack total 104/max_id 114 filled 103 ; karen 11/max_id 115 filled 10 --")
for aid in ("kalshi_jack","kalshi_karen"):
    tot,mx=pm.execute("SELECT COUNT(*),MAX(id) FROM pm_subdivision_order WHERE account_id=? AND dry_run=0",(aid,)).fetchone()
    fil=pm.execute("SELECT COUNT(*) FROM pm_subdivision_order WHERE account_id=? AND dry_run=0 AND outcome_status='filled'",(aid,)).fetchone()[0]
    plc=pm.execute("SELECT COUNT(*) FROM pm_subdivision_order WHERE account_id=? AND dry_run=0 AND response_ts>=? AND (close_source IS NULL OR close_source NOT LIKE 'settlement%')",(aid,R)).fetchone()[0]
    stl=pm.execute("SELECT COUNT(*) FROM pm_subdivision_order WHERE account_id=? AND dry_run=0 AND response_ts>=? AND close_source LIKE 'settlement%'",(aid,R)).fetchone()[0]
    print("     %-13s total=%s max_id=%s filled=%s  PLACED_SINCE_RESTART=%s  SETTLEMENTS_SINCE_RESTART=%s"%(aid,tot,mx,fil,plc,stl))
print("  -- EVERY new/updated row since wrap (id>wrap_max OR response_ts>=restart): classify placement vs settlement --")
for aid in ("kalshi_jack","kalshi_karen"):
    rows=pm.execute("SELECT id,ticker,outcome_leg,is_exit,close_source,outcome_status,fill_count,fill_price,realized_pnl,won,response_ts FROM pm_subdivision_order WHERE account_id=? AND dry_run=0 AND (id>? OR response_ts>=?) ORDER BY id",(aid,WRAP[aid],R)).fetchall()
    print("     %s: %d new/updated row(s)"%(aid,len(rows)))
    for r in rows:
        print("        id=%s tk=%s leg=%s is_exit=%s src=%s st=%s n=%s px=%s pnl=%s won=%s resp=%s"%(r["id"],r["ticker"],r["outcome_leg"],r["is_exit"],r["close_source"],r["outcome_status"],r["fill_count"],r["fill_price"],r["realized_pnl"],r["won"],ts(r["response_ts"])))
print("  -- OPEN net positions per account (filled entries - exits, net>0; wrap had jack 3 / karen 3) --")
for aid in ("kalshi_jack","kalshi_karen"):
    rows=pm.execute("SELECT ticker,outcome_leg,SUM(CASE WHEN is_exit=0 THEN COALESCE(fill_count,0) ELSE -COALESCE(fill_count,0) END) net FROM pm_subdivision_order WHERE account_id=? AND dry_run=0 AND outcome_status='filled' AND ticker IS NOT NULL GROUP BY ticker,outcome_leg HAVING net>0.0001",(aid,)).fetchall()
    print("     %-13s open_legs=%d %s"%(aid,len(rows),[(r[0],r[1],r[2]) for r in rows][:12]))
print("  -- SHARD snapshots + age (wrap: jack $496.32 sh0 $0.0081 sh3 $496.31 ; karen $471.39 sh0 $25.01 sh3 $446.38) --")
try:
    from trading_corp.prediction_markets import shard_snapshot as SS
    for aid in ("kalshi_jack","kalshi_karen"):
        v=SS.read_latest(pm,aid,now_ts=now)
        if v: print("     %-13s total=$%.2f by_shard=%s age=%dmin"%(aid,getattr(v,"total_dollars",0.0),getattr(v,"by_shard",None),(now-int(getattr(v,"snapshot_ts",now)))//60))
        else: print("     %-13s no snapshot"%aid)
except Exception as e: print("     shard err", e)
PY

echo
echo "### [4] JOURNAL since restart -- loop alive? why no placement? settlements booked? faults? ###"
J=$(journalctl -u trading-corp --since "2026-09-02 23:10:00" --no-pager 2>/dev/null)
if [ -z "$J" ]; then echo "  journalctl EMPTY/DENIED since restart"; else
  echo "  window coverage (retention): first/last trading-corp line since 23:10 --"
  echo "$J" | head -1 | sed 's/^/    first: /'
  echo "$J" | tail -1 | sed 's/^/    last : /'
  for aid in kalshi_jack kalshi_karen; do
    C=$(echo "$J" | grep -aE "pm_live_driver cycle $aid/")
    n_cyc=$(echo "$C" | grep -ac .)
    n_plc=$(echo "$C" | grep -ac "placed': [1-9]")
    n_wp=$(echo "$C" | grep -ac "n_would_place': [1-9]")
    n_sig=$(echo "$C" | grep -ac "n_signals': [1-9]")
    n_skp=$(echo "$C" | grep -ac "n_skip': [1-9]")
    n_rej=$(echo "$C" | grep -ac "n_reject': [1-9]")
    n_err=$(echo "$C" | grep -ac "errors': [1-9]")
    echo "  --- $aid ---"
    echo "    cycle summary lines     : $n_cyc"
    echo "    cycles placed>=1        : $n_plc"
    echo "    cycles n_would_place>=1 : $n_wp"
    echo "    cycles n_signals>=1     : $n_sig"
    echo "    cycles n_skip>=1        : $n_skp"
    echo "    cycles n_reject>=1      : $n_rej"
    echo "    cycles errors>=1        : $n_err"
    echo "    latest 3 cycles:"
    echo "$C" | tail -3 | sed 's/^/      /'
    echo "    latest 3 cycles with n_would_place>=1:"
    echo "$C" | grep -aE "n_would_place': [1-9]" | tail -3 | sed 's/^/      /'
    echo "    latest 3 cycles with n_signals>=1:"
    echo "$C" | grep -aE "n_signals': [1-9]" | tail -3 | sed 's/^/      /'
  done
  echo "  --- whole-loop / shared events since restart ---"
  g_opp=$(echo "$J" | grep -ac "OPPOSING-PAIR guard")
  g_oc=$(echo "$J" | grep -ac "opposed_closes=")
  g_eu=$(echo "$J" | grep -ac "skip:exposure_unknown")
  g_su=$(echo "$J" | grep -ac "skip:shard_underfunded")
  g_al=$(echo "$J" | grep -ac "SUSTAINED SHARD UNDERFUNDING")
  g_st=$(echo "$J" | grep -ac "settlement-scan booked")
  g_tb=$(echo "$J" | grep -ac "Traceback")
  echo "    OPPOSING-PAIR guard fires     : $g_opp"
  echo "$J" | grep -aE "OPPOSING-PAIR guard" | tail -3 | sed 's/^/      /'
  echo "    opposed_closes detections     : $g_oc"
  echo "    skip:exposure_unknown         : $g_eu"
  echo "    skip:shard_underfunded        : $g_su"
  echo "    SUSTAINED SHARD UNDERFUNDING  : $g_al"
  echo "    settlement-scan booked        : $g_st"
  echo "$J" | grep -aE "settlement-scan booked|BOOT settlement-scan" | tail -8 | sed 's/^/      /'
  echo "    Tracebacks (any)              : $g_tb"
  echo "    latch / auth-failure lines    :"
  echo "$J" | grep -aiE "latch|auth failure|manual_exit|LATCHED" | tail -6 | sed 's/^/      /'
  echo "    boot-reconcile verdict lines (a NEW one after 23:14 => engine bounced overnight):"
  echo "$J" | grep -aE "boot-reconcile account=" | tail -6 | sed 's/^/      /'
  echo "    PM LIVE DRIVER WIRED (roster) :"
  echo "$J" | grep -aE "PM LIVE DRIVER WIRED" | tail -3 | sed 's/^/      /'
fi
echo
echo "### DONE ###"
