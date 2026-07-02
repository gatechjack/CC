"""Wick-Test spike backtest (GROSS R). See PRE_REGISTRATION.md for the locked spec.

Mechanizes the discretionary "wick test" (3-candle momentum-continuation scalp, 3m) and tests for
+EV GROSS on BTC/ETH/SOL/XRP. Read-only; no prod/live/SFP writes. k=1 causal, no lookahead.

Regime formula (ema200_pos_slope) is copied VERBATIM from the live-parity regime_filter.py.
"""
from __future__ import annotations
import bisect, math, os, random, sqlite3
from statistics import mean, median

DATA_DIR = os.environ.get("WICK_DATA_DIR", r"C:/Users/AA Incorporado/cc/data")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
DB_KEY = {"BTCUSDT": "btc", "ETHUSDT": "eth", "SOLUSDT": "sol", "XRPUSDT": "xrp"}

_3M = 180_000
_15M = 900_000
TOL = 0.0005          # 0.05% tap band
STOP_FRAC = 0.001     # stop = extreme -/+ 0.001*entry -> rp = 0.1% of price (constant by construction)
FILL_WINDOW = 3       # honest limit fill within next 3 bars
MAX_HOLD_3M = 100     # resolution cap (5h); timeout -> mark-to-market
TARGETS = [1.0, 1.5, 2.0]
LEVELS = ["L1_swing", "L2_vwap"]
SIDES = ["long", "short"]
REGIMES = ("up", "down", "range")
NULL_RUNS = 200
NULL_PCT = 95
NULL_SEED = 20260702


# ---- data ---------------------------------------------------------------------

def load(coin, table):
    p = os.path.join(DATA_DIR, f"{DB_KEY[coin]}_scalping.db")
    con = sqlite3.connect(p)
    rows = con.execute(
        f"SELECT ts,open,high,low,close,volume FROM {table} "
        f"WHERE open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL "
        f"ORDER BY ts").fetchall()
    con.close()
    ts = [int(r[0]) * 1000 for r in rows]
    o = [float(r[1]) for r in rows]; h = [float(r[2]) for r in rows]
    l = [float(r[3]) for r in rows]; c = [float(r[4]) for r in rows]
    v = [float(r[5]) if r[5] is not None else 0.0 for r in rows]
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v, "n": len(ts)}


def count_gaps(ts, step):
    return sum(1 for i in range(len(ts) - 1) if ts[i + 1] - ts[i] != step)


# ---- regime (ema200_pos_slope, VERBATIM from regime_filter.py) -----------------

def ema(vals, span):
    a = 2.0 / (span + 1); e = None; out = []
    for c in vals:
        e = c if e is None else a * c + (1 - a) * e
        out.append(e)
    return out


def regime_labels_15m(m15):
    """Return (close_ts_sorted, label_list) for causal lookup. label None until warm.
    close_ts = open + 15m (bar's close time). ema200_pos_slope with K=32."""
    closes = m15["c"]; em = ema(closes, 200); K = 32
    close_ts = [t + _15M for t in m15["ts"]]
    labels = [None] * len(closes)
    for i in range(len(closes)):
        if i >= K:
            rising = em[i] > em[i - K]
            if closes[i] > em[i] and rising:        labels[i] = "up"
            elif closes[i] < em[i] and not rising:   labels[i] = "down"
            else:                                    labels[i] = "range"
    return close_ts, labels


def regime_before(close_ts, labels, sig_ts):
    """Regime of the last 15m bar CLOSED at or before sig_ts (causal)."""
    j = bisect.bisect_right(close_ts, sig_ts) - 1
    return labels[j] if j >= 0 else None


# ---- L1 swings (two-candle rule, 15m, causal) ---------------------------------

def swings_15m(m15):
    """Confirmed swing highs/lows via the two-candle rule (bodies). Returns
    (sh_ts, sh_lvl), (sl_ts, sl_lvl) - confirm-ts sorted ascending."""
    o, h, l, c, ts = m15["o"], m15["h"], m15["l"], m15["c"], m15["ts"]
    sh_ts = []; sh_lvl = []; sl_ts = []; sl_lvl = []
    for j in range(2, len(c)):
        bear = lambda k: c[k] < o[k]
        bull = lambda k: c[k] > o[k]
        conf = ts[j] + _15M
        if bear(j - 1) and bear(j):        # two bearish -> swing HIGH above them
            sh_ts.append(conf); sh_lvl.append(max(h[j - 2], h[j - 1]))
        if bull(j - 1) and bull(j):        # two bullish -> swing LOW below them
            sl_ts.append(conf); sl_lvl.append(min(l[j - 2], l[j - 1]))
    return (sh_ts, sh_lvl), (sl_ts, sl_lvl)


