#!/usr/bin/env python3
# TASK 2b -- PRE-TARGET TRAIL SWEEP on the CURRENT LIVE SFP construct.
# Entries = two-candle SFP + fresh-inst + with-trend, 1h detect, 4 coins (identical to the deployed
# construct; parity-gated against 627/+0.182 gross). ONLY exit/stop-management changes.
# Sweeps: activation in {none, +1R, +1.5R, +2R} x ATR-multiple trail-distance {wide..tight}.
# Adds vs the prior betrail study: (a) the full grid, (b) the SFP FEE model (net-R), (c) per coin x
# regime (macro60), (d) time holdout split, (e) clustered SE on the net dAvgR (block bootstrap by
# coin x calendar-month). NULL H0 = the 2026-06-26 tight-stop finding: early stop-ratchet destroys
# the BOS wide-stop edge. PATH-DEPENDENT 3m replay, stop-first (honest/worst-case). Box, in-sample
# Binance proxy. EVIDENCE not closure -- only the operator rules out.
import os, sys, sqlite3, random, datetime
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
from _sfp_betrail_exit import walk_betrail   # the already-run R-ladder reference (+1R->BE, +2R->lock1R)

DB = P1C.DB
REPORT = os.path.join(HERE, "SFP_PRETRAIL_SWEEP.txt")
COINS = DR.COINS
TF_MS = P1C.TF_MS
YEAR_MS = P1C.YEAR_MS
MAXH = DR.MAX_HOLD_MS
EXP = {"BTCUSDT": (149, 0.085), "ETHUSDT": (139, 0.397), "SOLUSDT": (170, 0.073),
       "XRPUSDT": (169, 0.199), "POOLED": (627, 0.182)}

# --- SFP fee model (build spec): entry taker; exit maker if the 3R TP fills, taker if stop/timeout.
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001
def net_of_fee(gross, exit_type, entry, r_unit):
    exit_fee = MK if exit_type == "tp" else TK
    fee_frac = ENTRY_FEE + exit_fee + SLIP2
    return gross - fee_frac * entry / r_unit          # fee expressed in R = fee_frac * entry/r_unit

# --- the sweep grid ---
ACTIVATIONS = [("none", 0.0), ("+1R", 1.0), ("+1.5R", 1.5), ("+2R", 2.0)]
ATR_MULTS = [3.0, 2.5, 2.0, 1.5, 1.0, 0.5]           # wide -> tight
TP_R = 3.0


def classify_flat(R):
    return "tp" if R >= TP_R - 1e-9 else "taker"      # -1R stop / fractional timeout|eod are taker


def walk_trail(b3, i0, entry, stop, r_unit, entry_ts, side, atr, activation_r, atr_mult):
    """Monotonic pre-target ATR trail. NO look-ahead: exit is checked against the stop as set through
    the PRIOR bar; the extreme/trail then update from THIS bar for the next. Stop-first on ties.
    Returns (grossR, exit_type in {tp,taker}, exit_ts, atr_missing:bool)."""
    tp = entry + TP_R * r_unit if side == "long" else entry - TP_R * r_unit
    def rlvl(p):
        return (p - entry) / r_unit if side == "long" else (entry - p) / r_unit
    if atr is None or atr <= 0:                        # no ATR -> flat-3R fallback (counted, reported)
        R, ts = H2H.walk_r(b3, i0, entry, stop, r_unit, entry_ts, side, TP_R)
        return R, classify_flat(R), ts, True
    trail_dist = atr_mult * atr
    cap = tp - 1e-9 * entry if side == "long" else tp + 1e-9 * entry   # keep the trail strictly < TP
    cur = stop
    extreme = entry
    armed = (activation_r <= 0.0)
    for i in range(i0, len(b3)):
        bar = b3[i]
        # exit checks first, against the stop established through the previous bar (stop-first)
        if side == "long":
            if bar.low <= cur:
                return rlvl(cur), "taker", bar.ts_ms, False
            if bar.high >= tp:
                return TP_R, "tp", bar.ts_ms, False
        else:
            if bar.high >= cur:
                return rlvl(cur), "taker", bar.ts_ms, False
            if bar.low <= tp:
                return TP_R, "tp", bar.ts_ms, False
        if bar.ts_ms - entry_ts >= MAXH:
            return rlvl(bar.close), "taker", bar.ts_ms, False
        # now update extreme + ratchet the stop from THIS bar (applies to the NEXT bar)
        if side == "long":
            if bar.high > extreme:
                extreme = bar.high
            if not armed and (extreme - entry) / r_unit >= activation_r:
                armed = True
            if armed:
                nt = min(extreme - trail_dist, cap)
                if nt > cur:
                    cur = nt
        else:
            if bar.low < extreme:
                extreme = bar.low
            if not armed and (entry - extreme) / r_unit >= activation_r:
                armed = True
            if armed:
                nt = max(extreme + trail_dist, cap)
                if nt < cur:
                    cur = nt
    return rlvl(b3[-1].close), "taker", b3[-1].ts_ms, False


