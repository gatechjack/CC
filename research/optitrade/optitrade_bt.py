"""
optitrade_bt.py -- Independent replication of the "OptiTrade" strategy + honest
backtest engine. Research only. Reads bars read-only; writes nothing to any DB.

Pine semantics replicated exactly (see STRATEGY SPEC in the task):
  fastEMA = EMA(close, L);  slowEMA = EMA(close, round(L*2.2))
  RSI(14), ATR(14)  -- Wilder's smoothing (Pine ta.rma), like Pine ta.rsi/ta.atr
  Long  = crossover(fast,slow)  AND fast>slow  AND RSI > 50+bias
  Short = crossunder(fast,slow) AND fast<slow  AND RSI < 50-bias
  Cooldown: >= minSep bars since last emitted same-direction signal (default 6)
  Entry at signal-bar CLOSE. One position at a time per symbol.
  R (risk) = slMult*ATR(14) at the entry bar.  SL = entry -/+ R.
  4 TP rungs at entry +/- RR*(i/4)*R, i=1..4, each closes 1/4; SL closes remainder.
  Intrabar ambiguity: SL-FIRST (conservative primary) or TP-FIRST (sensitivity).

EMA seed matches Pine ta.ema (first value = first source value, adjust=False).
RSI/ATR use Wilder's RMA with an SMA seed over the first `len` values.

All indicators are CAUSAL (value at bar i uses only bars <= i) -> no look-ahead.
Windowing (IS/OOS) confines trade management to the window's end bar, so an
in-sample trade can never realize its outcome using out-of-sample bars.
"""
import numpy as np
from numba import njit

# ----------------------------------------------------------------------------
# Indicators (JIT, causal, Pine-faithful)
# ----------------------------------------------------------------------------
@njit(fastmath=False)
def ema(src, length):
    n = src.shape[0]
    out = np.empty(n, np.float64)
    if n == 0:
        return out
    a = 2.0 / (length + 1.0)
    out[0] = src[0]
    for i in range(1, n):
        out[i] = a * src[i] + (1.0 - a) * out[i - 1]
    return out


@njit(fastmath=False)
def rsi_wilder(close, length):
    n = close.shape[0]
    out = np.full(n, np.nan)
    if n <= length:
        return out
    # gains/losses of price change
    gain = 0.0
    loss = 0.0
    for i in range(1, length + 1):
        ch = close[i] - close[i - 1]
        if ch > 0:
            gain += ch
        else:
            loss += -ch
    avg_gain = gain / length
    avg_loss = loss / length
    # first RSI at index = length
    if avg_loss == 0.0:
        out[length] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[length] = 100.0 - 100.0 / (1.0 + rs)
    for i in range(length + 1, n):
        ch = close[i] - close[i - 1]
        up = ch if ch > 0 else 0.0
        dn = -ch if ch < 0 else 0.0
        avg_gain = (avg_gain * (length - 1) + up) / length
        avg_loss = (avg_loss * (length - 1) + dn) / length
        if avg_loss == 0.0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


@njit(fastmath=False)
def atr_wilder(high, low, close, length):
    n = high.shape[0]
    out = np.full(n, np.nan)
    if n < length:
        return out
    tr = np.empty(n, np.float64)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        a = high[i] - low[i]
        b = abs(high[i] - close[i - 1])
        c = abs(low[i] - close[i - 1])
        m = a
        if b > m:
            m = b
        if c > m:
            m = c
        tr[i] = m
    # seed = SMA of first `length` TRs at index length-1
    s = 0.0
    for i in range(length):
        s += tr[i]
    out[length - 1] = s / length
    for i in range(length, n):
        out[i] = (out[i - 1] * (length - 1) + tr[i]) / length
    return out


@njit(fastmath=False)
def cross_arrays(fast, slow):
    """Return (cross_up, cross_dn) boolean arrays, Pine ta.crossover/crossunder."""
    n = fast.shape[0]
    cu = np.zeros(n, np.bool_)
    cd = np.zeros(n, np.bool_)
    for i in range(1, n):
        if fast[i] > slow[i] and fast[i - 1] <= slow[i - 1]:
            cu[i] = True
        if fast[i] < slow[i] and fast[i - 1] >= slow[i - 1]:
            cd[i] = True
    return cu, cd


# ----------------------------------------------------------------------------
# Signal generation with cooldown (JIT). Cooldown is per-window (resets at start).
# Emission is independent of position state (spec: "since last same-dir signal").
# sig_dir[i] = +1 long entry-signal, -1 short, 0 none.
# ----------------------------------------------------------------------------
@njit(fastmath=False)
def gen_signals(cu, cd, rsi, fast, slow, bias, minSep, start, end):
    n = fast.shape[0]
    sig = np.zeros(n, np.int8)
    last_long = -1_000_000_000
    last_short = -1_000_000_000
    lo = 50.0 - bias
    hi = 50.0 + bias
    for i in range(start, end):
        r = rsi[i]
        if r != r:   # nan
            continue
        if cu[i] and fast[i] > slow[i] and r > hi:
            if i - last_long >= minSep:
                sig[i] = 1
                last_long = i
        elif cd[i] and fast[i] < slow[i] and r < lo:
            if i - last_short >= minSep:
                sig[i] = -1
                last_short = i
    return sig


