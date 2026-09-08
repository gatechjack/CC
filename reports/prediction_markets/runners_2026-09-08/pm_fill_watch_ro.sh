set -u; ROOT=/home/azureuser/trading_corp; V=$ROOT/venv/bin/python; DB=$ROOT/data/prediction_markets.db
echo "### FILL-WATCH soccer + cfb + nfl + cs2 (READ-ONLY) -- Kalshi title read back against the whale's bet $(date -u +%Y-%m-%dT%H:%M:%SZ) ###"
echo "### soccer(incl mex): leg/-TIE/PARIS ; cfb+nfl+wnba STRUCTURAL: both teams + sdst../LA,WSH,LAS/GSV,POR ; cs2 ESPORTS: both orgs EXACT + academy/fe/NXT/Ares/ex- flag ###"
echo
echo "## shard-0 skips / underfunded in the journal (last 2h): ##"
# tightened: require a NON-ZERO count after the key -- the per-cycle summary dict enumerates every skip key
# at 0, so a bare 'shard_underfunded' match reads those as events. '[^,}]*[1-9]' fires only on a real count>=1.
journalctl -u trading-corp --since "2 hours ago" 2>/dev/null | grep -iE "shard_underfunded[^,}]*[1-9]|insufficient_balance" | tail -6 | sed -E 's/.*xvfb-run\[[0-9]+\]: //' | cut -c1-130 | sed 's/^/   /'
echo "   (empty = none -- real shard-underfunded skips only, not the count-0 summary enumeration)"
cd "$ROOT" && PM_DB="$DB" PYTHONPATH="$ROOT" "$V" - <<'PY'
import os,sqlite3,json,re,urllib.request
from trading_corp.data import soccer_poly_kalshi_match as SO
from trading_corp.data import sports_structural_match as ssm
from trading_corp.data import cs2_poly_kalshi_match as CS2
DB=os.environ["PM_DB"]
CATS=tuple(sorted(SO.LEAGUES))   # every soccer league (bra/bun/epl/fl1/lal/mex/mls/sea/ucl/uel)
c=sqlite3.connect("file:%s?mode=ro"%DB,uri=True); c.row_factory=sqlite3.Row
def kmarket(ticker):
    try:
        d=json.loads(urllib.request.urlopen("https://api.elections.kalshi.com/trade-api/v2/markets/%s"%ticker,timeout=15).read())
        m=d.get("market") or {}; return m.get("title"),m.get("yes_sub_title")
    except Exception as e: return None,"(fetch err %s)"%str(e)[:30]
def whale_bet(w,cid,oidx):
    # FIX (2026-09-07): team/leg for a market is wallet-INDEPENDENT. Look up by MARKET (condition_id),
    # not the exact (wallet,condition_id,outcome_index) key -- that returned None when the copied whale had
    # EXITED, and the guard then read empty-vs-real as "TEAMS DISAGREE" on a CORRECT fill. Return the wallet
    # found too, so a co-whale match is visible. None from here means COULD-NOT-READ, never confirmed-wrong.
    for cond,args in (("condition_id=? AND outcome_index=?",(cid,oidx)), ("condition_id=?",(cid,))):
        for t in ("pm_open_position","pm_closed_position"):
            r=c.execute("SELECT slug,outcome,title,wallet FROM %s WHERE %s LIMIT 1"%(t,cond),args).fetchone()
            if r: return r["slug"],r["outcome"],r["title"],r["wallet"]
    return None,None,None,None
q=",".join("?"*len(CATS))
rows=c.execute("SELECT * FROM pm_subdivision_order WHERE category IN (%s) AND dry_run=0 ORDER BY response_ts DESC LIMIT 60"%q,CATS).fetchall()
print("\n## soccer REAL orders (dry_run=0) across %s: %d ##"%(",".join(CATS),len(rows)))
if not rows: print("   (none yet -- fills arrive as each whale opens an upcoming position with a live market)")
for r in rows:
    slug,oc,title,fw=whale_bet(r["wallet"],r["condition_id"],r["outcome_index"])
    kt,ys=kmarket(r["ticker"])
    tieflag="  <-- TIE-leg (draw mapping, never exercised with money)" if (r["ticker"] or "").upper().endswith("-TIE") else ""
    print("   ------------------------------------------------------------")
    print("   %s/%s  leg=%s status=%s fill=%s@%s%s"%(r["account_id"],r["category"],r["outcome_leg"],r["outcome_status"],r["fill_count"],r["fill_price"],tieflag))
    if slug is None:
        print("     VERDICT: UNVERIFIED -- whale side unreadable (no position in market %s). This is COULD-NOT-READ,"%r["condition_id"][:14])
        print("              NOT a confirmed wrong bet. Verify by hand; do NOT global-disarm on this alone.")
        print("     KALSHI: %s  leg=%s  kalshi_title=%r yes_sub=%r"%(r["ticker"],r["outcome_leg"],kt,ys))
    else:
        cowhale = "" if (fw and r["wallet"] and fw.lower()==r["wallet"].lower()) else "  (via co-whale %s in same market)"%((fw or "?")[:10]+"..")
        want="yes" if (oc=="Yes") else ("no" if oc=="No" else None)
        legok = (want is None) or (r["outcome_leg"]==want)
        blob=("%s %s %s %s"%(slug,title,r["ticker"],ys)).lower()
        paris="paris" in blob
        if not legok: print("     VERDICT: *** CONFIRMED WRONG LEG (whale %s -> want %s, we sent %s) -> pm_global_disarm ***"%(oc,want,r["outcome_leg"]))
        elif paris:  print("     VERDICT: MATCH on leg -- but PARIS/PSG in the market: SANITY-CHECK OPPONENT (PSG: Rennes/Lille/Monaco ; Paris FC: Nice/Lyon)")
        else:        print("     VERDICT: MATCH (leg %s ok)%s"%(r["outcome_leg"],cowhale))
        print("     WHALE : %s  outcome=%r  title=%r"%(slug,oc,title))
        print("     KALSHI: %s  leg=%s  kalshi_title=%r yes_sub=%r"%(r["ticker"],r["outcome_leg"],kt,ys))
    if r["outcome_status"] in ("rejected","error"): print("     ** %s: %s **"%(r["outcome_status"],r["error_detail"]))

