"""EXP A2 — VWAP Stdev Band-FADE (AlexOCrypto 'VWAP Stdev Bands v2' replication), ETHUSDT 15m.

PRE-REGISTERED, read-only, no optimisation beyond the stated grid. Reports the
WHOLE grid (every anchor x band-tier x trigger x trend cell), not the best cell.

WHY THIS EXISTS: Exp A tested a BARE VWAP line break-and-retest (dead). That tested
the wrong object. AlexO's real tool is "VWAP Stdev Bands v2" and his real trade is a
mean-reversion FADE from a deviation band back to VWAP. This replicates his indicator
exactly and tests that fade.

INDICATOR REPLICATION (from the provided Pine source):
  typical = hl2 = (H+L)/2                       (NOT HLC3)
  vwapsum = Sum(hl2 * vol);  volumesum = Sum(vol);   myvwap = vwapsum/volumesum
  v2sum   = Sum(vol * hl2^2)
  sigma   = sqrt( max(v2sum/volumesum - myvwap^2, 0) )   (vol-weighted std of price)
  bands   = myvwap +/- N*sigma  for N in {1.28, 2.01, 2.51}   (his on-by-default tiers;
            3.09/4.01 are OFF and not faded -- 3.09 is computed ONLY as the 2.51 stop.)

ANCHOR -- TWO ARMS, REPORTED SIDE BY SIDE (open question):
  ARM-1 (et) : session reset at 09:30 ET, DST-aware wall-clock (zoneinfo).
  ARM-2 (utc): daily reset at 00:00 UTC (exchange-day boundary; matches his
               security(tickerid,"D",time) daily anchor for crypto perps).
  Both reset every calendar day (crypto is 24/7).

THE FADE (his "deviate from the band, revert to VWAP"):
  Upper band tag -> SHORT ; lower band tag -> LONG.
  TARGET = VWAP (at the signal bar; fixed -> clean R, faithful "revert to median").
  STOP   = the NEXT band out (1.28 fade -> 2.01 stop ; 2.01 -> 2.51 ; 2.51 -> 3.09),
           computed at the signal bar. This gives a REAL R distance -- NOT pinned to
           VWAP (that was Exp A's artifact source; explicitly avoided here).
  k=1: band tag confirmed on a CLOSED bar; entry = NEXT bar open. 15m timeframe.

TRIGGER ARMS (both reported):
  (a) BARE TAG       : the signal bar's range reaches the band (high>=upper / low<=lower).
  (b) TAG+REJECTION  : the same tag bar ALSO closes back INSIDE the band (rejects).

TREND (two operationalisations of "momentum trend", reported separately):
  (i)  REG  : sign of the OLS slope of close over the last LOOKBACK_TREND closed bars
              (positive = uptrend). LOOKBACK_TREND = 50 bars (12.5h on 15m), stated a priori.
  (ii) OPEN : body direction of the session's anchor (first) bar -- bullish first bar
              (close>=open) = "entered the range from below" = up-bias, else down-bias.
  TREND-ALIGNED fade (fade-with-momentum) = long-at-lower-band in uptrend OR
  short-at-upper-band in downtrend. COUNTER-TREND = the opposite.

DEGENERACY GUARDS (pre-registered, NOT tuned):
  - MIN_BARS_SINCE_ANCHOR = 8 (2h): sigma needs a sample; early-session pencil-thin
    bands would give a near-zero r_dist (the Exp A artifact). Fades only after 8 bars.
  - geometry sanity: require target < entry < stop (short) / stop < entry < target (long);
    skip gap-throughs.
  - robustness: every cell reports MEAN and MEDIAN net-R plus an |gross|>5 extreme count.

FEE MODEL (Bitunix corrected, same as the SFP p6 / Exp A/B model):
  entry taker 0.000243 ; TP(at VWAP, resting limit) maker 0.00014 ; SL/timeout taker
  0.0004 ; slippage 0.0001 ;  net_R = gross_R - (entry+exit+slip)/(r_dist/entry).

DATA: Bitunix public REST klines for ETHUSDT 15m (the same endpoint LiveBarCache uses and
the same source bitunix_bar_history symbol='ETHUSDT' was REST-backfilled from). Self-
contained + reproducible. Volume = baseVol (USDT notional) -- consistent vol weight.
"""
from __future__ import annotations

import asyncio
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE))

from trading_corp.agents.paper_trade_replay import _bitunix_kline_fetcher

