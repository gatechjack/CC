#!/usr/bin/env python3
# READ-ONLY kalshi review probe. No writes (PRAGMA query_only=ON).
# Covers: kalshi_arbitrage + kalshi_llm_arbitrage divisions.
# Fee model: per-ORDER ceil(0.07*C*P*(1-P)) in dollars (rounded up to cent).
import sqlite3, math, os, sys, glob, subprocess
from collections import defaultdict, Counter

DB_CANDIDATES = [
    "/home/azureuser/trading_corp/data/trading_corp.db",
    os.path.expanduser("~/trading_corp/data/trading_corp.db"),
    "trading_corp/data/trading_corp.db",
]
EPOCH_LLM = "2026-07-07T16:40"   # kalshi_llm epoch (a.ts / entry_ts cut)
FIX_ARB   = "2026-07-07"         # arbitrage leg_date fix day
ARB_DIV = "kalshi_arbitrage"
LLM_DIV = "kalshi_llm_arbitrage"
ARB_ACTORS = ("kalshi_tail_price_arb", "kalshi_temporal_bucket_arb")
LLM_ACTORS = ("kalshi_llm_arbitrage",)


def fee(c, p):
    if p is None or c is None or float(c) <= 0:
        return 0.0
    p = max(0.0, min(1.0, float(p)))
    return math.ceil(0.07 * float(c) * p * (1.0 - p) * 100.0) / 100.0


def hr(t):
    print("\n===== " + t + " =====")


def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return "<err %r>" % (e,)


def classify_half(ticker, category):
    t = (ticker or "").upper()
    c = (category or "").upper()
    elect_keys = ["MICH", "TURNOUT", "ELECT", "GOVERNOR", "SENATE", "PRES",
                  "MAYOR", "BALLOT", "VOTE", "POLL", "PRIMARY", "CONGRESS"]
    for k in elect_keys:
        if k in t or k in c:
            return "Elections"
    if "POLIT" in c:
        return "Elections"
    return "Economics"


def wl_line(rows):
    n = len(rows)
    if n == 0:
        return "n=0"
    w = sum(1 for r in rows if r["won"])
    gross = sum(float(r["realized_pnl"] or 0) for r in rows)
    fees = sum(fee(r["qty"], r["entry_price"]) for r in rows)
    net = gross - fees
    voids = sum(1 for r in rows if (r["market_result"] or "").lower() == "void")
    return ("n=%d  W=%d L=%d (void=%d)  WR=%.1f%%  gross=$%.2f  fees=$%.2f  net=$%.2f"
            % (n, w, n - w, voids, 100.0 * w / n, gross, fees, net))


