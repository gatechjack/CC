"""Step 1 — per-signal × per-tf edge measurement.

For each (signal_name, tf) emitted by `synth_ledger`, measure:

- n_fires
- fires per day
- forward % return at horizons h ∈ {5, 15, 30, 60, 120} minutes in the
  signal's STATED direction (buy → return positive; sell → return positive
  when price falls, so we negate)
- hit rate at each horizon (% of fires where direction-adjusted return > 0)
- mean R-multiple if stop = max(1.5 × ATR(14), 0.3% × price), TP = 2R,
  resolution walked on 3m bars up to MAX_HOLD_BARS bars

Writes `reports/scoring_inventory.md`. Compares each signal's measured edge
to its current YAML weight; flags under/over-weighted signals.
"""
from __future__ import annotations

import sqlite3
import statistics
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from synth_ledger import (
    COL_TO_FACTOR,
    DB_PATH,
    SynthAlert,
    load_bars_3m_for_resolution,
    load_synth_ledger,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = REPO_ROOT / "config" / "strategies.yaml"
OUT_PATH = REPO_ROOT / "reports" / "scoring_inventory.md"

HORIZONS_MIN = (5, 15, 30, 60, 120)
MAX_HOLD_BARS = 24 * 20  # 24 × 20 × 3m = 24h
STOP_FLOOR_PCT = 0.003
ATR_MULTIPLIER = 1.5
TP_R = 2.0
ROUND_TRIP_BPS = 9.0   # 0.04% × 2 taker + 0.005% × 2 slippage


def load_current_factors() -> dict[str, dict]:
    with open(YAML_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg["bitunix_futures"]["scoring"]["factors"]


@dataclass
class BarIndex:
    """Index into the 3m bar stream by unix-seconds open ts."""
    ts: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    atr: list[float]

    @classmethod
    def build(cls, bars):
        return cls(
            ts=[b.ts for b in bars],
            open=[b.open for b in bars],
            high=[b.high for b in bars],
            low=[b.low for b in bars],
            close=[b.close for b in bars],
            atr=[b.atr for b in bars],
        )

    def find_at_or_after(self, ts_secs: int) -> int:
        """Return index of first bar with ts >= ts_secs; -1 if out of range.

        Returns -1 in BOTH directions: ts before first bar OR after last
        bar. This is critical — without it, pre-window alerts map to bar
        0 and every alert appears to enter on the first bar of the window.
        """
        if ts_secs < self.ts[0] or ts_secs > self.ts[-1]:
            return -1
        i = bisect_left(self.ts, ts_secs)
        return i if i < len(self.ts) else -1


def fwd_return_pct(idx: BarIndex, alert_ts_secs: int, horizon_min: int) -> float | None:
    """% close-to-close return from the bar at/after alert_ts to horizon_min later."""
    i = idx.find_at_or_after(alert_ts_secs)
    if i < 0:
        return None
    entry = idx.close[i]
    target_ts = idx.ts[i] + horizon_min * 60
    j = bisect_left(idx.ts, target_ts)
    if j >= len(idx.ts):
        return None
    exit_px = idx.close[j]
    if entry <= 0:
        return None
    return (exit_px - entry) / entry * 100.0


def simulate_2r_trade(
    idx: BarIndex, alert_ts_secs: int, side: str,
) -> tuple[str, float] | None:
    """Walk forward; return ("tp" / "sl" / "timeout", r_multiple).

    Entry at next bar open. Stop = max(1.5×ATR, 0.3% × entry). TP = 2R.
    Within the same bar, assume SL hit before TP (pessimistic).
    R-multiple measured AFTER 9 bps round-trip cost.
    """
    i = idx.find_at_or_after(alert_ts_secs)
    if i < 0 or i + 1 >= len(idx.ts):
        return None
    entry_i = i + 1  # next bar open
    entry = idx.open[entry_i]
    atr = idx.atr[entry_i] if idx.atr[entry_i] > 0 else 0.0004 * entry
    stop_dist = max(ATR_MULTIPLIER * atr, STOP_FLOOR_PCT * entry)
    tp_dist = TP_R * stop_dist
    if side == "buy":
        sl_px = entry - stop_dist
        tp_px = entry + tp_dist
    else:
        sl_px = entry + stop_dist
        tp_px = entry - tp_dist

    end_i = min(entry_i + MAX_HOLD_BARS, len(idx.ts))
    outcome = "timeout"
    exit_px = idx.close[end_i - 1]
    for j in range(entry_i, end_i):
        hi, lo = idx.high[j], idx.low[j]
        if side == "buy":
            sl_hit = lo <= sl_px
            tp_hit = hi >= tp_px
        else:
            sl_hit = hi >= sl_px
            tp_hit = lo <= tp_px
        if sl_hit and tp_hit:
            outcome, exit_px = "sl", sl_px
            break
        if sl_hit:
            outcome, exit_px = "sl", sl_px
            break
        if tp_hit:
            outcome, exit_px = "tp", tp_px
            break

    # P&L in R-units, net of 9 bps round-trip cost on entry notional
    if side == "buy":
        gross_pct = (exit_px - entry) / entry
    else:
        gross_pct = (entry - exit_px) / entry
    net_pct = gross_pct - (ROUND_TRIP_BPS / 10_000.0)
    r_multiple = net_pct * entry / stop_dist
    return outcome, r_multiple


def main() -> None:
    print("loading synthetic ledger...")
    alerts = load_synth_ledger()
    print(f"  {len(alerts)} alerts across {len({a.tf for a in alerts})} TFs")
    print("loading 3m bars for resolution...")
    bars = load_bars_3m_for_resolution()
    idx = BarIndex.build(bars)
    print(f"  {len(bars)} 3m bars, {datetime.fromtimestamp(bars[0].ts, tz=timezone.utc)} to {datetime.fromtimestamp(bars[-1].ts, tz=timezone.utc)}")

    yaml_factors = load_current_factors()

    # Resolve side per (signal_name, tf). Side comes from YAML.
    def side_of(name: str) -> str:
        body = yaml_factors.get(name)
        return body["side"] if body else "buy"

    # Bucket alerts by (signal, tf)
    buckets: dict[tuple[str, str], list[SynthAlert]] = defaultdict(list)
    for a in alerts:
        buckets[(a.signal_name, a.tf)].append(a)

    # Span in days
    span_secs = bars[-1].ts - bars[0].ts
    span_days = span_secs / 86400

    rows: list[dict] = []
    for (sig, tf), alist in buckets.items():
        side = side_of(sig)
        if side not in ("buy", "sell"):
            continue
        n = len(alist)
        per_day = n / span_days

        fwd: dict[int, list[float]] = {h: [] for h in HORIZONS_MIN}
        for a in alist:
            ts_s = int(a.ts.timestamp())
            for h in HORIZONS_MIN:
                ret = fwd_return_pct(idx, ts_s, h)
                if ret is None:
                    continue
                # Direction-adjusted: positive = "moved in signal's stated direction"
                signed = ret if side == "buy" else -ret
                fwd[h].append(signed)

        # 2R-trade simulation
        r_mults: list[float] = []
        outcomes = {"tp": 0, "sl": 0, "timeout": 0}
        for a in alist:
            ts_s = int(a.ts.timestamp())
            res = simulate_2r_trade(idx, ts_s, side)
            if res is None:
                continue
            outcome, r = res
            outcomes[outcome] += 1
            r_mults.append(r)

        row = {
            "signal": sig,
            "tf": tf,
            "side": side,
            "n": n,
            "per_day": round(per_day, 2),
            "current_weight": yaml_factors.get(sig, {}).get("weight"),
            "current_ttl_min": (
                yaml_factors.get(sig, {}).get("ttl_per_tf", {}).get(tf)
                or yaml_factors.get(sig, {}).get("ttl_minutes")
            ),
        }
        for h in HORIZONS_MIN:
            vals = fwd[h]
            if vals:
                row[f"mean_pct_{h}m"] = round(statistics.mean(vals), 4)
                row[f"hit_pct_{h}m"] = round(100 * sum(1 for v in vals if v > 0) / len(vals), 1)
            else:
                row[f"mean_pct_{h}m"] = None
                row[f"hit_pct_{h}m"] = None
        if r_mults:
            row["mean_r"] = round(statistics.mean(r_mults), 3)
            row["median_r"] = round(statistics.median(r_mults), 3)
            row["tp_rate"] = round(100 * outcomes["tp"] / len(r_mults), 1)
            row["sl_rate"] = round(100 * outcomes["sl"] / len(r_mults), 1)
            row["timeout_rate"] = round(100 * outcomes["timeout"] / len(r_mults), 1)
            row["positive_r_pct"] = round(100 * sum(1 for r in r_mults if r > 0) / len(r_mults), 1)
        else:
            row["mean_r"] = None
            row["tp_rate"] = None
        rows.append(row)

    # Sort by tf then by mean_r descending
    rows.sort(key=lambda r: (r["tf"], -(r.get("mean_r") or -99)))

    # Write report
    lines: list[str] = []
    lines.append("# Scoring inventory — per-signal × per-TF edge measurement")
    lines.append("")
    lines.append("**Source data:** synthesized ledger from `data/btc_scalping.db` "
                 "bar tables (3m / 15m / 30m), rising-edge of each TradingView "
                 "indicator column → factor mapping (see "
                 "`reports/scoring_decision_log.md` for mapping appendix). "
                 f"Window: {datetime.fromtimestamp(bars[0].ts, tz=timezone.utc).date()} → "
                 f"{datetime.fromtimestamp(bars[-1].ts, tz=timezone.utc).date()} "
                 f"({span_days:.0f} days). 3m bars used for SL/TP resolution.")
    lines.append("")
    lines.append("**Edge measurement:**")
    lines.append("- `mean_pct_Nm` = mean direction-adjusted % close-to-close return at horizon N min")
    lines.append("- `hit_pct_Nm` = % of fires where direction-adjusted return > 0")
    lines.append("- `mean_r` = mean R-multiple of a 2R-target trade, stop = max(1.5×ATR(14), 0.3%×price), 24h timeout, NET of 9 bps round-trip cost")
    lines.append("- `tp_rate / sl_rate / timeout_rate` = % of fires resolving as TP / SL / timeout")
    lines.append("")
    lines.append("**Quality flags:** ★ = positive net R AND ≥45% positive-R fires; "
                 "△ = breakeven (|mean_r| < 0.05); ✗ = negative mean R.")
    lines.append("")

    for tf in ("3m", "15m", "30m"):
        lines.append(f"## TF = {tf}")
        lines.append("")
        lines.append("| Signal | Side | Wt | TTL | N | /day | mean_r | +R% | TP% | SL% | TO% | hit_60m% | mean_60m% | flag |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|")
        for r in rows:
            if r["tf"] != tf:
                continue
            flag = ""
            if r["mean_r"] is None:
                flag = "?"
            elif r["mean_r"] >= 0.05 and (r.get("positive_r_pct") or 0) >= 45:
                flag = "★"
            elif abs(r["mean_r"]) < 0.05:
                flag = "△"
            elif r["mean_r"] < -0.05:
                flag = "✗"
            lines.append(
                f"| {r['signal']} | {r['side']} | {r['current_weight']} | "
                f"{r['current_ttl_min']} | {r['n']} | {r['per_day']} | "
                f"{r['mean_r']} | {r.get('positive_r_pct')} | "
                f"{r.get('tp_rate')} | {r.get('sl_rate')} | "
                f"{r.get('timeout_rate')} | {r['hit_pct_60m']} | "
                f"{r['mean_pct_60m']} | {flag} |"
            )
        lines.append("")

    # Per-family aggregate
    lines.append("## Per-family aggregate (3m fires only — primary execution TF)")
    lines.append("")
    fam_map = {
        "Cypher A": ["mc_a_"],
        "Cypher B": ["mc_b_"],
        "Otter trigger": ["otter_"],
        "Otter precision": ["money_bag_", "water_", "spoon_"],
        "CVD": ["cvd_"],
        "Bias / ribbon": ["bias_"],
    }
    fam_rows = []
    for fam_name, prefixes in fam_map.items():
        fam_r: list[float] = []
        fam_n = 0
        for r in rows:
            if r["tf"] != "3m":
                continue
            if not any(r["signal"].startswith(p) for p in prefixes):
                continue
            if r["mean_r"] is None:
                continue
            # Weight by n
            fam_r.extend([r["mean_r"]] * r["n"])
            fam_n += r["n"]
        if fam_r:
            fam_rows.append({
                "family": fam_name,
                "n": fam_n,
                "weighted_mean_r": round(statistics.mean(fam_r), 3),
            })
    fam_rows.sort(key=lambda x: -x["weighted_mean_r"])
    lines.append("| Family | N (3m fires) | weighted mean_r |")
    lines.append("|---|---:|---:|")
    for fr in fam_rows:
        lines.append(f"| {fr['family']} | {fr['n']} | {fr['weighted_mean_r']} |")
    lines.append("")

    # Weight-vs-edge gap analysis
    lines.append("## Current weight vs measured edge (3m primary, sorted by gap)")
    lines.append("")
    lines.append("Higher absolute gap = weight is mis-calibrated to measured edge. "
                 "The gap column is `(measured_mean_r × 4) - current_weight` — "
                 "i.e. a signal with mean_r=+0.5 'argues for' weight ~2 in a "
                 "0–5 scale; comparing to current weight surfaces miscalibration.")
    lines.append("")
    lines.append("| Signal | Side | Wt | mean_r (3m) | implied_wt | gap |")
    lines.append("|---|---|---:|---:|---:|---:|")
    gap_rows = []
    for r in rows:
        if r["tf"] != "3m" or r["mean_r"] is None or r["current_weight"] is None:
            continue
        implied = max(0, min(5, round(r["mean_r"] * 4 + 2.5)))  # map mean_r∈[-1,1] → wt∈[0,5]
        gap = implied - r["current_weight"]
        gap_rows.append((r["signal"], r["side"], r["current_weight"], r["mean_r"], implied, gap))
    gap_rows.sort(key=lambda x: -abs(x[5]))
    for sig, side, wt, mr, impl, gap in gap_rows:
        lines.append(f"| {sig} | {side} | {wt} | {mr} | {impl} | {gap:+d} |")

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
