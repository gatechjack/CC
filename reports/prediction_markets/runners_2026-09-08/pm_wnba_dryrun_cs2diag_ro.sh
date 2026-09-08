set -u
ROOT=/home/azureuser/trading_corp
V=$ROOT/venv/bin/python
DB=$ROOT/data/prediction_markets.db
BOOT=$(date -d "$(systemctl show trading-corp -p ActiveEnterTimestamp --value)" +%s 2>/dev/null)
echo "### WNBA DRY-RUN (v2 fixed fetch) + cs2 STARVATION ROOT-CAUSE (READ-ONLY) -- $(date -u +%Y-%m-%dT%H:%M:%SZ) ###"
echo "### engine last-boot epoch=$BOOT (attach added_ts > boot => attached AFTER boot => NOT in the running task cats => needs restart) ###"
echo
cd "$ROOT" && PM_DB="$DB" BOOT_TS="$BOOT" PYTHONPATH="$ROOT" "$V" - <<'PY'
import os,sqlite3,json,re,urllib.request,urllib.parse
from collections import Counter
from trading_corp.data import sports_structural_match as ssm
PM=os.environ["PM_DB"]; BOOT=int(os.environ.get("BOOT_TS") or 0)
c=sqlite3.connect("file:%s?mode=ro"%PM,uri=True); c.row_factory=sqlite3.Row

def fetch_series(series, status=None, maxpages=30):
    base="https://api.elections.kalshi.com/trade-api/v2/markets"
    tks=[]; cursor=None; pg=0; err=None; nopen=0
    try:
        while pg<maxpages:
            q={"series_ticker":series,"limit":"1000"}
            if status: q["status"]=status
            if cursor: q["cursor"]=cursor
            d=json.loads(urllib.request.urlopen(base+"?"+urllib.parse.urlencode(q),timeout=25).read())
            ms=d.get("markets") or []
            for m in ms:
                if m.get("ticker"): tks.append(m["ticker"])
                if (m.get("status") or "") in ("open","active"): nopen+=1
            cursor=d.get("cursor"); pg+=1
            if not cursor or not ms: break
    except Exception as e: err=str(e)[:120]
    return tks,nopen,err

# ---- attach added_ts vs boot: which target subs were in the RUNNING task's boot-fixed cats ----
print("== ATTACH-vs-BOOT (the running engine cycles a category ONLY if it was attached at boot) ==")
for cat in ("cs2","wnba","mex"):
    for acct in ("kalshi_jack","kalshi_karen"):
        r=c.execute("SELECT wallet,added_ts FROM pm_subdivision_attachment WHERE account_id=? AND category=? AND active=1",(acct,cat)).fetchone()
        if not r: print("   %-13s/%-4s no active attach"%(acct,cat)); continue
        rel="AFTER boot -> NOT in running cats -> RESTART REQUIRED" if (r["added_ts"] and BOOT and r["added_ts"]>BOOT) else "before boot -> in running cats (already cycling)"
        print("   %-13s/%-4s added_ts=%s  (boot=%s) -> %s"%(acct,cat,r["added_ts"],BOOT,rel))

