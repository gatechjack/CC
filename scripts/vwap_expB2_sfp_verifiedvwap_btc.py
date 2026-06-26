"""EXP B2 — SFP x VWAP-proximity, RECOMPUTED with AlexO's VERIFIED VWAP indicator. BTCUSDT 15m.

PRE-REGISTERED re-analysis, read-only. Changes NOTHING live.

WHY: Exp B found NEAR-VWAP SFPs beat FAR by ~0.21R (NEAR n48 +0.235R vs FAR n49 +0.023R),
but it used the WRONG VWAP — HLC3 typical price, 09:30-ET anchor only. His REAL indicator
(numerically verified in Exp A2) uses hl2, the exact vol-weighted σ formula, and a daily
exchange-day anchor. This recomputes the proximity lift with the correct line, under BOTH
anchors, and asks: did the lift survive, grow, shrink, or vanish — i.e. was it real or an
artifact of the wrong VWAP?

REUSE (not rebuilt):
  - VWAP/σ: compute_session_arrays() imported VERBATIM from the A2 script (hl2 vol-weighted
    VWAP; σ=sqrt(max(v2sum/volumesum − vwap², 0))). Same code A2 proved by hand.
  - SFP signals: the validated SfpDetector (REAL+CONSIDERABLE), unchanged.
  - Resolution + fee model: replicated EXACTLY from Exp B (CAP_BARS=672, 2R, SL-first,
    timeout mark-to-close; entry taker, TP maker, SL/timeout taker, slip) so the BASE
    reproduces original B (n=97, +0.1279R) as an internal check — only the VWAP-distance
    classification changes.

DISTANCE (at the BOS bar = closed, BEFORE next-bar-open entry; k=1, no look-ahead):
  (a) raw %  = 100*(entry − VWAP)/entry            (original B metric, for comparison)
  (b) σ-norm = (entry − VWAP)/σ                     (band-widths from VWAP — principled)
  signed: + = entry ABOVE VWAP.

TWO ANCHORS, separate (same open question as A2):
  ARM-1 (et) : 09:30 ET reset, DST-aware.   ARM-2 (utc): 00:00 UTC exchange-day reset.
"""
from __future__ import annotations

import asyncio
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # scripts/ — for the A2 import

from trading_corp.agents.paper_trade_replay import _bitunix_kline_fetcher
from trading_corp.agents.strategies.bitunix_sfp import (
    SfpBar, SfpDetector, MODE_REAL, MODE_CONSIDERABLE, compute_geometry,
)
# Reuse A2's VERIFIED VWAP/σ code + fee model verbatim.
from vwap_expA2_bandfade_eth import compute_session_arrays, net_r_calc

SYMBOL    = "BTCUSDT"
SINCE_MS  = 1_704_067_200_000          # 2024-01-01 00:00 UTC
BIG_LIMIT = 90_000
CAP_BARS  = 672                        # 168h max hold (exact Exp B value)


def resolve_trade(bars, entry_bar_idx, entry, stop, tp_2r, r_dist):
    """EXACT Exp B resolution: walk forward, SL-first on both-hit, 2R, timeout m2c."""
    n = len(bars)
    for j in range(entry_bar_idx, min(entry_bar_idx + CAP_BARS, n)):
        h, l = bars[j][2], bars[j][3]
        sl_hit = l <= stop
        tp_hit = h >= tp_2r
        if sl_hit and tp_hit:
            return "sl", -1.0
        if sl_hit:
            return "sl", -1.0
        if tp_hit:
            return "tp", 2.0
    last_close = bars[min(entry_bar_idx + CAP_BARS - 1, n - 1)][4]
    return "timeout", (last_close - entry) / r_dist


def stats(rows):
    if not rows:
        return dict(n=0, wr=0.0, mean=0.0, med=0.0)
    n = len(rows)
    wr = 100.0 * sum(r["win"] for r in rows) / n
    nets = [r["net_r"] for r in rows]
    return dict(n=n, wr=wr, mean=statistics.mean(nets), med=statistics.median(nets))


def fmt(s):
    thin = "  THIN(<30)" if 0 < s["n"] < 30 else ""
    return f"n={s['n']:>3}  win={s['wr']:5.1f}%  mean={s['mean']:+.4f}  med={s['med']:+.4f}{thin}"


