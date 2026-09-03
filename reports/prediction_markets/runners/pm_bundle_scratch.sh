set -u
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
SCRATCH=/home/azureuser/pm_bundle_scratch_$TS
V=$ROOT/venv/bin/python
TAR=/home/azureuser/pm_bundle_overlay.tar
echo "### BUNDLE BOX-SCRATCH (READ-ONLY; scratch overlay -- the LIVE tree is never modified) $TS ###"
echo "engine PID (must be UNTOUCHED): $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"

echo
echo "### [0] RECONCILE -- box pre-overlay file hashes (LF, tr -d CR) vs expected ###"
for f in prediction_markets/db.py prediction_markets/execution.py prediction_markets/live_driver.py prediction_markets/boot_reconcile.py; do
  h=$(tr -d '\r' < "$ROOT/trading_corp/$f" 2>/dev/null | sha256sum | cut -c1-16); echo "  trading_corp/$f = $h"
done
h=$(tr -d '\r' < "$ROOT/trading_corp/main.py" 2>/dev/null | sha256sum | cut -c1-16); echo "  trading_corp/main.py = $h"
echo "  ufc matcher on box: $([ -f "$ROOT/trading_corp/data/ufc_poly_kalshi_match.py" ] && echo PRESENT || echo ABSENT)"
echo "  EXPECT: db.py=46e612f152d96b12(loss-om17) execution.py=bc806bc4eb289072(e5d6506) live_driver.py=4b85f93f...(A) boot_reconcile=ecce77770f951f74(A) main.py=bba046e8f1ce9801(A); ufc ABSENT"

echo
echo "### [1] COPY live CODE -> scratch (NOT the venv -- reuse the box venv), OVERLAY my HEAD files, replace tests ###"
mkdir -p "$SCRATCH"
cp -a "$ROOT/trading_corp" "$SCRATCH/"
cp -a "$ROOT/tests" "$SCRATCH/"
for x in pyproject.toml conftest.py pytest.ini setup.cfg; do [ -f "$ROOT/$x" ] && cp -a "$ROOT/$x" "$SCRATCH/"; done
[ -d "$ROOT/config" ] && cp -a "$ROOT/config" "$SCRATCH/"
[ -f "$TAR" ] && echo "  overlay tar present ($(stat -c%s "$TAR") bytes)" || { echo "  ** overlay tar MISSING -- abort"; rm -rf "$SCRATCH"; exit 2; }
rm -rf "$SCRATCH/tests/prediction_markets"
tar xf "$TAR" -C "$SCRATCH"
echo "  overlaid hashes in scratch (must MATCH my branch HEAD):"
for f in prediction_markets/db.py prediction_markets/execution.py prediction_markets/live_driver.py; do
  h=$(tr -d '\r' < "$SCRATCH/trading_corp/$f" 2>/dev/null | sha256sum | cut -c1-16); echo "    trading_corp/$f = $h"
done
echo "    ufc matcher in scratch: $([ -f "$SCRATCH/trading_corp/data/ufc_poly_kalshi_match.py" ] && echo PRESENT || echo ABSENT)"
echo "  EXPECT: db.py=aa5126bae6219e5f execution.py=1f48b6b3517295a9 live_driver.py=6c20891eae9253fa; ufc PRESENT(2fa2166b87948b0e)"