def level_before(ts_arr, lvl_arr, sig_ts):
    j = bisect.bisect_right(ts_arr, sig_ts) - 1
    return lvl_arr[j] if j >= 0 else None


# ---- L2 session VWAP (00:00 UTC), causal running value on 3m ------------------

def session_vwap_3m(m3):
    ts, h, l, c, v = m3["ts"], m3["h"], m3["l"], m3["c"], m3["v"]
    out = [None] * len(ts)
    day = None; cum_pv = 0.0; cum_v = 0.0
    for i in range(len(ts)):
        d = ts[i] // 86_400_000
        if d != day:
            day = d; cum_pv = 0.0; cum_v = 0.0
        tp = (h[i] + l[i] + c[i]) / 3.0
        cum_pv += tp * v[i]; cum_v += v[i]
        out[i] = (cum_pv / cum_v) if cum_v > 0 else c[i]
    return out


# ---- 3m causal regime array (for filter + regime-matched null) ----------------

def regime_3m(m3, close_ts, labels):
    return [regime_before(close_ts, labels, t + _3M) for t in m3["ts"]]


# ---- simulate one trade (GROSS R, stop-first) ---------------------------------

def sim(side, entry, stop, tp, start_idx, m3):
    """From start_idx inclusive. Returns (R, outcome, exit_idx, body_held, ambig) where
    body_held = stop-triggered AND that bar's close came back inside (past entry);
    ambig = the RESOLVING bar's range also contained the OTHER level (OHLC can't say
    which hit first -> stop-first is a conservative assumption, true outcome unknowable)."""
    h, l, c = m3["h"], m3["l"], m3["c"]
    rp = abs(entry - stop)
    end = min(start_idx + MAX_HOLD_3M, m3["n"] - 1)
    for i in range(start_idx, end + 1):
        if side == "long":
            if l[i] <= stop:
                held = c[i] > entry            # wick pierced stop but body closed back above setup low
                return (-1.0, "loss", i, held, h[i] >= tp)
            if h[i] >= tp:
                return (tp_R(entry, tp, stop), "win", i, False, l[i] <= stop)
        else:
            if h[i] >= stop:
                held = c[i] < entry
                return (-1.0, "loss", i, held, l[i] <= tp)
            if l[i] <= tp:
                return (tp_R(entry, tp, stop), "win", i, False, h[i] >= stop)
    last = c[end]
    r = (last - entry) / rp if side == "long" else (entry - last) / rp
    return (r, "timeout", end, False, False)


def tp_R(entry, tp, stop):
    return abs(tp - entry) / abs(entry - stop)


def find_fill(side, entry, c3_idx, m3):
    """Limit at `entry` fills if a bar in {c3+1..c3+FILL_WINDOW} trades back to it."""
    h, l = m3["h"], m3["l"]
    for i in range(c3_idx + 1, min(c3_idx + FILL_WINDOW, m3["n"] - 1) + 1):
        if side == "long" and l[i] <= entry:
            return i
        if side == "short" and h[i] >= entry:
            return i
    return None


# ---- raw signal generation (per coin, side, level) ----------------------------

