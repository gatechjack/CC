"""range_scalp_mtf.py — SKILL-FAITHFUL multi-timeframe range fade. BACKTEST ONLY, read-only.

Tests whether "bounce between the boxes" nulled because the IDEA is wrong or because the
TIMEFRAME was wrong. Prior range_scalp.py confirmed structure on 3m (the AlexO market-structure
skill calls that too noisy) and nulled. This applies the skill's master loop faithfully:

  * STRUCTURE / RANGE defined on 15m (two-candle-rule swings, body-based, NON-repainting:
    a swing is known only at the close of the 2nd confirming opposite-body candle).
  * REGIME gate: only fade when 15m is ranging (Kaufman efficiency ratio <= thr) — "don't fade
    aggressive one-directional momentum".
  * BIAS: 1H trend sets the tilt (this corpus is bear -> premium shorts favored; discount longs
    only inside a confirmed 15m range pocket).
  * ENTRY = ACCEPTANCE back inside, not the deviation: a 15m bar that wicks beyond the box edge
    and BODY-CLOSES back inside (SFP/rejection). EXECUTION condensed to 3m (entry on the 3m bar
    at/after that 15m close; stop just beyond the swept extreme = the structural-break level).
  * TARGETS: TP1 = range median (50% off, stop to breakeven), TP2 = far edge (runner).

HONESTY RAILS (where the prior nulls died):
  * No repaint: at 3m decision time, only 15m/1H bars FULLY CLOSED at-or-before that time are used.
  * Entry-delay test: k=0 AND k=1. An edge that exists only at k=0 is repaint -> that is THE headline.
  * Walk-forward: TRAIN<=May15 / VALIDATE<=Jun1 / LOCKBOX>Jun1 (lockbox only for positive-both).
  * Corrected fees (continuity with range_scalp.py): entry, maker on TP legs, taker on stop/timeout.

OUTPUT CONTRACT (printed): this is a MECHANICS test, not a profitability verdict. One bear/neutral
regime cannot answer "does range-fade pay". A NULL here is REGIME-INCONCLUSIVE, not method-dead;
a POSITIVE is a CANDIDATE, not a live strategy. No live code / no wiring / no deploy.
"""
from __future__ import annotations
import sqlite3, statistics, bisect
from datetime import datetime, timezone
from range_scalp import ENTRY_FEE, MK, TK, SLIP2   # fee-model continuity

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()

STEP15, STEP1H, STEP3 = 900, 3600, 180
M15 = 80           # active-swing lookback in 15m bars (~20h)
ER_K15 = 14        # efficiency-ratio window on 15m closes
K1H = 6            # 1H bias lookback (bars)
WMIN, WMAX = 0.003, 0.030   # tradeable box width (R-S)/mid
MAXBARS3 = 160     # max hold in 3m bars (~8h)
TOL = 0.0015       # cluster tol for EQH/EQL boxes (fraction)
ER_THRS = [0.30, 0.45]
BUFS = [0.0005, 0.0010]
KS = [0, 1]


def load_bars(table):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(f"SELECT ts,open,high,low,close FROM {table} ORDER BY ts").fetchall()
    con.close()
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]


def swings_15m(b15):
    """Two-candle-rule swings, body-based, NON-repainting.
    Swing HIGH at p: high[p] tops its neighbours and the 2 following candles are bearish-bodied
    -> confirmed (known) at the CLOSE of bar p+2. Mirror for swing lows. Returns (highs, lows) as
    (confirm_ts, price) sorted by confirm_ts."""
    highs, lows = [], []
    n = len(b15)
    for p in range(1, n - 2):
        h, l = b15[p][2], b15[p][3]
        b1, b2 = b15[p + 1], b15[p + 2]
        bear1, bear2 = b1[4] < b1[1], b2[4] < b2[1]
        bull1, bull2 = b1[4] > b1[1], b2[4] > b2[1]
        confirm_ts = b15[p + 2][0] + STEP15
        if bear1 and bear2 and h >= b1[2] and h >= b2[2] and h > b15[p - 1][2]:
            highs.append((confirm_ts, h))
        if bull1 and bull2 and l <= b1[3] and l <= b2[3] and l < b15[p - 1][3]:
            lows.append((confirm_ts, l))
    highs.sort(); lows.sort()
    return highs, lows