echo
echo "### [2] PROOF A -- MLB byte-identical: run the PM suite on the BOX venv (-p no:pytest_ethereum) ###"
echo "     (the pykalshi-path tests -- kill_switch / live_driver_r7c / shard_gate -- CANNOT run locally; they are the gate)"
echo "     (ENGINE/schema tests ONLY -- WEB tests are excluded: my branch is e5d6506-era on web/ while the box has the"
echo "      newer loss-omission+UI web deployed, so branch web tests mismatch the box web -- a divergence, not my change)"
ENGINE="test_live_driver_r7c test_kill_switch_r7d test_shard_gate_r2 test_boot_reconcile_r55 test_venue_exposure_r7 test_b2_dispatch test_opposing_close_r5 test_execution_r4 test_ufc_match test_mlb_match_r2 test_search_r1 test_shard_snapshot_m3 test_shard_snapshot_task_m3 test_per_account_driver_n2 test_optiond_r1 test_idempotency_r7h test_disarm_r7i test_arm_r5 test_settlement_rd test_sizing_contracts_r8 test_liquidity_floor_r7f test_null_caps_r7f test_exchange_index_r3 test_shard_balance_r1 test_db test_names"
FILES=""; for t in $ENGINE; do FILES="$FILES tests/prediction_markets/$t.py"; done
cd "$SCRATCH" && PYTHONPATH="$SCRATCH" "$V" -m pytest $FILES -p no:pytest_ethereum -q -p no:cacheprovider 2>&1 | tail -30

echo
echo "### [3] CREATE-SQL COMPARE -- back-ported pm_loss_grounding_cache vs the BOX's LIVE table (Jack's addition) ###"
FRESH=/home/azureuser/pm_bundle_fresh_$TS.db
PYTHONPATH="$SCRATCH" "$V" - <<PY
import sqlite3
from trading_corp.prediction_markets import db
db.init_db("$FRESH")
c=sqlite3.connect("$FRESH")
mine=c.execute("SELECT sql FROM sqlite_master WHERE name='pm_loss_grounding_cache'").fetchone()
print("  scratch head:", c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0], "(expect 18)")
box=sqlite3.connect("file:$ROOT/data/prediction_markets.db?mode=ro", uri=True)
live=box.execute("SELECT sql FROM sqlite_master WHERE name='pm_loss_grounding_cache'").fetchone()
def norm(s):
    import re; return re.sub(r"\s+"," ",(s or "").strip())
mn=norm(mine[0] if mine else None); ln=norm(live[0] if live else None)
print("  back-ported (scratch):", mn)
print("  box LIVE table       :", ln)
print("  ** CREATE SQL IDENTICAL:", mn==ln)
print("  box live schema head :", box.execute("SELECT MAX(version) FROM schema_version").fetchone()[0], "(expect 17)")
PY
rm -f "$FRESH"

echo
echo "### [4] PROOF B -- DISARMED UFC DRY-RUN vs REAL live UFC markets (matcher has never met the live path) ###"
echo "     matches / MISSES-BY-REASON / WRONG-FIGHT (both fighters) / WRONG-MARKET-TYPE -- a miss is fine; a wrong pick is a STOP"
PYTHONPATH="$SCRATCH" "$V" - <<'PY'
import json, urllib.request, urllib.error, re
from collections import Counter
from trading_corp.data import ufc_poly_kalshi_match as U

def _get(url):
    req=urllib.request.Request(url, headers={"User-Agent":"curl/8.4.0"})
    try:
        r=urllib.request.urlopen(req, timeout=30); return json.loads(r.read())
    except Exception as e:
        print("  GET err", url[:60], e); return None

# ---- real Kalshi UFC index (public) ----
KB="https://api.elections.kalshi.com/trade-api/v2"
def kmk(series):
    out=[]
    for st in ("open",):
        d=_get(KB+"/markets?series_ticker=%s&status=%s&limit=400"%(series,st)) or {}
        out += (d.get("markets") or [])
    return out
fight=[{"ticker":m.get("ticker"),"title":m.get("title")} for m in kmk("KXUFCFIGHT")]
dist=[{"ticker":m.get("ticker"),"title":m.get("title")} for m in kmk("KXUFCDISTANCE")]
idx=U.build_kalshi_fight_index(fight); idx=U.attach_distance_tickers(idx, dist)
dates=frozenset(k[0] for k in idx)
print("  Kalshi: %d fight markets, %d distance markets -> %d fights in index, %d dates"%(len(fight),len(dist),len(idx),len(dates)))
by_ticker={}
for kf in idx.values():
    by_ticker[kf.ticker_a]=kf; by_ticker[kf.ticker_b]=kf