def gen_signals(m3, side, level_type, vwap3, swings, reg3):
    """List of dicts: {c3, entry, stop, level, regime}. Raw (pre-filter, pre-fill)."""
    o, h, l, c = m3["o"], m3["h"], m3["l"], m3["c"]
    (sh_ts, sh_lvl), (sl_ts, sl_lvl) = swings
    sigs = []
    for k in range(2, m3["n"]):
        c1, c2 = k - 2, k - 1
        if side == "long":
            if not (c[c1] > o[c1] and c[c2] > o[c2]):        # two bullish momentum
                continue
        else:
            if not (c[c1] < o[c1] and c[c2] < o[c2]):        # two bearish momentum
                continue
        sig_ts = m3["ts"][k] + _3M
        if level_type == "L1_swing":
            level = level_before(sh_ts, sh_lvl, sig_ts) if side == "long" \
                else level_before(sl_ts, sl_lvl, sig_ts)
        else:
            level = vwap3[k]
        if level is None or level <= 0:
            continue
        band = TOL * level
        if side == "long":
            tap = abs(l[k] - level) <= band
            wick = l[k] < min(o[k], c[k])
            close_ok = c[k] > level
            if not (tap and wick and close_ok):
                continue
            entry = l[k]; stop = l[k] - STOP_FRAC * entry
        else:
            tap = abs(h[k] - level) <= band
            wick = h[k] > max(o[k], c[k])
            close_ok = c[k] < level
            if not (tap and wick and close_ok):
                continue
            entry = h[k]; stop = h[k] + STOP_FRAC * entry
        sigs.append({"c3": k, "entry": entry, "stop": stop, "level": level,
                     "regime": reg3[k]})
    return sigs


# ---- build trades for a (target, filter) --------------------------------------

def build_trades(sigs, side, target, m3, regime_filter):
    """regime_filter: None=control(all) or 'up'/'down' for with-trend. One-open-at-a-time.
    Returns (trades, n_filtered, n_filled) where trades=list of dicts."""
    trades = []; busy_until = -1; n_filtered = 0; n_filled = 0
    for s in sigs:
        if regime_filter is not None and s["regime"] != regime_filter:
            continue
        n_filtered += 1
        if s["c3"] <= busy_until:
            continue
        f = find_fill(side, s["entry"], s["c3"], m3)
        if f is None:
            continue
        n_filled += 1
        entry, stop = s["entry"], s["stop"]
        rp = abs(entry - stop)
        tp = entry + target * rp if side == "long" else entry - target * rp
        R, outcome, exit_idx, held, ambig = sim(side, entry, stop, tp, f, m3)
        trades.append({"regime": s["regime"], "R": R, "outcome": outcome, "rp": rp,
                       "entry": entry, "held": held, "exit": exit_idx, "ambig": ambig})
        busy_until = exit_idx
    return trades, n_filtered, n_filled


# ---- aggregation --------------------------------------------------------------

def agg(trades):
    rs = [t["R"] for t in trades]
    n = len(rs)
    if n == 0:
        return {"n": 0, "wr": float("nan"), "avgR": float("nan"), "totR": 0.0}
    wins = sum(1 for t in trades if t["outcome"] == "win")
    return {"n": n, "wr": wins / n, "avgR": sum(rs) / n, "totR": sum(rs)}


# ---- null-gate: random-entry, side+regime matched, same stop geometry ---------

def null_p95(coin_m3, side, regime_match, reg3, target, n_target, rng):
    """p95 of avgR over NULL_RUNS random-entry samples of size n_target. Entry at a
    random bar's close (regime-matched), stop 0.1%, tp target*R, stop-first."""
    if n_target == 0:
        return float("nan")
    pool = [i for i in range(coin_m3["n"] - 2)
            if (regime_match is None or reg3[i] == regime_match)]
    if len(pool) < 5:
        return float("nan")
    c = coin_m3["c"]
    means = []
    for _ in range(NULL_RUNS):
        rs = []
        for _ in range(n_target):
            j = pool[rng.randrange(len(pool))]
            entry = c[j]
            stop = entry - STOP_FRAC * entry if side == "long" else entry + STOP_FRAC * entry
            tp = entry + target * STOP_FRAC * entry if side == "long" else entry - target * STOP_FRAC * entry
            R = sim(side, entry, stop, tp, j + 1, coin_m3)[0]
            rs.append(R)
        means.append(mean(rs))
    means.sort()
    idx = (NULL_PCT / 100) * (len(means) - 1)
    lo = int(idx); hi = min(lo + 1, len(means) - 1)
    return means[lo] * (1 - (idx - lo)) + means[hi] * (idx - lo)


# ---- main ---------------------------------------------------------------------

def quart(vals):
    if not vals:
        return (float("nan"),) * 3
    s = sorted(vals); n = len(s)
    def q(p):
        idx = p * (n - 1); lo = int(idx); hi = min(lo + 1, n - 1)
        return s[lo] * (1 - (idx - lo)) + s[hi] * (idx - lo)
    return (q(0.25), q(0.5), q(0.75))


