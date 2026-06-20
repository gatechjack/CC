"""range_scalp_xtf.py — CROSS-REGIME test of the AlexO RANGE-FADE METHOD. BACKTEST ONLY, read-only.

>>> THIS TESTS THE METHOD, NOT THE 3m BITUNIX BOT. <<<
The 3m bear-only window (range_scalp_mtf.py) was repaint-clean but regime-inconclusive: it could not
say whether the IDEA pays, only that it doesn't pay on a down-trending tape. This run lifts the whole
stack ONE NOTCH to reach a corpus that contains bull + ranging regimes:
    DEFINE 1H (bias)  /  CONFIRM 30m (structure, range, acceptance, ER regime gate)  /  EXECUTE 15m.
15m execution != 3m scalp: different hold, different fee-in-R, different trade count. A POSITIVE here
says "the range-fade METHOD can pay in a non-bear regime" — it does NOT say "the 3m bot will pay."
There is no native 3m bull data and resampling cannot manufacture it. Do not read a 15m-execution
result onto the 3m strategy.

WINDOW: bound by the shortest leg = 15m execution, which starts 2025-11-01 -> ~7.5 months (Nov-2025..
Jun-2026), spanning a full up/down cycle. (1H reaches Aug-2024 and 30m Jan-2025, but a trade needs an
execution bar, so the test cannot start before 15m data exists.)

GATE (load-bearing): regime mix is reported FIRST, before any PnL. If the in-span tape is still mostly
bear/quiet (non-bear = bull+range < 30% at ER<=0.45) the script STOPS before PnL — that is the one
case where the longer 30m-execution window earns its keep. Otherwise it runs straight through.

PARAMETER DISCIPLINE: every constant is BAR-COUNT-IDENTICAL to the 3m run; only the timeframes lifted.
No grid expansion just because the corpus got longer (p-hacking risk rises with more data, not falls).

Honesty rails carry over unchanged: no-repaint (k=0 vs k=1 headline), walk-forward TRAIN/VAL/LOCKBOX,
break-rate + long/short legs separate, corrected fee model. No live code / no wiring / no deploy.
"""
from __future__ import annotations
import sqlite3, statistics, bisect
from datetime import datetime, timezone
from range_scalp import ENTRY_FEE, MK, TK, SLIP2   # fee-model continuity

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
BIAS_TF, STRUCT_TF, EXEC_TF = "bars_1h", "bars_30m", "bars_15m"
BIAS_STEP, STRUCT_STEP, EXEC_STEP = 3600, 1800, 900

# walk-forward split for the Nov-2025..Jun-2026 window (~52% / 26% / 21%)
TRAIN_END = datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp()

# --- constants: bar-count-identical to the 3m run, timeframes only lifted ---
M = 80             # active-swing lookback on STRUCT TF (bars)
ER_K = 14          # efficiency-ratio window on STRUCT closes (bars)
K1H = 6            # bias lookback on BIAS TF (bars)
WMIN, WMAX = 0.003, 0.030
MAXBARS = 160      # max hold on EXEC TF (bars)
TOL = 0.0015
ER_THRS = [0.30, 0.45]
BUFS = [0.0005, 0.0010]
KS = [0, 1]
GATE_THR, GATE_MIN_NONBEAR = 0.45, 0.30   # stop-before-PnL gate


def load_bars(table):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(f"SELECT ts,open,high,low,close FROM {table} ORDER BY ts").fetchall()
    con.close()
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]


def swings(bars, step):
    """Two-candle-rule swings, body-based, NON-repainting; confirmed at close of bar p+2."""
    highs, lows = [], []
    n = len(bars)
    for p in range(1, n - 2):
        h, l = bars[p][2], bars[p][3]
        b1, b2 = bars[p + 1], bars[p + 2]
        bear1, bear2 = b1[4] < b1[1], b2[4] < b2[1]
        bull1, bull2 = b1[4] > b1[1], b2[4] > b2[1]
        confirm_ts = bars[p + 2][0] + step
        if bear1 and bear2 and h >= b1[2] and h >= b2[2] and h > bars[p - 1][2]:
            highs.append((confirm_ts, h))
        if bull1 and bull2 and l <= b1[3] and l <= b2[3] and l < bars[p - 1][3]:
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
    c = [v for v in levels if v < ref]
    return max(c) if c else None


