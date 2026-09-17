set -u
ROOT=/home/azureuser/trading_corp
V=$ROOT/venv/bin/python
DB=$ROOT/data/prediction_markets.db
TICKER=KXCS2GAME-26SEP171100NIPLG-LG
BLOB=KXCS2GAME-26SEP171100NIPLG
echo "### CS2 LEG-AUDIT LG-not-in-Luminosity DIAGNOSTIC (READ-ONLY) $(date -u +%Y-%m-%dT%H:%M:%SZ) ###"
echo "### ticker=$TICKER  blob=$BLOB ###"
cd "$ROOT" && PM_DB="$DB" TICKER="$TICKER" BLOB="$BLOB" PYTHONPATH="$ROOT" "$V" - <<'PY'
import os, sqlite3, json, re, urllib.request, urllib.parse
PM=os.environ["PM_DB"]; TICKER=os.environ["TICKER"]; BLOB=os.environ["BLOB"]
c=sqlite3.connect("file:%s?mode=ro"%PM, uri=True); c.row_factory=sqlite3.Row

def http(url):
    try:
        req=urllib.request.Request(url, headers={"User-Agent":"ro-diag"})
        return json.loads(urllib.request.urlopen(req, timeout=25).read())
    except Exception as e:
        return {"__err__": str(e)[:200]}

print("========== SECTION A: ORDER ROWS on event blob %s ==========" % BLOB)
cols=["id","account_id","category","wallet","ticker","outcome_leg","order_side","is_exit",
      "signal_outcome","signal_slug","condition_id","outcome_index","signal_id",
      "submitted_count","submitted_price","fill_count","fill_price","outcome_status",
      "client_order_id","broker_order_id","close_source","won","realized_pnl",
      "leg_audit","dry_run","submitted_ts","response_ts","error_detail"]
q="SELECT %s FROM pm_subdivision_order WHERE ticker LIKE ? ORDER BY id" % ",".join(cols)
rows=c.execute(q, (BLOB+"%",)).fetchall()
print("rows on blob: %d" % len(rows))
for r in rows:
    print("-"*60)
    for k in cols:
        print("  %-16s = %r" % (k, r[k]))

print()
print("========== SECTION B: CS2 fill census (is this the first cs2 fill?) ==========")
for r in c.execute("SELECT account_id, outcome_status, is_exit, dry_run, COUNT(*) n, MIN(response_ts) first_ts, MAX(response_ts) last_ts FROM pm_subdivision_order WHERE category='cs2' GROUP BY account_id, outcome_status, is_exit, dry_run ORDER BY account_id, outcome_status, is_exit, dry_run"):
    print("  acct=%s status=%s is_exit=%s dry=%s -> n=%s first_ts=%s last_ts=%s" % (r["account_id"], r["outcome_status"], r["is_exit"], r["dry_run"], r["n"], r["first_ts"], r["last_ts"]))
print("  -- distinct cs2 tickers ever ORDERED (real dry_run=0), oldest first:")
for r in c.execute("SELECT ticker, COUNT(*) n, GROUP_CONCAT(DISTINCT outcome_status) sts, MIN(response_ts) first_ts FROM pm_subdivision_order WHERE category='cs2' AND dry_run=0 GROUP BY ticker ORDER BY MIN(response_ts)"):
    print("     first_ts=%s  %s  n=%s statuses=%s" % (r["first_ts"], r["ticker"], r["n"], r["sts"]))
print("  -- earliest cs2 FILLED real entry:")
r=c.execute("SELECT id, account_id, ticker, response_ts, datetime(response_ts,'unixepoch') utc FROM pm_subdivision_order WHERE category='cs2' AND dry_run=0 AND outcome_status='filled' AND is_exit=0 ORDER BY response_ts LIMIT 1").fetchone()
print("     %s" % (dict(r) if r else "NONE"))

print()
print("========== SECTION C: all cs2 leg_audit verdicts (real) ==========")
for r in c.execute("SELECT leg_audit, COUNT(*) n FROM pm_subdivision_order WHERE category='cs2' AND dry_run=0 GROUP BY leg_audit ORDER BY n DESC"):
    print("  %r -> %s" % (r["leg_audit"], r["n"]))

