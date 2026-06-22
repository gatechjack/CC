"""Scan bars_3m for the most-BULLISH and highest-VOL ~2-week sub-window.

Read-only over the clean corpus. For each rolling 2-week window (step 7d),
compute close-to-close % change (bullishness) and realized vol (std of 3m
log returns, annualised-agnostic — relative ranking only). Reports the top
windows so the /goal can run the cap sweep in the most-bull / highest-vol slice
WITHOUT manufacturing a regime: if the whole corpus is bear/neutral, the
"most bullish" window may still be slightly negative — that is reported plainly.
"""
from __future__ import annotations

import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from scripts.run_redeem_sim import _resolve_db  # noqa: E402


def _load(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT ts, close FROM bars_3m ORDER BY ts").fetchall()
    finally:
        con.close()
    return [(datetime.fromtimestamp(ts, tz=timezone.utc), c) for ts, c in rows]


def _window_stats(rows, s, e):
    seg = [(ts, c) for ts, c in rows if s <= ts < e and c and c > 0]
    if len(seg) < 50:
        return None
    first_c, last_c = seg[0][1], seg[-1][1]
    pct = (last_c / first_c - 1.0) * 100.0
    rets = []
    for i in range(1, len(seg)):
        p0, p1 = seg[i - 1][1], seg[i][1]
        if p0 > 0 and p1 > 0:
            rets.append(math.log(p1 / p0))
    mu = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mu) ** 2 for r in rets) / len(rets) if rets else 0.0
    vol_per_bar = math.sqrt(var)
    # scale to a per-2-week realized vol figure (sqrt of bar count) for intuition
    vol_window = vol_per_bar * math.sqrt(len(rets)) * 100.0
    hi = max(c for _, c in seg)
    lo = min(c for _, c in seg)
    range_pct = (hi / lo - 1.0) * 100.0 if lo > 0 else 0.0
    return {
        "start": s.date().isoformat(), "end": e.date().isoformat(),
        "n_bars": len(seg), "pct_change": pct,
        "vol_per_bar_pct": vol_per_bar * 100.0,
        "vol_window_pct": vol_window, "range_pct": range_pct,
        "first": first_c, "last": last_c,
    }


def main():
    db = _resolve_db(None)
    rows = _load(db)
    span_s, span_e = rows[0][0], rows[-1][0]
    print(f"corpus span: {span_s.date()} .. {span_e.date()}  ({len(rows)} bars_3m)")
    win = timedelta(days=14)
    step = timedelta(days=7)
    stats = []
    s = datetime(span_s.year, span_s.month, span_s.day, tzinfo=timezone.utc)
    while s + win <= span_e + timedelta(days=1):
        st = _window_stats(rows, s, s + win)
        if st:
            stats.append(st)
        s += step
    print(f"\n{len(stats)} rolling 2-week windows (step 7d):")
    print(f"{'window':<26}{'pct_chg':>9}{'vol/bar%':>10}{'vol_win%':>10}{'range%':>9}")
    for st in stats:
        print(f"{st['start']}..{st['end']:<14}{st['pct_change']:>+9.2f}"
              f"{st['vol_per_bar_pct']:>10.4f}{st['vol_window_pct']:>10.2f}"
              f"{st['range_pct']:>9.2f}")
    bull = max(stats, key=lambda x: x["pct_change"])
    volw = max(stats, key=lambda x: x["vol_per_bar_pct"])
    print(f"\nMOST BULLISH : {bull['start']}..{bull['end']}  "
          f"pct_change={bull['pct_change']:+.2f}%  vol/bar={bull['vol_per_bar_pct']:.4f}%")
    print(f"HIGHEST VOL  : {volw['start']}..{volw['end']}  "
          f"vol/bar={volw['vol_per_bar_pct']:.4f}%  pct_change={volw['pct_change']:+.2f}%")
    n_up = sum(1 for x in stats if x["pct_change"] > 0)
    print(f"\nwindows net-UP: {n_up}/{len(stats)}  "
          f"(corpus is {'mixed' if 0 < n_up < len(stats) else ('bull' if n_up else 'bear')})")


if __name__ == "__main__":
    main()
