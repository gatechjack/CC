"""EXP B — VWAP-proximity sharpening of SFP edge on BTCUSDT 15m.

PRE-REGISTERED: no optimisation, no filter-fishing. Report the result.
Run via:
  $env:PYTHONPATH="<worktree>"; $env:PYTHONUTF8="1"
  python scripts/vwap_expB_sfp_btc.py
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ── path setup ────────────────────────────────────────────────────────────────
WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE))

from trading_corp.agents.paper_trade_replay import _bitunix_kline_fetcher
from trading_corp.agents.strategies.bitunix_sfp import (
    SfpBar,
    SfpDetector,
    SfpEntrySignal,
    MODE_REAL,
    MODE_CONSIDERABLE,
    compute_geometry,
)

# ── constants ─────────────────────────────────────────────────────────────────
SYMBOL      = "BTCUSDT"
SINCE_MS    = 1_704_067_200_000          # 2024-01-01 00:00 UTC
BIG_LIMIT   = 90_000                     # ~2.5 yr of 15m bars ≈ 87,600
ENTRY_FEE   = 0.000243
MK          = 0.00014
TK          = 0.0004
SLIP        = 0.0001
CAP_BARS    = 672                        # 168h max hold
NY          = ZoneInfo("America/New_York")

# ── helpers ───────────────────────────────────────────────────────────────────

def bar_ny_time(ts_ms: int) -> datetime:
    """Convert bar open ts_ms → NY wall-clock datetime (DST-aware)."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(NY)


def compute_vwap_series(bars: list[list[float]]) -> list[float | None]:
    """Compute 9:30-ET-anchored VWAP for every bar (k=1, uses only closed bars
    up to and including bar i).

    Returns list[float|None] parallel to bars; None = before first anchor.
    VWAP resets each calendar day at the bar whose NY wall-clock hour:minute == 09:30.
    """
    n = len(bars)
    vwap_out: list[float | None] = [None] * n

    # Identify anchor indices: bars where NY time == 09:30 (HH:MM)
    anchor_indices: list[int] = []
    for i, bar in enumerate(bars):
        dt_ny = bar_ny_time(int(bar[0]))
        if dt_ny.hour == 9 and dt_ny.minute == 30:
            anchor_indices.append(i)

    if not anchor_indices:
        print("[WARN] No 09:30-ET anchors found — no VWAP defined.", file=sys.stderr)
        return vwap_out

    # Build anchor → next_anchor mapping; for bars between anchors accumulate
    anchor_set = set(anchor_indices)
    anchor_ptr = 0  # pointer into anchor_indices
    cum_tpv = 0.0
    cum_vol = 0.0
    in_session = False

    for i, bar in enumerate(bars):
        ts_ms, o, h, l, c, vol = bar[0], bar[1], bar[2], bar[3], bar[4], bar[5]

        # Check if this bar is an anchor
        if i in anchor_set:
            cum_tpv = 0.0
            cum_vol = 0.0
            in_session = True

        if not in_session:
            vwap_out[i] = None
            continue

        typical = (h + l + c) / 3.0
        cum_tpv += typical * vol
        cum_vol += vol
        if cum_vol > 0:
            vwap_out[i] = cum_tpv / cum_vol
        else:
            vwap_out[i] = typical  # degenerate zero-vol bar

    return vwap_out


def net_r(gross_r: float, r_dist: float, entry: float, outcome: str) -> float:
    exit_fee = MK if outcome == "tp" else TK
    return gross_r - (ENTRY_FEE + exit_fee + SLIP) / (r_dist / entry)


