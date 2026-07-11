"""LuxAlgo Range-Detector break-state as the SFP construct's with-trend DIRECTION gate.

This is the ENGINE port of the RD-trend arm that won the with-trend gate bake-off
(``Desktop/backtest_corpus/_sfp_trend_gate_bakeoff.py``): pooled +0.095 avgR over the
live tight ema200 gate (RD wins BTC/ETH/XRP; ema200 wins SOL — hence the deploy is a
per-coin ``trend_mode`` map, NOT a universal swap). GROSS / in-sample Binance-perp
proxy — a LEAD, not an OOS edge (RD sits at the 66th pctile of its own drift-null); the
live SFP log is the arbiter.

``range_detector`` + ``sma_trailing`` + ``atr_wilder`` are copied BYTE-FOR-BYTE from the
bake-off (which itself copied them verbatim from ``_classifier_bakeoff``) so the parity
gate (``_rd_gate_parity.py``) can import THIS module's ``range_detector`` and reproduce
the bake-off arm exactly — proving the engine port is identical to the researched code.

CAUSALITY (the trap that bit the VWAP fade): ``rd_os_at`` looks up the break-state os of
the LAST 1h bar whose bar CLOSED strictly BEFORE the entry timestamp (close_ts = ts_ms +
3_600_000). No look-ahead. It reads the PERSISTENT ``bitunix_bar_history`` (timeframe='1h',
symbol-filtered, ALL rows ASC) — NOT the ~200-cap in-memory live cache — so the ATR(500)
warmup is satisfied (needs >=520 bars). Returns None if <520 bars or no causal bar; the
observer sits the trade out (audit ``sfp_skip_rd_no_data``) rather than firing blind.

The archiver (main.py) must keep 1h bars flowing for ETH/SOL/XRP into bitunix_bar_history
(PREREQ 1(b)); BTC 1h was already archived, the other three are the archiver extension.
"""
from __future__ import annotations

import logging

from trading_corp.persistence import db

log = logging.getLogger(__name__)

# Range-Detector params — EXACTLY the ones that won the classifier + trend-gate bake-offs.
RD_LEN = 20
RD_ATRLEN = 500
RD_MULT = 1.0
TF_1H_MS = 3_600_000
# ATR(500) needs 501 bars to seed; +19 more for the first non-warmup SMA(20)-containment
# check. Below RD_MIN_BARS os is meaningless (all warmup) -> rd_os_at returns None.
RD_MIN_BARS = RD_ATRLEN + RD_LEN   # 520


# ===================== LuxAlgo Range-Detector (VERBATIM from _sfp_trend_gate_bakeoff) =====================
def sma_trailing(x, L):
    n = len(x); out = [None] * n; s = 0.0; cnt = 0
    for t in range(n):
        v = x[t]
        if v is None:
            out[t] = None; continue
        s += v; cnt += 1
        if cnt > L:
            s -= x[t - L]; cnt -= 1
        if cnt >= L:
            out[t] = s / L
    return out


def atr_wilder(h, l, c, L):
    n = len(c); out = [None] * n
    if n < L + 1:
        return out
    tr = [0.0] * n
    for t in range(1, n):
        tr[t] = max(h[t] - l[t], abs(h[t] - c[t - 1]), abs(l[t] - c[t - 1]))
    a = sum(tr[1:L + 1]) / L; out[L] = a
    for t in range(L + 1, n):
        a = (a * (L - 1) + tr[t]) / L; out[t] = a
    return out


