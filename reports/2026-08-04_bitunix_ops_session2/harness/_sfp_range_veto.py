#!/usr/bin/env python3
# TASK 3 -- RANGE-ONLY VETO on up_but_bearish SHORTS (current live SFP construct). Backtest-only;
# NO live-logic change. Enumerates the construct's SHORT trades, labels each with the existing
# regime classifiers (macro60 bull/bear/neutral; LuxAlgo range-detector rd_os in {-1,0,+1} at 1h;
# 15m regime label up/down/range), applies two computable veto definitions, and measures how many
# shorts each vetoes, the vetoed set's R distribution, and the net book effect (gross + net-of-fee).
#
# up_but_bearish = a SHORT taken while the higher context is UP (price above a trend baseline) but the
# setup is bearish. Veto it UNLESS the range detector says we are genuinely ranging.
#   DEF A (rd-only):  veto a short iff rd_os(1h)==+1  (range detector in a confirmed UP-break/uptrend;
#                     shorting into it = up_but_bearish. rd_os==0 range fades and rd_os==-1 downtrend
#                     shorts are KEPT).
#   DEF B (macro x rd): veto a short iff macro60=='bull' AND rd_os != 0  (slow macro is up and we are
#                     not in a confirmed range = counter-trend short into a bull macro; keep bull-macro
#                     shorts only when rd_os==0).
# Longs are never vetoed. GROSS + NET(fee), stop-first 3m replay, in-sample Binance proxy. EVIDENCE.
import os, sys, sqlite3, datetime
from collections import defaultdict
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
HERE = os.path.dirname(os.path.abspath(__file__))
SFP_DIR = r"C:\Users\AA Incorporado\cc\trading_corp\agents\strategies"
for p in (SFP_DIR, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import _sfp_degree_rerun as DR
import _inst_levels as IL
import _sfp_head_to_head as H2H
import _sfp_p1c_htf as P1C
from _sfp_trend_gate_bakeoff import rd_os_lookup_builder
from bisect import bisect_right

DB = P1C.DB
REPORT = os.path.join(HERE, "SFP_RANGE_VETO.txt")
COINS = DR.COINS
TF_MS = P1C.TF_MS
YEAR_MS = P1C.YEAR_MS
TP_R = 3.0
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001


def net_of_fee(gross, entry, r_unit):
    exit_fee = MK if gross >= TP_R - 1e-9 else TK        # tp maker else taker (stop/timeout)
    return gross - (ENTRY_FEE + exit_fee + SLIP2) * entry / r_unit


def regime_label_at(labels, cts, ts):
    i = bisect_right(cts, ts) - 1
    return labels[i] if 0 <= i < len(labels) else None


def stats(rs):
    n = len(rs)
    if not n:
        return (0, 0.0, 0.0, 0.0)
    s = sum(rs); w = sum(1 for r in rs if r > 1e-9)
    return (n, s, 100.0 * w / n, s / n)


def month_key(ts):
    d = datetime.datetime.utcfromtimestamp(ts / 1000.0)
    return (d.year, d.month)


def main():
    L = []; W = L.append
    def hr(c="="): W(c * 100)
    con = sqlite3.connect(DB)
    m60 = {(s, d): r for s, d, r in con.execute("SELECT symbol,day_ts_ms,regime FROM macro_regime_60d")}
    shorts = []          # dicts: coin, macro60, rd_os, reg15, g, net, entry_ts
    longs_g = []; longs_n = []
    span_lo = span_hi = None
    for coin in COINS:
        b3 = DR.load_bars(con, coin, "3m"); b1h = DR.load_bars(con, coin, "1h")
        b1d = DR.load_bars(con, coin, "1d"); b15 = DR.load_bars(con, coin, "15m")
        labels, cts = DR.precompute_regime(b15); il = IL.InstLevels(coin, b15, b1d)
        rd_lookup = rd_os_lookup_builder(b1h)
        lo, hi = b3[0].ts_ms, b3[-1].ts_ms
        span_lo = lo if span_lo is None else min(span_lo, lo)
        span_hi = hi if span_hi is None else max(span_hi, hi)
        cands, _ = P1C.fresh_wt_candidates(coin, b1h, TF_MS["1h"], b3, labels, cts, il, m60)
        booked, _ = P1C.book_and_pool(cands, b3)
        for d in booked:
            g = d["R"]; nt = net_of_fee(g, d["entry"], d["r_unit"])
            if d["side"] == "short":
                shorts.append({"coin": coin, "macro60": d["macro60"], "rd_os": rd_lookup(d["entry_ts"]),
                               "reg15": regime_label_at(labels, cts, d["entry_ts"]),
                               "g": g, "net": nt, "entry_ts": d["entry_ts"]})
            else:
                longs_g.append(g); longs_n.append(nt)
        del b3, b1h, b1d, b15, labels, cts, il
    con.close()
    years = (span_hi - span_lo) / YEAR_MS

    def veto_A(s):
        return s["rd_os"] == 1
    def veto_B(s):
        return s["macro60"] == "bull" and s["rd_os"] != 0

    hr(); W("TASK 3 -- RANGE-ONLY VETO on up_but_bearish SHORTS -- current live SFP construct, backtest-only")
    W("Longs untouched. NET = SFP fee model. %.2f yr, in-sample Binance proxy. EVIDENCE not closure." % years); hr()

    nS = len(shorts)
    W(""); W("### 0. SHORT-BOOK baseline (no veto)")
    n, s, wr, av = stats([x["g"] for x in shorts]); _, sn, wrn, avn = stats([x["net"] for x in shorts])
    W("  shorts n=%d  grossAvgR=%+.3f WR=%.1f%%  netAvgR=%+.3f  net R/yr=%+.2f" % (n, av, wr, avn, sn / years))
    nl, sl, wl, avl = stats(longs_g); _, sln, _, avln = stats(longs_n)
    W("  (longs n=%d  grossAvgR=%+.3f  netAvgR=%+.3f  -- shown for whole-book context, never vetoed)" % (nl, avl, avln))
    # rd_os / macro60 composition of shorts
    W(""); W("### 0b. SHORT composition by classifier")
    comp = defaultdict(int)
    for x in shorts:
        comp[("rd_os", x["rd_os"])] += 1; comp[("macro60", x["macro60"])] += 1; comp[("reg15", x["reg15"])] += 1
    for key in ("rd_os", "macro60", "reg15"):
        parts = ["%s=%d" % (k[1], v) for k, v in sorted(comp.items(), key=lambda kv: str(kv[0])) if k[0] == key]
        W("  %-8s: %s" % (key, "  ".join(parts)))

    def evaluate(name, vf):
        vetoed = [x for x in shorts if vf(x)]
        kept = [x for x in shorts if not vf(x)]
        W(""); hr("#"); W("### DEF %s" % name); hr("#")
        vn, vs, vwr, vav = stats([x["g"] for x in vetoed]); _, vsn, _, vavn = stats([x["net"] for x in vetoed])
        kn, ks, kwr, kav = stats([x["g"] for x in kept]); _, ksn, _, kavn = stats([x["net"] for x in kept])
        W("  VETOED shorts: n=%d (%.0f%% of shorts)  grossAvgR=%+.3f WR=%.1f%%  netAvgR=%+.3f  (net sumR removed=%+.2f)"
          % (vn, 100.0 * vn / max(1, nS), vav, vwr, vavn, vsn))
        W("  KEPT   shorts: n=%d  grossAvgR=%+.3f WR=%.1f%%  netAvgR=%+.3f  net R/yr=%+.2f" % (kn, kav, kwr, kavn, ksn / years))
        # net effect on the SHORT book and the WHOLE book
        allS_net = sum(x["net"] for x in shorts); keptS_net = sum(x["net"] for x in kept)
        W("  SHORT-book net avgR: before %+.3f -> after-veto %+.3f  (delta %+.3f) ; net total-R %+.2f -> %+.2f"
          % (avn, kavn if kn else 0.0, (kavn if kn else 0.0) - avn, allS_net, keptS_net))
        whole_before = (allS_net + sum(longs_n)) / (nS + nl)
        whole_after = (keptS_net + sum(longs_n)) / (kn + nl) if (kn + nl) else 0.0
        W("  WHOLE-book net avgR (longs+shorts): before %+.3f -> after %+.3f  (delta %+.3f)"
          % (whole_before, whole_after, whole_after - whole_before))
        # per coin
        W("  per-coin vetoed / kept-net:")
        for coin in COINS:
            cs = [x for x in shorts if x["coin"] == coin]
            cv = [x for x in cs if vf(x)]; ck = [x for x in cs if not vf(x)]
            av_v = stats([x["net"] for x in cv])[3]; av_k = stats([x["net"] for x in ck])[3]
            W("    %-9s shorts=%3d  vetoed=%3d (vet netAvgR=%+.3f)  kept=%3d (kept netAvgR=%+.3f)"
              % (coin, len(cs), len(cv), av_v, len(ck), av_k))
        # holdout (time median split on shorts)
        if shorts:
            cut = sorted(x["entry_ts"] for x in shorts)[nS // 2]
            for lab, sub in (("IS ", [x for x in shorts if x["entry_ts"] < cut]),
                             ("OOS", [x for x in shorts if x["entry_ts"] >= cut])):
                sub_v = [x for x in sub if vf(x)]; sub_k = [x for x in sub if not vf(x)]
                bav = stats([x["net"] for x in sub])[3]; kavh = stats([x["net"] for x in sub_k])[3]
                W("  %s shorts n=%d: vetoed=%d  short-book net %+.3f -> %+.3f (delta %+.3f)"
                  % (lab, len(sub), len(sub_v), bav, kavh if sub_k else 0.0, (kavh if sub_k else 0.0) - bav))
        return {"vetoed": vetoed, "kept": kept, "kept_avn": kavn, "removed_avn": vavn, "vn": vn}

    ra = evaluate("A (veto rd_os==+1)", veto_A)
    rb = evaluate("B (veto macro60==bull AND rd_os!=0)", veto_B)

    # recommendation logic
    W(""); hr("#"); W("### RECOMMENDATION"); hr("#")
    base = stats([x["net"] for x in shorts])[3]
    for name, r in (("A", ra), ("B", rb)):
        lift = (r["kept_avn"] - base) if r["kept"] else 0.0
        good = (r["removed_avn"] < 0) and (lift > 0) and (r["vn"] >= 10)
        W("  DEF %s: removes n=%d at netAvgR %+.3f ; short-book netAvgR lift %+.3f ; %s"
          % (name, r["vn"], r["removed_avn"], lift, "removes a losing subset -> candidate" if good else "weak/insufficient"))
    W("  (A def wins if it removes a clearly-negative, adequately-sized short subset and lifts the kept")
    W("   short-book net avgR, surviving the holdout. Longs are unaffected. Only the operator rules in.)")
    hr()
    open(REPORT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L)); print("\n[report -> %s]" % REPORT)


if __name__ == "__main__":
    main()
