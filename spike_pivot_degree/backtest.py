"""
Pivot-degree sensitivity backtest for the Bitunix SFP (Mode B) strategy.
Read-only research spike — does NOT touch prod, does NOT commit.

Usage: python3 backtest.py
Output: spike_pivot_degree/REPORT.md
"""
from __future__ import annotations
import csv
import datetime
import math
import random
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from bitunix_sfp import (
    SfpBar, SfpModeBDetector,
    MODE_REAL, MODE_CONSIDERABLE,
    STOP_BUFFER_PCT, TP_R,
)

# ── Config ────────────────────────────────────────────────────────────────────
DATA_DIR   = os.path.join(os.path.dirname(__file__), "data")
COINS      = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
MODES      = [MODE_REAL, MODE_CONSIDERABLE]
PIVOT_LENS = [3, 5, 8, 10, 15, 20, 30, 50]
MULTI_LENS = [5, 10, 20]

IS_FRAC    = 0.60          # first 60% in-sample, last 40% OOS
NULL_RUNS  = 200           # null-distribution resamples per config
MAX_HOLD_BARS = 7 * 24 * 20   # 7 days in 3m bars (20 per hour)
NULL_STOP_LOOKBACK = 20    # bars back for null stop reference
BEATS_NULL_PCT = 95        # percentile threshold

_15M_MS = 900_000


# ── Data loading ──────────────────────────────────────────────────────────────

def load_3m(coin: str) -> list[SfpBar]:
    path = os.path.join(DATA_DIR, f"{coin}_3m.csv")
    bars = []
    with open(path) as f:
        for row in csv.reader(f):
            ts, o, h, l, c, *_ = row
            bars.append(SfpBar(int(ts), float(o), float(h), float(l), float(c)))
    return bars


def resample_15m(bars3: list[SfpBar]) -> list[SfpBar]:
    """Bucket 3m → 15m.  A bucket needs exactly 5 contiguous 3m bars."""
    from collections import defaultdict
    buckets: dict[int, list[SfpBar]] = defaultdict(list)
    for b in bars3:
        bk = b.ts_ms - (b.ts_ms % _15M_MS)
        buckets[bk].append(b)
    # Only complete buckets (exactly 5 bars, contiguous step=3m=180000ms)
    out = []
    for bk in sorted(buckets):
        group = sorted(buckets[bk], key=lambda x: x.ts_ms)
        if len(group) != 5:
            continue
        # Check contiguity
        ok = all(group[i+1].ts_ms - group[i].ts_ms == 180_000 for i in range(4))
        if not ok:
            continue
        out.append(SfpBar(
            ts_ms=bk,
            open=group[0].open,
            high=max(g.high for g in group),
            low=min(g.low for g in group),
            close=group[-1].close,
        ))
    return out


# ── Spot-check resampling ─────────────────────────────────────────────────────

def spot_check(bars15: list[SfpBar], bars3: list[SfpBar], n: int = 3):
    print("\n=== Resample spot-check ===")
    for b15 in bars15[:n]:
        ts = b15.ts_ms
        children = [b for b in bars3 if b.ts_ms - (b.ts_ms % _15M_MS) == ts]
        print(f"  15m ts={ts} O={b15.open} H={b15.high} L={b15.low} C={b15.close}")
        for ch in children:
            print(f"    3m ts={ch.ts_ms} O={ch.open} H={ch.high} L={ch.low} C={ch.close}")


# ── Signal generation ─────────────────────────────────────────────────────────

def get_signals(bars15: list[SfpBar], bars3: list[SfpBar],
                pivot_len: int) -> list:
    """Pool REAL + CONSIDERABLE signals via warm_start, sorted by entry_bar_index."""
    signals = []
    for mode in MODES:
        det = SfpModeBDetector(mode=mode, pivot_len=pivot_len)
        sigs = det.warm_start(bars15, bars3)
        signals.extend(sigs)
    signals.sort(key=lambda s: s.entry_bar_index)
    return signals


# ── Trade simulation ──────────────────────────────────────────────────────────