# ---------------- OS / SERVICE HEALTH ----------------
hr("OS_SERVICE_HEALTH")
print("hostname:", run(["hostname"]))
print("utc_now :", run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"]))
print("svc trading-corp is-active:", run(["systemctl", "is-active", "trading-corp"]))
print("pgrep -af trading_corp:")
print(run(["pgrep", "-af", "trading_corp"]) or "  <none>")
print("ps python3 (pid/etimes/rss/cmd):")
print(run(["ps", "-o", "pid,etimes,rss,cmd", "-C", "python3"]) or "  <none>")
for pf in ["/home/azureuser/trading_corp/data/trading_corp.pid",
           os.path.expanduser("~/trading_corp/data/trading_corp.pid")]:
    if os.path.exists(pf):
        try:
            print("pidfile", pf, "=", open(pf).read().strip())
        except Exception as e:
            print("pidfile", pf, "err", e)
        break
print("journalctl errors last 6h (may be perm-restricted):")
jc = run(["journalctl", "-u", "trading-corp", "--since", "-6h", "--no-pager"], timeout=25)
if jc.startswith("<err") or "No journal" in jc or not jc:
    print("  <journal unavailable to azureuser or empty>")
else:
    keep = [ln for ln in jc.splitlines()
            if any(k in ln.lower() for k in ("traceback", "error", "exception", "critical"))]
    print("  error-ish lines:", len(keep))
    for ln in keep[-20:]:
        print("   ", ln[-200:])
# app log files
logs = []
for pat in ["/home/azureuser/trading_corp/logs/*.log",
            "/home/azureuser/trading_corp/*.log"]:
    logs += glob.glob(pat)
print("log files found:", logs[:10])

# ---------------- DB CONNECT ----------------
db = next((p for p in DB_CANDIDATES if os.path.exists(p)), None)
if not db:
    print("\nDB_NOT_FOUND", DB_CANDIDATES)
    sys.exit(1)
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
con.execute("PRAGMA query_only=ON")

hr("DB_META")
print("DB_PATH:", db, " size_MB:", round(os.path.getsize(db) / 1e6, 2))
for tbl in ("kalshi_round_trips", "audit_event", "kalshi_equity_history", "agent_state"):
    try:
        n = con.execute("SELECT COUNT(*) FROM %s" % tbl).fetchone()[0]
        print("table %-22s rows=%s" % (tbl, n))
    except Exception as e:
        print("table %-22s <missing: %s>" % (tbl, e))

# pull all kalshi_round_trips (all divisions) for full picture
cols = ("id,order_id,ticker,event_ticker,event_title,category,strategy,division,"
        "arb_type,arb_set_id,outcome_bet,qty,entry_price,notional,entry_ts,resolved_ts,"
        "market_result,won,realized_pnl,roi_pct,implied_at_entry,llm_prob,divergence_pct,"
        "edge_cents,entry_order_id")
allrt = con.execute("SELECT %s FROM kalshi_round_trips" % cols).fetchall()

hr("KRT_DIVISION_STRATEGY_MATRIX (all divisions)")
mat = defaultdict(list)
for r in allrt:
    mat[(r["division"], r["strategy"])].append(r)
for k in sorted(mat):
    rs = mat[k]
    span = "%s .. %s" % (min(x["entry_ts"] for x in rs), max(x["entry_ts"] for x in rs))
    print("div=%-24s strat=%-28s %s  entry_span[%s]"
          % (k[0], k[1], wl_line(rs), span))

# focus rows
arb = [r for r in allrt if r["division"] == ARB_DIV]
llm = [r for r in allrt if r["division"] == LLM_DIV]

# ---------------- RESOLVER HEALTH ----------------
hr("RESOLVER_HEALTH")
def recent_resolved(rows, label):
    if not rows:
        print("%s: no resolved rows" % label)
        return
    rts = sorted((r["resolved_ts"] or "") for r in rows)
    print("%s: resolved n=%d  earliest_resolved=%s  latest_resolved=%s"
          % (label, len(rows), rts[0], rts[-1]))
    byday = Counter((r["resolved_ts"] or "")[:10] for r in rows)
    last = sorted(byday)[-14:]
    print("   resolved-by-day (last 14 present days): "
          + ", ".join("%s:%d" % (d, byday[d]) for d in last))
recent_resolved(arb, ARB_DIV)
recent_resolved(llm, LLM_DIV)

hr("PENDING_UNRESOLVED_AUDIT_BY_ACTOR")
q = ("SELECT a.actor AS actor, COUNT(*) n, MIN(a.ts) old, MAX(a.ts) new "
     "FROM audit_event a "
     "LEFT JOIN kalshi_round_trips r ON r.order_id = json_extract(a.payload_json,'$.order_id') "
     "WHERE a.kind IN ('would_have_placed','kalshi_copy_placed_live') "
     "  AND COALESCE(json_extract(a.payload_json,'$.side'),'buy')='buy' "
     "  AND r.order_id IS NULL "
     "  AND json_extract(a.payload_json,'$.order_id') NOT IN "
     "      (SELECT entry_order_id FROM kalshi_round_trips WHERE entry_order_id IS NOT NULL) "
     "GROUP BY a.actor ORDER BY n DESC")
for r in con.execute(q).fetchall():
    print("actor=%-28s pending=%-6d oldest=%s newest=%s"
          % (r["actor"], r["n"], r["old"], r["new"]))

hr("AUDIT_ENTRY_RATE_BY_ACTOR (would_have_placed / copy_placed_live)")
q2 = ("SELECT actor, COUNT(*) n, MIN(ts) old, MAX(ts) new "
      "FROM audit_event WHERE kind IN ('would_have_placed','kalshi_copy_placed_live') "
      "AND actor LIKE 'kalshi_%' GROUP BY actor ORDER BY n DESC")
for r in con.execute(q2).fetchall():
    print("actor=%-28s emissions=%-7d span[%s .. %s]"
          % (r["actor"], r["n"], r["old"], r["new"]))

hr("ARB_ENTRY_RATE_BY_DAY (last 30 present days, arb actors)")
q3 = ("SELECT substr(ts,1,10) d, actor, COUNT(*) n FROM audit_event "
      "WHERE kind IN ('would_have_placed','kalshi_copy_placed_live') "
      "AND actor IN (?,?) GROUP BY d, actor ORDER BY d DESC LIMIT 60")
for r in con.execute(q3, ARB_ACTORS).fetchall():
    print("  %s  %-28s %d" % (r["d"], r["actor"], r["n"]))

# ---------------- BACKLOG VS FORWARD ----------------
def split_report(rows, fix, label):
    hr("BACKLOG_VS_FORWARD  %s  (cut entry_ts >= %s)" % (label, fix))
    back = [r for r in rows if (r["entry_ts"] or "") < fix]
    fwd = [r for r in rows if (r["entry_ts"] or "") >= fix]
    print("(a) BACKLOG (entry_ts <  %s): %s" % (fix, wl_line(back)))
    print("(b) FORWARD (entry_ts >= %s): %s" % (fix, wl_line(fwd)))
    # entry_ts weekly histogram
    bywk = Counter()
    for r in rows:
        ts = r["entry_ts"] or ""
        bywk[ts[:7]] += 1  # by month
    print("   entry_ts by month: " + ", ".join("%s:%d" % (m, bywk[m]) for m in sorted(bywk)))
    return back, fwd

arb_back, arb_fwd = split_report(arb, FIX_ARB, ARB_DIV)
llm_back, llm_fwd = split_report(llm, EPOCH_LLM, LLM_DIV)

# ---------------- LLM FORWARD: OPTION A vs B ----------------
hr("LLM_FORWARD_OPTION_A_PER_EMISSION")
print("Option A (every resolved emission counted): " + wl_line(llm_fwd))

hr("LLM_FORWARD_OPTION_B_DISTINCT_MARKET (canonical=MIN(entry_ts) per ticker)")
bym = defaultdict(list)
for r in llm_fwd:
    bym[r["ticker"]].append(r)
canon = []
for tk, rs in bym.items():
    rs2 = sorted(rs, key=lambda x: (x["entry_ts"] or ""))
    c = rs2[0]  # first emission = canonical
    canon.append(c)
print("distinct markets=%d  (from %d emissions; re-emit factor=%.1fx)"
      % (len(canon), len(llm_fwd), (len(llm_fwd) / len(canon)) if canon else 0))
print("Option B (distinct markets, canonical): " + wl_line(canon))

hr("LLM_FORWARD_OPTION_B_ECON_VS_ELECTIONS")
econ = [c for c in canon if classify_half(c["ticker"], c["category"]) == "Economics"]
elec = [c for c in canon if classify_half(c["ticker"], c["category"]) == "Elections"]
print("Economics: " + wl_line(econ))
print("Elections: " + wl_line(elec))

hr("LLM_FORWARD_DISTINCT_MARKET_TABLE (canonical row per market)")
print("half | ticker | category | first_entry | outcome | result | won | div%% | llm_p | impl | qty | px | gross | fee | net")
for c in sorted(canon, key=lambda x: (x["entry_ts"] or "")):
    f = fee(c["qty"], c["entry_price"])
    g = float(c["realized_pnl"] or 0)
    print("%s | %s | %s | %s | %s | %s | %d | %s | %s | %s | %s | %s | %.2f | %.2f | %.2f" % (
        classify_half(c["ticker"], c["category"]), c["ticker"], c["category"],
        (c["entry_ts"] or "")[:16], c["outcome_bet"], c["market_result"], c["won"] or 0,
        ("%.1f" % c["divergence_pct"]) if c["divergence_pct"] is not None else "?",
        ("%.3f" % c["llm_prob"]) if c["llm_prob"] is not None else "?",
        ("%.3f" % c["implied_at_entry"]) if c["implied_at_entry"] is not None else "?",
        c["qty"], c["entry_price"], g, f, g - f))

hr("LLM_INVERSION_TEST (distinct markets: WR by divergence bucket)")
def bucket(d):
    if d is None:
        return "unknown"
    d = abs(float(d))
    if d >= 40: return "40+%"
    if d >= 25: return "25-40%"
    if d >= 11: return "11-25%"
    return "<11%"
for half, group in (("ALL", canon), ("Economics", econ), ("Elections", elec)):
    print("[%s]" % half)
    b = defaultdict(list)
    for c in group:
        b[bucket(c["divergence_pct"])].append(c)
    for k in ["40+%", "25-40%", "11-25%", "<11%", "unknown"]:
        if b[k]:
            print("   div %-8s %s" % (k, wl_line(b[k])))

# ---------------- ARB FORWARD DETAIL ----------------
hr("ARB_FORWARD_DETAIL (entry_ts >= %s)" % FIX_ARB)
if not arb_fwd:
    print("NO forward arbitrage round-trips (entered AND resolved post-fix). Still 100%% backlog.")
else:
    print("ticker | event | arb_type | set_id | outcome | result | won | qty | px | gross | fee | net | entry_ts | resolved_ts")
    for r in sorted(arb_fwd, key=lambda x: (x["entry_ts"] or "")):
        f = fee(r["qty"], r["entry_price"])
        g = float(r["realized_pnl"] or 0)
        print("%s | %s | %s | %s | %s | %s | %d | %s | %s | %.2f | %.2f | %.2f | %s | %s" % (
            r["ticker"], (r["event_ticker"] or r["event_title"] or "")[:24], r["arb_type"],
            (r["arb_set_id"] or "")[:14], r["outcome_bet"], r["market_result"], r["won"] or 0,
            r["qty"], r["entry_price"], g, f, g - f,
            (r["entry_ts"] or "")[:16], (r["resolved_ts"] or "")[:16]))

# ---------------- CONCENTRATION ----------------
def concentration(rows, keyfn, label, topn=8):
    hr("CONCENTRATION %s (net-of-fee by %s)" % (label, keyfn.__name__))
    agg = defaultdict(lambda: [0, 0.0])  # n, net
    for r in rows:
        k = keyfn(r) or "?"
        agg[k][0] += 1
        agg[k][1] += float(r["realized_pnl"] or 0) - fee(r["qty"], r["entry_price"])
    total = sum(v[1] for v in agg.values())
    print("total net=$%.2f across %d groups" % (total, len(agg)))
    ordered = sorted(agg.items(), key=lambda kv: -abs(kv[1][1]))
    for k, v in ordered[:topn]:
        share = (100.0 * v[1] / total) if total else 0.0
        print("  %-40s n=%-4d net=$%.2f  share=%.1f%%" % (str(k)[:40], v[0], v[1], share))
    top3 = sum(v[1] for _, v in ordered[:3])
    print("  >>> top-3 groups net=$%.2f (%.1f%% of total net)"
          % (top3, (100.0 * top3 / total) if total else 0.0))

concentration(arb, lambda r: r["event_ticker"] or r["arb_set_id"], "ARB(all)")
if arb_fwd:
    concentration(arb_fwd, lambda r: r["event_ticker"] or r["arb_set_id"], "ARB(forward)")
concentration(llm_fwd, lambda r: r["ticker"], "LLM(forward per-emission)")
concentration(canon, lambda r: r["ticker"], "LLM(forward distinct)")

# ---------------- KAREN EQUITY ----------------
hr("EQUITY_HISTORY (kalshi_arbitrage / Karen)")
try:
    cur = con.execute("SELECT * FROM kalshi_equity_history WHERE division=? ORDER BY ts DESC LIMIT 5", (ARB_DIV,))
    r0 = None
    for r in cur.fetchall():
        r0 = r
        print("  ", dict(r))
    if r0 is None:
        print("  <no rows for division kalshi_arbitrage>")
except Exception as e:
    print("  <equity table err: %s>" % e)

hr("PROBE_DONE")