# ---- STRUCTURAL matchers (cfb + nfl + wnba) -- verify BOTH teams + each league's known-ambiguous codes ----
# cfb: sdst DROPPED (SanDiegoSt vs SouthDakotaSt), K/W/C-SU pinned FBS. nfl: LA/WSH/LAS = the 3 dry-run
# team-map bugs (fixed; the shape that comes in threes) -> flag on a fill so a wrong team can't pass silently.
# wnba: the cross-venue-aliased codes (Poly gsv/por vs Kalshi GS/PDX -> both map to Golden State Valkyries /
# Portland Fire) are the most-likely-to-be-wrong -> flag them so a bad alias can't pass as the right club.
STRUCT={"cfb":({"SDST","KSU","WSU","CSU"},"sdst DROPPED; K/W/C-SU=FBS"),
        "nfl":({"LA","WSH","LAS"},"LA/WSH/LAS were the 3 fixed dry-run bugs -- verify the exact team"),
        "wnba":({"GSV","POR","GS","PDX"},"Poly gsv/por vs Kalshi GS/PDX cross-venue aliases -- verify the exact team")}
for scat,(AMBIG,note) in STRUCT.items():
    cfg=ssm.LEAGUES.get(scat)
    sre=re.compile(r"^%s-([a-z0-9]+)-([a-z0-9]+)-\d{4}-\d{2}-\d{2}"%scat)
    srows=c.execute("SELECT * FROM pm_subdivision_order WHERE category=? AND dry_run=0 ORDER BY response_ts DESC LIMIT 40",(scat,)).fetchall()
    print("\n## %s REAL orders (dry_run=0) -- STRUCTURAL: confirm BOTH teams, not just the one we bet: %d ##"%(scat,len(srows)))
    if not srows: print("   (none yet)")
    for r in srows:
        slug,oc,title,fw=whale_bet(r["wallet"],r["condition_id"],r["outcome_index"])
        kt,ys=kmarket(r["ticker"])
        kp=ssm.parse_kalshi_ticker(r["ticker"],cfg) if cfg else None
        kteams={kp[4],kp[5]} if kp else set()               # what WE bought (from the Kalshi ticker) -- always readable
        kyes = kp[4] if kp else None                        # the yes/to-win side we took
        m=sre.match(slug or "")
        away=(m.group(1) or "").upper() if m else None; home=(m.group(2) or "").upper() if m else None
        pteams={cfg.team_map.get(away),cfg.team_map.get(home)} if (cfg and m) else set()
        hit=({away,home}&AMBIG) if m else set()
        ambigflag="  ** AMBIGUOUS/HISTORICALLY-BUGGY CODE (%s) -- %s **"%(",".join(sorted(hit)),note) if hit else ""
        print("   ------------------------------------------------------------")
        print("   %s/%s  leg=%s status=%s fill=%s@%s"%(r["account_id"],scat,r["outcome_leg"],r["outcome_status"],r["fill_count"],r["fill_price"]))
        if slug is None or not m:
            # COULD-NOT-READ: whale side unreadable OR slug not parseable -> NOT a confirmed wrong team
            print("     VERDICT: UNVERIFIED -- whale side unreadable (market %s). COULD-NOT-READ, not confirmed-wrong."%r["condition_id"][:14])
            print("              We bought: %s (from ticker). Verify the whale bet this game by hand; do NOT disarm on this alone.%s"%(sorted(kteams),ambigflag))
        else:
            cowhale = "" if (fw and r["wallet"] and fw.lower()==r["wallet"].lower()) else "  (via co-whale %s, same market)"%((fw or "?")[:10]+"..")
            teams_ok = (None not in pteams) and (kteams and pteams==kteams)
            if not teams_ok:
                print("     VERDICT: *** CONFIRMED WRONG/UNMAPPED TEAMS -> pm_global_disarm ***")
            else:
                print("     VERDICT: MATCH -- same game, both teams agree%s%s"%(cowhale,ambigflag))
            print("     WHALE : %s  outcome=%r  -> teams=%s"%(slug,oc,sorted(x for x in pteams if x)))
        print("     KALSHI: %s -> teams=%s  (we took '%s' to win)"%(r["ticker"],sorted(kteams),kyes))
        print("             kalshi_title=%r"%(kt,))
        if r["outcome_status"] in ("rejected","error"): print("     ** %s: %s **"%(r["outcome_status"],r["error_detail"]))