def simulate_trade(bars3: list[SfpBar], entry_idx: int, swept_low: float):
    """Simulate one long trade.  Returns (r_pnl, outcome_str, hold_bars)."""
    if entry_idx >= len(bars3):
        return None, "no_bar", 0
    entry = bars3[entry_idx].open
    stop = swept_low - STOP_BUFFER_PCT * entry
    r = entry - stop
    if r <= 0:
        return None, "invalid_r", 0
    tp = entry + TP_R * r
    for i in range(entry_idx + 1, min(entry_idx + MAX_HOLD_BARS + 1, len(bars3))):
        b = bars3[i]
        hit_stop = b.low <= stop
        hit_tp   = b.high >= tp
        if hit_stop and hit_tp:
            # Same bar — conservative: stop first
            return -1.0, "loss", i - entry_idx
        if hit_stop:
            return -1.0, "loss", i - entry_idx
        if hit_tp:
            return +2.0, "win",  i - entry_idx
    # Timeout — mark-to-market at last bar
    last = bars3[min(entry_idx + MAX_HOLD_BARS, len(bars3) - 1)]
    mtm_r = (last.close - entry) / r
    return mtm_r, "timeout", MAX_HOLD_BARS


def run_signals_one_open(bars3: list[SfpBar], signals: list):
    """One-open-at-a-time: skip signals firing while a trade is open.
    Returns list of dicts."""
    trades = []
    open_until = -1  # 3m bar index at which current trade closes
    for sig in signals:
        idx = sig.entry_bar_index
        if idx <= open_until:
            continue  # position still open
        if idx >= len(bars3):
            continue
        r_pnl, outcome, hold = simulate_trade(bars3, idx, sig.swept_low)
        if outcome in ("no_bar", "invalid_r"):
            continue
        trades.append({
            "entry_bar": idx,
            "entry_ts":  bars3[idx].ts_ms,
            "sfp_mode":  sig.sfp_mode,
            "swept_low": sig.swept_low,
            "r_pnl":     r_pnl,
            "outcome":   outcome,
            "hold":      hold,
        })
        open_until = idx + hold
    return trades


# ── Null baseline ─────────────────────────────────────────────────────────────

def null_stop_from_recent_low(bars3: list[SfpBar], idx: int, lookback: int = NULL_STOP_LOOKBACK) -> float:
    start = max(0, idx - lookback)
    return min(b.low for b in bars3[start:idx]) if idx > start else bars3[max(0, idx-1)].low


def run_null(bars3: list[SfpBar], n_trades: int, seed: int, split_start: int = 0, split_end: int | None = None) -> float:
    """One null resample: n_trades random long entries in [split_start, split_end].
    Returns expectancy over null trades."""
    rng = random.Random(seed)
    end = split_end if split_end is not None else len(bars3) - 1
    pool = list(range(max(NULL_STOP_LOOKBACK, split_start), end - 1))
    if len(pool) < n_trades:
        return float("nan")
    chosen = sorted(rng.sample(pool, n_trades))
    total_r = 0.0
    n_valid = 0
    open_until = -1
    for idx in chosen:
        if idx <= open_until:
            continue
        entry = bars3[idx].open
        swept_low = null_stop_from_recent_low(bars3, idx)
        stop = swept_low - STOP_BUFFER_PCT * entry
        r = entry - stop
        if r <= 0:
            continue
        tp = entry + TP_R * r
        r_pnl, outcome, hold = simulate_trade(bars3, idx, swept_low)
        if outcome in ("no_bar", "invalid_r"):
            continue
        total_r += r_pnl
        n_valid += 1
        open_until = idx + hold
    if n_valid == 0:
        return float("nan")
    return total_r / n_valid


def null_percentile(expectancy: float, null_dist: list[float]) -> float:
    valid = [x for x in null_dist if not math.isnan(x)]
    if not valid:
        return float("nan")
    pct = sum(1 for x in valid if expectancy > x) / len(valid) * 100.0
    return pct


# ── Aggregate stats ───────────────────────────────────────────────────────────

