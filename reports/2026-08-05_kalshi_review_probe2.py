#!/usr/bin/env python3
# READ-ONLY follow-up probe. (1) reconcile LLM distinct-market count vs operator verdict
# (2) determine arb scanner: dormant vs erroring (post-2026-08-04 restart liveness).
import sqlite3, math, os, sys, subprocess
from collections import defaultdict

DB = next((p for p in [
    "/home/azureuser/trading_corp/data/trading_corp.db",
    os.path.expanduser("~/trading_corp/data/trading_corp.db"),
    "trading_corp/data/trading_corp.db"] if os.path.exists(p)), None)
EPOCH_LLM = "2026-07-07T16:40"
LLM_DIV = "kalshi_llm_arbitrage"
ARB_ACTORS = ("kalshi_tail_price_arb", "kalshi_temporal_bucket_arb")


def fee(c, p):
    if p is None or c is None or float(c) <= 0:
        return 0.0
    p = max(0.0, min(1.0, float(p)))
    return math.ceil(0.07 * float(c) * p * (1.0 - p) * 100.0) / 100.0


def run(cmd, timeout=40):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return ((r.stdout or "") + (r.stderr or ""))
    except Exception as e:
        return "<err %r>" % (e,)


def hr(t):
    print("\n===== " + t + " =====")


con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
con.execute("PRAGMA query_only=ON")