def simulate(bx, bs, hi, lo, er, cb_ts, cb_close, er_thr, buf, kdelay):
    """bx=EXEC bars, bs=STRUCT bars. One config -> trades (ts, side, net_R, outcome, bias)."""
    tx = [b[0] for b in bx]
    trades = []
    for ks in range(2, len(bs)):
        if er[ks] is None or er[ks] > er_thr:
            continue
        ct = bs[ks][0] + STRUCT_STEP
        h, l, c = bs[ks][2], bs[ks][3], bs[ks][4]
        lo_ts = ct - M * STRUCT_STEP
        sh = [p for (t, p) in hi if lo_ts <= t <= ct]
        sl = [p for (t, p) in lo if lo_ts <= t <= ct]
        if not sh or not sl:
            continue
        mid = c
        R = cluster_nearest(sh, c, TOL * mid, "above")
        S = cluster_nearest(sl, c, TOL * mid, "below")
        if R is None or S is None:
            continue
        if not (WMIN <= (R - S) / mid <= WMAX):
            continue
        side = None
        if h > R and c <= R:
            side = "sell"; swept = h
        elif l < S and c >= S:
            side = "buy"; swept = l
        if side is None:
            continue
        bi = bisect.bisect_right(cb_ts, ct) - 1
        bias = 0
        if bi - K1H >= 0:
            d = cb_close[bi] - cb_close[bi - K1H]
            bias = 1 if d > 0 else (-1 if d < 0 else 0)
        idx0 = bisect.bisect_left(tx, ct - EXEC_STEP + 1)
        while idx0 < len(bx) and bx[idx0][0] + EXEC_STEP < ct:
            idx0 += 1
        ei = idx0 + kdelay
        if ei >= len(bx):
            continue
        entry = bx[ei][4]
        median = (S + R) / 2.0
        if side == "buy":
            stop = swept - buf * mid; risk = entry - stop; tp1, tp2 = median, R
            if not (risk > 0 and tp1 > entry and tp2 > tp1):
                continue
        else:
            stop = swept + buf * mid; risk = stop - entry; tp1, tp2 = median, S
            if not (risk > 0 and tp1 < entry and tp2 < tp1):
                continue
        sp = risk / entry
        R1, R2 = abs(tp1 - entry) / risk, abs(tp2 - entry) / risk
        half1, gross, fees, outcome = False, None, None, None
        fee_entry = ENTRY_FEE / sp
        for j in range(ei + 1, min(len(bx), ei + 1 + MAXBARS)):
            hh, ll = bx[j][2], bx[j][3]
            if not half1:
                hit_stop = (ll <= stop) if side == "buy" else (hh >= stop)
                hit_tp1 = (hh >= tp1) if side == "buy" else (ll <= tp1)
                if hit_stop:
                    gross = -1.0; fees = fee_entry + (TK + SLIP2) / sp; outcome = "stop"; break
                if hit_tp1:
                    half1 = True; gross = 0.5 * R1; fees = fee_entry + 0.5 * (MK + SLIP2) / sp
            else:
                hit_be = (ll <= entry) if side == "buy" else (hh >= entry)
                hit_tp2 = (hh >= tp2) if side == "buy" else (ll <= tp2)
                if hit_be:
                    gross += 0.0; fees += 0.5 * (TK + SLIP2) / sp; outcome = "tp1_be"; break
                if hit_tp2:
                    gross += 0.5 * R2; fees += 0.5 * (MK + SLIP2) / sp; outcome = "tp2"; break
        if outcome is None:
            last = bx[min(len(bx) - 1, ei + MAXBARS)][4]
            close_R = ((last - entry) if side == "buy" else (entry - last)) / risk
            if not half1:
                gross = close_R; fees = fee_entry + (TK + SLIP2) / sp; outcome = "timeout"
            else:
                gross += 0.5 * close_R; fees += 0.5 * (TK + SLIP2) / sp; outcome = "tp1_timeout"
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
    print("=" * 92)
    print("RANGE-SCALP XTF  —  TEST OF THE RANGE-FADE *METHOD*, NOT THE 3m BITUNIX BOT.")
    print("  Stack lifted one notch: DEFINE 1H / CONFIRM 30m / EXECUTE 15m. 15m exec != 3m scalp.")
    print("  A POSITIVE => 'the METHOD can pay in a non-bear regime', NOT 'the bot will pay'.")
    print("  No 3m bull data exists; nothing here transfers to the 3m strategy. Read-only, no deploy.")
    print("=" * 92)
    bias = load_bars(BIAS_TF); bs = load_bars(STRUCT_TF); bx = load_bars(EXEC_TF)
    hi, lo = swings(bs, STRUCT_STEP)
    er = eff_ratio([b[4] for b in bs], ER_K)
    cb_ts = [b[0] + BIAS_STEP for b in bias]
    cb_close = [b[4] for b in bias]
    span_lo, span_hi = bx[0][0], bx[-1][0] + EXEC_STEP
    print(f"\ncorpus: 1h={len(bias)} 30m={len(bs)} 15m={len(bx)} | EXEC(15m) span "
          f"{datetime.utcfromtimestamp(span_lo):%Y-%m-%d}..{datetime.utcfromtimestamp(span_hi):%Y-%m-%d}")
    print(f"confirmed 30m swings: hi={len(hi)} lo={len(lo)}")

    # ---- REGIME MIX FIRST (load-bearing gate) ----
    print("\n" + "#" * 92)
    print("# REGIME MIX (in-span 30m bars, same ER classifier) — reported BEFORE any PnL.")
    print("#   range = ER<=thr ; else bull if close>close[-ER_K] else bear")
    print("#" * 92)
    cl = [b[4] for b in bs]
    nonbear_gate = None
    for thr in ER_THRS:
        tot = bull = rng = bear = 0
        for i in range(len(bs)):
            ct = bs[i][0] + STRUCT_STEP
            if not (span_lo <= ct <= span_hi) or er[i] is None:
                continue
            tot += 1
            if er[i] <= thr:
                rng += 1
            elif cl[i] > cl[i - ER_K]:
                bull += 1
            else:
                bear += 1
        if tot:
            pb, pr, pe = 100 * bull / tot, 100 * rng / tot, 100 * bear / tot
            print(f"  ER<={thr}:  bull {pb:4.1f}%   range {pr:4.1f}%   bear {pe:4.1f}%   (n={tot})")
            if thr == GATE_THR:
                nonbear_gate = (pb + pr)
    print(f"\n  GATE: non-bear (bull+range) at ER<={GATE_THR} = {nonbear_gate:.1f}%  "
          f"(threshold {GATE_MIN_NONBEAR*100:.0f}%)")
    if nonbear_gate is None or nonbear_gate < GATE_MIN_NONBEAR * 100:
        print("  >>> GATE FAILED: in-span tape is still mostly bear/quiet. STOPPING before PnL per")
        print("      directive — this is the case where the longer 30m-execution window earns its keep.")
        return
    print("  >>> GATE PASSED: real bull+range tape present. Proceeding to PnL.\n")

    cache = {}
    for thr in ER_THRS:
        for buf in BUFS:
            for k in KS:
                cache[(thr, buf, k)] = simulate(bx, bs, hi, lo, er, cb_ts, cb_close, thr, buf, k)

    g = lambda d: f"n={d[0]:<4} win{d[1]:<5} N={d[2]:+.3f} brk{d[3]:.0f}%" if d else "(none)"
    print("#" * 92)
    print("# HEADLINE — REPAINT TEST (k=0 vs k=1). Positive ONLY at k=0 => repaint, dead.")
    print("#" * 92)
    for thr in ER_THRS:
        for buf in BUFS:
            for side in ("sell", "buy"):
                a0, a1 = agg(cache[(thr, buf, 0)], side, 0, 9e12), agg(cache[(thr, buf, 1)], side, 0, 9e12)
                flag = ""
                if a0 and a1 and a0[2] > 0 >= a1[2]:
                    flag = "  <-- REPAINT (k0+ / k1<=0)"
                elif a1 and a1[2] > 0:
                    flag = "  <-- survives k=1"
                print(f"  ER<={thr} buf{buf*100:.2f}% {side:<4}  k0 {g(a0):<34} k1 {g(a1):<34}{flag}")
            print()

    print("#" * 92)
    print("# WALK-FORWARD (k=1, repaint-honest). TRAIN<=Mar1 | VALIDATE<=May1 ; brk%=structural-break stop-rate")
    print("#   * = positive net on BOTH train+validate (N>=20 / >=8) -> lockbox computed")
    print("#" * 92)
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
        print("  (none) -> even WITH bull+range tape the skill-faithful fade produced no positive-both")
        print("          candidate. That is a stronger (regime-fair) null than the bear-only run.")


if __name__ == "__main__":
    main()