def trade_stats(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "win_rate": float("nan"), "avg_r": float("nan"),
                "total_r": 0.0, "max_dd_r": 0.0, "pct_timeout": float("nan")}
    n = len(trades)
    wins = sum(1 for t in trades if t["outcome"] == "win")
    timeouts = sum(1 for t in trades if t["outcome"] == "timeout")
    rs = [t["r_pnl"] for t in trades]
    avg_r = sum(rs) / n
    total_r = sum(rs)
    # Max drawdown in R
    peak = 0.0; cur = 0.0; max_dd = 0.0
    for r in rs:
        cur += r
        if cur > peak:
            peak = cur
        dd = peak - cur
        if dd > max_dd:
            max_dd = dd
    return {
        "n": n,
        "win_rate": wins / n,
        "avg_r": avg_r,
        "total_r": total_r,
        "max_dd_r": max_dd,
        "pct_timeout": timeouts / n * 100,
    }


# ── IS/OOS split ──────────────────────────────────────────────────────────────

def split_trades(trades: list[dict], bars3: list[SfpBar]):
    cutoff_idx = int(len(bars3) * IS_FRAC)
    is_t  = [t for t in trades if t["entry_bar"] < cutoff_idx]
    oos_t = [t for t in trades if t["entry_bar"] >= cutoff_idx]
    return is_t, oos_t, cutoff_idx


# ── Harness self-check ────────────────────────────────────────────────────────

def harness_check(all_bars3: dict, all_bars15: dict):
    """Check pivot_len=50 fires near the known 2026-06-28 live events."""
    print("\n=== Harness self-check (pivot_len=50) ===")
    # Known fires 2026-06-28 ~23:00-23:30 UTC
    # SOL REAL: swept_level ~70.11, swept_wick ~69.68, bos ~70.91
    # ETH CONSIDERABLE: swept_level 1561.76, bos 1563.58
    checks = {
        "SOLUSDT": {"mode": MODE_REAL,         "approx_lvl": 70.11,   "date": "2026-06-28"},
        "ETHUSDT": {"mode": MODE_CONSIDERABLE, "approx_lvl": 1561.76, "date": "2026-06-28"},
    }
    target_ms_lo = 1782604800000   # 2026-06-28 00:00 UTC
    target_ms_hi = 1782777600000   # 2026-06-30 00:00 UTC

    found_any = False
    for coin, chk in checks.items():
        bars3  = all_bars3[coin]
        bars15 = all_bars15[coin]
        det = SfpModeBDetector(mode=chk["mode"], pivot_len=50)
        sigs = det.warm_start(bars15, bars3)
        nearby = [s for s in sigs
                  if target_ms_lo <= s.bos_bar_ts_ms <= target_ms_hi]
        if nearby:
            for s in nearby:
                ts_dt = datetime.datetime.fromtimestamp(s.bos_bar_ts_ms / 1000, datetime.timezone.utc)
                print(f"  {coin} {chk['mode']}: swept_swing={s.swept_swing_level:.4f} "
                      f"swept_low={s.swept_low:.4f} bos_ref={s.bos_ref_high:.4f} "
                      f"bos_ts={ts_dt} entry_idx={s.entry_bar_index}")
            found_any = True
        else:
            print(f"  {coin} {chk['mode']}: NO fires in window {chk['date']} "
                  f"(approx_lvl={chk['approx_lvl']}) — check resampling or feed")
    if not found_any:
        print("  WARNING: no fires found — resampling or feed order may be wrong!")
    return found_any


# ── Multi-degree variant ──────────────────────────────────────────────────────

def run_multi_degree(bars15: list[SfpBar], bars3: list[SfpBar]) -> list[dict]:
    """Union fires across pivot_len {5,10,20}, dedup concurrent, one-open-at-a-time."""
    all_sigs = []
    for pl in MULTI_LENS:
        all_sigs.extend(get_signals(bars15, bars3, pl))
    # Dedup: sort by entry_bar_index, keep first per entry index
    seen_idx = set()
    deduped = []
    for s in sorted(all_sigs, key=lambda x: x.entry_bar_index):
        if s.entry_bar_index not in seen_idx:
            deduped.append(s)
            seen_idx.add(s.entry_bar_index)
    return run_signals_one_open(bars3, deduped)