# ===================== WNBA DRY-RUN (fetch ALL markets so finalized games are in-window) =====================
cfg=ssm.LEAGUES["wnba"]
tks,nopen,err=fetch_series("KXWNBAGAME")   # no status -> all (open + finalized)
print("\n== WNBA: KXWNBAGAME fetch -> %d markets (%d currently open/active)%s =="%(len(tks),nopen,("  FETCH-ERR: "+err) if err else ""))
idx=ssm.build_game_index(tks,cfg); dates=frozenset(k[0] for k in idx)
print("   games in index=%d ; index dates=%d ; NOTE: 0 open => wnba won't COPY live until games are listed (map still provable vs finalized)"%(sum(len(v) for v in idx.values()),len(dates)))
gre=re.compile(r"^wnba-([a-z0-9]+)-([a-z0-9]+)-(\d{4}-\d{2}-\d{2})(.*)$")
ALIAS={"GSV","POR","GS","PDX"}
wal=c.execute("SELECT account_id,wallet FROM pm_subdivision_attachment WHERE category='wnba' AND active=1 ORDER BY account_id").fetchall()
gwg=0; gwt=0; gmatched=0
for row in wal:
    acct=row["account_id"]; w=row["wallet"]
    bets=c.execute("SELECT 'open' src,slug,outcome FROM pm_open_position WHERE wallet=? AND category='wnba' "
                   "UNION ALL SELECT 'closed' src,slug,outcome FROM pm_closed_position WHERE wallet=? AND category='wnba'",(w,w)).fetchall()
    nopenb=sum(1 for b in bets if b["src"]=="open")
    print("\n   -- %s  wallet=%s  (%d wnba bets: %d open, %d closed) --"%(acct,w,len(bets),nopenb,len(bets)-nopenb))
    cnt=Counter(); wrong_game=[]; wrong_type=[]; matched=[]; alias_hits=[]
    for b in bets:
        slug=b["slug"] or ""; oc=b["outcome"] or ""
        r=ssm.match_bet(ssm.parse_poly_bet(slug,oc,cfg),idx,dates,cfg)
        cnt[r.status]+=1
        if r.status=="matched" and r.kalshi_ticker:
            m=gre.match(slug)
            pteams={cfg.team_map.get((m.group(1) or "").upper()),cfg.team_map.get((m.group(2) or "").upper())} if m else set()
            kp=ssm.parse_kalshi_ticker(r.kalshi_ticker,cfg); kteams={kp[4],kp[5]} if kp else set()
            if (None in pteams) or (pteams!=kteams): wrong_game.append((slug,oc,r.kalshi_ticker,sorted(x for x in pteams if x),sorted(kteams)))
            if m and m.group(4): wrong_type.append((slug,r.kalshi_ticker))
            hit=({(m.group(1) or "").upper(),(m.group(2) or "").upper()}&ALIAS) if m else set()
            if hit: alias_hits.append((slug,oc,r.kalshi_ticker,sorted(hit)))
            if len(matched)<8: matched.append((b["src"],slug,oc,r.kalshi_ticker))
    inw=cnt["matched"]+cnt.get("no_kalshi_contract",0)
    print("      classification: %s"%dict(cnt.most_common()))
    if inw: print("      in-window matched %d/%d = %.0f%%"%(cnt["matched"],inw,100.0*cnt["matched"]/inw))
    print("      ** GATE ** wrong_game=%d wrong_market_type=%d matched=%d -> %s"%(len(wrong_game),len(wrong_type),cnt["matched"],"PASS" if (not wrong_game and not wrong_type) else "*** STOP ***"))
    gwg+=len(wrong_game); gwt+=len(wrong_type); gmatched+=cnt["matched"]
    for x in wrong_game[:6]: print("         WRONG GAME:",x)
    for x in wrong_type[:6]: print("         WRONG TYPE:",x)
    if alias_hits:
        print("      GSV/POR alias-code matches (verify each mapped to the right club):")
        for a in alias_hits[:10]: print("         ",a)
    else:
        print("      (no GSV/POR alias-code bets matched in this whale's wnba book)")
    print("      matched samples:")
    for s in matched: print("         [%s] %-42s %-16s %s"%(s[0],s[1][:42],s[2][:16],s[3]))
print("\n   ===== WNBA GATE (both accounts): wrong_game=%d wrong_type=%d matched=%d -> %s ====="%(gwg,gwt,gmatched,("PASS - gate CLOSED by dry-run" if (not gwg and not gwt and gmatched>0) else ("*** STOP ***" if (gwg or gwt) else "INCONCLUSIVE - 0 matched (no in-window games)"))))

# ===================== cs2 ROOT-CAUSE =====================
print("\n== cs2 STARVATION ROOT-CAUSE ==")
for acct in ("kalshi_jack","kalshi_karen"):
    h=c.execute("SELECT reached_ts,evaluated_ts,n_signals,state,updated_ts FROM pm_driver_heartbeat WHERE account_id=? AND category='cs2'",(acct,)).fetchone()
    print("   %-13s/cs2 heartbeat=%s"%(acct,("state=%s reached=%s evaluated=%s"%(h["state"],h["reached_ts"],h["evaluated_ts"])) if h else "NONE (task never cycled cs2)"))
ct,copen,cerr=fetch_series("KXCS2GAME",status="open")
print("   KXCS2GAME open markets: %d%s"%(len(ct),("  FETCH-ERR: "+cerr) if cerr else ""))
if cerr: verdict="Kalshi fetch failed (network) -- re-check"
elif len(ct)>0: verdict="markets EXIST (%d open) + no heartbeat => cs2 was attached AFTER boot, NOT in the running task cats => RESTART REQUIRED to evaluate+copy cs2 (arming without a restart leaves it inert)"%len(ct)
else: verdict="0 open cs2 markets -> benign lull"
print("   VERDICT: %s"%verdict)
c.close()
print("\n### WNBA DRY-RUN v2 + cs2 ROOT-CAUSE DONE ###")
PY
echo "### exit ###"