def range_detector(h, l, c, length=RD_LEN, atr_len=RD_ATRLEN, mult=RD_MULT):
    """LuxAlgo Range Detector. RANGE at t when all last `length` closes within mult*ATR(atr_len) of
    SMA(close,length) (count==0). Causal. Returns (labels in {range,trend,warmup}, latching break-state os).
    os: +1 close above range top (up-break), -1 below bottom (down-break), 0 fresh/unbroken. LATCHES."""
    n = len(c)
    ma = sma_trailing(c, length)
    atr = atr_wilder(h, l, c, atr_len)
    lab = [None] * n; osl = [0] * n
    cur_os = 0; top = bot = None
    for t in range(n):
        if ma[t] is None or atr[t] is None or t < length - 1:
            lab[t] = "warmup"; osl[t] = cur_os; continue
        band = mult * atr[t]
        cnt = sum(1 for i in range(t - length + 1, t + 1) if abs(c[i] - ma[t]) > band)
        if cnt == 0:
            lab[t] = "range"; top = ma[t] + band; bot = ma[t] - band; cur_os = 0
        else:
            lab[t] = "trend"
            if top is not None:
                if c[t] > top:
                    cur_os = 1
                elif c[t] < bot:
                    cur_os = -1
        osl[t] = cur_os
    return lab, osl


# ===================== causal engine lookup (reads the PERSISTENT bar history) =====================
def _load_1h_series(db_url: str, symbol: str):
    """Read the FULL 1h OHLC series for `symbol` from bitunix_bar_history (persistent, NOT the
    ~200-cap live cache), ascending by ts_ms. Returns (ts_ms, h, l, c) parallel lists. Symbol is the
    WIRE form ('BTCUSDT'). ALL rows — RD's ATR(500) needs the depth. Fail-soft: () on any error."""
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT ts_ms, high, low, close FROM bitunix_bar_history "
            "WHERE symbol=? AND timeframe='1h' AND close IS NOT NULL "
            "ORDER BY ts_ms ASC",
            (symbol,),
        ).fetchall()
    ts = [int(r[0]) for r in rows]
    h = [float(r[1]) for r in rows]
    l = [float(r[2]) for r in rows]
    c = [float(r[3]) for r in rows]
    return ts, h, l, c


def rd_os_at(db_url: str, symbol: str, entry_ts: int):
    """CAUSAL RD break-state os for a with-trend gate decision at `entry_ts` (ms).

    Reads the 1h series for `symbol` (WIRE form) from bitunix_bar_history, computes the latching
    break-state os on the whole series, and returns the os of the LAST 1h bar whose bar CLOSED
    (close_ts = ts_ms + 3_600_000) STRICTLY BEFORE entry_ts — no look-ahead.

      os == +1  -> last completed 1h broke UP out of its range   (long with-trend)
      os == -1  -> broke DOWN out of its range                   (short with-trend)
      os ==  0  -> ranging / fresh / unbroken                    (sit the trade out)

    Returns None when the series has < RD_MIN_BARS (520) bars (ATR(500) not warmed) OR no 1h bar has
    closed before entry_ts. Fail-soft: returns None on any DB/compute error (the observer then sits
    the trade out via sfp_skip_rd_no_data — never fires blind on a missing/short series)."""
    try:
        ts, h, l, c = _load_1h_series(db_url, symbol)
    except Exception as e:                                   # fail-soft — never into the trade path
        log.warning("bitunix_rd_trend: 1h load failed for %s (os=None -> sit out): %s", symbol, e)
        return None
    if len(c) < RD_MIN_BARS:
        return None
    _lab, osl = range_detector(h, l, c)
    # close_ts[i] = ts[i] + TF_1H_MS, ascending. Want the last bar closed STRICTLY before entry_ts =
    # the greatest close_ts < entry_ts. bisect_right(close_ts, entry_ts-1)-1 == the bake-off's
    # bisect_right(close_ts, entry_ts)-1 for a bar that closes AT entry_ts, but excludes an exact tie
    # so a bar whose close lands exactly on the entry timestamp is NOT peeked.
    from bisect import bisect_right
    close_ts = [t + TF_1H_MS for t in ts]
    j = bisect_right(close_ts, entry_ts - 1) - 1            # strictly < entry_ts
    if j < 0:
        return None
    return osl[j]