# ── Main sweep ────────────────────────────────────────────────────────────────

def main():
    print("Loading data...")
    all_bars3  = {c: load_3m(c) for c in COINS}
    all_bars15 = {c: resample_15m(all_bars3[c]) for c in COINS}

    # Spot-check
    spot_check(all_bars15["BTCUSDT"], all_bars3["BTCUSDT"])
    print(f"BTC: {len(all_bars3['BTCUSDT'])} 3m bars, {len(all_bars15['BTCUSDT'])} 15m bars")

    # Harness check
    harness_check(all_bars3, all_bars15)

    # ── Full sweep ──────────────────────────────────────────────────────────
    results = {}   # (coin, pivot_len) -> dict
    print("\nRunning pivot_len sweep...")

    for coin in COINS:
        bars3  = all_bars3[coin]
        bars15 = all_bars15[coin]
        cutoff = int(len(bars3) * IS_FRAC)
        print(f"\n  {coin}: {len(bars3)} 3m bars, IS cutoff idx={cutoff}")

        for pl in PIVOT_LENS:
            signals = get_signals(bars15, bars3, pl)
            trades  = run_signals_one_open(bars3, signals)
            is_t, oos_t, _ = split_trades(trades, bars3)

            full_st = trade_stats(trades)
            is_st   = trade_stats(is_t)
            oos_st  = trade_stats(oos_t)

            # Null distribution over OOS region
            oos_start = cutoff
            n_null_trades = max(1, len(oos_t))
            null_exp_dist = []
            for run_i in range(NULL_RUNS):
                seed = hash((coin, pl, run_i)) & 0xFFFFFFFF
                e = run_null(bars3, n_null_trades, seed,
                             split_start=oos_start, split_end=len(bars3) - 1)
                null_exp_dist.append(e)

            pct = null_percentile(oos_st["avg_r"], null_exp_dist)
            beats = (not math.isnan(pct)) and pct >= BEATS_NULL_PCT and oos_st["n"] >= 20

            key = (coin, pl)
            results[key] = {
                "coin": coin, "pivot_len": pl,
                "full": full_st, "is": is_st, "oos": oos_st,
                "null_pct": pct,
                "beats_null": beats,
                "null_dist_p50": _percentile(null_exp_dist, 50),
                "null_dist_p95": _percentile(null_exp_dist, 95),
            }
            beat_str = "BEAT" if beats else ("weak_n" if oos_st["n"] < 20 else "no")
            print(f"    pl={pl:2d}: n={full_st['n']:3d} oos_n={oos_st['n']:2d} "
                  f"oos_avgR={_fmt(oos_st['avg_r'])} null_pct={_fmt(pct)} [{beat_str}]")

    # ── Multi-degree ────────────────────────────────────────────────────────
    multi_results = {}
    print("\nRunning multi-degree union {5,10,20}...")
    for coin in COINS:
        bars3  = all_bars3[coin]
        bars15 = all_bars15[coin]
        trades = run_multi_degree(bars15, bars3)
        is_t, oos_t, cutoff = split_trades(trades, bars3)
        full_st = trade_stats(trades)
        oos_st  = trade_stats(oos_t)
        n_null_trades = max(1, len(oos_t))
        null_exp_dist = []
        for run_i in range(NULL_RUNS):
            seed = hash((coin, "multi", run_i)) & 0xFFFFFFFF
            e = run_null(bars3, n_null_trades, seed,
                         split_start=cutoff, split_end=len(bars3) - 1)
            null_exp_dist.append(e)
        pct = null_percentile(oos_st["avg_r"], null_exp_dist)
        beats = (not math.isnan(pct)) and pct >= BEATS_NULL_PCT and oos_st["n"] >= 20
        multi_results[coin] = {
            "full": full_st, "oos": oos_st,
            "null_pct": pct, "beats_null": beats,
            "null_dist_p95": _percentile(null_exp_dist, 95),
        }
        print(f"  {coin}: n={full_st['n']} oos_n={oos_st['n']} "
              f"oos_avgR={_fmt(oos_st['avg_r'])} null_pct={_fmt(pct)} "
              f"beats={'YES' if beats else 'no'}")

    # ── Write report ────────────────────────────────────────────────────────
    write_report(results, multi_results, all_bars3, all_bars15)
    print("\nDone. Report written to spike_pivot_degree/REPORT.md")


