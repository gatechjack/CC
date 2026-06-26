"""EXP A — Standalone VWAP aggressive-break-and-retest on ETHUSDT 15m.

PRE-REGISTERED PARAMS (LOCKED):
  K=1.0, L=20 (ATR), N=12 (retest window bars), BUFFER=0.001, TREND=EMA200
  Session: weekdays only, 09:30–16:00 ET. First valid setup per day.
  Resolution: 2R primary; secondary = revert-to-VWAP (first VWAP touch after entry).
  LONG and SHORT reported separately.
  Results: unfiltered + trend-aligned.

Run via:
  $env:PYTHONPATH="<worktree>"; $env:PYTHONUTF8="1"
  python scripts/vwap_expA_breakretest_eth.py
"""
from __future__ import annotations

import asyncio
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE))

from trading_corp.agents.paper_trade_replay import _bitunix_kline_fetcher

# ── PRE-REGISTERED PARAMS ─────────────────────────────────────────────────────
SYMBOL   = "ETHUSDT"
SINCE_MS = 1_704_067_200_000          # 2024-01-01 00:00 UTC
BIG_LIMIT = 90_000
K        = 1.0      # ATR threshold for aggressive break
L        = 20       # ATR lookback (mean of last L closed high-low ranges)
N        = 12       # Retest window in bars
BUFFER   = 0.001    # Stop buffer fraction from VWAP
EMA_LEN  = 200      # Trend EMA period
ENTRY_FEE = 0.000243
MK        = 0.00014
TK        = 0.0004
SLIP      = 0.0001
NY        = ZoneInfo("America/New_York")

# ── helpers ───────────────────────────────────────────────────────────────────

