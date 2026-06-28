"""ORB round-1 — bare mechanical core on ETHUSDT, PRE-REGISTERED, read-only.

Tests ONLY the 9:30-ET opening-range box breakout. No confluence, no filter, no
optimization. Long & short reported SEPARATELY. k=1 (no look-ahead) and DST-aware
US-Eastern wall-clock anchoring are proven in-code.

Rules (locked): box = high/low of the 09:30 ET 15m candle (US equity open),
DST-aware via zoneinfo, weekdays only. Entry = breakout CLOSE outside the box,
first valid per day, entered at the NEXT 15m bar's open (k=1). Stop = opposite
box side. Target = 2R primary (+ N*box-height secondary). Intraday; EOD timeout
at 16:00 ET. Bitunix corrected fee model.
"""
from __future__ import annotations

import asyncio
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_corp.agents.paper_trade_replay import _bitunix_kline_fetcher

NY = ZoneInfo("America/New_York")
SINCE_MS = 1704067200000          # 2024-01-01 UTC
TF_MS = 900_000                   # 15m
# Bitunix corrected fee model (same as the SFP p6 model).
ENTRY_FEE, MK, TK, SLIP = 0.000243, 0.00014, 0.0004, 0.0001
SESSION_OPEN_MIN = 9 * 60 + 30    # 09:30 ET (box)
SESSION_FIRST_BREAK_MIN = 9 * 60 + 45   # 09:45 ET (first eligible breakout bar)
SESSION_LAST_MIN = 15 * 60 + 45   # 15:45 ET bar (closes 16:00 — last in-session)