def eff_ratio(closes, K):
    er = [None] * len(closes)
    for i in range(K, len(closes)):
        num = abs(closes[i] - closes[i - K])
        den = sum(abs(closes[j] - closes[j - 1]) for j in range(i - K + 1, i + 1))
        er[i] = (num / den) if den > 0 else 1.0
    return er


def cluster_nearest(prices, ref, tol_abs, want):
    """Cluster confirmed swing prices (greedy, tol_abs); return the level (cluster mean) nearest to
    ref on the requested side. want='above' -> lowest level > ref ; 'below' -> highest level < ref."""
    if not prices:
        return None
    sp = sorted(prices)
    levels, grp = [], [sp[0]]
    for x in sp[1:]:
        if x - grp[0] <= tol_abs:
            grp.append(x)
        else:
            levels.append(statistics.fmean(grp)); grp = [x]
    levels.append(statistics.fmean(grp))
    if want == "above":
        c = [v for v in levels if v > ref]
        return min(c) if c else None
    else:
        c = [v for v in levels if v < ref]
        return max(c) if c else None


def simulate(b3, b15, hi, lo, er15, ts15, c1h_ts, c1h_close, er_thr, buf, kdelay):
    """One config -> list of trades: (ts, side, net_R, outcome, bias1h, segment_ts).
    Driven off 15m acceptance events; executed on 3m. Independent (overlapping) trades, as in the
    prior harness."""
    t3 = [b[0] for b in b3]
    trades = []
    n15 = len(b15)
    for k15 in range(2, n15):
        if er15[k15] is None or er15[k15] > er_thr:      # regime gate: only fade ranges
            continue
        ct = b15[k15][0] + STEP15                          # this 15m bar's close time
        o, h, l, c = b15[k15][1], b15[k15][2], b15[k15][3], b15[k15][4]
        # confirmed swings known strictly before this bar's close, within the M15 lookback window
        lo_ts = ct - M15 * STEP15
        sh = [p for (t, p) in hi if lo_ts <= t <= ct]
        sl = [p for (t, p) in lo if lo_ts <= t <= ct]
        if not sh or not sl:
            continue
        mid = c
        R = cluster_nearest(sh, c, TOL * mid, "above")
        S = cluster_nearest(sl, c, TOL * mid, "below")
        if R is None or S is None:
            continue
        width = (R - S) / mid
        if not (WMIN <= width <= WMAX):
            continue
        # ACCEPTANCE back inside: wick beyond the edge, body-close back in (SFP-style rejection)
        side = None
        if h > R and c <= R:
            side = "sell"; swept = h
        elif l < S and c >= S:
            side = "buy"; swept = l
        if side is None:
            continue
        # 1H bias at the last 1H bar closed at/before ct
        bi = bisect.bisect_right(c1h_ts, ct) - 1
        bias = 0
        if bi - K1H >= 0:
            d = c1h_close[bi] - c1h_close[bi - K1H]
            bias = 1 if d > 0 else (-1 if d < 0 else 0)
        # execution on 3m: first 3m bar closing at/after ct, plus k-delay
        idx0 = bisect.bisect_left(t3, ct - STEP3 + 1)     # bar whose close (ts+STEP3) >= ct
        while idx0 < len(b3) and b3[idx0][0] + STEP3 < ct:
            idx0 += 1
        ei = idx0 + kdelay
        if ei >= len(b3):
            continue
        entry = b3[ei][4]
        median = (S + R) / 2.0
        if side == "buy":
            stop = swept - buf * mid
            risk = entry - stop
            tp1, tp2 = median, R
            if not (risk > 0 and tp1 > entry and tp2 > tp1):
                continue
        else:
            stop = swept + buf * mid
            risk = stop - entry
            tp1, tp2 = median, S
            if not (risk > 0 and tp1 < entry and tp2 < tp1):
                continue
        sp = risk / entry
        R1 = abs(tp1 - entry) / risk
        R2 = abs(tp2 - entry) / risk
        # walk 3m bars; 50% at TP1 then stop->breakeven on the runner
        half1 = False
        gross = fees = None
        fee_entry = ENTRY_FEE / sp
        outcome = None
        for j in range(ei + 1, min(len(b3), ei + 1 + MAXBARS3)):
            hh, ll = b3[j][2], b3[j][3]
            if not half1:
                hit_stop = (ll <= stop) if side == "buy" else (hh >= stop)
                hit_tp1 = (hh >= tp1) if side == "buy" else (ll <= tp1)
                if hit_stop:                              # structural break stopped the fade
                    gross = -1.0; fees = fee_entry + (TK + SLIP2) / sp
                    outcome = "stop"; break
                if hit_tp1:
                    half1 = True
                    gross = 0.5 * R1; fees = fee_entry + 0.5 * (MK + SLIP2) / sp
            else:
                hit_be = (ll <= entry) if side == "buy" else (hh >= entry)
                hit_tp2 = (hh >= tp2) if side == "buy" else (ll <= tp2)
                if hit_be:                                # runner stopped at breakeven
                    gross += 0.0; fees += 0.5 * (TK + SLIP2) / sp
                    outcome = "tp1_be"; break
                if hit_tp2:
                    gross += 0.5 * R2; fees += 0.5 * (MK + SLIP2) / sp
                    outcome = "tp2"; break
        if outcome is None:                               # timeout: mark remaining to close
            last = b3[min(len(b3) - 1, ei + MAXBARS3)][4]
            close_R = ((last - entry) if side == "buy" else (entry - last)) / risk
            if not half1:
                gross = close_R; fees = fee_entry + (TK + SLIP2) / sp
                outcome = "timeout"
            else:
                gross += 0.5 * close_R; fees += 0.5 * (TK + SLIP2) / sp
                outcome = "tp1_timeout"
        trades.append((ct, side, gross - fees, outcome, bias))
    return trades