# ── PRE-REGISTERED PARAMS (LOCKED) ─────────────────────────────────────────────
SYMBOL    = "ETHUSDT"
SINCE_MS  = 1_704_067_200_000          # 2024-01-01 00:00 UTC
BIG_LIMIT = 90_000
TIERS     = [1.28, 2.01, 2.51]         # faded tiers (his on-by-default bands)
STOP_OF   = {1.28: 2.01, 2.01: 2.51, 2.51: 3.09}   # next band out -> stop
LOOKBACK_TREND = 50                    # bars for the OLS-slope trend (12.5h on 15m)
MIN_BARS_SINCE_ANCHOR = 8              # 2h: let sigma stabilise before fading
ENTRY_FEE = 0.000243
MK        = 0.00014
TK        = 0.0004
SLIP      = 0.0001
NY        = ZoneInfo("America/New_York")

# ── time helpers ───────────────────────────────────────────────────────────────

def bar_ny(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(NY)


def is_anchor(ts_ms: int, mode: str) -> bool:
    if mode == "et":
        dt = bar_ny(ts_ms)
        return dt.hour == 9 and dt.minute == 30          # 09:30 ET, DST-aware
    else:  # "utc"
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return dt.hour == 0 and dt.minute == 0            # 00:00 UTC exchange day


def half_year(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return f"{dt.year}-{'H1' if dt.month <= 6 else 'H2'}"


# ── indicator: vol-weighted VWAP + sigma, anchored, k=1 ───────────────────────--

def compute_session_arrays(bars: list[list[float]], mode: str):
    """Return (vwap, sigma, since, sess_end, resets) — all parallel to bars.

    vwap[i]/sigma[i] use ONLY closed bars from the current anchor through bar i
    (k=1: bar i is closed when its band is evaluated). His exact hl2 vol-weighted
    formulas. since[i] = bars elapsed in the session (1 at the anchor bar).
    sess_end[i] = last bar index before the NEXT anchor reset (the fade must
    resolve within the same VWAP session — bands reset at the next anchor).
    """
    n = len(bars)
    vwap: list[float | None] = [None] * n
    sigma: list[float | None] = [None] * n
    since: list[int | None] = [None] * n
    resets: list[int] = []
    cum_pv = cum_v = cum_p2v = 0.0
    bars_in = 0
    started = False
    for i, b in enumerate(bars):
        if is_anchor(int(b[0]), mode):
            cum_pv = cum_v = cum_p2v = 0.0
            bars_in = 0
            started = True
            resets.append(i)
        if not started:
            continue
        h, l, vol = b[2], b[3], b[5]
        hl2 = (h + l) / 2.0
        cum_pv += hl2 * vol
        cum_v += vol
        cum_p2v += (hl2 * hl2) * vol
        bars_in += 1
        if cum_v > 0:
            vw = cum_pv / cum_v
            var = max(cum_p2v / cum_v - vw * vw, 0.0)
            sg = math.sqrt(var)
        else:
            vw, sg = hl2, 0.0
        vwap[i] = vw
        sigma[i] = sg
        since[i] = bars_in
    # session end per bar
    sess_end = [n - 1] * n
    for k, ri in enumerate(resets):
        end = (resets[k + 1] - 1) if k + 1 < len(resets) else n - 1
        for j in range(ri, end + 1):
            sess_end[j] = end
    return vwap, sigma, since, sess_end, resets


def compute_trend_reg(bars: list[list[float]], lookback: int) -> list[str | None]:
    """OLS-slope sign of close over the last `lookback` closed bars ending at i."""
    n = len(bars)
    out: list[str | None] = [None] * n
    xs = list(range(lookback))
    xbar = sum(xs) / lookback
    sxx = sum((x - xbar) ** 2 for x in xs)
    for i in range(lookback - 1, n):
        ys = [bars[j][4] for j in range(i - lookback + 1, i + 1)]
        ybar = sum(ys) / lookback
        sxy = sum((xs[k] - xbar) * (ys[k] - ybar) for k in range(lookback))
        slope = sxy / sxx if sxx else 0.0
        out[i] = "up" if slope > 0 else "down"
    return out


def compute_trend_open(bars: list[list[float]], resets: list[int], n: int) -> list[str | None]:
    """Session anchor-bar body direction; mapped to every bar in that session."""
    out: list[str | None] = [None] * n
    for k, ri in enumerate(resets):
        end = (resets[k + 1] - 1) if k + 1 < len(resets) else n - 1
        ob = bars[ri]
        lab = "up" if ob[4] >= ob[1] else "down"        # close>=open -> entered from below
        for j in range(ri, end + 1):
            out[j] = lab
    return out


# ── economics ──────────────────────────────────────────────────────────────────

def net_r_calc(gross: float, r_dist: float, entry: float, outcome: str) -> float:
    exit_fee = MK if outcome == "tp" else TK
    return gross - (ENTRY_FEE + exit_fee + SLIP) / (r_dist / entry)


def align(direction: str, trend_label: str | None) -> str | None:
    if trend_label is None:
        return None
    if direction == "long":
        return "aligned" if trend_label == "up" else "counter"
    return "aligned" if trend_label == "down" else "counter"


# ── resolution: fixed stop (next band out) + fixed target (VWAP@signal) ─────────

def resolve(bars, direction, entry_idx, sess_end_idx, entry, stop, target, r_dist):
    for j in range(entry_idx, min(sess_end_idx, len(bars) - 1) + 1):
        h, l = bars[j][2], bars[j][3]
        if direction == "short":
            sl_hit = h >= stop
            tp_hit = l <= target
        else:
            sl_hit = l <= stop
            tp_hit = h >= target
        if sl_hit:                                   # stop-first on a both-hit bar
            return "sl", -1.0
        if tp_hit:
            gross = (entry - target) / r_dist if direction == "short" else (target - entry) / r_dist
            return "tp", gross
    last = bars[min(sess_end_idx, len(bars) - 1)][4]
    gross = (entry - last) / r_dist if direction == "short" else (last - entry) / r_dist
    return "timeout", gross


# ── proofs ─────────────────────────────────────────────────────────────────────

def dst_proof(bars) -> str:
    lines = ["=== PROOF 1: DST (ARM-1 09:30 ET anchor) ==="]
    est, edt = [], []
    for b in bars:
        dt_ny = bar_ny(int(b[0]))
        if dt_ny.hour == 9 and dt_ny.minute == 30:
            u = datetime.fromtimestamp(b[0] / 1000, tz=timezone.utc)
            lab = f"  {dt_ny.strftime('%Y-%m-%d %H:%M')} ET ({dt_ny.tzname()}) = {u.strftime('%H:%M')} UTC"
            if dt_ny.month == 2 and len(est) < 2:
                est.append(lab)
            elif dt_ny.month in (4, 7) and len(edt) < 2:
                edt.append(lab)
        if len(est) >= 2 and len(edt) >= 2:
            break
    lines.append("EST (expect 14:30 UTC):"); lines += est or ["  [none]"]
    lines.append("EDT (expect 13:30 UTC):"); lines += edt or ["  [none]"]
    return "\n".join(lines)


def indicator_proof(bars, vwap, sigma, since, mode) -> str:
    """Numeric replication proof: recompute sigma by hand at a mid-session bar."""
    lines = [f"=== PROOF 2: INDICATOR REPLICATION ({mode}) ==="]
    # find a session reset, show the reset bar (sigma must be 0, vwap==hl2),
    # then a bar ~10 in and recompute sigma from scratch.
    cum_pv = cum_v = cum_p2v = 0.0
    started = False
    for i, b in enumerate(bars):
        if is_anchor(int(b[0]), mode):
            cum_pv = cum_v = cum_p2v = 0.0
            started = True
            anchor_i = i
        if not started:
            continue
        h, l, vol = b[2], b[3], b[5]
        hl2 = (h + l) / 2.0
        cum_pv += hl2 * vol; cum_v += vol; cum_p2v += (hl2 * hl2) * vol
        if since[i] == 1:
            lines.append(f"  RESET bar {datetime.fromtimestamp(b[0]/1000, tz=timezone.utc)}:")
            lines.append(f"    hl2={hl2:.4f}  vwap={vwap[i]:.4f} (==hl2)  sigma={sigma[i]:.6f} (==0)")
        if since[i] == 10:
            vw = cum_pv / cum_v
            sg = math.sqrt(max(cum_p2v / cum_v - vw * vw, 0.0))
            lines.append(f"  10th bar of a session:")
            lines.append(f"    by-hand vwap={vw:.4f}  sigma={sg:.4f}")
            lines.append(f"    array    vwap={vwap[i]:.4f}  sigma={sigma[i]:.4f}  (must match)")
            lines.append(f"    bands: -2.51s={vw-2.51*sg:.2f}  -1.28s={vw-1.28*sg:.2f}  "
                         f"VWAP={vw:.2f}  +1.28s={vw+1.28*sg:.2f}  +2.51s={vw+2.51*sg:.2f}")
            break
    return "\n".join(lines)


# ── stats ──────────────────────────────────────────────────────────────────────

def stats(rows):
    if not rows:
        return dict(n=0, wr=0.0, mean=0.0, med=0.0, ext=0)
    n = len(rows)
    wr = 100.0 * sum(r["win"] for r in rows) / n
    nets = [r["net_r"] for r in rows]
    ext = sum(1 for r in rows if abs(r["gross"]) > 5)
    return dict(n=n, wr=wr, mean=statistics.mean(nets), med=statistics.median(nets), ext=ext)


def fmt(s, flag_thin=True):
    thin = "  THIN(<30)" if (flag_thin and 0 < s["n"] < 30) else ""
    extn = f"  ext={s['ext']}" if s["ext"] else ""
    return f"n={s['n']:>4}  win={s['wr']:5.1f}%  mean={s['mean']:+.4f}  med={s['med']:+.4f}{extn}{thin}"


# ── main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"Fetching {SYMBOL} 15m …", flush=True)
    raw = await _bitunix_kline_fetcher(SYMBOL, "15m", SINCE_MS, BIG_LIMIT)
    seen: dict[int, list[float]] = {}
    for row in raw:
        seen[int(row[0])] = row
    bars = sorted(seen.values(), key=lambda r: r[0])
    n = len(bars)
    span0 = datetime.fromtimestamp(bars[0][0] / 1000, tz=timezone.utc)
    span1 = datetime.fromtimestamp(bars[-1][0] / 1000, tz=timezone.utc)
    print(f"  bars={n}  span={span0} -> {span1}", flush=True)

    tr_reg = compute_trend_reg(bars, LOOKBACK_TREND)   # anchor-independent

    all_records: list[dict] = []
    proofs: dict[str, str] = {}

    for mode in ("et", "utc"):
        vwap, sigma, since, sess_end, resets = compute_session_arrays(bars, mode)
        tr_open = compute_trend_open(bars, resets, n)
        if mode == "et":
            proofs["dst"] = dst_proof(bars)
        proofs[f"ind_{mode}"] = indicator_proof(bars, vwap, sigma, since, mode)

        for tier in TIERS:
            stop_tier = STOP_OF[tier]
            for direction in ("short", "long"):
                armed = False
                for i in range(n):
                    if since[i] == 1:
                        armed = False                       # new session
                    if vwap[i] is None or sigma[i] is None:
                        armed = False
                        continue
                    vw, sg = vwap[i], sigma[i]
                    o, h, l, c = bars[i][1], bars[i][2], bars[i][3], bars[i][4]
                    if direction == "short":
                        band = vw + tier * sg
                        inside = c < band
                        tag = h >= band
                    else:
                        band = vw - tier * sg
                        inside = c > band
                        tag = l <= band

                    if tag:
                        valid = (since[i] is not None and since[i] >= MIN_BARS_SINCE_ANCHOR and sg > 0)
                        if valid and armed:
                            entry_idx = i + 1
                            if entry_idx <= sess_end[i] and entry_idx < n:
                                entry = bars[entry_idx][1]
                                if direction == "short":
                                    stop = vw + stop_tier * sg
                                    target = vw
                                    ok = (target < entry < stop)
                                    r_dist = stop - entry
                                else:
                                    stop = vw - stop_tier * sg
                                    target = vw
                                    ok = (stop < entry < target)
                                    r_dist = entry - stop
                                if ok and r_dist > 0:
                                    outcome, gross = resolve(
                                        bars, direction, entry_idx, sess_end[i],
                                        entry, stop, target, r_dist)
                                    nr = net_r_calc(gross, r_dist, entry,
                                                    "tp" if outcome == "tp" else "sl")
                                    all_records.append({
                                        "anchor": mode, "tier": tier, "dir": direction,
                                        "is_rej": inside,            # tag bar closed back inside
                                        "trend_reg": tr_reg[i], "trend_open": tr_open[i],
                                        "align_reg": align(direction, tr_reg[i]),
                                        "align_open": align(direction, tr_open[i]),
                                        "outcome": outcome, "gross": gross, "net_r": nr,
                                        "win": 1 if outcome == "tp" else 0,
                                        "hy": half_year(int(bars[entry_idx][0])),
                                        "entry_ts": int(bars[entry_idx][0]),
                                    })
                            armed = False
                    elif inside:                                # clean inside -> re-arm
                        armed = True

    print(f"\nTotal fade signals (all cells, both anchors): {len(all_records)}", flush=True)

    # ── proofs ──
    print("\n" + proofs["dst"])
    print("\n" + proofs["ind_et"])
    print("\n" + proofs["ind_utc"])
    print("\n=== PROOF 3: k=1 / no-look-ahead ===")
    print("  Band tag tested on CLOSED bar i (vwap[i]/sigma[i] use bars<=i only).")
    print("  Entry = open of bar i+1. Stop/target fixed from bar i's VWAP/sigma.")
    print("  Trend (reg slope, open-bar body) computed from bars<=i. No future read.")

    # ── grid ──
    def sel(anchor, tier, trigger, split):
        rs = [r for r in all_records if r["anchor"] == anchor and r["tier"] == tier]
        if trigger == "reject":
            rs = [r for r in rs if r["is_rej"]]
        if split == "all":
            return rs
        if split == "reg_aligned":
            return [r for r in rs if r["align_reg"] == "aligned"]
        if split == "reg_counter":
            return [r for r in rs if r["align_reg"] == "counter"]
        if split == "open_aligned":
            return [r for r in rs if r["align_open"] == "aligned"]
        if split == "open_counter":
            return [r for r in rs if r["align_open"] == "counter"]
        return rs

    SPLITS = [("ALL", "all"), ("reg-aligned", "reg_aligned"), ("reg-counter", "reg_counter"),
              ("open-aligned", "open_aligned"), ("open-counter", "open_counter")]

    report_lines = [
        "# Exp A2 — VWAP Stdev Band-FADE (AlexO replication), ETHUSDT 15m",
        "",
        "**Status:** PRE-REGISTERED, read-only, no optimisation. Full grid reported.",
        "",
        f"**Data:** Bitunix REST klines ETHUSDT 15m, {span0.date()} → {span1.date()} ({n:,} bars).",
        f"**Total fade signals (both anchors, all tiers/triggers):** {len(all_records)}",
        "",
        "Indicator: hl2 vol-weighted VWAP; σ=√(Σ(vol·hl2²)/Σvol − VWAP²); bands ±{1.28,2.01,2.51}σ.",
        "Fade: band tag→revert to VWAP. Target=VWAP@signal. Stop=next band out. k=1, 15m.",
        f"Guards: ≥{MIN_BARS_SINCE_ANCHOR} bars since anchor; geometry-sane; mean+median+|gross|>5 ext count.",
        "Trend: REG=OLS slope of close over 50 bars; OPEN=anchor-bar body direction.",
        "Trend-aligned fade = long-at-lower in uptrend / short-at-upper in downtrend.",
        "",
    ]

    for anchor in ("et", "utc"):
        head = "ARM-1 (09:30 ET anchor, DST-aware)" if anchor == "et" else "ARM-2 (00:00 UTC anchor)"
        print(f"\n{'='*78}\n{head}\n{'='*78}")
        report_lines += [f"## {head}", ""]
        for tier in TIERS:
            for trig_lbl, trig in (("bare-tag", "bare"), ("tag+reject", "reject")):
                print(f"\n-- tier {tier}σ  |  {trig_lbl}  (stop @ {STOP_OF[tier]}σ) --")
                report_lines += [
                    f"### tier {tier}σ — {trig_lbl}  (stop @ {STOP_OF[tier]}σ)",
                    "",
                    "| split | n | win% | mean net-R | median net-R | ext(|g|>5) |",
                    "|-------|---|------|-----------|--------------|-----------|",
                ]
                for split_lbl, split in SPLITS:
                    s = stats(sel(anchor, tier, trig, split))
                    print(f"    {split_lbl:14} {fmt(s)}")
                    thin = " ⚠" if 0 < s["n"] < 30 else ""
                    report_lines.append(
                        f"| {split_lbl} | {s['n']} | {s['wr']:.1f}% | {s['mean']:+.4f} | "
                        f"{s['med']:+.4f} | {s['ext']}{thin} |")
                report_lines.append("")

    # ── walk-forward on cells that look live (n>=30, mean>0.05, median>0) ──
    print(f"\n{'='*78}\nWALK-FORWARD on live-looking cells (n>=30, mean>+0.05, median>0)\n{'='*78}")
    report_lines += ["## Walk-forward on live-looking cells (n≥30, mean>+0.05, median>0)", ""]
    any_live = False
    for anchor in ("et", "utc"):
        for tier in TIERS:
            for trig_lbl, trig in (("bare-tag", "bare"), ("tag+reject", "reject")):
                for split_lbl, split in SPLITS:
                    rs = sel(anchor, tier, trig, split)
                    s = stats(rs)
                    if s["n"] >= 30 and s["mean"] > 0.05 and s["med"] > 0:
                        any_live = True
                        cell = f"{anchor} | tier {tier}σ | {trig_lbl} | {split_lbl}"
                        print(f"\n  CELL: {cell}  ({fmt(s, flag_thin=False)})")
                        report_lines += [f"**CELL: {cell}** — {fmt(s, flag_thin=False)}", "",
                                         "| period | n | win% | mean net-R |", "|---|---|---|---|"]
                        byhy = defaultdict(list)
                        for r in rs:
                            byhy[r["hy"]].append(r)
                        for hy in sorted(byhy):
                            hs = stats(byhy[hy])
                            flag = " ⚠" if hs["n"] < 30 else ""
                            print(f"    {hy}: n={hs['n']:>3}  win={hs['wr']:5.1f}%  mean={hs['mean']:+.4f}{flag}")
                            report_lines.append(f"| {hy} | {hs['n']} | {hs['wr']:.1f}% | {hs['mean']:+.4f}{flag} |")
                        report_lines.append("")
    if not any_live:
        print("  (none — no cell clears n>=30 AND mean>+0.05 AND median>0)")
        report_lines += ["_(none — no cell clears n≥30 AND mean>+0.05 AND median>0)_", ""]

    # ── auto-summary (machine verdict scaffold; prose verdict written by reviewer) ──
    print(f"\n{'='*78}\nAUTO-SUMMARY (scaffold)\n{'='*78}")
    # best ALL-trend cell per (anchor,tier,trigger) by mean, n>=30
    powered = [(f"{a}|{t}σ|{tl}|{sl}", stats(sel(a, t, tg, sp)))
               for a in ("et", "utc") for t in TIERS
               for tl, tg in (("bare", "bare"), ("reject", "reject"))
               for sl, sp in SPLITS]
    powered = [(c, s) for c, s in powered if s["n"] >= 30]
    powered.sort(key=lambda cs: cs[1]["mean"], reverse=True)
    print("  Top powered cells by mean net-R:")
    for c, s in powered[:8]:
        print(f"    {c:34} {fmt(s, flag_thin=False)}")
    report_lines += ["## Auto-summary — top powered cells (n≥30) by mean net-R", "",
                     "| cell | n | win% | mean | median | ext |", "|---|---|---|---|---|---|"]
    for c, s in powered[:10]:
        report_lines.append(f"| {c} | {s['n']} | {s['wr']:.1f}% | {s['mean']:+.4f} | {s['med']:+.4f} | {s['ext']} |")
    report_lines += ["", "_Verdict prose appended after review._", "",
                     "---", "## Methodology Proofs", "",
                     "```", proofs["dst"], "```", "",
                     "```", proofs["ind_et"], "```", "",
                     "```", proofs["ind_utc"], "```", "",
                     "```", "=== PROOF 3: k=1 ===",
                     "Band tag on CLOSED bar i; entry=open[i+1]; stop/target from bar i VWAP/σ;",
                     "trend from bars<=i. No future bar read.", "```", "",
                     "---", "*Generated by scripts/vwap_expA2_bandfade_eth.py — pre-registered, no optimisation.*"]

    report_path = WORKTREE / "reports" / "2026-06-26_vwap_expA2_bandfade_eth.md"
    report_path.parent.mkdir(exist_ok=True)
    text = "\n".join(report_lines)
    report_path.write_text(text, encoding="utf-8")
    print(f"\nReport written: {report_path}")
    desk = Path(r"C:\Users\AA Incorporado\Desktop\bitunix_reports")
    if desk.exists():
        (desk / report_path.name).write_text(text, encoding="utf-8")
        print(f"Copied to Desktop: {desk / report_path.name}")


if __name__ == "__main__":
    asyncio.run(main())
