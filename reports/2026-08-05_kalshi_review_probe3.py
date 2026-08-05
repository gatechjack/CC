#!/usr/bin/env python3
# READ-ONLY audit: kalshi_llm dashboard read-view vs raw table.
# Dumps LIVE data.py read-view source + cutoffs, then runs the actual
# read-view SQL against the live DB and diffs per-market vs raw canonical.
import sqlite3, math, os, re
from collections import defaultdict

DB = "/home/azureuser/trading_corp/data/trading_corp.db"
DATA_PY = next((p for p in [
    "/home/azureuser/trading_corp/trading_corp/web/data.py",
    os.path.expanduser("~/trading_corp/trading_corp/web/data.py")] if os.path.exists(p)), None)
LLM = "kalshi_llm_arbitrage"
B10_CUT = {"kalshi_llm_arbitrage": "2026-07-07T16:40:00+00:00"}


def hr(t):
    print("\n===== " + t + " =====")


def fee(c, p):
    if p is None or c is None or float(c) <= 0:
        return 0.0
    p = max(0.0, min(1.0, float(p)))
    return math.ceil(0.07 * float(c) * p * (1.0 - p) * 100.0) / 100.0


# ---------- LIVE SOURCE ----------
hr("LIVE_DATA_PY")
print("path:", DATA_PY)
src = open(DATA_PY, encoding="utf-8", errors="replace").read() if DATA_PY else ""
print("bytes:", len(src), " has_query_fn:", "_query_kalshi_distinct_market_stats" in src)

hr("LIVE_DASHBOARD_RT_CUTOFFS (parsed)")
mblk = re.search(r"DASHBOARD_RT_CUTOFFS[^\{]*\{(.*?)\n\}", src, re.S)
cuts = {}
if mblk:
    cuts = dict(re.findall(r'"(kalshi_[a-z_]+)"\s*:\s*"([^"]+)"', mblk.group(1)))
for k, v in cuts.items():
    print("  %-24s %s" % (k, v))
LIVE_CUT = {LLM: cuts[LLM]} if LLM in cuts else {}
print("LIVE llm cutoff:", cuts.get(LLM, "<ABSENT>"))
print("b10a010 llm cutoff:", B10_CUT[LLM])
print("cutoffs MATCH b10a010:", cuts.get(LLM) == B10_CUT[LLM])

hr("LIVE _query_kalshi_distinct_market_stats SOURCE")
if "_query_kalshi_distinct_market_stats" in src:
    i = src.find("def _query_kalshi_distinct_market_stats")
    j = src.find("\ndef ", i + 10)
    print(src[i:j if j > 0 else i + 2800])
else:
    print("  <NOT PRESENT in live data.py>")

hr("LIVE CALL SITES (non-def)")
lines = src.splitlines()
for idx, ln in enumerate(lines, 1):
    if "_query_kalshi_distinct_market_stats" in ln and "def " not in ln:
        print("  L%d: %s" % (idx, ln.strip()[:170]))
        # show 4 lines of context to see the division_slugs argument
        for k in range(max(0, idx - 4), min(len(lines), idx + 2)):
            print("        %d| %s" % (k + 1, lines[k].strip()[:150]))

# ---------- DB REPLICATION ----------
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
con.execute("PRAGMA query_only=ON")


def readview(divs, cutoffs, label):
    ph = ",".join("?" for _ in divs)
    nc = "".join(" AND NOT (division='%s' AND entry_ts < '%s')" % (d, c) for d, c in cutoffs.items())
    ranked = ("WITH ranked AS (SELECT ticker,won,market_result,realized_pnl,qty,entry_price,id,entry_ts,"
              "ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY entry_ts ASC, id ASC) rn "
              "FROM kalshi_round_trips WHERE division IN (%s)%s) " % (ph, nc))
    a = con.execute(ranked + "SELECT COUNT(*) n, COALESCE(SUM(won),0) w, "
                    "COALESCE(SUM(CASE WHEN COALESCE(market_result,'')='void' THEN 1 ELSE 0 END),0) v, "
                    "COALESCE(SUM(realized_pnl),0.0) pnl FROM ranked WHERE rn=1", tuple(divs)).fetchone()
    rows = con.execute(ranked + "SELECT ticker,won,market_result,realized_pnl,qty,entry_price,entry_ts "
                       "FROM ranked WHERE rn=1 ORDER BY entry_ts", tuple(divs)).fetchall()
    losses = a["n"] - a["w"] - a["v"]
    print("[%s]  n=%d  W=%d L=%d void=%d  total_pnl(GROSS)=$%.2f" % (label, a["n"], a["w"], losses, a["v"], a["pnl"]))
    return rows


hr("READVIEW SCENARIOS (canonical rn=1, gross SUM like the dashboard)")
r_live = readview([LLM], LIVE_CUT, "A: llm, LIVE cutoff")
r_b10 = readview([LLM], B10_CUT, "B: llm, b10a010 cutoff 16:40")
r_none = readview([LLM], {}, "C: llm, NO cutoff (all-time)")
# hypothesis: caller passes the whole kalshi division set
all_divs = [r["division"] for r in con.execute(
    "SELECT DISTINCT division FROM kalshi_round_trips WHERE division LIKE 'kalshi_%'").fetchall()]
readview(all_divs, cuts, "D: ALL kalshi divs, LIVE cutoffs")

hr("PER-MARKET (scenario A = live read-view) with fee-adjusted net")
gtot = ntot = 0.0
for c in r_live:
    f = fee(c["qty"], c["entry_price"])
    g = float(c["realized_pnl"] or 0)
    gtot += g
    ntot += g - f
    print("  %-32s won=%d res=%-4s gross=%6.2f fee=%.2f net=%6.2f  %s" % (
        c["ticker"], c["won"] or 0, c["market_result"], g, f, g - f, (c["entry_ts"] or "")[:16]))
print("  TOTALS scenario A: gross=$%.2f  net(after fee)=$%.2f  n=%d" % (gtot, ntot, len(r_live)))

hr("RAW GROUND-TRUTH (my probe method: division=llm, entry_ts>=16:40)")
raw = con.execute("SELECT ticker,won,market_result,realized_pnl,qty,entry_price,entry_ts FROM kalshi_round_trips "
                  "WHERE division=? AND entry_ts>=? ", (LLM, "2026-07-07T16:40")).fetchall()
bym = defaultdict(list)
for r in raw:
    bym[r["ticker"]].append(r)
canon = [sorted(v, key=lambda x: (x["entry_ts"] or ""))[0] for v in bym.values()]
w = sum(1 for c in canon if c["won"])
g = sum(float(c["realized_pnl"] or 0) for c in canon)
n = sum(g2 for g2 in [sum(float(c["realized_pnl"] or 0) - fee(c["qty"], c["entry_price"]) for c in canon)])
print("  distinct=%d  W=%d L=%d  gross=$%.2f  net(after fee)=$%.2f" % (len(canon), w, len(canon) - w, g, n))

hr("DIFF: tickers in A-not-raw and raw-not-A")
sa = {c["ticker"] for c in r_live}
sr = {c["ticker"] for c in canon}
print("  in read-view A but NOT raw:", sorted(sa - sr) or "none")
print("  in raw but NOT read-view A:", sorted(sr - sa) or "none")
print("  |A|=%d |raw|=%d shared=%d" % (len(sa), len(sr), len(sa & sr)))

hr("PROBE3_DONE")