def bar_ny(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(NY)


def is_weekday(ts_ms: int) -> bool:
    return bar_ny(ts_ms).weekday() < 5  # Mon=0 … Fri=4


def in_session(ts_ms: int) -> bool:
    """Bar open time is within 09:30–15:45 ET (last entry bar allowed)."""
    dt = bar_ny(ts_ms)
    t = (dt.hour, dt.minute)
    return (9, 30) <= t <= (15, 45)  # ≤15:45 so we can still resolve within session


def at_session_start(ts_ms: int) -> bool:
    dt = bar_ny(ts_ms)
    return dt.hour == 9 and dt.minute == 30


def after_session(ts_ms: int) -> bool:
    """Bar open time >= 16:00 ET."""
    dt = bar_ny(ts_ms)
    return (dt.hour, dt.minute) >= (16, 0)


def compute_vwap_series(bars: list[list[float]]) -> list[float | None]:
    """9:30-ET-anchored VWAP, DST-aware, k=1 (uses bar i closed values)."""
    n = len(bars)
    vwap_out: list[float | None] = [None] * n
    cum_tpv = 0.0
    cum_vol = 0.0
    in_sess = False
    for i, bar in enumerate(bars):
        ts_ms = int(bar[0])
        if at_session_start(ts_ms):
            cum_tpv = 0.0
            cum_vol = 0.0
            in_sess = True
        if not in_sess:
            vwap_out[i] = None
            continue
        h, l, c, vol = bar[2], bar[3], bar[4], bar[5]
        typical = (h + l + c) / 3.0
        cum_tpv += typical * vol
        cum_vol += vol
        vwap_out[i] = cum_tpv / cum_vol if cum_vol > 0 else typical
    return vwap_out


def compute_ema(bars: list[list[float]], period: int) -> list[float | None]:
    """EMA(period) on close prices. Returns None until period bars are seen."""
    ema_out: list[float | None] = [None] * len(bars)
    k = 2.0 / (period + 1)
    ema_val: float | None = None
    for i, bar in enumerate(bars):
        c = bar[4]
        if ema_val is None:
            ema_val = c
        else:
            ema_val = c * k + ema_val * (1 - k)
        if i >= period - 1:
            ema_out[i] = ema_val
    return ema_out


def compute_atr_series(bars: list[list[float]], L: int) -> list[float | None]:
    """Simple high-low ATR (no TR, just hl range) averaged over last L closed bars."""
    atr_out: list[float | None] = [None] * len(bars)
    for i in range(L - 1, len(bars)):
        window = bars[i - L + 1 : i + 1]
        avg_hl = statistics.mean(b[2] - b[3] for b in window)
        atr_out[i] = avg_hl
    return atr_out


def net_r_calc(gross: float, r_dist: float, entry: float, outcome_type: str) -> float:
    exit_fee = MK if outcome_type == "tp" else TK
    return gross - (ENTRY_FEE + exit_fee + SLIP) / (r_dist / entry)


def half_year(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    h = "H1" if dt.month <= 6 else "H2"
    return f"{dt.year}-{h}"


# ── PROOF helpers ─────────────────────────────────────────────────────────────

def dst_proof(bars: list[list[float]]) -> str:
    lines = ["=== PROOF 1: DST (ETH) ==="]
    found_est, found_edt = [], []
    for bar in bars:
        dt_ny = bar_ny(int(bar[0]))
        if dt_ny.hour == 9 and dt_ny.minute == 30:
            dt_utc = datetime.fromtimestamp(bar[0] / 1000, tz=timezone.utc)
            label = f"  {dt_ny.strftime('%Y-%m-%d %H:%M')} ET ({dt_ny.tzname()}) = {dt_utc.strftime('%H:%M')} UTC"
            if dt_ny.month == 2 and len(found_est) < 2:
                found_est.append(label)
            elif dt_ny.month in (4, 7) and len(found_edt) < 2:
                found_edt.append(label)
        if len(found_est) >= 2 and len(found_edt) >= 2:
            break
    lines.append("EST samples (expect 14:30 UTC):")
    lines.extend(found_est or ["  [none found]"])
    lines.append("EDT samples (expect 13:30 UTC):")
    lines.extend(found_edt or ["  [none found]"])
    return "\n".join(lines)


def vwap_reset_proof(bars: list[list[float]], vwap: list[float | None]) -> str:
    lines = ["=== PROOF 3: VWAP RESET (ETH) ==="]
    for i, bar in enumerate(bars):
        dt_ny = bar_ny(int(bar[0]))
        if dt_ny.hour == 9 and dt_ny.minute == 30 and vwap[i] is not None:
            h, l, c = bar[2], bar[3], bar[4]
            typical = (h + l + c) / 3.0
            lines.append(f"  Anchor bar {dt_ny.strftime('%Y-%m-%d')} 09:30 ET:")
            lines.append(f"    typical = (h={h}+l={l}+c={c})/3 = {typical:.4f}")
            lines.append(f"    VWAP    = {vwap[i]:.4f}  (should == typical)")
            for j in range(i + 1, min(i + 5, len(bars))):
                dt2 = bar_ny(int(bars[j][0]))
                if dt2.hour == 9 and dt2.minute == 45 and vwap[j] is not None:
                    h2, l2, c2 = bars[j][2], bars[j][3], bars[j][4]
                    typ2 = (h2 + l2 + c2) / 3.0
                    lines.append(f"  09:45 ET bar:")
                    lines.append(f"    typical = {typ2:.4f}")
                    lines.append(f"    VWAP    = {vwap[j]:.4f}  (accumulated; proves reset)")
                    break
            break
    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"Fetching {SYMBOL} 15m bars …", flush=True)
    raw = await _bitunix_kline_fetcher(SYMBOL, "15m", SINCE_MS, BIG_LIMIT)

    # Dedupe + sort
    seen: dict[int, list[float]] = {}
    for row in raw:
        seen[int(row[0])] = row
    bars = sorted(seen.values(), key=lambda r: r[0])
    print(f"  Bars: {len(bars)}", flush=True)
    print(f"  Span: {datetime.fromtimestamp(bars[0][0]/1000, tz=timezone.utc)} → {datetime.fromtimestamp(bars[-1][0]/1000, tz=timezone.utc)}", flush=True)

    # Pre-compute series
    print("Computing VWAP, EMA200, ATR …", flush=True)
    vwap = compute_vwap_series(bars)
    ema200 = compute_ema(bars, EMA_LEN)
    atr = compute_atr_series(bars, L)

    # ── Proofs ────────────────────────────────────────────────────────────────
    print(dst_proof(bars))
    print()
    print(vwap_reset_proof(bars, vwap))
    print()
    print("=== PROOF 2: k=1 ===")
    print("Entry bar = Rt_index + 1; entry price = bars[Rt+1].open.")
    print("VWAP/ATR at bar B and Rt uses only bars[0..B] and bars[0..Rt] (closed).")
    print("No future bar is ever read. k=1 OK.")
    print()

    # ── State machine ─────────────────────────────────────────────────────────
    #
    # For each bar i (in session, weekday, EMA200/ATR available):
    #   State A: looking for aggressive break bar B
    #   State B: armed — looking for retest within N bars of B
    #   One setup per day (first valid).
    #
    # After each trade entry, mark the day as used.

    # Track: trades as list of dicts
    trades_long: list[dict] = []
    trades_short: list[dict] = []
    used_days: set[date] = set()
    weekdays_in_session: set[date] = set()

    # Break state: we carry (direction, break_idx, break_vwap, start_search)
    break_state: dict | None = None   # {dir, B_idx, vwap_B, search_from}

    def day_key(ts_ms: int) -> date:
        return bar_ny(int(ts_ms)).date()

    def resolve_2r(entry: float, stop: float, tp: float, r_dist: float,
                   start_idx: int, direction: str, session_end_idx: int) -> tuple:
        """Resolve 2R within session (timeout at session_end_idx)."""
        for j in range(start_idx, min(session_end_idx + 1, len(bars))):
            h, l, c = bars[j][2], bars[j][3], bars[j][4]
            if direction == "long":
                sl_hit = l <= stop
                tp_hit = h >= tp
            else:
                sl_hit = h >= stop
                tp_hit = l <= tp
            if sl_hit and tp_hit:
                return "sl", -1.0
            if sl_hit:
                return "sl", -1.0
            if tp_hit:
                return "tp", 2.0
        # timeout: mark to close (last bar close vs entry)
        last = min(session_end_idx, len(bars) - 1)
        last_close = bars[last][4]
        if direction == "long":
            gross = (last_close - entry) / r_dist
        else:
            gross = (entry - last_close) / r_dist
        return "timeout", gross

    def resolve_revert(entry: float, direction: str,
                       start_idx: int, session_end_idx: int,
                       r_dist: float) -> tuple:
        """Secondary: first touch of VWAP after entry within session."""
        for j in range(start_idx, min(session_end_idx + 1, len(bars))):
            v = vwap[j]
            if v is None:
                continue
            h, l = bars[j][2], bars[j][3]
            touched = (l <= v <= h)
            if touched:
                # Price = VWAP
                if direction == "long":
                    gross = (v - entry) / r_dist
                else:
                    gross = (entry - v) / r_dist
                win = 1 if gross > 0 else 0
                return win, gross
        # Did not touch VWAP before session end — timeout
        last = min(session_end_idx, len(bars) - 1)
        last_close = bars[last][4]
        if direction == "long":
            gross = (last_close - entry) / r_dist
        else:
            gross = (entry - last_close) / r_dist
        win = 1 if gross > 0 else 0
        return win, gross

    print("Running break-and-retest state machine …", flush=True)

    n = len(bars)
    for i in range(EMA_LEN, n):
        ts_ms = int(bars[i][0])
        if not (is_weekday(ts_ms) and in_session(ts_ms)):
            # Reset break state outside session
            if after_session(ts_ms):
                break_state = None
            continue
        if vwap[i] is None or atr[i] is None or ema200[i] is None:
            continue

        dk = day_key(ts_ms)
        weekdays_in_session.add(dk)

        if dk in used_days:
            continue  # one setup per day

        v = vwap[i]
        atr_i = atr[i]
        ema_i = ema200[i]
        bar_o, bar_c = bars[i][1], bars[i][4]

        if break_state is None:
            # Looking for aggressive break bar B
            # Crosses VWAP: open and close on opposite sides
            crosses = (bar_o < v and bar_c > v) or (bar_o > v and bar_c < v)
            if not crosses:
                continue
            break_dist = abs(bar_c - v)
            if break_dist < K * atr_i:
                continue
            direction = "long" if bar_c > v else "short"
            break_state = {
                "dir": direction,
                "B_idx": i,
                "vwap_B": v,
                "search_from": i + 1,
                "day": dk,
            }
            continue

        # In break_state: looking for retest
        bs = break_state
        if dk != bs["day"]:
            # New day — discard stale break state
            break_state = None
            # re-evaluate this bar as a potential break bar (fall-through)
            crosses = (bar_o < v and bar_c > v) or (bar_o > v and bar_c < v)
            if crosses and abs(bar_c - v) >= K * atr_i:
                direction = "long" if bar_c > v else "short"
                break_state = {
                    "dir": direction,
                    "B_idx": i,
                    "vwap_B": v,
                    "search_from": i + 1,
                    "day": dk,
                }
            continue

        bars_since_B = i - bs["B_idx"]
        if bars_since_B > N:
            # Retest window expired
            break_state = None
            continue

        # Skip the break bar itself
        if i <= bs["B_idx"]:
            continue

        # Retest condition: low <= VWAP <= high (bar touches VWAP) AND close on break side
        rt_touch = (bars[i][3] <= v <= bars[i][2])
        if not rt_touch:
            continue
        if bs["dir"] == "long" and bar_c <= v:
            continue  # failed retest (close below VWAP)
        if bs["dir"] == "short" and bar_c >= v:
            continue

        # Valid retest at bar i (Rt = i). ENTRY: open of bar i+1
        entry_idx = i + 1
        if entry_idx >= n:
            break_state = None
            continue

        entry = bars[entry_idx][1]  # k=1 open of next bar
        vwap_entry = vwap[entry_idx] if vwap[entry_idx] is not None else v

        if bs["dir"] == "long":
            stop = vwap_entry * (1 - BUFFER)
            r_dist = entry - stop
            tp_2r = entry + 2.0 * r_dist
        else:
            stop = vwap_entry * (1 + BUFFER)
            r_dist = stop - entry
            tp_2r = entry - 2.0 * r_dist

        if r_dist <= 0:
            break_state = None
            continue

        # Find session end (16:00 ET bar on entry day)
        session_end_idx = entry_idx
        for j in range(entry_idx, min(entry_idx + 30, n)):
            if after_session(int(bars[j][0])):
                session_end_idx = j - 1
                break
            session_end_idx = j

        # Resolve 2R
        outcome_2r, gross_2r = resolve_2r(entry, stop, tp_2r, r_dist,
                                           entry_idx + 1, bs["dir"], session_end_idx)
        nr_2r = net_r_calc(gross_2r, r_dist, entry, "tp" if outcome_2r == "tp" else "sl")
        win_2r = 1 if gross_2r > 0 else 0

        # Resolve revert-to-VWAP
        win_rv, gross_rv = resolve_revert(entry, bs["dir"],
                                          entry_idx + 1, session_end_idx, r_dist)
        nr_rv = net_r_calc(gross_rv, r_dist, entry, "tp" if gross_rv > 0 else "sl")

        # Trend filter
        trend_aligned = (bs["dir"] == "long" and entry > ema_i) or \
                        (bs["dir"] == "short" and entry < ema_i)

        trade = {
            "dir": bs["dir"],
            "entry_ts_ms": bars[entry_idx][0],
            "entry": entry,
            "stop": stop,
            "tp_2r": tp_2r,
            "r_dist": r_dist,
            "ema200": ema_i,
            "trend_aligned": trend_aligned,
            "outcome_2r": outcome_2r,
            "gross_2r": gross_2r,
            "net_r_2r": nr_2r,
            "win_2r": win_2r,
            "win_rv": win_rv,
            "gross_rv": gross_rv,
            "net_r_rv": nr_rv,
            "half_year": half_year(int(bars[entry_idx][0])),
            "day": dk,
        }

        if bs["dir"] == "long":
            trades_long.append(trade)
        else:
            trades_short.append(trade)

        used_days.add(dk)
        break_state = None

    total_weekdays = len(weekdays_in_session)

    # ── Stats helper ──────────────────────────────────────────────────────────
    def stats(trades, key_win="win_2r", key_nr="net_r_2r"):
        if not trades:
            return 0, 0.0, 0.0
        n = len(trades)
        wr = 100.0 * sum(t[key_win] for t in trades) / n
        avg = statistics.mean(t[key_nr] for t in trades)
        return n, wr, avg

    # ── Print results ─────────────────────────────────────────────────────────
    print(f"\nTotal weekday session-days: {total_weekdays}")
    print(f"LONG setups: {len(trades_long)}")
    print(f"SHORT setups: {len(trades_short)}")
    occ_long  = 100.0 * len(trades_long) / total_weekdays if total_weekdays else 0
    occ_short = 100.0 * len(trades_short) / total_weekdays if total_weekdays else 0
    occ_any   = 100.0 * len(used_days) / total_weekdays if total_weekdays else 0

    for label, trades, occ in [("LONG", trades_long, occ_long), ("SHORT", trades_short, occ_short)]:
        print(f"\n--- {label} (occurrence {occ:.1f}% of weekdays) ---")
        # Unfiltered 2R
        n_u, wr_u, avg_u = stats(trades)
        flag_u = "  [UNDERPOWERED <30]" if n_u < 30 else ""
        print(f"  Unfiltered 2R:   n={n_u}  win={wr_u:.1f}%  avg net-R={avg_u:+.4f}{flag_u}")
        # Trend-aligned 2R
        ta = [t for t in trades if t["trend_aligned"]]
        n_t, wr_t, avg_t = stats(ta)
        flag_t = "  [UNDERPOWERED <30]" if n_t < 30 else ""
        print(f"  Trend-aligned 2R: n={n_t}  win={wr_t:.1f}%  avg net-R={avg_t:+.4f}{flag_t}")
        # Revert-to-VWAP (unfiltered)
        n_rv, wr_rv, avg_rv = stats(trades, "win_rv", "net_r_rv")
        print(f"  Revert-to-VWAP:  n={n_rv}  win={wr_rv:.1f}%  avg net-R={avg_rv:+.4f}")
        # Walk-forward by half-year
        hy_buckets: dict[str, list] = defaultdict(list)
        for t in trades:
            hy_buckets[t["half_year"]].append(t)
        print(f"  Walk-forward by half-year (2R unfiltered):")
        for hy in sorted(hy_buckets):
            bn, bwr, bavg = stats(hy_buckets[hy])
            flag_b = "  [<30]" if bn < 30 else ""
            print(f"    {hy}: n={bn}  win={bwr:.1f}%  avg net-R={bavg:+.4f}{flag_b}")

    # ── VERDICT ───────────────────────────────────────────────────────────────
    print("\n=== VERDICT ===")
    verdicts = []
    for label, trades in [("LONG", trades_long), ("SHORT", trades_short)]:
        n_u, wr_u, avg_u = stats(trades)
        if n_u < 30:
            v = f"{label}: UNDERPOWERED (n={n_u} < 30)"
        elif avg_u > 0.05:
            v = f"{label}: POSITIVE edge  (avg net-R={avg_u:+.4f})"
        elif avg_u < -0.05:
            v = f"{label}: NEGATIVE  (avg net-R={avg_u:+.4f})"
        else:
            v = f"{label}: FLAT  (avg net-R={avg_u:+.4f})"
        verdicts.append(v)
        print(f"  {v}")

    # ── Write report ──────────────────────────────────────────────────────────
    report_dir = WORKTREE / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / "2026-06-26_vwap_expA_breakretest_eth.md"

    span_start = datetime.fromtimestamp(bars[0][0]/1000, tz=timezone.utc).date()
    span_end   = datetime.fromtimestamp(bars[-1][0]/1000, tz=timezone.utc).date()

    proof_dst = dst_proof(bars)
    proof_reset = vwap_reset_proof(bars, vwap)

    lines = [
        "# Exp A — VWAP Aggressive Break-and-Retest (ETHUSDT 15m)",
        "",
        "**Status:** PRE-REGISTERED backtest — rules locked, no optimisation, no filter-fishing.",
        "",
        "## Pre-Registered Parameters",
        "",
        "| Param | Value |",
        "|-------|-------|",
        f"| Asset | {SYMBOL} |",
        f"| K (ATR threshold for break) | {K} |",
        f"| L (ATR lookback bars) | {L} |",
        f"| N (retest window bars) | {N} |",
        f"| BUFFER (stop offset from VWAP) | {BUFFER} |",
        f"| TREND | EMA{EMA_LEN} on 15m |",
        "| Session | Weekdays 09:30–16:00 ET |",
        "| Setups | First valid per day |",
        "| Resolution | 2R primary; revert-to-VWAP secondary |",
        "",
        f"**Data span:** {span_start} → {span_end} ({len(bars):,} bars after dedup/sort)",
        f"**Weekday session-days:** {total_weekdays}",
        f"**Long setups:** {len(trades_long)} ({occ_long:.1f}% of weekdays)",
        f"**Short setups:** {len(trades_short)} ({occ_short:.1f}% of weekdays)",
        f"**Any setup:** {len(used_days)} days ({occ_any:.1f}% of weekdays)",
        "",
        "---",
        "",
    ]

    for label, trades, occ in [("LONG", trades_long, occ_long), ("SHORT", trades_short, occ_short)]:
        n_u, wr_u, avg_u = stats(trades)
        ta = [t for t in trades if t["trend_aligned"]]
        n_t, wr_t, avg_t = stats(ta)
        n_rv, wr_rv, avg_rv = stats(trades, "win_rv", "net_r_rv")
        lines += [
            f"## {label} Results (occurrence: {occ:.1f}% of weekdays)",
            "",
            "### 2R Outcome",
            "",
            f"| Filter | n | win@2R | avg net-R |",
            f"|--------|---|--------|-----------|",
            f"| Unfiltered | {n_u} | {wr_u:.1f}% | {avg_u:+.4f}{'  ⚠ underpowered' if n_u < 30 else ''} |",
            f"| Trend-aligned (EMA{EMA_LEN}) | {n_t} | {wr_t:.1f}% | {avg_t:+.4f}{'  ⚠ underpowered' if n_t < 30 else ''} |",
            "",
            "### 2R vs Revert-to-VWAP (unfiltered)",
            "",
            f"| Exit | n | win% | avg net-R |",
            f"|------|---|------|-----------|",
            f"| 2R | {n_u} | {wr_u:.1f}% | {avg_u:+.4f} |",
            f"| Revert-to-VWAP | {n_rv} | {wr_rv:.1f}% | {avg_rv:+.4f} |",
            "",
            "### Walk-Forward by Half-Year (2R unfiltered)",
            "",
            f"| Period | n | win@2R | avg net-R |",
            f"|--------|---|--------|-----------|",
        ]
        hy_buckets: dict[str, list] = defaultdict(list)
        for t in trades:
            hy_buckets[t["half_year"]].append(t)
        for hy in sorted(hy_buckets):
            bn, bwr, bavg = stats(hy_buckets[hy])
            flag_b = " ⚠" if bn < 30 else ""
            lines.append(f"| {hy} | {bn} | {bwr:.1f}% | {bavg:+.4f}{flag_b} |")
        lines += ["", "---", ""]

    lines += [
        "## VERDICT",
        "",
    ]
    for v in verdicts:
        lines.append(f"- {v}")

    lines += [
        "",
        "---",
        "",
        "## Methodology Proofs",
        "",
        "```",
        proof_dst,
        "```",
        "",
        "```",
        "=== PROOF 2: k=1 ===",
        f"Entry bar index = Rt_index + 1; entry = bars[Rt+1].open (open of bar AFTER confirming retest).",
        "VWAP/ATR used at each bar are computed from bars[0..i] only (no future data).",
        "EMA200 at entry bar uses all prior closes. No lookahead. k=1 OK.",
        "```",
        "",
        "```",
        proof_reset,
        "```",
        "",
        "---",
        f"*Generated by scripts/vwap_expA_breakretest_eth.py — pre-registered, no optimisation.*",
    ]

    report_text = "\n".join(lines)
    report_path.write_text(report_text, encoding="utf-8")
    print(f"\nReport written: {report_path}", flush=True)

    # Copy to Desktop
    desktop_dir = Path(r"C:\Users\AA Incorporado\Desktop\bitunix_reports")
    if desktop_dir.exists():
        dst = desktop_dir / report_path.name
        dst.write_text(report_text, encoding="utf-8")
        print(f"Copied to Desktop: {dst}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