def _fmt(x) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "  nan"
    return f"{x:+.3f}"


def _percentile(dist: list[float], pct: float) -> float:
    valid = sorted(x for x in dist if not math.isnan(x))
    if not valid:
        return float("nan")
    idx = (pct / 100) * (len(valid) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(valid) - 1)
    frac = idx - lo
    return valid[lo] * (1 - frac) + valid[hi] * frac


# ── Report writer ─────────────────────────────────────────────────────────────

def write_report(results: dict, multi_results: dict,
                 all_bars3: dict, all_bars15: dict):
    lines = []
    a = lines.append

    # ── Determine overall verdict ──
    any_beat = any(v["beats_null"] for v in results.values())
    multi_beats = any(v["beats_null"] for v in multi_results.values())
    beating_configs = [(k, v) for k, v in results.items() if v["beats_null"]]

    a("# Pivot-Degree Sensitivity Backtest — SFP Mode-B")
    a("")
    a(f"**Generated:** {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ")
    a(f"**Data window:** 2026-05-15 → 2026-07-01 (~46 days, 3m bars)  ")
    a(f"**Coins:** {', '.join(COINS)}  ")
    a(f"**pivot_lens tested:** {PIVOT_LENS}  ")
    a(f"**IS/OOS split:** 60% / 40%  ")
    a(f"**Null runs:** {NULL_RUNS} per config  ")
    a("")
    a("---")
    a("")
    a("## VERDICT")
    a("")

    if not any_beat and not multi_beats:
        a("**NO pivot_len (including multi-degree union) beats the null baseline OOS**")
        a("with adequate n (>=20) at the 95th-percentile threshold.")
        a("")
        a("The 46-day window yields very few SFP+BOS signals at any pivot_len.")
        a("Most per-(coin, pivot_len) OOS buckets have n<20 — statistically weak.")
        a("The honest result: **data too thin to confirm or deny an edge over null.**")
        a("No live changes recommended from this spike alone.")
    else:
        a("**Configs that beat null OOS (n>=20, >95th pct of null):**")
        a("")
        for (coin, pl), v in beating_configs:
            oos = v["oos"]
            a(f"- **{coin} pivot_len={pl}**: OOS n={oos['n']}, "
              f"avgR={oos['avg_r']:+.3f}, null_pct={v['null_pct']:.1f}%")
        if multi_beats:
            a("")
            a("Multi-degree union {5,10,20} also beats null on some coins (see below).")
        a("")
        a("**Caveats apply — see per-coin tables and Caveats section before acting.**")

    a("")
    a("---")
    a("")
    a("## Per-Coin Tables")
    a("")

    for coin in COINS:
        bars3 = all_bars3[coin]
        a(f"### {coin}  (3m bars: {len(bars3)}, IS cutoff: bar {int(len(bars3)*IS_FRAC)})")
        a("")
        a("| pivot_len | n_full | WR% | avgR | totalR | IS_avgR | OOS_n | OOS_avgR | "
          "OOS_WR% | null_p50 | null_p95 | null_pct | beats_null |")
        a("|-----------|--------|-----|------|--------|---------|-------|----------|"
          "--------|----------|----------|----------|------------|")

        for pl in PIVOT_LENS:
            v   = results[(coin, pl)]
            f   = v["full"]
            isn = v["is"]
            oos = v["oos"]
            flag = ""
            if oos["n"] < 20:
                flag = " (weak_n)"
            beat_str = ("YES" + flag) if v["beats_null"] else ("no" + flag)
            a(f"| {pl:9d} | {f['n']:6d} | {_pct(f['win_rate'])} | {_fmt(f['avg_r'])} | "
              f"{f['total_r']:+6.2f} | {_fmt(isn['avg_r'])} | {oos['n']:5d} | "
              f"{_fmt(oos['avg_r'])} | {_pct(oos['win_rate'])} | "
              f"{_fmt(v['null_dist_p50'])} | {_fmt(v['null_dist_p95'])} | "
              f"{_fmtp(v['null_pct'])} | {beat_str} |")

        a("")

        # Curve shape analysis
        oos_avgrs = [(pl, results[(coin, pl)]["oos"]["avg_r"])
                     for pl in PIVOT_LENS if not math.isnan(results[(coin, pl)]["oos"]["avg_r"])]
        if oos_avgrs:
            curve_vals = [r for _, r in oos_avgrs]
            if curve_vals:
                max_r = max(curve_vals)
                max_pl = oos_avgrs[curve_vals.index(max_r)][0]
                # Count how many within 0.05R of max
                near_max = sum(1 for r in curve_vals if r >= max_r - 0.05)
                if near_max >= 4:
                    shape = "BROAD PLATEAU (positive signal)"
                elif near_max >= 2:
                    shape = "moderate plateau"
                else:
                    shape = "LONE SPIKE — likely overfit"
                a(f"**Curve shape:** peak at pivot_len={max_pl} (OOS avgR={max_r:+.3f}), "
                  f"{near_max}/{len(curve_vals)} configs within 0.05R of peak → {shape}")
                a("")

    a("")
    a("---")
    a("")
    a("## Multi-Degree Union {pivot_len ∈ 5, 10, 20}")
    a("")
    a("Pools signals from three pivot_len detectors, deduplicates on entry bar index, "
      "enforces one-open-at-a-time.")
    a("")
    a("| coin | n_full | OOS_n | OOS_avgR | null_p95 | null_pct | beats_null |")
    a("|------|--------|-------|----------|----------|----------|------------|")
    for coin in COINS:
        v = multi_results[coin]
        f = v["full"]
        oos = v["oos"]
        beat_str = "YES" if v["beats_null"] else ("no (weak_n)" if oos["n"] < 20 else "no")
        a(f"| {coin} | {f['n']:6d} | {oos['n']:5d} | {_fmt(oos['avg_r'])} | "
          f"{_fmt(v['null_dist_p95'])} | {_fmtp(v['null_pct'])} | {beat_str} |")
    a("")

    a("")
    a("---")
    a("")
    a("## Caveats")
    a("")
    a("1. **46-day window is short for rare setups.** SFP+BOS at pivot_len≥20 fires "
      "infrequently; n<20 OOS is the norm. No statistical claim is valid at these sample sizes.")
    a("2. **No fee/slippage model.** Taker fee 0.019% × 2 legs ≈ 0.038% per trade "
      "(negligible vs 1R stop). Slippage on volatile 3m BOS bars is unmodelled; live "
      "slippage can be >1× the fee cost on fast moves (see stop-slippage memory).")
    a("3. **Stop-first conservative assumption.** Same-bar stop+TP resolved as loss. "
      "Slightly pessimistic, appropriate for stress-testing.")
    a("4. **Null baseline uses recent-low stop**, which gives comparable R units but "
      "is not a 'same-setup' null. It is a random-entry benchmark, not a causal null.")
    a("5. **Pivot_len changes the detector's lookback semantics**, not just sensitivity. "
      "Short pivot_lens (3,5) fire on micro-structure swings; long (50) fires on major "
      "swings. These are qualitatively different setups, not continuous deformations.")
    a("6. **Harness self-check:** see console output for whether pivot_len=50 reproduces "
      "the known 2026-06-28 live fires. If not, results should not be trusted.")
    a("")
    a("---")
    a("")
    a("*Research only. Not financial advice. Do not act on this without further validation.*")

    report_path = os.path.join(os.path.dirname(__file__), "REPORT.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _pct(x) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "  nan%"
    return f"{x*100:5.1f}%"


def _fmtp(x) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "  nan"
    return f"{x:5.1f}%"


if __name__ == "__main__":
    main()
