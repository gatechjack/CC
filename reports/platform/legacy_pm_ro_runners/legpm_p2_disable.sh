set -u
ROOT=/home/azureuser/trading_corp
CFG="$ROOT/config/strategies.yaml"
PY="$ROOT/venv/bin/python3"
LEGACY="$ROOT/data/trading_corp.db"
TS=$(date -u +%Y%m%dT%H%M%SZ)
BAK="$CFG.bak_phase2disable_$TS"
echo "### PHASE-2 CONFIG DISABLE (WRITE) $TS ###"
echo "cfg=$CFG"
echo "backup=$BAK"
echo
echo "## PRE: top-level enabled flags (legacy PM + fence) ##"
for k in polymarket_arbitrage polymarket_copy_trader kalshi_tail_price_arb kalshi_temporal_bucket_arb kalshi_llm_arbitrage kalshi_weather_arb kalshi_crypto_arb kalshi_sports_scout kalshi_sports_arb_observer kalshi_copy_trader poly_kalshi_mlb pm_live_driver; do
  v=$(awk -v k="^$k:" '$0~k{f=1;next} f&&/^[A-Za-z_]/{exit} f&&/^  enabled:/{print $2; exit}' "$CFG")
  echo "  $k = $v"
done
echo
echo "## PRE: polymarket_copy_trader activity (hot-reload baseline) ##"
"$PY" - "$LEGACY" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
print("pre_max_whale_state_ts:", c.execute("SELECT MAX(updated_ts) FROM agent_state WHERE agent='polymarket_copy_trader' AND key LIKE 'whale_state:%'").fetchone()[0])
print("pre_max_audit_ts:", c.execute("SELECT MAX(ts) FROM audit_event WHERE actor='polymarket_copy_trader'").fetchone()[0])
c.close()
PYEOF
echo "pre_wallclock=$(date -u +%FT%TZ)"
echo
echo "## BACKUP ##"
cp -p "$CFG" "$BAK" && echo "backup_ok bytes=$(wc -c < "$BAK")" || { echo "BACKUP FAILED -- ABORT"; exit 2; }
echo
echo "## EDIT: flip 5 top-level enabled true->false; validate parse + fence; atomic replace ##"
"$PY" - "$CFG" <<'PYEOF'
import re,sys,os,yaml
path=sys.argv[1]
raw=open(path,encoding="utf-8").read()
lines=raw.split("\n")
targets=["poly_kalshi_mlb","polymarket_copy_trader","kalshi_tail_price_arb","kalshi_temporal_bucket_arb","kalshi_sports_scout"]
changed=[];already=[];notfound=[]
def flip(name):
    hdr=None
    for i,l in enumerate(lines):
        if re.match(r'^'+re.escape(name)+r':',l): hdr=i;break
    if hdr is None: notfound.append(name);return
    for i in range(hdr+1,len(lines)):
        l=lines[i]
        if re.match(r'^[A-Za-z_][\w]*:',l): notfound.append(name+":no_enabled");return
        m=re.match(r'^(  enabled:\s*)(true|True)(\b.*)?$',l)
        if m:
            lines[i]=m.group(1)+"false"+(m.group(3) or "")
            changed.append((name,i+1,l.strip(),lines[i].strip()));return
        m2=re.match(r'^(  enabled:\s*)(false|False)(\b.*)?$',l)
        if m2: already.append((name,i+1));return
    notfound.append(name+":scan_end")
for t in targets: flip(t)
new="\n".join(lines)
try:
    y=yaml.safe_load(new)
    assert isinstance(y,dict), "not a dict"
    assert (y.get("pm_live_driver") or {}).get("enabled") is True, "FENCE: pm_live_driver.enabled must stay true"
    for t in targets:
        assert (y.get(t) or {}).get("enabled") is False, "target %s not false after edit"%t
except Exception as e:
    print("VALIDATE FAILED -- NOT WRITTEN:",e); print("notfound=",notfound); sys.exit(3)
if notfound:
    print("NOTFOUND -- NOT WRITTEN:",notfound); sys.exit(3)
tmp=path+".tmp_p2"
open(tmp,"w",encoding="utf-8",newline="").write(new)
os.replace(tmp,path)
print("WROTE ok  changed=%d already=%d notfound=%d"%(len(changed),len(already),len(notfound)))
for c in changed: print("  FLIP %-28s L%d: [%s] -> [%s]"%(c[0],c[1],c[2],c[3]))
for a in already: print("  ALREADY-FALSE %-28s L%d"%(a[0],a[1]))
PYEOF
RC=$?
echo "edit_rc=$RC"
[ "$RC" != "0" ] && echo "EDIT ABORTED -- config UNCHANGED (backup retained)"
echo
echo "## POST: re-read top-level enabled flags ##"
for k in polymarket_copy_trader kalshi_tail_price_arb kalshi_temporal_bucket_arb kalshi_sports_scout poly_kalshi_mlb pm_live_driver; do
  v=$(awk -v k="^$k:" '$0~k{f=1;next} f&&/^[A-Za-z_]/{exit} f&&/^  enabled:/{print $2; exit}' "$CFG")
  echo "  $k = $v"
done
echo
echo "## PROOF: poly_kalshi_mlb persist-halt row present + UNTOUCHED (READ-ONLY, do not modify) ##"
"$PY" - "$LEGACY" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
r=c.execute("SELECT agent,key,value_json,updated_ts FROM agent_state WHERE agent='strategy_state' AND key='poly_kalshi_mlb'").fetchone()
print("halt_row:",r)
c.close()
PYEOF
echo
echo "## HOT-RELOAD EMPIRICAL: sleep 95s, then confirm polymarket_copy_trader froze ##"
sleep 95
"$PY" - "$LEGACY" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
print("post_max_whale_state_ts:", c.execute("SELECT MAX(updated_ts) FROM agent_state WHERE agent='polymarket_copy_trader' AND key LIKE 'whale_state:%'").fetchone()[0])
print("post_max_audit_ts:", c.execute("SELECT MAX(ts) FROM audit_event WHERE actor='polymarket_copy_trader'").fetchone()[0])
c.close()
PYEOF
echo "post_wallclock=$(date -u +%FT%TZ)"
echo "### DONE ###"