# ---- real Poly UFC events (public gamma) -> synth bets on the SAME cards ----
gd=_get("https://gamma-api.polymarket.com/events?tag_slug=ufc&closed=false&limit=100")
evs=gd if isinstance(gd,list) else ((gd or {}).get("events") or [])
evs=[e for e in evs if str(e.get("slug","")).startswith("ufc-") and re.search(r"\d{4}-\d{2}-\d{2}", e.get("slug",""))]
print("  Polymarket: %d dated ufc-* events (current cards)"%len(evs))

def _in_fight(name, kf):
    return kf is not None and (U.match_fighter_name(name, kf.fighter_a_name) or U.match_fighter_name(name, kf.fighter_b_name))

miss=Counter(); total=matches=ok_ml=ok_dist=0
wrong_fight=[]; wrong_type=[]
for e in evs:
    slug=e.get("slug"); mkts=e.get("markets") or []
    gt=""
    for m in mkts:
        g=(m.get("groupItemTitle") or "")
        if re.search(r"\bvs\.?\b", g): gt=g; break
    parts=re.split(r"\s+vs\.?\s+", gt)
    if len(parts)!=2: continue
    A,B=parts[0].strip(), parts[1].strip()
    polyset={U._norm(A), U._norm(B)}
    # moneyline bets on each fighter
    for who in (A,B):
        total+=1
        r=U.match_bet(U.parse_poly_ufc_bet(slug, who), idx, dates)
        if r.status!="matched": miss["ml:"+r.status]+=1; continue
        matches+=1; tk=r.kalshi_ticker or ""
        if not tk.startswith("KXUFCFIGHT-"): wrong_type.append(("ML",slug,who,tk)); continue     # STOP: moneyline on a non-fight ticker
        kf=by_ticker.get(tk)
        kfset={U._norm(kf.fighter_a_name), U._norm(kf.fighter_b_name)} if kf else set()
        # WRONG-FIGHT: verify BOTH fighters of the matched Kalshi fight are the Poly bet's pair (catches the 3-char
        # abbrev collision -- a match where the bet fighter is right but the OPPONENT belongs to a different bout)
        if not (_in_fight(A, kf) and _in_fight(B, kf)):
            wrong_fight.append(("ML",slug,who,tk,"kfight="+str(sorted(kfset)),"poly="+str(sorted(polyset)))); continue
        ok_ml+=1
    # go-the-distance bet
    total+=1
    r=U.match_bet(U.parse_poly_ufc_bet(slug+"-go-the-distance","Yes"), idx, dates)
    if r.status!="matched": miss["dist:"+r.status]+=1
    else:
        matches+=1; tk=r.kalshi_ticker or ""
        if not tk.startswith("KXUFCDISTANCE-"): wrong_type.append(("DIST",slug,tk))            # STOP: distance on a non-distance ticker
        else: ok_dist+=1

print()
print("  TOTAL synth bets: %d  |  MATCHED: %d (ml_ok=%d dist_ok=%d)  |  MISSED: %d"%(total,matches,ok_ml,ok_dist,sum(miss.values())))
print("  MISS BREAKDOWN BY REASON:")
for k,v in sorted(miss.items(), key=lambda x:-x[1]): print("     %-34s %d"%(k,v))
print("  ** WRONG-FIGHT (STOP if > 0): %d"%len(wrong_fight))
for w in wrong_fight[:12]: print("       ",w)
print("  ** WRONG-MARKET-TYPE (STOP if > 0): %d"%len(wrong_type))
for w in wrong_type[:12]: print("       ",w)
PY

echo
echo "### [5] CLEANUP scratch + tar ###"
rm -rf "$SCRATCH" "$TAR"
echo "  engine PID (UNTOUCHED throughout): $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
echo "### DONE ###"