def _mins(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


async def load_bars() -> list[dict]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    limit = (now_ms - SINCE_MS) // TF_MS + 200
    raw = await _bitunix_kline_fetcher("ETHUSDT", "15m", SINCE_MS, limit)
    seen = {}
    for k in raw:
        seen[int(k[0])] = k          # dedupe by ts_ms
    bars = []
    for ts in sorted(seen):
        k = seen[ts]
        utc = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        ny = utc.astimezone(NY)
        bars.append({
            "ts": ts, "utc": utc, "ny": ny,
            "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
            "c": float(k[4]),
        })
    return bars


def group_sessions(bars: list[dict]) -> dict:
    """Group bars by NY calendar date (weekdays only)."""
    by_day: dict = defaultdict(list)
    for b in bars:
        ny = b["ny"]
        if ny.weekday() >= 5:        # 5,6 = Sat,Sun — no US equity open
            continue
        by_day[ny.date()].append(b)
    return by_day


def fee_in_r(entry: float, r_dist: float, outcome: str) -> float:
    exit_fee = MK if outcome == "tp" else TK
    return (ENTRY_FEE + exit_fee + SLIP) / (r_dist / entry)


def resolve(side: str, entry: float, stop: float, tp: float, r_dist: float,
            walk: list[dict]) -> tuple[str, float]:
    """Return (outcome, gross_R). SL-first on a both-hit bar (conservative)."""
    for b in walk:
        if side == "long":
            if b["l"] <= stop:
                return "sl", -1.0
            if b["h"] >= tp:
                return "tp", (tp - entry) / r_dist
        else:
            if b["h"] >= stop:
                return "sl", -1.0
            if b["l"] <= tp:
                return "tp", (entry - tp) / r_dist
    last = walk[-1]["c"]
    gross = (last - entry) / r_dist if side == "long" else (entry - last) / r_dist
    return "to", gross


def run():
    bars = asyncio.run(load_bars())
    print(f"DATA: ETHUSDT 15m  bars={len(bars)}  "
          f"span={bars[0]['ny'].date()} .. {bars[-1]['ny'].date()}")
    by_day = group_sessions(bars)

    trades = []                      # each: dict with side, box_pct, R metrics
    n_box = n_entry = n_up = n_down = n_held = 0
    dst_samples = []
    k1_violations = 0

    for day in sorted(by_day):
        day_bars = sorted(by_day[day], key=lambda b: b["ts"])
        box = next((b for b in day_bars if _mins(b["ny"]) == SESSION_OPEN_MIN), None)
        if box is None:
            continue                 # no 09:30 ET bar this day (gap) — skip
        n_box += 1
        box_hi, box_lo = box["h"], box["l"]
        box_height = box_hi - box_lo
        # in-session post-box bars: ET 09:45 .. 15:45, ordered
        session = [b for b in day_bars
                   if SESSION_FIRST_BREAK_MIN <= _mins(b["ny"]) <= SESSION_LAST_MIN]
        session.sort(key=lambda b: b["ts"])
        # DST sample capture (a few representative days)
        if len(dst_samples) < 40:
            dst_samples.append((day, box["utc"].strftime("%H:%M UTC"),
                                box["ny"].strftime("%H:%M ET %Z")))

        # first valid breakout-close
        brk_idx = None
        side = None
        for i, b in enumerate(session):
            if b["c"] > box_hi:
                brk_idx, side = i, "long"
                break
            if b["c"] < box_lo:
                brk_idx, side = i, "short"
                break
        if brk_idx is None:
            n_held += 1
            continue
        if brk_idx + 1 >= len(session):
            n_held += 1              # break on the last bar — no next-open entry
            continue
        # k=1: entry = OPEN of the bar AFTER the breakout-close bar
        entry_bar = session[brk_idx + 1]
        if entry_bar["ts"] != session[brk_idx]["ts"] + TF_MS and \
           session.index(entry_bar) != brk_idx + 1:
            k1_violations += 1
        entry = entry_bar["o"]
        n_entry += 1
        if side == "long":
            n_up += 1
            stop = box_lo
        else:
            n_down += 1
            stop = box_hi
        r_dist = abs(entry - stop)
        if r_dist <= 0:
            continue
        walk = session[brk_idx + 1:]

        rec = {"day": day, "side": side, "box_pct": 100 * box_height / entry}
        # primary 2R
        tp2 = entry + 2 * r_dist if side == "long" else entry - 2 * r_dist
        oc, gross = resolve(side, entry, stop, tp2, r_dist, walk)
        rec["net2"] = gross - fee_in_r(entry, r_dist, oc)
        rec["win2"] = 1 if oc == "tp" else 0
        # secondary: N*box_height targets
        for N in (1, 2):
            tpn = entry + N * box_height if side == "long" else entry - N * box_height
            ocn, grn = resolve(side, entry, stop, tpn, r_dist, walk)
            rec[f"netN{N}"] = grn - fee_in_r(entry, r_dist, ocn)
        trades.append(rec)

    print(f"k=1 PROOF: {n_entry} trades, entry=breakout+1 enforced, "
          f"violations={k1_violations} (must be 0). Box built only from the "
          f"09:30 ET bar; breakout scan starts 09:45 ET; entry = next-bar open.")

    # ---- DST proof: representative months (EST vs EDT) ----
    print("\nDST PROOF (box anchor; ET stays 09:30, UTC shifts across DST):")
    want_months = {2, 4, 7, 11}
    rep = {}
    for day in sorted(by_day):
        db = sorted(by_day[day], key=lambda b: b["ts"])
        box = next((b for b in db if _mins(b["ny"]) == SESSION_OPEN_MIN), None)
        if not box:
            continue
        m = day.month
        if m in want_months and m not in rep:
            rep[m] = (day, box["utc"].strftime("%H:%M UTC"), box["ny"].strftime("%H:%M %Z"))
    for m in sorted(rep):
        d, u, e = rep[m]
        print(f"  {d} (month {m:02d}): box bar = {u}  =  {e}")

    # ---- per-side stats ----
    def side_stats(rows, key):
        if not rows:
            return (0, 0.0, 0.0, 0.0)
        wins = sum(r["win2"] for r in rows)
        nets = [r[key] for r in rows]
        return (len(rows), round(100 * wins / len(rows), 1),
                round(statistics.fmean(nets), 4), round(statistics.median(nets), 4))

    print("\n== PER SIDE (separate) — 2R primary, fee-net ==")
    print(f"  {'side':6} | {'n':>4} | win@2R | avgR@2R | medR@2R | avgR N=1box | avgR N=2box")
    for side in ("long", "short"):
        rows = [t for t in trades if t["side"] == side]
        n, w, a, md = side_stats(rows, "net2")
        a1 = round(statistics.fmean([r["netN1"] for r in rows]), 4) if rows else 0
        a2 = round(statistics.fmean([r["netN2"] for r in rows]), 4) if rows else 0
        flag = "  <30 UNDERPOWERED" if 0 < n < 30 else ""
        print(f"  {side:6} | {n:4} | {w:5}% | {a:+.4f} | {md:+.4f} | {a1:+.4f} | {a2:+.4f}{flag}")

    # ---- walk-forward by half-year ----
    print("\n== WALK-FORWARD (avg net-R@2R per side, by half-year) ==")
    def half(day):
        return f"{day.year}H{1 if day.month <= 6 else 2}"
    periods = sorted({half(t["day"]) for t in trades})
    print(f"  {'side':6} | " + " | ".join(f"{p:>9}" for p in periods))
    for side in ("long", "short"):
        cells = []
        for p in periods:
            rows = [t for t in trades if t["side"] == side and half(t["day"]) == p]
            cells.append(f"{statistics.fmean([r['net2'] for r in rows]):+.3f}({len(rows)})"
                         if rows else "   -")
        print(f"  {side:6} | " + " | ".join(f"{c:>9}" for c in cells))

    # ---- ORB-size buckets ----
    print("\n== ORB-SIZE -> OUTCOME (box height as % of entry; avg net-R@2R) ==")
    bins = [(0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0), (1.0, 99)]
    print(f"  {'bucket':12} | {'side':6} | {'n':>4} | avg net-R@2R")
    for lo, hi in bins:
        for side in ("long", "short"):
            rows = [t for t in trades if t["side"] == side and lo <= t["box_pct"] < hi]
            if rows:
                a = statistics.fmean([r["net2"] for r in rows])
                hi_lbl = ">1.0 " if hi >= 99 else f"{hi:.2f}"
                print(f"  {lo:.2f}-{hi_lbl}% | {side:6} | {len(rows):4} | {a:+.4f}")

    # ---- breakout stats ----
    print("\n== BREAKOUT STATS (of eligible weekday boxes) ==")
    print(f"  eligible boxes (had a 09:30 ET bar) : {n_box}")
    print(f"  breakout-close entry rate           : {100*n_entry/n_box:.1f}%  ({n_entry}/{n_box})")
    print(f"  break UP (long)                     : {100*n_up/n_box:.1f}%  ({n_up})")
    print(f"  break DOWN (short)                  : {100*n_down/n_box:.1f}%  ({n_down})")
    print(f"  box HELD all session (no trade)     : {100*n_held/n_box:.1f}%  ({n_held})")

    # ---- pooled-for-reference (NOT the verdict basis) ----
    allnet = [t["net2"] for t in trades]
    print(f"\n(reference only, NOT pooled-verdict) all trades avg net-R@2R = "
          f"{statistics.fmean(allnet):+.4f} over n={len(allnet)}")


if __name__ == "__main__":
    run()