print("utc_now:", run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"]).strip())
print("DB:", DB, "size_MB", round(os.path.getsize(DB) / 1e6, 2))

# ---------- (1) LLM DISTINCT-MARKET RECONCILIATION ----------
cols = ("ticker,category,outcome_bet,market_result,won,qty,entry_price,realized_pnl,"
        "entry_ts,resolved_ts,divergence_pct,llm_prob")
llm = con.execute(
    "SELECT %s FROM kalshi_round_trips WHERE division=? AND entry_ts>=?" % cols,
    (LLM_DIV, EPOCH_LLM)).fetchall()

hr("LLM_FORWARD_NOW")
print("emissions(now)=%d" % len(llm))
if llm:
    print("entry_ts range:", min(r["entry_ts"] for r in llm), "..", max(r["entry_ts"] for r in llm))
    print("resolved_ts max(now):", max((r["resolved_ts"] or "") for r in llm))

bym = defaultdict(list)
for r in llm:
    bym[r["ticker"]].append(r)

# canonical = first emission
canon_w = canon_l = 0
canon_net = 0.0
# sum-all-emissions method
sum_w = sum_l = 0
sum_net_total = 0.0
mixed = []
hr("LLM_PER_TICKER (n_emis | won_set | firstWon | first_entry | sumGross | sumFee | sumNet | canonNet)")
for tk in sorted(bym, key=lambda k: min(x["entry_ts"] for x in bym[k])):
    rs = sorted(bym[tk], key=lambda x: (x["entry_ts"] or ""))
    won_set = sorted(set(int(x["won"]) for x in rs))
    if len(won_set) > 1:
        mixed.append(tk)
    first = rs[0]
    cnet = float(first["realized_pnl"] or 0) - fee(first["qty"], first["entry_price"])
    sg = sum(float(x["realized_pnl"] or 0) for x in rs)
    sf = sum(fee(x["qty"], x["entry_price"]) for x in rs)
    sn = sg - sf
    canon_net += cnet
    if int(first["won"]):
        canon_w += 1
    else:
        canon_l += 1
    if sn > 0:
        sum_w += 1
    else:
        sum_l += 1
    sum_net_total += sn
    print("%-32s | n=%-3d | won=%s | fw=%d | %s | %.2f | %.2f | %.2f | %.2f" % (
        tk, len(rs), won_set, int(first["won"]), (first["entry_ts"] or "")[:16], sg, sf, sn, cnet))

hr("LLM_DISTINCT_METHOD_COMPARISON")
print("distinct markets = %d  (emissions=%d)" % (len(bym), len(llm)))
print("Method A  canonical=first-emission won:   W=%d L=%d  net=$%.2f" % (canon_w, canon_l, canon_net))
print("Method B  sum-all-emissions, W if net>0:  W=%d L=%d  net=$%.2f" % (sum_w, sum_l, sum_net_total))
print("mixed-won tickers (bet flipped / re-resolved):", mixed or "NONE")

# ---------- (2) ARB SCANNER LIVENESS ----------
hr("ARB_SCANNER_LIVENESS")
# last emission per arb actor
for a in ARB_ACTORS:
    row = con.execute(
        "SELECT COUNT(*) n, MAX(ts) mx FROM audit_event WHERE actor=? "
        "AND kind IN ('would_have_placed','kalshi_copy_placed_live')", (a,)).fetchone()
    # any post-restart (2026-08-04) emissions?
    post = con.execute(
        "SELECT COUNT(*) n FROM audit_event WHERE actor=? AND ts>='2026-08-04' "
        "AND kind IN ('would_have_placed','kalshi_copy_placed_live')", (a,)).fetchone()["n"]
    print("actor=%-28s total_emis=%s last_emis=%s post_0804_emis=%d"
          % (a, row["n"], row["mx"], post))

# any audit_event AT ALL from arb actors since restart (incl non-placement kinds)?
hr("ARB_AUDIT_KINDS_SINCE_0804")
for r in con.execute(
        "SELECT actor, kind, COUNT(*) n, MAX(ts) mx FROM audit_event "
        "WHERE actor IN (?,?) AND ts>='2026-08-04' GROUP BY actor, kind ORDER BY n DESC",
        ARB_ACTORS).fetchall():
    print("  %-28s %-24s n=%d last=%s" % (r["actor"], r["kind"], r["n"], r["mx"]))
print("(empty above = arb actors wrote NOTHING to audit since the 08-04 restart)")

# agent_state for arb / kalshi scanners
hr("AGENT_STATE (kalshi arb-related keys)")
try:
    for r in con.execute(
            "SELECT * FROM agent_state WHERE key LIKE '%temporal%' OR key LIKE '%tail_price%' "
            "OR key LIKE '%kalshi_arb%' OR key LIKE '%kalshi_temporal%' LIMIT 40").fetchall():
        print("  ", dict(r))
except Exception as e:
    print("  agent_state schema:", e)
    try:
        cols2 = [d[1] for d in con.execute("PRAGMA table_info(agent_state)").fetchall()]
        print("  columns:", cols2)
    except Exception as e2:
        print("  ", e2)

# journal: scan activity + errors for arb since restart
hr("JOURNAL_ARB_SCAN_SINCE_0804 (temporal / tail_price / kalshi arb scan lines + errors)")
jc = run(["journalctl", "-u", "trading-corp", "--since", "2026-08-04 00:00", "--no-pager"], timeout=60)
if jc.startswith("<err") or not jc or "No journal" in jc[:60]:
    print("  <journal unavailable to azureuser>")
else:
    lines = jc.splitlines()
    scan = [ln for ln in lines if any(k in ln.lower() for k in
            ("temporal_bucket", "tail_price", "kalshi_temporal", "kalshi_tail", "kalshi_arbitrage"))]
    errs = [ln for ln in lines if any(k in ln.lower() for k in ("traceback", "exception"))
            and any(k in ln.lower() for k in ("temporal", "tail", "arb"))]
    print("total journal lines since 08-04:", len(lines))
    print("arb-scanner-related lines:", len(scan))
    for ln in scan[-25:]:
        print("   ", ln[-220:])
    print("arb-related traceback/exception lines:", len(errs))
    for ln in errs[-15:]:
        print("   ", ln[-220:])
    # generic scan-loop heartbeat presence (any kalshi scan cadence at all)
    kalshi_scan = [ln for ln in lines if "kalshi" in ln.lower()
                   and ("scan" in ln.lower() or "tick" in ln.lower())]
    print("any-kalshi scan/tick lines since 08-04:", len(kalshi_scan))
    for ln in kalshi_scan[-6:]:
        print("   ", ln[-200:])

hr("PROBE2_DONE")