def agg(trades, side, lo, hi):
    sub = [t for t in trades if t[1] == side and lo <= t[0] < hi]
    if not sub:
        return None
    nets = [t[2] for t in sub]
    brk = sum(1 for t in sub if t[3] == "stop")
    win = sum(1 for t in sub if t[2] > 0)
    return len(nets), round(100 * win / len(nets), 1), round(statistics.fmean(nets), 4), round(100 * brk / len(nets), 0)


def main():
    print("=" * 90)
    print("RANGE-SCALP MTF — MECHANICS TEST, NOT A PROFITABILITY VERDICT.")
    print("  One bear/neutral regime cannot answer 'does range-fade pay'.")
    print("  NULL here = REGIME-INCONCLUSIVE (not method-dead). POSITIVE = CANDIDATE (not live).")
    print("  §5 momentum/regime gate holds. No live code / no wiring / no deploy.")
    print("=" * 90)
    b3 = load_bars("bars_3m"); b15 = load_bars("bars_15m"); b1h = load_bars("bars_1h")
    hi, lo = swings_15m(b15)
    er15 = eff_ratio([b[4] for b in b15], ER_K15)
    ts15 = [b[0] for b in b15]
    c1h_ts = [b[0] + STEP1H for b in b1h]     # 1H close times
    c1h_close = [b[4] for b in b1h]
    # regime mix over the 3m-tradeable span (15m bars whose close is within the 3m window)
    span_lo, span_hi = b3[0][0], b3[-1][0] + STEP3
    in_span = [er15[i] for i in range(len(b15)) if span_lo <= ts15[i] + STEP15 <= span_hi and er15[i] is not None]
    print(f"\ncorpus: 3m bars={len(b3)} 15m={len(b15)} 1h={len(b1h)} | "
          f"3m span {datetime.utcfromtimestamp(span_lo):%Y-%m-%d}..{datetime.utcfromtimestamp(span_hi):%Y-%m-%d}")
    print(f"confirmed 15m swings: hi={len(hi)} lo={len(lo)}")
    for thr in ER_THRS:
        frac = 100 * sum(1 for e in in_span if e <= thr) / len(in_span)
        print(f"  regime mix: {frac:.0f}% of in-span 15m bars rank 'ranging' at ER<={thr}")

    cache = {}
    for thr in ER_THRS:
        for buf in BUFS:
            for k in KS:
                cache[(thr, buf, k)] = simulate(b3, b15, hi, lo, er15, ts15, c1h_ts, c1h_close, thr, buf, k)

    # ---- HEADLINE: repaint test. k=0 vs k=1 full-sample net per side. ----
    print("\n" + "#" * 90)
    print("# HEADLINE — REPAINT TEST (k=0 vs k=1). If net is positive ONLY at k=0 -> REPAINT, dead.")
    print("#" * 90)
    g = lambda d: f"n={d[0]:<4} win{d[1]:<5} N={d[2]:+.3f} brk{d[3]:.0f}%" if d else "(none)"
    for thr in ER_THRS:
        for buf in BUFS:
            for side in ("sell", "buy"):
                a0 = agg(cache[(thr, buf, 0)], side, 0, 9e12)
                a1 = agg(cache[(thr, buf, 1)], side, 0, 9e12)
                flag = ""
                if a0 and a1 and a0[2] > 0 >= a1[2]:
                    flag = "  <-- REPAINT (k0+ / k1<=0)"
                elif a1 and a1[2] > 0:
                    flag = "  <-- survives k=1"
                print(f"  ER<={thr} buf{buf*100:.2f}% {side:<4}  k0 {g(a0):<34} k1 {g(a1):<34}{flag}")
            print()

    # ---- WALK-FORWARD on the honest k=1, long vs short separate, break% shown ----
    print("#" * 90)
    print("# WALK-FORWARD (k=1, repaint-honest). TRAIN<=May15 | VALIDATE<=Jun1 ; brk%=structural-break stop-rate")
    print("#   * = positive net on BOTH train+validate (N>=20 train / >=8 val) -> lockbox computed")
    print("#" * 90)
    flagged = []
    for thr in ER_THRS:
        for buf in BUFS:
            for side in ("sell", "buy"):
                tr = agg(cache[(thr, buf, 1)], side, 0, TRAIN_END)
                va = agg(cache[(thr, buf, 1)], side, TRAIN_END, VAL_END)
                flag = ""
                if tr and va and tr[2] > 0 and va[2] > 0 and tr[0] >= 20 and va[0] >= 8:
                    flag = " *"; flagged.append((thr, buf, side))
                print(f"  ER<={thr} buf{buf*100:.2f}% {side:<4} TRAIN {g(tr):<34} VAL {g(va)}{flag}")
            print()
    print(f"=== positive on BOTH train+validate (k=1): {len(flagged)} ===")
    for (thr, buf, side) in flagged:
        lb = agg(cache[(thr, buf, 1)], side, VAL_END, 9e12)
        print(f"  ER<={thr} buf{buf*100:.2f}% {side}: LOCKBOX {g(lb)}")
    if not flagged:
        print("  (none) -> on THIS bear/neutral 3m tape the skill-faithful fade produced no positive-both")
        print("          candidate. Per the contract: REGIME-INCONCLUSIVE, not method-dead. Re-run on a")
        print("          ranging/native window before any verdict on the idea.")


if __name__ == "__main__":
    main()
