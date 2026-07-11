"""One-sided/trailing-confirm Pagan-Sossounov DAILY trend classifier as the SFP construct's
with-trend DIRECTION gate — the DAILY analog of ``bitunix_rd_trend`` (the 1h RD break-state gate).

This is the ENGINE port of the ``ps-trail30`` arm from the causal-macro60 study
(``Desktop/backtest_corpus/_sfp_causal_macro60.py``): a STRICTLY CAUSAL, live-computable daily trend
classifier that approximates the NON-CAUSAL macro60 gate. ps-trail30 is the CAUSAL WINNER of that
study family (pooled +0.128 avgR; BTC +0.067). GROSS / in-sample Binance-perp proxy — a LEAD, not an
OOS edge; the live SFP log is the arbiter. (macro60 itself is non-causal hindsight dating and is NOT
live-recoverable in full — ps_trail30 keeps the causally-knowable part.)

``st_ps_trail`` is copied VERBATIM from ``_sfp_causal_macro60.st_ps_trail`` (with its two argmin/argmax
helpers) so the parity gate (``_ps_trail30_parity.py``) can prove the engine reproduces the researched
ps-trail30 arm bar-for-bar (0 diffs) — the engine port is byte-faithful to the researched code.

CAUSALITY (the trap that bit the VWAP fade): ``ps_trail30_label_at`` returns the state AFTER the CLOSE
of the PRIOR trading day (st[D-1]) — day D's own daily bar is INVISIBLE to an entry during day D. It
reads the PERSISTENT ``bitunix_bar_history`` (timeframe='1d', symbol-filtered, ALL rows ASC) — the SAME
persistent table RD uses — NOT the in-memory live cache. Returns None during warmup (state still None)
or when no daily bar opens on/before entry_ts; the observer then sits the trade out (audit
``sfp_skip_ps_no_data``) rather than firing blind. Fail-soft: None on any DB/compute error.

DAILY-DEPTH: st_ps_trail is STATEFUL, but the trailing-confirm resets bound the path dependence — a
depth study (``_ps_trail30_depth.py``) proved prod's 257-bar BTC 1d series reproduces the full-corpus
st_ps_trail(30) labels on every shared non-warmup day (0 diffs; latest label converges by >=60 bars).
So the persistent 257-day series is sufficient; no backfill is required for BTC.
"""
from __future__ import annotations

import logging
from bisect import bisect_right

from trading_corp.persistence import db

log = logging.getLogger(__name__)

# Trailing-confirm window (days). N=30 — the deployed ps-trail30 arm.
PS_TRAIL_N = 30


# ===================== VERBATIM port of _sfp_causal_macro60.{_argmax,_argmin,st_ps_trail} =====================
def _argmax(vals, lo, hi):
    bi, bv = lo, vals[lo]
    for j in range(lo + 1, hi + 1):
        if vals[j] > bv:
            bi, bv = j, vals[j]
    return bi, bv


def _argmin(vals, lo, hi):
    bi, bv = lo, vals[lo]
    for j in range(lo + 1, hi + 1):
        if vals[j] < bv:
            bi, bv = j, vals[j]
    return bi, bv