lg=[r for r in rows if (r["ticker"] or "").endswith("-LG")]
slug = lg[0]["signal_slug"] if lg else None
whale_oc = lg[0]["signal_outcome"] if lg else None

print()
print("========== SECTION D: KALSHI public market metadata (verbatim) ==========")
knames={}
for suff in ("LG","NIP"):
    tk = BLOB + "-" + suff
    d = http("https://api.elections.kalshi.com/trade-api/v2/markets/%s" % tk)
    m = d.get("market") if isinstance(d, dict) else None
    if not m:
        print("  %s -> %r" % (tk, d)); continue
    knames[suff]=(m.get("yes_sub_title") or m.get("yes") or "").strip()
    print("  ticker=%s" % tk)
    for k in ("title","yes_sub_title","no_sub_title","subtitle","status","result","event_ticker","open_time","close_time","expiration_time"):
        if k in m: print("     %-14s = %r" % (k, m[k]))

print()
print("========== SECTION E: POLYMARKET market for whale slug ==========")
print("  whale signal_slug=%r  signal_outcome=%r" % (slug, whale_oc))
if slug:
    d = http("https://gamma-api.polymarket.com/markets?slug=%s" % urllib.parse.quote(slug))
    if isinstance(d, list) and d:
        m=d[0]
        for k in ("question","slug","outcomes","outcomePrices","umaResolutionStatus","closed","conditionId"):
            if k in m: print("     %-16s = %r" % (k, m[k]))
    else:
        print("     gamma slug lookup -> %r" % d)

print()
print("========== SECTION F: EXACT-NORMALIZED MATCH via DEPLOYED matcher ==========")
try:
    from trading_corp.data import cs2_poly_kalshi_match as CS2
    lg_name=knames.get("LG",""); nip_name=knames.get("NIP","")
    cw=CS2._canon(whale_oc or ""); clg=CS2._canon(lg_name); cnip=CS2._canon(nip_name)
    print("  Kalshi -LG  yes_sub_title=%r  canon=%r" % (lg_name, clg))
    print("  Kalshi -NIP yes_sub_title=%r  canon=%r" % (nip_name, cnip))
    print("  whale outcome=%r  canon=%r" % (whale_oc, cw))
    print("  MATCH  canon(whale)==canon(-LG)?  -> %s" % (cw==clg))
    print("  ANTI   canon(whale)==canon(-NIP)? -> %s (must be False)" % (cw==cnip))
    try:
        from trading_corp.data.ufc_poly_kalshi_match import _code_of, labels_code_swapped, _code_match_score
        ca, cb = _code_of(BLOB+"-LG"), _code_of(BLOB+"-NIP")
        print("  codes: -LG=%r -NIP=%r" % (ca, cb))
        print("  code_match_score direct: LG~LGname=%s  NIP~NIPname=%s" % (_code_match_score(ca,lg_name), _code_match_score(cb,nip_name)))
        print("  code_match_score cross : LG~NIPname=%s  NIP~LGname=%s" % (_code_match_score(ca,nip_name), _code_match_score(cb,lg_name)))
        print("  labels_code_swapped -> %s (True=refuse-as-swap; must be False)" % labels_code_swapped(ca,lg_name,cb,nip_name))
    except Exception as e2:
        print("  code-swap import/compute note: %r" % e2)
    fold=lambda s: re.sub(r"[^a-z0-9]","", CS2._canon(s or ""))
    def subseq(code,oc):
        cc=re.sub(r"[^a-z0-9]","",(code or "").lower()); nn=fold(oc); i=0
        for ch in nn:
            if i<len(cc) and ch==cc[i]: i+=1
        return bool(cc) and i==len(cc)
    print("  leg-audit subseq('LG', whale=%r) -> %s (False => code_review flag fires; benign per matcher comment)" % (whale_oc, subseq("LG", whale_oc or "")))
except Exception as e:
    import traceback; print("  matcher import/compute error: %r"%e); traceback.print_exc()

c.close()
PY
echo "### DONE ###"