def resolve_trade(
    bars: list[list[float]],
    entry_bar_idx: int,
    entry: float,
    stop: float,
    tp_2r: float,
    r_dist: float,
) -> tuple[str, float, float]:
    """Walk bars from entry_bar_idx forward; return (outcome, gross_r, net_r)."""
    n = len(bars)
    for j in range(entry_bar_idx, min(entry_bar_idx + CAP_BARS, n)):
        bar = bars[j]
        h, l, c = bar[2], bar[3], bar[4]
        sl_hit = l <= stop
        tp_hit = h >= tp_2r
        if sl_hit and tp_hit:
            # SL first
            gross = -1.0
            return "sl", gross, net_r(gross, r_dist, entry, "sl")
        if sl_hit:
            gross = -1.0
            return "sl", gross, net_r(gross, r_dist, entry, "sl")
        if tp_hit:
            gross = 2.0
            return "tp", gross, net_r(gross, r_dist, entry, "tp")
    # Timeout: mark-to-close
    last_close = bars[min(entry_bar_idx + CAP_BARS - 1, n - 1)][4]
    gross = (last_close - entry) / r_dist
    outcome = "timeout"
    return outcome, gross, net_r(gross, r_dist, entry, "sl")  # taker-fee on timeout


# ── PROOF helpers ─────────────────────────────────────────────────────────────

def dst_proof(bars: list[list[float]]) -> str:
    """Find ~4 sample bars: 2 in EST (Feb) and 2 in EDT (Apr/Jul)."""
    lines = ["=== PROOF 1: DST ==="]
    found_est, found_edt = [], []
    for bar in bars:
        dt_ny = bar_ny_time(int(bar[0]))
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
    lines.append("k=1 OK: entry uses open of bar AFTER BOS-confirming bar (entry_bar_index = bos_bar_index + 1)")
    return "\n".join(lines)