async def main() -> None:
    print(f"Fetching {SYMBOL} 15m …", flush=True)
    raw = await _bitunix_kline_fetcher(SYMBOL, "15m", SINCE_MS, BIG_LIMIT)
    seen = {}
    for row in raw:
        seen[int(row[0])] = row
    bars = sorted(seen.values(), key=lambda r: r[0])
    n = len(bars)
    span0 = datetime.fromtimestamp(bars[0][0] / 1000, tz=timezone.utc).date()
    span1 = datetime.fromtimestamp(bars[-1][0] / 1000, tz=timezone.utc).date()
    print(f"  bars={n}  span={span0} -> {span1}", flush=True)

    # SFP signals (validated detector, unchanged) — REAL + CONSIDERABLE pooled
    sfp_bars = [SfpBar(ts_ms=int(b[0]), open=b[1], high=b[2], low=b[3], close=b[4]) for b in bars]
    det_real, det_cons = SfpDetector(mode=MODE_REAL), SfpDetector(mode=MODE_CONSIDERABLE)
    signals = []
    for sb in sfp_bars:
        signals.extend(det_real.on_closed_bar(sb))
        signals.extend(det_cons.on_closed_bar(sb))
    print(f"  SFP signals (REAL+CONSIDERABLE): {len(signals)}", flush=True)

    # Resolve every signal ONCE (outcome is VWAP-independent); attach per-anchor distances.
    base = []
    for sig in signals:
        ei = sig.entry_bar_index
        if ei >= n:
            continue
        entry = bars[ei][1]
        geo = compute_geometry(entry, sig.swept_low)
        if geo is None:
            continue
        stop, tp_2r, r_dist = geo
        outcome, gross = resolve_trade(bars, ei, entry, stop, tp_2r, r_dist)
        base.append({
            "sig": sig, "entry": entry, "entry_idx": ei, "bos_idx": sig.bos_bar_index,
            "outcome": outcome, "gross": gross,
            "net_r": net_r_calc(gross, r_dist, entry, "tp" if outcome == "tp" else "sl"),
            "win": 1 if gross > 0 else 0,
        })

    report = [
        "# Exp B2 — SFP × VWAP-proximity, AlexO's VERIFIED VWAP (hl2 / vol-weighted σ). BTCUSDT 15m",
        "", "**Status:** PRE-REGISTERED re-analysis, read-only. Changes nothing live.", "",
        f"**Data:** Bitunix REST BTCUSDT 15m, {span0} → {span1} ({n:,} bars).",
        f"**SFP signals resolved (REAL+CONSIDERABLE):** {len(base)}",
        "", "VWAP/σ reused VERBATIM from the A2-verified code (hl2 vol-weighted; "
        "σ=√(Σ(vol·hl2²)/Σvol − VWAP²)). Distance at the BOS bar (closed, k=1). "
        "Resolution + fees replicate Exp B exactly (BASE must reproduce original B).", "",
    ]

    for mode in ("et", "utc"):
        head = "ARM-1 (09:30 ET anchor, DST-aware)" if mode == "et" else "ARM-2 (00:00 UTC exchange-day anchor)"
        print(f"\n{'='*78}\n{head}\n{'='*78}")
        report += [f"## {head}", ""]
        vwap, sigma, _since, _se, _rs = compute_session_arrays(bars, mode)

        rows, no_vwap, no_sigma = [], 0, 0
        for r in base:
            bi = r["bos_idx"]
            v = vwap[bi] if bi < n else None
            if v is None:
                no_vwap += 1
                continue
            sg = sigma[bi]
            raw_signed = 100.0 * (r["entry"] - v) / r["entry"]
            sig_signed = (r["entry"] - v) / sg if (sg is not None and sg > 0) else None
            if sig_signed is None:
                no_sigma += 1
            rows.append({**r, "raw_signed": raw_signed, "raw_abs": abs(raw_signed),
                         "sig_signed": sig_signed,
                         "sig_abs": abs(sig_signed) if sig_signed is not None else None})

        b = stats(rows)
        print(f"BASE: {fmt(b)}   (orig B: n=97 win40.2% +0.1279R)   dropped no_vwap={no_vwap}, σ=0 (raw-only)={no_sigma}")
        report += [f"**BASE:** {fmt(b)}  — original B: n=97, win 40.2%, +0.1279R. "
                   f"Dropped no_vwap={no_vwap}; σ=0 (kept for raw, excluded from σ views)={no_sigma}.", ""]

        # ---- NEAR/FAR median split on RAW % (original B metric) ----
        med_raw = statistics.median(r["raw_abs"] for r in rows)
        near_r = [r for r in rows if r["raw_abs"] < med_raw]
        far_r = [r for r in rows if r["raw_abs"] >= med_raw]
        sn, sf = stats(near_r), stats(far_r)
        lift_raw = sn["mean"] - sf["mean"]
        print(f"\nNEAR/FAR — RAW % (median |dist|={med_raw:.3f}%):")
        print(f"  NEAR {fmt(sn)}")
        print(f"  FAR  {fmt(sf)}")
        print(f"  LIFT (NEAR−FAR) = {lift_raw:+.4f}R   (orig B raw lift ≈ +0.2112R)")
        report += [f"### NEAR/FAR — raw % (median |dist| = {med_raw:.3f}%)", "",
                   "| bucket | n | win% | mean net-R | median |", "|---|---|---|---|---|",
                   f"| NEAR | {sn['n']} | {sn['wr']:.1f}% | {sn['mean']:+.4f} | {sn['med']:+.4f} |",
                   f"| FAR | {sf['n']} | {sf['wr']:.1f}% | {sf['mean']:+.4f} | {sf['med']:+.4f} |",
                   f"| **LIFT NEAR−FAR** | | | **{lift_raw:+.4f}R** | _(orig B ≈ +0.2112R)_ |", ""]

        # ---- NEAR/FAR median split on σ-NORMALIZED (principled) ----
        srows = [r for r in rows if r["sig_abs"] is not None]
        if srows:
            med_sig = statistics.median(r["sig_abs"] for r in srows)
            near_s = [r for r in srows if r["sig_abs"] < med_sig]
            far_s = [r for r in srows if r["sig_abs"] >= med_sig]
            sns, sfs = stats(near_s), stats(far_s)
            lift_sig = sns["mean"] - sfs["mean"]
            print(f"\nNEAR/FAR — σ-NORMALIZED (median |σ-dist|={med_sig:.3f}σ, on n={len(srows)}):")
            print(f"  NEAR {fmt(sns)}")
            print(f"  FAR  {fmt(sfs)}")
            print(f"  LIFT (NEAR−FAR) = {lift_sig:+.4f}R")
            report += [f"### NEAR/FAR — σ-normalized (median |σ-dist| = {med_sig:.3f}σ, n={len(srows)})", "",
                       "| bucket | n | win% | mean net-R | median |", "|---|---|---|---|---|",
                       f"| NEAR | {sns['n']} | {sns['wr']:.1f}% | {sns['mean']:+.4f} | {sns['med']:+.4f} |",
                       f"| FAR | {sfs['n']} | {sfs['wr']:.1f}% | {sfs['mean']:+.4f} | {sfs['med']:+.4f} |",
                       f"| **LIFT NEAR−FAR** | | | **{lift_sig:+.4f}R** | |", ""]

            # ---- σ-distance buckets ----
            print(f"\nσ-DISTANCE BUCKETS (|entry−VWAP| in σ):")
            report += ["### σ-distance buckets (|entry − VWAP| in σ)", "",
                       "| bucket | n | win% | mean net-R | median |", "|---|---|---|---|---|"]
            for lo, hi, lbl in [(0, 0.5, "<0.5σ"), (0.5, 1.0, "0.5–1σ"), (1.0, 2.0, "1–2σ"), (2.0, 1e9, ">2σ")]:
                bk = [r for r in srows if lo <= r["sig_abs"] < hi]
                s = stats(bk)
                print(f"  {lbl:7} {fmt(s)}")
                thin = " ⚠" if 0 < s["n"] < 30 else ""
                report.append(f"| {lbl} | {s['n']} | {s['wr']:.1f}% | {s['mean']:+.4f} | {s['med']:+.4f}{thin} |")
            report.append("")

            # ---- signed: above vs below VWAP ----
            above = [r for r in srows if r["sig_signed"] > 0]
            below = [r for r in srows if r["sig_signed"] <= 0]
            sa, sb_ = stats(above), stats(below)
            print(f"\nSIGNED (σ-norm): ABOVE VWAP {fmt(sa)}")
            print(f"                BELOW VWAP {fmt(sb_)}")
            report += ["### Signed — entry above vs below VWAP", "",
                       "| side | n | win% | mean net-R | median |", "|---|---|---|---|---|",
                       f"| ABOVE | {sa['n']} | {sa['wr']:.1f}% | {sa['mean']:+.4f} | {sa['med']:+.4f}"
                       f"{' ⚠' if 0 < sa['n'] < 30 else ''} |",
                       f"| BELOW | {sb_['n']} | {sb_['wr']:.1f}% | {sb_['mean']:+.4f} | {sb_['med']:+.4f}"
                       f"{' ⚠' if 0 < sb_['n'] < 30 else ''} |", ""]

    report += ["---", "## Verdict", "", "_Prose verdict appended after review._", "",
               "---", "*Generated by scripts/vwap_expB2_sfp_verifiedvwap_btc.py — pre-registered, no optimisation.*"]

    report_path = WORKTREE / "reports" / "2026-06-26_vwap_expB2_sfp_verifiedvwap_btc.md"
    text = "\n".join(report)
    report_path.write_text(text, encoding="utf-8")
    print(f"\nReport written: {report_path}")
    desk = Path(r"C:\Users\AA Incorporado\Desktop\bitunix_reports")
    if desk.exists():
        (desk / report_path.name).write_text(text, encoding="utf-8")
        print(f"Copied to Desktop: {desk / report_path.name}")


if __name__ == "__main__":
    asyncio.run(main())
