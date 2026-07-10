"""Institutional-levels tagger for the bitunix_sfp fresh-institutional gate (L3).

Port of the validated research tagger (``_inst_levels.InstLevels``) that produced the
fresh-institutional attribution (+0.066) in the two-candle SFP construct. At each bar it
resolves the ACTIVE prior-period institutional levels (PDH/PDL, PWH/PWL, PMH/PML, session
highs/lows — PRIOR completed period only, no look-ahead), tags a swept level as
``at_institutional`` when it sits within ``0.15 * ATR15`` of one, and marks it fresh/broken.

Only the ``tag()`` path is ported (the research Level-Hold engine and opposing-TP helper are
analytics, not needed by the gate). Reuses the prod ``bitunix_htf_regime.current_session``;
the Wilder ATR series is ported verbatim from the research ``_key_levels._atr_series`` so the
0.15xATR tolerance is byte-identical to what was backtested. Pure stdlib + one prod import.
"""
from __future__ import annotations

import datetime
from bisect import bisect_left, bisect_right
from collections import OrderedDict

from trading_corp.agents.strategies.bitunix_htf_regime import current_session

DAY_MS, MS15 = 86_400_000, 900_000
TOL_ATR = 0.15                                # 0.15 * ATR15 institutional-proximity tolerance
TIER_RANK = {"monthly": 4, "weekly": 3, "daily": 2, "session": 1}
UTC = datetime.timezone.utc


def _atr_series(highs, lows, closes, period: int = 14):
    """Wilder ATR series (SMA-seeded). Verbatim port of research _key_levels._atr_series."""
    n = len(closes)
    out = [None] * n
    if n < period + 1:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    a = sum(trs[1:period + 1]) / period
    out[period] = a
    for i in range(period + 1, n):
        a = (a * (period - 1) + trs[i]) / period
        out[i] = a
    return out


def _dt(ts):
    return datetime.datetime.fromtimestamp(ts / 1000, tz=UTC)


def _iso_week(ts):
    return _dt(ts).strftime("%G-%V")


def _month(ts):
    return _dt(ts).strftime("%Y-%m")


def _period_day_map(bars1d, keyfn):
    """day_start_ms -> {'H','L','O','start'} of the PRIOR completed period (week/month)."""
    groups = OrderedDict()
    for b in bars1d:
        groups.setdefault(keyfn(b.ts_ms), []).append(b)
    keys = list(groups)
    out = {}
    for gi in range(1, len(keys)):
        prev, cur = groups[keys[gi - 1]], groups[keys[gi]]
        H = max(x.high for x in prev)
        Lo = min(x.low for x in prev)
        O = cur[0].open
        start = cur[0].ts_ms
        for b in cur:
            out[b.ts_ms] = {"H": H, "L": Lo, "O": O, "start": start}
    return out


class InstLevels:
    """Build once from a 15m OHLC window + a 1d OHLC window; then ``tag(P, ts, side)``."""

    def __init__(self, coin, bars15, bars1d):
        self.coin = coin
        self.bars15 = bars15
        self.ts15 = [b.ts_ms for b in bars15]
        self.atr15 = _atr_series([b.high for b in bars15], [b.low for b in bars15],
                                 [b.close for b in bars15])
        self.daily = {bars1d[i].ts_ms: {"PDH": bars1d[i - 1].high, "PDL": bars1d[i - 1].low,
                                        "Dopen": bars1d[i].open} for i in range(1, len(bars1d))}
        self.weekly = _period_day_map(bars1d, _iso_week)
        self.monthly = _period_day_map(bars1d, _month)
        sess = []
        cur = cur_key = None
        for b in bars15:
            k = (b.ts_ms // DAY_MS, current_session(_dt(b.ts_ms)).value)
            if k != cur_key:
                if cur:
                    sess.append((cur["last"] + MS15, cur["hi"], cur["lo"], cur["start"]))
                cur_key = k
                cur = {"hi": b.high, "lo": b.low, "start": b.ts_ms, "last": b.ts_ms}
            else:
                cur["hi"] = max(cur["hi"], b.high)
                cur["lo"] = min(cur["lo"], b.low)
                cur["last"] = b.ts_ms
        if cur:
            sess.append((cur["last"] + MS15, cur["hi"], cur["lo"], cur["start"]))
        sess.sort()
        self.sessions = sess
        self.sess_end = [s[0] for s in sess]

    def atr_at(self, ts):
        i = bisect_right(self.ts15, ts) - 1
        for j in range(i, max(-1, i - 20), -1):
            if 0 <= j < len(self.atr15) and self.atr15[j]:
                return self.atr15[j]
        return None

    def active_levels(self, ts, side):
        """Side-matched active institutional levels at ts (long->low-side, short->high-side;
        opens both). Returns list of (price, kind, tier, active_start)."""
        out = []
        day = ts - ts % DAY_MS
        d = self.daily.get(day)
        if d:
            out.append((d["PDL"] if side == "long" else d["PDH"],
                        "PDL" if side == "long" else "PDH", "daily", day))
            out.append((d["Dopen"], "Dopen", "daily", day))
        w = self.weekly.get(day)
        if w:
            out.append((w["L"] if side == "long" else w["H"],
                        "PWL" if side == "long" else "PWH", "weekly", w["start"]))
            out.append((w["O"], "Wopen", "weekly", w["start"]))
        m = self.monthly.get(day)
        if m:
            out.append((m["L"] if side == "long" else m["H"],
                        "PML" if side == "long" else "PMH", "monthly", m["start"]))
            out.append((m["O"], "Mopen", "monthly", m["start"]))
        lo = bisect_left(self.sess_end, ts - DAY_MS)
        hi = bisect_right(self.sess_end, ts)
        for (end_ts, shi, slo, sstart) in self.sessions[lo:hi]:
            out.append((slo if side == "long" else shi,
                        "session_low" if side == "long" else "session_high", "session", end_ts))
        return out

    def _fresh(self, price, active_start, ts, side, tol):
        lo = bisect_left(self.ts15, active_start)
        hi = bisect_right(self.ts15, ts)
        for j in range(lo, hi):
            c = self.bars15[j].close
            if side == "long" and c < price - tol:
                return "broken"
            if side == "short" and c > price + tol:
                return "broken"
        return "fresh"

    def tag(self, P, ts, side):
        """Tag swept level P at ts/side: {at_institutional, kinds, tier, freshness}."""
        atr = self.atr_at(ts) or 0.0
        tol = TOL_ATR * atr
        if tol <= 0:
            return {"at_institutional": False, "kinds": "", "tier": "", "freshness": ""}
        matches = [(price, k, t, st) for (price, k, t, st) in self.active_levels(ts, side)
                   if abs(P - price) <= tol]
        if not matches:
            return {"at_institutional": False, "kinds": "", "tier": "", "freshness": ""}
        kinds = sorted(set(k for _p, k, _t, _s in matches))
        best = max(matches, key=lambda m: TIER_RANK[m[2]])
        tier = best[2]
        fresh = self._fresh(best[0], best[3], ts, side, tol)
        return {"at_institutional": True, "kinds": "|".join(kinds), "tier": tier, "freshness": fresh}