# ----------------------------------------------------------------------------
# Position simulator (JIT). One position at a time. Management confined to [start,end).
# Returns per-trade arrays. exit_reason: 1=SL, 2=all-TP, 3=EOD mark-to-market.
# ----------------------------------------------------------------------------
@njit(fastmath=False)
def simulate(o, h, l, c, atr, sig, slMult, RR, start, end, sl_first):
    n = c.shape[0]
    if end > n:
        end = n
    cap = 0
    for i in range(start, end):
        if sig[i] != 0:
            cap += 1
    t_entry_idx = np.empty(cap, np.int64)
    t_dir = np.empty(cap, np.int8)
    t_grossR = np.empty(cap, np.float64)
    t_entry_px = np.empty(cap, np.float64)
    t_exit_notional = np.empty(cap, np.float64)
    t_risk_px = np.empty(cap, np.float64)
    t_ntp = np.empty(cap, np.int8)
    t_reason = np.empty(cap, np.int8)
    t_exit_idx = np.empty(cap, np.int64)
    nt = 0

    i = start
    while i < end:
        d = sig[i]
        if d == 0:
            i += 1
            continue
        a = atr[i]
        if not (a > 0.0):        # nan or non-positive ATR -> cannot size
            i += 1
            continue
        entry = c[i]
        Rdist = slMult * a
        SL = entry - d * Rdist
        rem = 1.0
        grossR = 0.0
        exit_notional = 0.0
        ntp = 0
        k_next = 1               # next TP rung 1..4
        reason = 3
        exit_idx = end - 1
        j = i + 1
        while j < end:
            hi_j = h[j]
            lo_j = l[j]
            stop_hit = (lo_j <= SL) if d > 0 else (hi_j >= SL)
            if sl_first:
                if stop_hit:
                    grossR += rem * (-1.0)
                    exit_notional += rem * SL
                    reason = 1
                    exit_idx = j
                    rem = 0.0
                    break
                while k_next <= 4:
                    tp = entry + d * (RR * (k_next / 4.0)) * Rdist
                    hit = (hi_j >= tp) if d > 0 else (lo_j <= tp)
                    if hit:
                        grossR += 0.25 * (RR * (k_next / 4.0))
                        exit_notional += 0.25 * tp
                        rem -= 0.25
                        ntp += 1
                        k_next += 1
                    else:
                        break
                if k_next > 4:
                    reason = 2
                    exit_idx = j
                    rem = 0.0
                    break
            else:  # TP-first
                while k_next <= 4:
                    tp = entry + d * (RR * (k_next / 4.0)) * Rdist
                    hit = (hi_j >= tp) if d > 0 else (lo_j <= tp)
                    if hit:
                        grossR += 0.25 * (RR * (k_next / 4.0))
                        exit_notional += 0.25 * tp
                        rem -= 0.25
                        ntp += 1
                        k_next += 1
                    else:
                        break
                if k_next > 4:
                    reason = 2
                    exit_idx = j
                    rem = 0.0
                    break
                if stop_hit:
                    grossR += rem * (-1.0)
                    exit_notional += rem * SL
                    reason = 1
                    exit_idx = j
                    rem = 0.0
                    break
            j += 1
        if rem > 0.0:            # still open at window end -> MTM at last close
            lastc = c[end - 1]
            grossR += rem * ((lastc - entry) / Rdist) * d
            exit_notional += rem * lastc
            reason = 3
            exit_idx = end - 1
            rem = 0.0

        t_entry_idx[nt] = i
        t_dir[nt] = d
        t_grossR[nt] = grossR
        t_entry_px[nt] = entry
        t_exit_notional[nt] = exit_notional
        t_risk_px[nt] = Rdist
        t_ntp[nt] = ntp
        t_reason[nt] = reason
        t_exit_idx[nt] = exit_idx
        nt += 1
        i = exit_idx + 1         # one position at a time

    return (t_entry_idx[:nt], t_dir[:nt], t_grossR[:nt], t_entry_px[:nt],
            t_exit_notional[:nt], t_risk_px[:nt], t_ntp[:nt], t_reason[:nt],
            t_exit_idx[:nt])


# ----------------------------------------------------------------------------
# Metrics (pure numpy). Fees expressed in R using each trade's own risk unit.
# ----------------------------------------------------------------------------
def metrics(trades, fee_rates=(0.0006, 0.0004)):
    (eidx, d, grossR, entry_px, exit_notional, risk_px, ntp, reason, xidx) = trades
    n = grossR.shape[0]
    out = {"n": int(n)}
    if n == 0:
        out.update(dict(wr=None, avgR=None, sumR=0.0, pf=None, maxdd=0.0))
        for f in fee_rates:
            out[f"net_sumR_{f}"] = 0.0
        return out
    wins = grossR > 0
    out["wr"] = float(wins.mean())
    out["avgR"] = float(grossR.mean())
    out["sumR"] = float(grossR.sum())
    pos = grossR[grossR > 0].sum()
    neg = -grossR[grossR < 0].sum()
    out["pf"] = float(pos / neg) if neg > 0 else float("inf")
    # max drawdown of the cumulative-R equity curve (in R)
    eq = np.cumsum(grossR)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    out["maxdd"] = float(dd.min())          # <= 0
    # fees -> R
    for f in fee_rates:
        fee_price = f * (entry_px + exit_notional)   # both sides
        fee_R = fee_price / risk_px
        out[f"net_sumR_{f}"] = float((grossR - fee_R).sum())
    return out