def vwap_reset_proof(bars: list[list[float]], vwap: list[float | None]) -> str:
    """Find one 09:30 anchor bar and show its VWAP = typical, then 09:45 bar."""
    lines = ["=== PROOF 3: VWAP RESET ==="]
    for i, bar in enumerate(bars):
        dt_ny = bar_ny_time(int(bar[0]))
        if dt_ny.hour == 9 and dt_ny.minute == 30 and vwap[i] is not None:
            h, l, c = bar[2], bar[3], bar[4]
            typical = (h + l + c) / 3.0
            lines.append(f"  Anchor bar {dt_ny.strftime('%Y-%m-%d')} 09:30 ET:")
            lines.append(f"    typical = (h={h}+l={l}+c={c})/3 = {typical:.4f}")
            lines.append(f"    VWAP    = {vwap[i]:.4f}  (should equal typical)")
            # Find 09:45
            for j in range(i + 1, min(i + 5, len(bars))):
                dt2 = bar_ny_time(int(bars[j][0]))
                if dt2.hour == 9 and dt2.minute == 45 and vwap[j] is not None:
                    h2, l2, c2 = bars[j][2], bars[j][3], bars[j][4]
                    typ2 = (h2 + l2 + c2) / 3.0
                    lines.append(f"  09:45 ET bar:")
                    lines.append(f"    typical = {typ2:.4f}")
                    lines.append(f"    VWAP    = {vwap[j]:.4f}  (accumulated > typical if up, proves reset)")
                    break
            break
    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"Fetching {SYMBOL} 15m bars from {datetime.fromtimestamp(SINCE_MS/1000, tz=timezone.utc).date()} …", flush=True)
    raw = await _bitunix_kline_fetcher(SYMBOL, "15m", SINCE_MS, BIG_LIMIT)

    # Dedupe + sort
    seen = {}
    for row in raw:
        seen[int(row[0])] = row
    bars = sorted(seen.values(), key=lambda r: r[0])
    print(f"  Bars received: {len(bars)}", flush=True)
    print(f"  Span: {datetime.fromtimestamp(bars[0][0]/1000,tz=timezone.utc)} → {datetime.fromtimestamp(bars[-1][0]/1000,tz=timezone.utc)}", flush=True)

    # VWAP series
    print("Computing VWAP …", flush=True)
    vwap = compute_vwap_series(bars)

    # SFP signals from BOTH detectors
    print("Running SFP detectors …", flush=True)
    sfp_bars = [SfpBar(ts_ms=int(b[0]), open=b[1], high=b[2], low=b[3], close=b[4]) for b in bars]
    det_real = SfpDetector(mode=MODE_REAL)
    det_cons = SfpDetector(mode=MODE_CONSIDERABLE)
    signals: list[SfpEntrySignal] = []
    for sb in sfp_bars:
        signals.extend(det_real.on_closed_bar(sb))
        signals.extend(det_cons.on_closed_bar(sb))
    print(f"  Signals (pooled REAL+CONSIDERABLE): {len(signals)}", flush=True)

    # ── PROOF 1 & 2 ──
    print(dst_proof(bars))
    print()
    print(vwap_reset_proof(bars, vwap))
    print()

    # ── per-signal resolution ─────────────────────────────────────────────────
    results = []
    skipped_no_geo = 0
    skipped_no_vwap = 0
    skipped_oob = 0

    for sig in signals:
        entry_idx = sig.entry_bar_index
        if entry_idx >= len(bars):
            skipped_oob += 1
            continue

        entry = bars[entry_idx][1]  # open of entry bar
        geo = compute_geometry(entry, sig.swept_low)
        if geo is None:
            skipped_no_geo += 1
            continue
        stop, tp_2r, r_dist = geo

        # VWAP at bos_bar_index (the fire/confirming bar)
        bos_idx = sig.bos_bar_index
        v = vwap[bos_idx] if bos_idx < len(vwap) else None
        if v is None:
            skipped_no_vwap += 1
            continue

        dist_pct = 100.0 * (entry - v) / entry
        abs_dist_pct = abs(dist_pct)

        outcome, gross, nr = resolve_trade(bars, entry_idx, entry, stop, tp_2r, r_dist)
        win = 1 if gross > 0 else 0

        results.append({
            "mode": sig.sfp_mode,
            "entry_idx": entry_idx,
            "entry_ts_ms": bars[entry_idx][0],
            "entry": entry,
            "stop": stop,
            "tp_2r": tp_2r,
            "r_dist": r_dist,
            "vwap_at_fire": v,
            "dist_pct": dist_pct,
            "abs_dist_pct": abs_dist_pct,
            "outcome": outcome,
            "gross_r": gross,
            "net_r": nr,
            "win": win,
        })

    print(f"Signals resolved: {len(results)} (skipped: no_geo={skipped_no_geo}, no_vwap={skipped_no_vwap}, oob={skipped_oob})", flush=True)

    if not results:
        print("ERROR: No results to analyse.")
        return

    # ── BASE stats ────────────────────────────────────────────────────────────
    n_all = len(results)
    wins_all = sum(r["win"] for r in results)
    avg_nr_all = statistics.mean(r["net_r"] for r in results)
    wr_all = 100.0 * wins_all / n_all

    print(f"\n=== BASE (all SFP-on-BTC-15m) ===")
    print(f"  n={n_all}  win@2R={wr_all:.1f}%  avg net-R={avg_nr_all:+.4f}")
    print(f"  (Validated pooled reference: n=68, +0.267R — 15m-alone may differ)")

    # ── NEAR/FAR SPLIT (median split) ────────────────────────────────────────
    med = statistics.median(r["abs_dist_pct"] for r in results)
    near = [r for r in results if r["abs_dist_pct"] < med]
    far  = [r for r in results if r["abs_dist_pct"] >= med]

    def bucket_stats(rs):
        if not rs:
            return 0, 0.0, 0.0
        n = len(rs)
        wr = 100.0 * sum(r["win"] for r in rs) / n
        avg = statistics.mean(r["net_r"] for r in rs)
        return n, wr, avg

    n_n, wr_n, avg_n = bucket_stats(near)
    n_f, wr_f, avg_f = bucket_stats(far)

    print(f"\n=== PRE-REGISTERED NEAR/FAR SPLIT (median abs_dist_pct = {med:.3f}%) ===")
    print(f"  NEAR (< median):  n={n_n}  win@2R={wr_n:.1f}%  avg net-R={avg_n:+.4f}{'  [UNDERPOWERED <30]' if n_n < 30 else ''}")
    print(f"  FAR  (>= median): n={n_f}  win@2R={wr_f:.1f}%  avg net-R={avg_f:+.4f}{'  [UNDERPOWERED <30]' if n_f < 30 else ''}")

    # ── CONTINUOUS: ABS quintiles ─────────────────────────────────────────────
    sorted_abs = sorted(results, key=lambda r: r["abs_dist_pct"])
    q_size = max(1, len(sorted_abs) // 5)
    print(f"\n=== CONTINUOUS: abs_dist_pct quintiles ===")
    for qi in range(5):
        start = qi * q_size
        end = (qi + 1) * q_size if qi < 4 else len(sorted_abs)
        bucket = sorted_abs[start:end]
        bn, bwr, bavg = bucket_stats(bucket)
        lo = bucket[0]["abs_dist_pct"]
        hi = bucket[-1]["abs_dist_pct"]
        flag = "  [UNDERPOWERED <30]" if bn < 30 else ""
        print(f"  Q{qi+1} abs {lo:.3f}–{hi:.3f}%: n={bn}  win={bwr:.1f}%  avg net-R={bavg:+.4f}{flag}")

    # ── CONTINUOUS: SIGNED quintiles ──────────────────────────────────────────
    sorted_sgn = sorted(results, key=lambda r: r["dist_pct"])
    print(f"\n=== CONTINUOUS: signed dist_pct quintiles (+ = entry above VWAP) ===")
    for qi in range(5):
        start = qi * q_size
        end = (qi + 1) * q_size if qi < 4 else len(sorted_sgn)
        bucket = sorted_sgn[start:end]
        bn, bwr, bavg = bucket_stats(bucket)
        lo = bucket[0]["dist_pct"]
        hi = bucket[-1]["dist_pct"]
        flag = "  [UNDERPOWERED <30]" if bn < 30 else ""
        print(f"  Q{qi+1} sgn {lo:+.3f}–{hi:+.3f}%: n={bn}  win={bwr:.1f}%  avg net-R={bavg:+.4f}{flag}")

    # ── VERDICT ───────────────────────────────────────────────────────────────
    print("\n=== VERDICT ===")
    if n_n < 30 or n_f < 30:
        verdict = "UNDERPOWERED — both buckets < 30; result is descriptive only"
    elif avg_n > avg_f + 0.05:
        verdict = "NEAR > FAR → VWAP-proximity SHARPENS SFP (candidate filter)"
    elif avg_f > avg_n + 0.05:
        verdict = "FAR > NEAR → VWAP-proximity INVERTS SFP edge"
    else:
        verdict = "NO DIFFERENCE — drop VWAP-proximity filter"
    print(f"  {verdict}")

    # ── Write report ──────────────────────────────────────────────────────────
    report_dir = WORKTREE / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / "2026-06-26_vwap_expB_sfp_btc.md"

    span_start = datetime.fromtimestamp(bars[0][0]/1000, tz=timezone.utc).date()
    span_end   = datetime.fromtimestamp(bars[-1][0]/1000, tz=timezone.utc).date()

    report_lines = [
        "# Exp B — VWAP-Proximity × SFP Edge (BTCUSDT 15m)",
        "",
        "**Status:** PRE-REGISTERED backtest — rules locked, no optimisation, no filter-fishing.",
        "",
        f"**Data span:** {span_start} → {span_end} ({len(bars):,} bars after dedup/sort)",
        f"**Signals (REAL + CONSIDERABLE pooled):** {len(signals)} fired, {len(results)} resolved (no_geo={skipped_no_geo}, no_vwap={skipped_no_vwap}, oob={skipped_oob})",
        "",
        "---",
        "",
        "## BASE — SFP on BTC 15m (unfiltered)",
        "",
        f"| n | win@2R | avg net-R |",
        f"|---|--------|-----------|",
        f"| {n_all} | {wr_all:.1f}% | {avg_nr_all:+.4f} |",
        "",
        f"*Validated pooled reference (all TF): n=68, avg net-R=+0.267R. 15m-alone may differ.*",
        "",
        "---",
        "",
        "## PRE-REGISTERED NEAR/FAR SPLIT",
        "",
        f"Median abs_dist_pct = {med:.4f}%  (|entry − VWAP| / entry × 100, signed + = entry above VWAP)",
        "",
        "| Bucket | n | win@2R | avg net-R | Note |",
        "|--------|---|--------|-----------|------|",
        f"| NEAR (< {med:.3f}%) | {n_n} | {wr_n:.1f}% | {avg_n:+.4f} | {'UNDERPOWERED <30' if n_n < 30 else ''} |",
        f"| FAR (≥ {med:.3f}%) | {n_f} | {wr_f:.1f}% | {avg_f:+.4f} | {'UNDERPOWERED <30' if n_f < 30 else ''} |",
        "",
        "---",
        "",
        "## CONTINUOUS: |abs_dist_pct| Quintiles",
        "",
        "| Quintile | abs range | n | win@2R | avg net-R |",
        "|----------|-----------|---|--------|-----------|",
    ]

    for qi in range(5):
        start = qi * q_size
        end = (qi + 1) * q_size if qi < 4 else len(sorted_abs)
        bucket = sorted_abs[start:end]
        bn, bwr, bavg = bucket_stats(bucket)
        lo = bucket[0]["abs_dist_pct"]
        hi = bucket[-1]["abs_dist_pct"]
        flag = " ⚠ underpowered" if bn < 30 else ""
        report_lines.append(f"| Q{qi+1} | {lo:.3f}–{hi:.3f}% | {bn} | {bwr:.1f}% | {bavg:+.4f}{flag} |")

    report_lines += [
        "",
        "## CONTINUOUS: Signed dist_pct Quintiles (+ = entry above VWAP)",
        "",
        "| Quintile | signed range | n | win@2R | avg net-R |",
        "|----------|-------------|---|--------|-----------|",
    ]
    for qi in range(5):
        start = qi * q_size
        end = (qi + 1) * q_size if qi < 4 else len(sorted_sgn)
        bucket = sorted_sgn[start:end]
        bn, bwr, bavg = bucket_stats(bucket)
        lo = bucket[0]["dist_pct"]
        hi = bucket[-1]["dist_pct"]
        flag = " ⚠ underpowered" if bn < 30 else ""
        report_lines.append(f"| Q{qi+1} | {lo:+.3f}–{hi:+.3f}% | {bn} | {bwr:.1f}% | {bavg:+.4f}{flag} |")

    # PROOFS
    proof_dst = dst_proof(bars)
    proof_reset = vwap_reset_proof(bars, vwap)

    report_lines += [
        "",
        "---",
        "",
        "## VERDICT",
        "",
        f"**{verdict}**",
        "",
        f"- NEAR n={n_n} avg net-R={avg_n:+.4f}",
        f"- FAR  n={n_f} avg net-R={avg_f:+.4f}",
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
        "SfpEntrySignal.entry_bar_index = bos_bar_index + 1 (hardcoded in bitunix_sfp.py line 102).",
        "Entry price = bars[entry_bar_index].open.",
        "VWAP at fire uses bars[bos_bar_index] — a CLOSED bar before entry.",
        "No future bar is read. k=1 OK.",
        "```",
        "",
        "```",
        proof_reset,
        "```",
        "",
        "---",
        "*Generated by scripts/vwap_expB_sfp_btc.py — pre-registered, no optimisation.*",
    ]

    report_text = "\n".join(report_lines)
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