# ---- cs2 (ESPORTS pair-key; EXACT-normalized, never fuzzy) -- read back BOTH orgs; ENCE!=ENCE Academy etc ----
# cs2's Cerundolo case: Academy / Junior / NXT / fe / Ares / ex-<Org> are DISTINCT ENTITIES. Flag any such org
# so a fill onto a farm/academy roster can't pass as its parent club on a matching-looking title.
CS2AMBIG=re.compile(r"academy|junior|\bnxt\b|\bfe\b|\bares\b|(^|[^a-z])ex[- ]", re.I)
crows=c.execute("SELECT * FROM pm_subdivision_order WHERE category='cs2' AND dry_run=0 ORDER BY response_ts DESC LIMIT 40").fetchall()
print("\n## cs2 REAL orders (dry_run=0) -- ESPORTS: read back BOTH orgs, EXACT match (academy/fe/NXT/Ares/ex- = DISTINCT): %d ##"%len(crows))
if not crows: print("   (none yet)")
for r in crows:
    slug,oc,title,fw=whale_bet(r["wallet"],r["condition_id"],r["outcome_index"])
    kt,ys=kmarket(r["ticker"])                       # Kalshi cs2 title = "{Org} wins"
    korg=(kt or "").strip()
    if korg.lower().endswith(" wins"): korg=korg[:-5].strip()
    if not korg: korg=(ys or "").strip()
    print("   ------------------------------------------------------------")
    print("   %s/cs2  leg=%s status=%s fill=%s@%s"%(r["account_id"],r["outcome_leg"],r["outcome_status"],r["fill_count"],r["fill_price"]))
    if slug is None:
        print("     VERDICT: UNVERIFIED -- whale side unreadable (market %s). COULD-NOT-READ, not confirmed-wrong."%r["condition_id"][:14])
        print("              We bought org %r (from Kalshi title). Verify the whale bet this match by hand; do NOT disarm on this alone."%korg)
    else:
        p=CS2.parse_poly_cs2_bet(slug,oc,title)
        worg=p.outcome_name or oc
        both=[x for x in (p.org_a,p.org_b) if x]
        ambig=sorted({o for o in [worg,korg]+both if o and CS2AMBIG.search(o)})
        cowhale="" if (fw and r["wallet"] and fw.lower()==r["wallet"].lower()) else "  (via co-whale %s)"%((fw or "?")[:10]+"..")
        if not worg or not korg:
            print("     VERDICT: UNVERIFIED -- could not read whale org or Kalshi org.")
        elif CS2._canon(worg)!=CS2._canon(korg):
            print("     VERDICT: *** CONFIRMED WRONG ORG (whale bet %r, we took %r) -> pm_global_disarm ***"%(worg,korg))
        elif ambig:
            print("     VERDICT: MATCH -- but AMBIGUOUS ORG (%s): Academy/fe/NXT/Ares/ex- are DISTINCT rosters -- verify the EXACT one%s"%(",".join(ambig),cowhale))
        else:
            print("     VERDICT: MATCH -- same org, exact-normalized%s"%cowhale)
        print("     WHALE : %s  outcome=%r  (match: %s)"%(slug,oc," vs ".join(both) if both else "?"))
    print("     KALSHI: %s  we took org=%r  kalshi_title=%r"%(r["ticker"],korg,kt))
    if r["outcome_status"] in ("rejected","error"): print("     ** %s: %s **"%(r["outcome_status"],r["error_detail"]))

print("\n## shard-0 balance now (revolving-capital watch) ##")
for a in ("kalshi_jack","kalshi_karen"):
    r=c.execute("SELECT by_shard_json,snapshot_ts FROM pm_shard_balance_snapshot WHERE account_id=? ORDER BY snapshot_ts DESC LIMIT 1",(a,)).fetchone()
    bs=json.loads(r["by_shard_json"]) if r else {}
    print("   %-13s shard0=$%s shard3=$%s"%(a,bs.get("0"),bs.get("3")))
c.close()
PY
echo "### FILL-WATCH DONE ###"
