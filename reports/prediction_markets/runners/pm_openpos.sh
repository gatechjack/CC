set -u
ROOT=/home/azureuser/trading_corp
PKG=$ROOT/trading_corp
PMDB=$ROOT/data/prediction_markets.db
V=$ROOT/venv/bin/python
echo "### READ 1 -- WHEN was main.py overwritten (pin the time) $(date -u +%Y%m%dT%H%M%SZ) ###"
echo "## box main.py mtime (when the file was written) + size:"
stat -c '  main.py  mtime=%y  size=%s bytes' "$PKG/main.py" 2>/dev/null
echo "## MACE deploy backup dirs on the box (their timestamps bracket MACE deploys):"
ls -dt /home/azureuser/mace*backup* /home/azureuser/*mace*backup* 2>/dev/null | head -6 | while read d; do echo "  $d  ($(stat -c '%y' "$d" 2>/dev/null | cut -d. -f1))"; done
echo "  (MACE main.py commit 9380df3 was AUTHORED 2026-09-04 18:19:01Z; the mtime above is when it hit the box)"
echo "## engine restart that ACTIVATED it: $(systemctl show -p ActiveEnterTimestamp --value trading-corp 2>/dev/null) (PID 205004)"
echo
echo "### READ 2 -- OPEN POSITIONS across the 8 UNDRIVEN sub-divisions (journal-derived; NO whale-exit/settlement/opposed while driver down) ###"
cd "$ROOT"
PYTHONPATH="$ROOT" "$V" - <<'PY' 2>&1 | sed 's/^/  /'
import sqlite3, json, urllib.request
PMDB="/home/azureuser/trading_corp/data/prediction_markets.db"
from trading_corp.prediction_markets import subdivision
c=sqlite3.connect("file:%s?mode=ro"%PMDB, uri=True); c.row_factory=sqlite3.Row
SUBS=[("kalshi_jack","mlb"),("kalshi_jack","ufc"),("kalshi_jack","atp"),("kalshi_jack","wta"),
      ("kalshi_karen","mlb"),("kalshi_karen","ufc"),("kalshi_karen","atp"),("kalshi_karen","wta")]
KB="https://api.elections.kalshi.com/trade-api/v2"
def kstatus(tk):
    try:
        req=urllib.request.Request(KB+"/markets/"+tk, headers={"User-Agent":"curl/8"})
        m=json.loads(urllib.request.urlopen(req,timeout=20).read()).get("market",{})
        return (m.get("status"),m.get("result"),m.get("close_time"))
    except Exception as e:
        return ("ERR:"+repr(e)[:24],None,None)
RESOLVED={"settled","finalized","determined"}
gp=0; gc=0.0; gctr=0.0; unbooked=[]; pending=[]; contested=[]
for acct,cat in SUBS:
    pos=subdivision.live_positions(c,acct,cat)
    n=len(pos); cost=sum(p["cost_basis_usd"] for p in pos); ctr=sum(p["contracts"] for p in pos)
    gp+=n; gc+=cost; gctr+=ctr
    print("%-13s/%-3s : %d open ticker(s), %.0f contracts, $%.2f cost-basis" % (acct,cat,n,ctr,cost))
    for p in pos:
        st,res,ct=kstatus(p["ticker"])
        f=""
        if st in RESOLVED: f=" <== RESOLVED-but-UNBOOKED (R-d missed)"; unbooked.append((acct,cat,p["ticker"],res))
        elif st=="closed": f=" <-- trading CLOSED (game over; settlement pending)"; pending.append((acct,cat,p["ticker"]))
        print("    %-46s %-3s %-14s ct=%.0f $%.2f kalshi=%s result=%s%s" % (p["ticker"],p["held_leg"],p["market_type"],p["contracts"],p["cost_basis_usd"],st,res,f))
    byw=subdivision.live_positions_by_whale(c,acct,cat)
    legs={}
    for w in byw: legs.setdefault(w["ticker"],set()).add(w["held_leg"])
    for tk,ls in legs.items():
        if "yes" in ls and "no" in ls: contested.append((acct,cat,tk))
print()
print("TOTAL: %d open ticker-positions across 8 subs, %.0f contracts, $%.2f cost-basis AT STAKE (unmanaged)" % (gp,gctr,gc))
print("MISSED-SETTLEMENT (RESOLVED but not booked -- R-d would have closed these): %d" % len(unbooked))
for u in unbooked: print("   ", u)
print("SETTLEMENT-PENDING (trading closed, game over, will need booking): %d" % len(pending))
for u in pending: print("   ", u)
print("CONTESTED opposing pairs (opposed-guard would have flattened): %d" % len(contested))
for u in contested: print("   ", u)
print("NOTE whale-EXIT copies cannot be reconstructed cheaply read-only (the driver's matcher maps Poly cid->Kalshi ticker); flagged, not counted.")
PY
echo "### DONE ###"
