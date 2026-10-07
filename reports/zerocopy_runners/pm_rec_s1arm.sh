PY=/home/azureuser/trading_corp/venv/bin/python
ROOT=/home/azureuser/trading_corp
cd "$ROOT" || exit 2
export PYTHONPATH=.
PIDB=$(systemctl show trading-corp -p MainPID --value); NRB=$(systemctl show trading-corp -p NRestarts --value)
echo "engine BEFORE: PID=$PIDB NRestarts=$NRB"
arm_one(){ a=$1; c=$2; out=$("$PY" trading_corp/scripts/pm_cli.py live-arm --account "$a" --category "$c" --clear-latch --by claude 2>&1); rc=$?; ea=$(printf '%s' "$out" | grep -oE '"effective_armed": [a-z]+' | head -1); lt=$(printf '%s' "$out" | grep -oE '"latched": [a-z]+' | head -1); echo "  $a/$c rc=$rc $ea $lt"; }
echo "=== ARMING 57 (pm_cli live-arm --clear-latch --by claude) ==="
for c in atp bra bun boxing cfb cs2 epl f1 itf lal mex mlb mls nfl ucl ufc wnba wta; do arm_one kalshi_jack $c; done
for c in atp bra bun cfb cs2 epl itf lal mex mlb mls nfl ucl ufc wnba wta; do arm_one kalshi_karen $c; done
for c in atp cfb cs2 itf lal mex mlb mls nfl ufc wnba; do arm_one kalshi_marc $c; done
for c in atp cfb cs2 itf lal mex mlb mls nfl ufc wnba wta; do arm_one kalshi_trey $c; done
echo "=== STEP 2 VERIFY (legacy agent_state RO; columns agent,key,value_json) ==="
"$PY" - <<'PY'
import sqlite3, json
lg=sqlite3.connect('file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro',uri=True)
JACK="atp bra bun boxing cfb cs2 epl f1 itf lal mex mlb mls nfl ucl ufc wnba wta".split()
KAREN="atp bra bun cfb cs2 epl itf lal mex mlb mls nfl ucl ufc wnba wta".split()
MARC="atp cfb cs2 itf lal mex mlb mls nfl ufc wnba".split()
TREY="atp cfb cs2 itf lal mex mlb mls nfl ufc wnba wta".split()
intended=set([("kalshi_jack",c) for c in JACK]+[("kalshi_karen",c) for c in KAREN]+[("kalshi_marc",c) for c in MARC]+[("kalshi_trey",c) for c in TREY])
print("intended-to-arm count:", len(intended))
rows=lg.execute("select key,value_json from agent_state where agent='pm_live' and key like 'arm:%'").fetchall()
state={}
for k,v in rows:
    d=json.loads(v) if v else {}
    # key form arm:ACCOUNT:CATEGORY or arm:global
    if k=="arm:global":
        print("arm:global -> armed=%s latched=%s"%(d.get('armed'),d.get('latched'))); continue
    parts=k.split(":",2)  # arm, account, category  (account may contain no colon)
    if len(parts)==3:
        state[(parts[1],parts[2])]=(d.get('armed') is True, bool(d.get('latched')))
armed_true=[s for s,(a,l) in state.items() if a]
print("TOTAL per-sub arm rows:", len(state), "| armed=True:", len(armed_true))
# intended that are NOT armed-and-unlatched
bad_intended=[(s,state.get(s)) for s in intended if state.get(s)!=(True,False)]
print("intended NOT (armed & not-latched):", len(bad_intended))
for s,v in bad_intended: print("   MISS", s, "->", v)
# non-intended that ARE armed (should be none)
unexpected=[s for s,(a,l) in state.items() if a and s not in intended]
print("UNEXPECTED armed (not in the 57):", len(unexpected))
for s in sorted(unexpected): print("   EXTRA", s)
# disarmed set
disarmed=[s for s,(a,l) in state.items() if not a]
print("disarmed per-sub count:", len(disarmed))
print("RESULT: armed==57 and matches intended:", len(armed_true)==57 and not bad_intended and not unexpected)
PY
PIDA=$(systemctl show trading-corp -p MainPID --value); NRA=$(systemctl show trading-corp -p NRestarts --value)
echo "engine AFTER: PID=$PIDA NRestarts=$NRA (before PID=$PIDB NR=$NRB)"
echo "=== DONE s1arm ==="