def main():
    rng = random.Random(NULL_SEED)
    print("=" * 96)
    print("WICK-TEST SPIKE - GROSS R - 3-candle momentum continuation (3m). See PRE_REGISTRATION.md")
    print("=" * 96)

    data = {}
    for coin in COINS:
        m3 = load(coin, "bars_3m"); m15 = load(coin, "bars_15m")
        close_ts, labels = regime_labels_15m(m15)
        sw = swings_15m(m15); vwap3 = session_vwap_3m(m3); reg3 = regime_3m(m3, close_ts, labels)
        days3 = (m3["ts"][-1] - m3["ts"][0]) / 86_400_000
        data[coin] = dict(m3=m3, m15=m15, close_ts=close_ts, labels=labels, sw=sw,
                          vwap3=vwap3, reg3=reg3, days3=days3)
        dist = {}
        for r in reg3:
            dist[r] = dist.get(r, 0) + 1
        print(f"[{coin}] 3m n={m3['n']} ({days3:.1f}d, gaps={count_gaps(m3['ts'],_3M)}) | "
              f"15m n={m15['n']} (gaps={count_gaps(m15['ts'],_15M)}) | "
              f"swings H={len(sw[0][0])} L={len(sw[1][0])} | 3m-regime(causal)={dist}")

    # signal frequency (raw pattern, per level) + fill/skip
    print("\n" + "=" * 96)
    print("RAW PATTERN FREQUENCY (pre-regime-filter) & FILL RATE (honest limit, target-independent)")
    print("=" * 96)
    raw_sig = {}
    for coin in COINS:
        d = data[coin]
        for side in SIDES:
            for lvl in LEVELS:
                sigs = gen_signals(d["m3"], side, lvl, d["vwap3"], d["sw"], d["reg3"])
                raw_sig[(coin, side, lvl)] = sigs
                nfill = sum(1 for s in sigs if find_fill(side, s["entry"], s["c3"], d["m3"]) is not None)
                wk = d["days3"] / 7.0
                fr = (nfill / len(sigs) * 100) if sigs else float("nan")
                print(f"  {coin} {side:5s} {lvl:9s}: raw={len(sigs):4d} "
                      f"({len(sigs)/wk:5.1f}/wk) fill={nfill:4d} ({fr:4.1f}%) skip={len(sigs)-nfill}")

    # main grid
    hdr = f"\n{'coin':8s}{'side':6s}{'level':10s}{'tgt':5s}{'filter':10s}" \
          f"{'n':>5s}{'WR':>7s}{'avgR':>8s}{'totR':>9s}{'null_p95':>10s}{'beats':>7s}"
    print("\n" + "=" * 96)
    print("MAIN GRID - GROSS R  (filter=withtrend: long-up/short-down only; control=all regimes)")
    print("=" * 96)
    print(hdr)
    results = []
    for coin in COINS:
        d = data[coin]
        for side in SIDES:
            wt_regime = "up" if side == "long" else "down"
            for lvl in LEVELS:
                sigs = raw_sig[(coin, side, lvl)]
                for tgt in TARGETS:
                    # control (all regimes)
                    tr_all, nf_all, nfl_all = build_trades(sigs, side, tgt, d["m3"], None)
                    a_all = agg(tr_all)
                    # with-trend
                    tr_wt, nf_wt, nfl_wt = build_trades(sigs, side, tgt, d["m3"], wt_regime)
                    a_wt = agg(tr_wt)
                    for label, tr, a, rf in (("withtrend", tr_wt, a_wt, wt_regime),
                                             ("control", tr_all, a_all, None)):
                        p95 = float("nan"); beats = "-"
                        if a["n"] > 0 and a["avgR"] > 0:
                            p95 = null_p95(d["m3"], side, rf, d["reg3"], tgt, a["n"], rng)
                            beats = "YES" if (not math.isnan(p95) and a["avgR"] >= p95) else "no"
                        wrs = f"{a['wr']*100:5.1f}" if a["n"] else "  -- "
                        avs = f"{a['avgR']:+.3f}" if a["n"] else "  -- "
                        ps = f"{p95:+.3f}" if not math.isnan(p95) else "   -- "
                        thin = "*" if 0 < a["n"] < 30 else " "
                        print(f"  {coin:8s}{side:6s}{lvl:10s}{tgt:<5}{label:10s}"
                              f"{a['n']:>5d}{wrs:>7s}{avs:>8s}{a['totR']:>+9.2f}{ps:>10s}{beats:>6s}{thin}")
                        results.append(dict(coin=coin, side=side, lvl=lvl, tgt=tgt, filt=label,
                                            **a, p95=p95, beats=beats, trades=tr))

    # regime split of the control (does counter-trend bleed?)
    print("\n" + "=" * 96)
    print("REGIME SPLIT OF CONTROL (avgR by regime; target=2.0, L1_swing) - does counter-trend bleed?")
    print("=" * 96)
    print(f"  {'coin':8s}{'side':6s}{'L-up/S-dn(aligned)':>22s}{'range':>16s}{'counter':>16s}")
    for coin in COINS:
        d = data[coin]
        for side in SIDES:
            sigs = raw_sig[(coin, side, "L1_swing")]
            tr_all, _, _ = build_trades(sigs, side, 2.0, d["m3"], None)
            by = {r: [t["R"] for t in tr_all if t["regime"] == r] for r in REGIMES}
            aligned_r = "up" if side == "long" else "down"
            counter_r = "down" if side == "long" else "up"
            def cell(rs):
                return f"n={len(rs)} {mean(rs):+.3f}" if rs else "n=0   --"
            print(f"  {coin:8s}{side:6s}{cell(by[aligned_r]):>22s}{cell(by['range']):>16s}"
                  f"{cell(by[counter_r]):>16s}")

    # stop-distance distribution + wick-body-held (pool all filled trades per coin)
    print("\n" + "=" * 96)
    print("STOP-DISTANCE DISTRIBUTION (rp) & WICK-PIERCED-BODY-HELD (materiality of body-close rule)")
    print("  rp%-of-price is constant 0.1% BY CONSTRUCTION (entry=c3 extreme, stop=extreme-0.001*entry).")
    print("=" * 96)
    for coin in COINS:
        d = data[coin]
        pooled = []
        for side in SIDES:
            for lvl in LEVELS:
                tr, _, _ = build_trades(raw_sig[(coin, side, lvl)], side, 2.0, d["m3"], None)
                pooled += tr
        if not pooled:
            print(f"  {coin}: no filled trades"); continue
        rp_pct = [t["rp"] / t["entry"] * 100 for t in pooled]
        rp_usd = [t["rp"] for t in pooled]
        q_pct = quart(rp_pct); q_usd = quart(rp_usd)
        losses = [t for t in pooled if t["outcome"] == "loss"]
        held = sum(1 for t in losses if t["held"])
        hpct = (held / len(losses) * 100) if losses else float("nan")
        resolved = [t for t in pooled if t["outcome"] in ("win", "loss")]
        ambig = sum(1 for t in resolved if t["ambig"])
        apct = (ambig / len(resolved) * 100) if resolved else float("nan")
        print(f"  {coin}: rp %ofprice q25/med/q75 = {q_pct[0]:.3f}/{q_pct[1]:.3f}/{q_pct[2]:.3f}%  | "
              f"rp$ = {q_usd[0]:.5g}/{q_usd[1]:.5g}/{q_usd[2]:.5g}  | "
              f"stop-losses={len(losses)} body-held-wickthrough={held} ({hpct:.1f}%)")
        print(f"           PATH-AMBIGUOUS (resolving bar held BOTH stop & tp -> OHLC can't order "
              f"them; stop-first assumed): {ambig}/{len(resolved)} ({apct:.1f}%)")

    # headline: with-trend-at-level vs control, pooled across coins, that BEAT null
    print("\n" + "=" * 96)
    print("HEADLINE - cells that BEAT the null (avgR >= null p95), n>=30 unless flagged *")
    print("=" * 96)
    any_beat = False
    for r in results:
        if r["beats"] == "YES":
            any_beat = True
            thin = " (THIN n<30)" if r["n"] < 30 else ""
            print(f"  {r['coin']} {r['side']} {r['lvl']} tgt={r['tgt']} [{r['filt']}]: "
                  f"n={r['n']} WR={r['wr']*100:.1f}% avgR={r['avgR']:+.3f} "
                  f"totR={r['totR']:+.2f} (null_p95={r['p95']:+.3f}){thin}")
    if not any_beat:
        print("  (none - no cell cleared its random-entry null)")
    print("\nDONE.")


if __name__ == "__main__":
    main()