def agg_net(rs):
    n = len(rs)
    if not n:
        return 0, 0.0, 0.0, 0.0
    s = sum(rs); w = sum(1 for r in rs if r > 1e-9)
    return n, s, 100.0 * w / n, s / n


def month_key(ts_ms):
    d = datetime.datetime.utcfromtimestamp(ts_ms / 1000.0)
    return (d.year, d.month)


def clustered_se(deltas, clusters, iters=1000, seed=4242):
    """Block bootstrap of the pooled mean delta, resampling whole clusters (coin x month) with
    replacement. deltas[i] pairs with clusters[i]. Returns (mean, se)."""
    by = defaultdict(list)
    for dv, ck in zip(deltas, clusters):
        by[ck].append(dv)
    keys = list(by.keys())
    mean = sum(deltas) / len(deltas) if deltas else 0.0
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        pool = []
        for _ in range(len(keys)):
            pool.extend(by[keys[rng.randrange(len(keys))]])
        if pool:
            means.append(sum(pool) / len(pool))
    if len(means) < 2:
        return mean, 0.0
    mu = sum(means) / len(means)
    var = sum((x - mu) ** 2 for x in means) / (len(means) - 1)
    return mean, var ** 0.5


def main():
    L = []; W = L.append
    def hr(c="="): W(c * 100)
    con = sqlite3.connect(DB)
    m60 = {(s, d): r for s, d, r in con.execute("SELECT symbol,day_ts_ms,regime FROM macro_regime_60d")}

    # per-trade records: coin, side, macro60, entry_ts, entry, r_unit, flat_gross, flat_net,
    #   and per-config gross/net/exit; plus opp pools for the (gross) drift null.
    trades = []
    opp_flat = defaultdict(list)
    span_lo = span_hi = None
    atr_missing = 0
    grid = [("none/flat", None, None)]  # placeholder removed below; real grid built inline
    for coin in COINS:
        b3 = DR.load_bars(con, coin, "3m"); b1h = DR.load_bars(con, coin, "1h")
        b1d = DR.load_bars(con, coin, "1d"); b15 = DR.load_bars(con, coin, "15m")
        labels, cts = DR.precompute_regime(b15); il = IL.InstLevels(coin, b15, b1d)
        lo, hi = b3[0].ts_ms, b3[-1].ts_ms
        span_lo = lo if span_lo is None else min(span_lo, lo)
        span_hi = hi if span_hi is None else max(span_hi, hi)
        cands, _ = P1C.fresh_wt_candidates(coin, b1h, TF_MS["1h"], b3, labels, cts, il, m60)
        booked, _ = P1C.book_and_pool(cands, b3)
        for c in cands:
            fR, _ = H2H.walk_r(b3, c["ei"], c["entry"], c["stop"], c["r_unit"], c["entry_ts"], c["side"], TP_R)
            opp_flat[(c["side"], c["macro60"])].append(fR)
        for d in booked:
            atr = il.atr_at(d["entry_ts"])
            fg = d["R"]                                   # flat-3R gross (parity source)
            fet = classify_flat(fg)
            rec = {"coin": coin, "side": d["side"], "macro60": d["macro60"],
                   "entry_ts": d["entry_ts"], "entry": d["entry"], "r_unit": d["r_unit"],
                   "flat_g": fg, "flat_n": net_of_fee(fg, fet, d["entry"], d["r_unit"]),
                   "cfg": {}}
            # ATR-trail grid
            for aname, av in ACTIVATIONS:
                for mult in ATR_MULTS:
                    g, et, _, miss = walk_trail(b3, d["ei"], d["entry"], d["stop"], d["r_unit"],
                                                d["entry_ts"], d["side"], atr, av, mult)
                    if miss:
                        atr_missing += 1
                    rec["cfg"][(aname, mult)] = (g, net_of_fee(g, et, d["entry"], d["r_unit"]))
            # R-ladder reference (the already-run betrail: +1R->BE, +2R->lock1R), stop-first
            bg, _ = walk_betrail(b3, d["ei"], d["entry"], d["stop"], d["r_unit"], d["entry_ts"], d["side"], True)
            bet = "tp" if bg >= TP_R - 1e-9 else "taker"
            rec["cfg"][("R-ladder", "BE/1R")] = (bg, net_of_fee(bg, bet, d["entry"], d["r_unit"]))
            trades.append(rec)
        del b3, b1h, b1d, b15, labels, cts, il
    con.close()
    years = (span_hi - span_lo) / YEAR_MS
    cfg_keys = [(a, m) for a, _ in ACTIVATIONS for m in ATR_MULTS] + [("R-ladder", "BE/1R")]

    hr(); W("TASK 2b -- PRE-TARGET TRAIL SWEEP vs FLAT-3R -- current live SFP construct, exit-only, 3m replay")
    W("Entries: two-candle SFP + fresh-inst + with-trend, 1h detect, 4 coins. NET of the SFP fee model")
    W("(entry taker 0.000243; exit maker 0.00014 if 3R TP else taker 0.0004; slip 0.0001). %.2f yr." % years)
    W("H0 = 2026-06-26 tight-stop finding: early stop-ratchet destroys the BOS wide-stop edge."); hr()

    # 0. SANITY -- flat-3R gross parity
    W(""); W("### 0. SANITY -- flat-3R GROSS reproduces the deployed-construct parity")
    sane = True
    for coin in COINS:
        rs = [t["flat_g"] for t in trades if t["coin"] == coin]
        n, s, wr, av = agg_net(rs); en, eav = EXP[coin]
        ok = (n == en and abs(av - eav) < 0.004); sane = sane and ok
        W("  %-9s n=%d avgR=%+.3f  expect %d/%+.3f  %s" % (coin, n, av, en, eav, "OK" if ok else "*** MISMATCH ***"))
    allg = [t["flat_g"] for t in trades]
    n, s, wr, av = agg_net(allg); en, eav = EXP["POOLED"]
    okp = (n == en and abs(av - eav) < 0.004); sane = sane and okp
    W("  %-9s n=%d avgR=%+.3f  expect %d/%+.3f  %s" % ("POOLED", n, av, en, eav, "OK" if okp else "*** MISMATCH ***"))
    if not sane:
        W(""); W("*** SANITY FAILED -- STOP. ***"); open(REPORT, "w", encoding="utf-8").write("\n".join(L) + "\n"); print("\n".join(L)); return
    W("  (ATR-missing trade-configs falling back to flat: %d)" % atr_missing)

    # flat NET baseline
    flat_n = [t["flat_n"] for t in trades]
    fn_n, fn_s, fn_wr, fn_av = agg_net(flat_n)
    W(""); W("### FLAT-3R NET baseline: n=%d  netAvgR=%+.3f  netR/yr=%+.2f  WR=%.1f%%  (gross avgR=%+.3f)"
             % (fn_n, fn_av, fn_s / years, fn_wr, av))

    # 1. FULL GRID -- pooled net avgR and delta vs flat
    W(""); W("### 1. POOLED NET avgR by (activation x ATR-mult) -- delta vs flat in parens; * = beats flat net")
    W("  activation |   " + "   ".join("%5.1fx" % m for m in ATR_MULTS))
    for aname, _ in ACTIVATIONS:
        cells = []
        for m in ATR_MULTS:
            ns = [t["cfg"][(aname, m)][1] for t in trades]
            _, _, _, avn = agg_net(ns)
            d = avn - fn_av
            star = "*" if d > 1e-9 else " "
            cells.append("%+.3f(%+.3f)%s" % (avn, d, star))
        W("  %-10s | %s" % (aname, "  ".join(cells)))
    # R-ladder reference
    rl = [t["cfg"][("R-ladder", "BE/1R")][1] for t in trades]
    _, rls, _, rlav = agg_net(rl)
    W("  R-ladder (+1R->BE,+2R->lock1R): netAvgR=%+.3f (%+.3f vs flat)  netR/yr=%+.2f" % (rlav, rlav - fn_av, rls / years))

    # 2. Rank the configs; headline = best net avgR
    ranked = []
    for k in cfg_keys:
        ns = [t["cfg"][k][1] for t in trades]
        _, s2, wr2, av2 = agg_net(ns)
        ranked.append((av2, k, s2 / years, wr2))
    ranked.sort(reverse=True)
    W(""); W("### 2. TOP-5 configs by pooled NET avgR (vs flat net %+.3f / R/yr %+.2f)" % (fn_av, fn_s / years))
    for av2, k, ry, wr2 in ranked[:5]:
        W("  %-16s netAvgR=%+.3f (%+.3f vs flat)  netR/yr=%+.2f  WR=%.1f%%" % (str(k), av2, av2 - fn_av, ry, wr2))
    best = ranked[0][1]
    W("  WORST: %-16s netAvgR=%+.3f (%+.3f vs flat)" % (str(ranked[-1][1]), ranked[-1][0], ranked[-1][0] - fn_av))

    # 3. Headline configs: per-coin x regime, clustered SE, holdout, rescue/tax
    headline = [best]
    for pref in (("none", 0.5), ("+1R", 2.0), ("+2R", 2.0)):   # a tight, a mid, a wide reference
        if pref in cfg_keys and pref not in headline:
            headline.append(pref)
    cut = sorted(t["entry_ts"] for t in trades)[len(trades) // 2]   # time holdout median split
    W(""); hr("#"); W("### 3. HEADLINE CONFIGS -- per coin x regime, clustered SE, holdout"); hr("#")
    for k in headline:
        W(""); W("--- config %s ---" % str(k))
        ns = [t["cfg"][k][1] for t in trades]
        _, s2, wr2, av2 = agg_net(ns)
        W("  POOLED netAvgR=%+.3f (%+.3f vs flat)  netR/yr=%+.2f  WR=%.1f%%" % (av2, av2 - fn_av, s2 / years, wr2))
        # clustered SE on the per-trade delta
        deltas = [t["cfg"][k][1] - t["flat_n"] for t in trades]
        clusters = [(t["coin"], month_key(t["entry_ts"])) for t in trades]
        mean_d, se_d = clustered_se(deltas, clusters)
        z = mean_d / se_d if se_d > 0 else 0.0
        W("  dAvgR vs flat = %+.4f   clustered SE(coin x month) = %.4f   z = %+.2f" % (mean_d, se_d, z))
        # holdout
        for lab, sub in (("IS ", [t for t in trades if t["entry_ts"] < cut]),
                         ("OOS", [t for t in trades if t["entry_ts"] >= cut])):
            fa = agg_net([t["flat_n"] for t in sub])[3]; ca = agg_net([t["cfg"][k][1] for t in sub])[3]
            W("  %s (n=%d): flat net %+.3f  trail net %+.3f  (delta %+.3f)" % (lab, len(sub), fa, ca, ca - fa))
        # per coin x regime (macro60)
        W("  per coin x macro60 regime (net avgR trail vs flat):")
        seen = set()
        for coin in COINS:
            for reg in ("bull", "bear", "neutral", "n/a"):
                sub = [t for t in trades if t["coin"] == coin and t["macro60"] == reg]
                if not sub:
                    continue
                fa = agg_net([t["flat_n"] for t in sub])[3]; ca = agg_net([t["cfg"][k][1] for t in sub])[3]
                W("    %-9s %-8s n=%3d  flat %+.3f  trail %+.3f  (%+.3f)" % (coin, reg, len(sub), fa, ca, ca - fa))
        # rescue/tax on GROSS (like the prior study) for interpretability
        losers = [t for t in trades if t["flat_g"] <= -0.999]
        winners = [t for t in trades if t["flat_g"] >= TP_R - 0.001]
        dResc = sum(t["cfg"][k][0] - t["flat_g"] for t in losers)
        dTax = sum(t["cfg"][k][0] - t["flat_g"] for t in winners)
        W("  gross rescue (on %d flat losers) = %+.1fR ; gross tax (on %d flat winners) = %+.1fR ; net shift = %+.1fR"
          % (len(losers), dResc, len(winners), dTax, dResc + dTax))

    # 4. drift null (GROSS) for flat + best config, continuity with the prior study
    def null_pct(rows_R_side_mac, opp):
        real = [{"R": R, "side": sd, "macro60": mc} for (R, sd, mc) in rows_R_side_mac]
        res = P1C.bootstrap_null(real, opp, seed=7777)
        return res[2] if res else None
    fnull = null_pct([(t["flat_g"], t["side"], t["macro60"]) for t in trades], opp_flat)
    bnull = null_pct([(t["cfg"][best][0], t["side"], t["macro60"]) for t in trades], opp_flat)
    W(""); W("### 4. drift null (GROSS, own opportunity set): flat pct=%s  best(%s) pct=%s  (lift != better)"
             % (("%.0f%%" % fnull) if fnull is not None else "n/a", str(best),
                ("%.0f%%" % bnull) if bnull is not None else "n/a"))

    # 5. verdict
    beat = [r for r in ranked if r[0] > fn_av + 1e-9]
    W(""); hr("#"); W("### 5. VERDICT (evidence, not closure -- operator rules out)"); hr("#")
    W("  configs beating flat on pooled NET avgR: %d of %d" % (len(beat), len(cfg_keys)))
    if beat:
        for av2, k, ry, wr2 in beat:
            W("    %-16s +%.3f net avgR, netR/yr %+.2f" % (str(k), av2 - fn_av, ry))
    else:
        W("    NONE. H0 not rejected: no trail configuration escapes the tight-stop tax on this construct.")
    W("  NOTE: GROSS + in-sample Binance proxy; a beat must ALSO survive holdout + exceed its clustered SE.")
    hr()
    open(REPORT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L)); print("\n[report -> %s]" % REPORT)


if __name__ == "__main__":
    main()
