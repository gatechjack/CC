set -u
ROOT=/home/azureuser/trading_corp
PY="$ROOT/venv/bin/python3"
LEGACY="$ROOT/data/trading_corp.db"
PMDB="$ROOT/data/prediction_markets.db"
echo "### LEGACY-PM INVENTORY READ (READ-ONLY) $(date -u +%FT%TZ) ###"
echo "legacy=$LEGACY"
echo "pmdb=$PMDB"
"$PY" - "$LEGACY" "$PMDB" <<'PYEOF'
import sqlite3, sys, os, json
legacy, pmdb = sys.argv[1], sys.argv[2]
def ro(p): return sqlite3.connect("file:%s?mode=ro" % p, uri=True)
def sz(p):
    try: return os.path.getsize(p)
    except Exception as e: return -1
print("## FILE SIZES ##")
print("legacy_bytes=%d (%.3f GB)" % (sz(legacy), sz(legacy)/1e9))
print("pmdb_bytes=%d (%.2f MB)" % (sz(pmdb), sz(pmdb)/1e6))
c = ro(legacy)
print()
print("## ARM re-read (agent=pm_live key LIKE arm:%%) ##")
rows = list(c.execute("SELECT agent,key,value_json,updated_ts FROM agent_state WHERE agent='pm_live' AND key LIKE 'arm:%'"))
armed=0; latched=[]; trig=[]
for a,k,vj,u in rows:
    v = json.loads(vj) if vj else {}
    if v.get('armed') is True: armed+=1
    if v.get('latched') is True: latched.append(k)
    if v.get('trigger') not in (None,"",False): trig.append(k)
print("arm_rows=%d armed_true=%d latched=%s trigger=%s" % (len(rows), armed, latched, trig))
print()
print("## LEGACY-PM agent_state (roster/halt/pause keys) ##")
q = ("SELECT agent,key,substr(value_json,1,200),updated_ts FROM agent_state "
     "WHERE agent IN ('poly_kalshi_mlb','polymarket_copy_trader','kalshi_copy_trader','polymarket_arbitrage',"
     "'kalshi_llm_arbitrage','kalshi_weather_arb','kalshi_crypto_arb','kalshi_sports_scout') "
     "OR key LIKE '%halt%' OR key LIKE '%whale%' OR key LIKE '%roster%' ORDER BY agent,key")
try:
    for a,k,vj,u in c.execute(q):
        print("%-24s %-22s ts=%s | %s" % (a,k,u,vj))
except Exception as e:
    print("agent_state query ERR:", e)
print()
print("## LEGACY DB dbstat top-45 tables by bytes ##")
try:
    tot=0
    for name,b in c.execute("SELECT name, SUM(pgsize) b FROM dbstat GROUP BY name ORDER BY b DESC LIMIT 45"):
        print("%-46s %14d bytes (%8.1f MB)" % (name, b, b/1e6))
except Exception as e:
    print("dbstat unavailable:", e)
print()
print("## LEGACY-PM table row counts + open/unsettled ##")
legacy_tabs=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("legacy_total_tables=%d" % len(legacy_tabs))
KEYS=('kalshi','polymarket','poly_kalshi','kcv2','weather','crypto','sports','whale','round_trip','would_have','arb','nbm','metar','odds')
pm_like=[t for t in legacy_tabs if any(s in t.lower() for s in KEYS)]
print("legacy_pm_candidate_tables=%d" % len(pm_like))
for t in pm_like:
    try:
        n=list(c.execute("SELECT COUNT(*) FROM \"%s\"" % t))[0][0]
        cols=[r[1].lower() for r in c.execute("PRAGMA table_info(\"%s\")" % t)]
        extra=""
        if 'settled_ts' in cols:
            o=list(c.execute("SELECT COUNT(*) FROM \"%s\" WHERE settled_ts IS NULL" % t))[0][0]; extra+=" open_settled_ts_null=%d"%o
        if 'status' in cols:
            try:
                st=list(c.execute("SELECT status,COUNT(*) FROM \"%s\" GROUP BY status ORDER BY 2 DESC LIMIT 5" % t)); extra+=" status=%s"%st
            except Exception: pass
        print("%-46s rows=%-9d%s" % (t,n,extra))
    except Exception as e:
        print("%-46s ERR %s" % (t,e))
print()
print("## KCV2 tables (legacy DB) ##")
for t in ('kcv2_index_ticks','kcv2_quotes','kcv2_signals','kcv2_heartbeat'):
    if t in legacy_tabs:
        try:
            n=list(c.execute("SELECT COUNT(*) FROM %s"%t))[0][0]
            mx=list(c.execute("SELECT MAX(rowid) FROM %s"%t))[0][0]
            print("%-20s rows=%-8d maxrowid=%s" % (t,n,mx))
        except Exception as e: print("%-20s ERR %s"%(t,e))
    else:
        print("%-20s ABSENT" % t)
try:
    hb=list(c.execute("SELECT * FROM kcv2_heartbeat ORDER BY rowid DESC LIMIT 1"))
    print("kcv2_heartbeat last row:", hb)
except Exception as e:
    print("kcv2_heartbeat last ERR:", e)
c.close()
print()
print("## PM DB (prediction_markets.db) ##")
p = ro(pmdb)
ptabs=[r[0] for r in p.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("pm_total_tables=%d" % len(ptabs))
print("pm_tables=%s" % ptabs)
for t in ptabs:
    tc=[r[1] for r in p.execute("PRAGMA table_info(%s)"%t)]
    if any(x.lower()=='version' for x in tc):
        try: print("schema_head %s: MAX(version)=%s count=%s" % (t, list(p.execute("SELECT MAX(version) FROM %s"%t))[0][0], list(p.execute("SELECT COUNT(*) FROM %s"%t))[0][0]))
        except Exception as e: print("schema head ERR %s"%e)
p.close()
PYEOF
echo
echo "## SYSTEMD units (trading*, kcv2*, pm*) ##"
for u in $(systemctl list-unit-files --no-legend --type=service 2>/dev/null | awk '{print $1}' | grep -iE 'trading|kcv2|pm[_-]?web'); do
  echo "-- $u --"
  systemctl show "$u" -p Id,ActiveState,SubState,MainPID,NRestarts,ActiveEnterTimestampMonotonic,ActiveEnterTimestamp 2>/dev/null | tr '\n' ' '
  echo
done
echo
echo "## CRON (azureuser) ##"
crontab -l 2>/dev/null || echo "(no user crontab)"
echo "## /etc/cron.d ##"
ls -1 /etc/cron.d 2>/dev/null || echo "(none)"
echo "### DONE ###"