def st_ps_trail(closes, N):
    """Trailing-confirm state machine: a peak is confirmed when N days pass without a new high
    above it (symmetric troughs). state = bear after confirmed peak, bull after confirmed trough.
    NO neutral state (documented). Causal: only closes[0..i] read at day i."""
    n = len(closes)
    st = [None] * n
    state = None
    hi_i, hi_v = 0, closes[0]
    lo_i, lo_v = 0, closes[0]
    for i in range(1, n):
        c = closes[i]
        if state is None:
            if c > hi_v:
                hi_i, hi_v = i, c
            if c < lo_v:
                lo_i, lo_v = i, c
            peak_ok = (i - hi_i) >= N
            trough_ok = (i - lo_i) >= N
            if peak_ok and trough_ok:
                if hi_i > lo_i:          # more recent extremum wins; equal -> stay undetermined
                    state = "bear"
                    lo_i, lo_v = _argmin(closes, hi_i + 1, i)
                elif lo_i > hi_i:
                    state = "bull"
                    hi_i, hi_v = _argmax(closes, lo_i + 1, i)
            elif peak_ok:
                state = "bear"
                lo_i, lo_v = _argmin(closes, hi_i + 1, i)
            elif trough_ok:
                state = "bull"
                hi_i, hi_v = _argmax(closes, lo_i + 1, i)
        elif state == "bull":
            if c > hi_v:
                hi_i, hi_v = i, c        # new high resets the clock
            elif (i - hi_i) >= N:
                state = "bear"
                lo_i, lo_v = _argmin(closes, hi_i + 1, i)
        else:                            # bear
            if c < lo_v:
                lo_i, lo_v = i, c
            elif (i - lo_i) >= N:
                state = "bull"
                hi_i, hi_v = _argmax(closes, lo_i + 1, i)
        st[i] = state
    return st


# ===================== causal engine lookup (reads the PERSISTENT bar history) =====================
def _load_1d_closes(db_url: str, symbol: str):
    """Read the FULL DAILY close series for `symbol` from bitunix_bar_history (persistent — the same
    table RD uses; NOT the live cache), ascending by ts_ms. Returns (day_ts, closes) parallel lists.
    ``day_ts[i]`` = the daily bar's OPEN ts (matches the backtest's ``day_ts=[b.ts_ms]``). Symbol is the
    WIRE form ('BTCUSDT'). Fail-soft: () on any error."""
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT ts_ms, close FROM bitunix_bar_history "
            "WHERE symbol=? AND timeframe='1d' AND close IS NOT NULL "
            "ORDER BY ts_ms ASC",
            (symbol,),
        ).fetchall()
    day_ts = [int(r[0]) for r in rows]
    closes = [float(r[1]) for r in rows]
    return day_ts, closes


def ps_trail30_label_at(db_url: str, symbol: str, entry_ts: int):
    """CAUSAL ps-trail30 daily trend label for a with-trend gate decision at `entry_ts` (ms).

    Reads the DAILY close series for `symbol` (WIRE form) from bitunix_bar_history, runs
    ``st_ps_trail(closes, 30)`` (verbatim research port), and returns the state of the PRIOR trading day
    — st[j-1] where j = index of the entry's own day bar = ``bisect_right(day_ts, entry_ts) - 1`` (the
    largest daily bar whose OPEN ts <= entry_ts). This is EXACTLY the backtest's ``build_gate_map``
    mapping ``day_ts[d] -> st[d-1]``: day D's own daily bar is INVISIBLE (strictly stale, D-1 confirmed).

      "bull" -> the prior day's confirmed daily trend is up   (long with-trend)
      "bear" -> down                                          (short with-trend)

    Returns None when there is no prior day (j < 1) OR the prior state is still warmup (None). Fail-soft:
    returns None on any DB/compute error (the observer then sits the trade out via sfp_skip_ps_no_data —
    never fires blind on a missing/short series or an unconfirmed state)."""
    try:
        day_ts, closes = _load_1d_closes(db_url, symbol)
    except Exception as e:                                   # fail-soft — never into the trade path
        log.warning("bitunix_ps_trail_trend: 1d load failed for %s (label=None -> sit out): %s",
                    symbol, e)
        return None
    if len(closes) < 2:
        return None
    try:
        st = st_ps_trail(closes, PS_TRAIL_N)
    except Exception as e:                                   # fail-soft — never into the trade path
        log.warning("bitunix_ps_trail_trend: st_ps_trail compute failed for %s (label=None): %s",
                    symbol, e)
        return None
    # j = the entry's own day bar (largest daily OPEN ts <= entry_ts). The gate uses st[j-1] (D-1).
    j = bisect_right(day_ts, entry_ts) - 1
    if j < 1:
        return None
    return st[j - 1]
