"""Phase 2 — compute realized 30/60min outcomes for HTF proximity rejections.

Inputs (in same dir):
    rejections.tsv  — 157 rejection rows (TSV w/ header) from audit_event
    bars_3m.tsv     — ~7680 3m bars (TSV w/ header) from bitunix_bar_history

Output:
    analysis.md     — Markdown report w/ cohort statistics
    rejections_enriched.tsv — per-row enrichment (decision_price, +30m, +60m,
                              MFE, MAE, block_was_wrong flags)
"""
from __future__ import annotations
import csv
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

HERE = Path(__file__).parent
REJECTIONS = HERE / "rejections.tsv"
BARS = HERE / "bars_3m.tsv"
ANALYSIS = HERE / "analysis.md"
ENRICHED = HERE / "rejections_enriched.tsv"

BAR_MS = 180_000  # 3 minutes in ms


def iso_to_ms(s: str) -> int:
    """'2026-05-31T13:57:01+00:00' -> epoch ms (int)."""
    dt = datetime.fromisoformat(s)
    return int(dt.timestamp() * 1000)


def load_bars() -> tuple[list[dict], dict[int, int]]:
    """Returns (bars_sorted, ts_to_idx)."""
    bars = []
    with BARS.open(newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            bars.append({
                "ts_ms": int(row["ts_ms"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
    bars.sort(key=lambda b: b["ts_ms"])
    idx = {b["ts_ms"]: i for i, b in enumerate(bars)}
    return bars, idx


def floor_to_bar(ts_ms: int) -> int:
    return (ts_ms // BAR_MS) * BAR_MS


def lookup_bar_idx(idx: dict[int, int], bars: list[dict], target_ts_ms: int) -> int | None:
    """Find the bar at-or-just-before target_ts_ms. Returns index or None."""
    if target_ts_ms in idx:
        return idx[target_ts_ms]
    # Linear scan back up to 5 slots (handles gaps where bars are missing)
    for back in range(1, 6):
        cand = target_ts_ms - back * BAR_MS
        if cand in idx:
            return idx[cand]
    return None


def compute_outcome(decision_ts_ms: int, bars: list[dict], idx: dict[int, int]) -> dict:
    """For a sell rejection at decision_ts_ms, compute fwd outcomes."""
    decision_bar_ts = floor_to_bar(decision_ts_ms)
    i = lookup_bar_idx(idx, bars, decision_bar_ts)
    if i is None:
        return {"decision_price": None, "reason": "no_decision_bar"}
    decision_price = bars[i]["close"]

    # +30min = i + 10 bars (closed at +30m)
    # +60min = i + 20 bars (closed at +60m)
    out = {"decision_price": decision_price}
    for label, n_fwd in (("close_30m", 10), ("close_60m", 20)):
        ti = i + n_fwd
        if ti < len(bars):
            out[label] = bars[ti]["close"]
        else:
            out[label] = None

    # MFE (sell): max favorable = decision_price - min low over [i+1 .. i+20]
    # MAE (sell): max adverse   = max high over [i+1 .. i+20] - decision_price
    fwd_slice = bars[i + 1 : i + 21]
    if fwd_slice:
        out["min_low_60m"] = min(b["low"] for b in fwd_slice)
        out["max_high_60m"] = max(b["high"] for b in fwd_slice)
    else:
        out["min_low_60m"] = None
        out["max_high_60m"] = None
    return out


def pct(numerator: float, denominator: float) -> float:
    return (numerator / denominator) * 100.0 if denominator else 0.0


def main() -> None:
    bars, idx = load_bars()
    print(f"Loaded {len(bars)} 3m bars (first ts_ms={bars[0]['ts_ms']}, last ts_ms={bars[-1]['ts_ms']})")

    enriched_rows = []
    skipped_no_bar = 0
    with REJECTIONS.open(newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            row["decision_ts_ms"] = iso_to_ms(row["ts"])
            outcome = compute_outcome(row["decision_ts_ms"], bars, idx)
            if outcome["decision_price"] is None:
                skipped_no_bar += 1
                row.update({k: None for k in ("decision_price", "close_30m", "close_60m",
                                              "delta_30m_pct", "delta_60m_pct",
                                              "mfe_sell_pct", "mae_sell_pct",
                                              "block_wrong_30m_loose", "block_wrong_60m_loose",
                                              "block_wrong_60m_strong")})
                enriched_rows.append(row)
                continue
            dp = outcome["decision_price"]
            row["decision_price"] = dp
            row["close_30m"] = outcome["close_30m"]
            row["close_60m"] = outcome["close_60m"]
            # Sign convention: for a SELL rejection, positive delta_pct = favor SELL = block was WRONG
            row["delta_30m_pct"] = pct(dp - outcome["close_30m"], dp) if outcome["close_30m"] else None
            row["delta_60m_pct"] = pct(dp - outcome["close_60m"], dp) if outcome["close_60m"] else None
            row["mfe_sell_pct"] = pct(dp - outcome["min_low_60m"], dp) if outcome["min_low_60m"] else None
            row["mae_sell_pct"] = pct(outcome["max_high_60m"] - dp, dp) if outcome["max_high_60m"] else None

            # For buy-rejections (proximity_to_resistance) flip the sign convention
            if row["side"] == "buy":
                if row["delta_30m_pct"] is not None: row["delta_30m_pct"] = -row["delta_30m_pct"]
                if row["delta_60m_pct"] is not None: row["delta_60m_pct"] = -row["delta_60m_pct"]
                # MFE_buy = max_high - decision_price; MAE_buy = decision_price - min_low
                row["mfe_buy_pct"] = pct(outcome["max_high_60m"] - dp, dp) if outcome["max_high_60m"] else None
                row["mae_buy_pct"] = pct(dp - outcome["min_low_60m"], dp) if outcome["min_low_60m"] else None
                # Reuse the sell-named cols for buy MFE/MAE for uniform downstream cohort math
                row["mfe_sell_pct"] = row["mfe_buy_pct"]
                row["mae_sell_pct"] = row["mae_buy_pct"]

            # "Block was wrong" flags — adverse move against block
            # Sign already canonicalized above so positive = block-was-wrong
            d30 = row["delta_30m_pct"]
            d60 = row["delta_60m_pct"]
            row["block_wrong_30m_loose"] = (d30 is not None and d30 >= 0.10)
            row["block_wrong_60m_loose"] = (d60 is not None and d60 >= 0.10)
            row["block_wrong_60m_strong"] = (d60 is not None and d60 >= 0.30)

            enriched_rows.append(row)

    print(f"Enriched {len(enriched_rows)} rows ({skipped_no_bar} had no decision bar)")

    # Write enriched TSV — union of all keys (buy rejections have mfe_buy_pct etc.)
    if enriched_rows:
        all_keys: list[str] = []
        seen = set()
        for r in enriched_rows:
            for k in r:
                if k not in seen:
                    seen.add(k)
                    all_keys.append(k)
        with ENRICHED.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=all_keys, delimiter="\t", extrasaction="ignore")
            w.writeheader()
            w.writerows(enriched_rows)

    # Cohort aggregation
    def stats(rows: list[dict], field: str) -> dict:
        vals = [r[field] for r in rows if r.get(field) is not None]
        if not vals:
            return {"n": 0, "mean": None, "median": None, "min": None, "max": None}
        return {
            "n": len(vals),
            "mean": round(mean(vals), 4),
            "median": round(median(vals), 4),
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
        }

    def cell_stats(rows: list[dict]) -> dict:
        valid = [r for r in rows if r.get("delta_60m_pct") is not None]
        n = len(valid)
        if n == 0:
            return {"n": 0}
        d30 = stats(valid, "delta_30m_pct")
        d60 = stats(valid, "delta_60m_pct")
        mfe = stats(valid, "mfe_sell_pct")
        mae = stats(valid, "mae_sell_pct")
        loose60 = sum(1 for r in valid if r.get("block_wrong_60m_loose"))
        strong60 = sum(1 for r in valid if r.get("block_wrong_60m_strong"))
        return {
            "n": n,
            "d30_mean": d30["mean"], "d30_median": d30["median"],
            "d60_mean": d60["mean"], "d60_median": d60["median"],
            "mfe_mean": mfe["mean"], "mae_mean": mae["mean"],
            "block_wrong_60m_loose_pct": round(loose60 / n * 100, 1),
            "block_wrong_60m_strong_pct": round(strong60 / n * 100, 1),
        }

    # Group: reason × regime × side × tier (matches Probe B)
    cohorts: dict[tuple, list[dict]] = {}
    for r in enriched_rows:
        key = (r["hard_zero_reason"], r["regime"], r["side"], r["tier"])
        cohorts.setdefault(key, []).append(r)

    # Group: distance bucket (proximity-to-support only, sell rejections)
    sell_support = [r for r in enriched_rows
                    if r["hard_zero_reason"] == "proximity_to_support" and r["side"] == "sell"]
    def dist_bucket(d_str) -> str:
        try:
            d = float(d_str)
        except (TypeError, ValueError):
            return "n/a"
        if d < 0.10: return "<0.10%"
        if d < 0.20: return "0.10-0.20%"
        if d < 0.30: return "0.20-0.30%"
        return ">=0.30% (?)"  # shouldn't happen since rule blocks at <0.30
    dist_cohorts: dict[str, list[dict]] = {}
    for r in sell_support:
        dist_cohorts.setdefault(dist_bucket(r["dist_support_pct"]), []).append(r)

    # By regime alone (collapsing tier)
    regime_cohorts: dict[str, list[dict]] = {}
    for r in sell_support:
        regime_cohorts.setdefault(r["regime"], []).append(r)

    # By trigger_signal
    trigger_cohorts: dict[str, list[dict]] = {}
    for r in sell_support:
        trigger_cohorts.setdefault(r["trigger_signal"] or "(none)", []).append(r)

    # Overall (all 155 sell support rejections)
    overall = cell_stats(sell_support)

    # Write Markdown report
    with ANALYSIS.open("w", encoding="utf-8") as f:
        f.write("# HTF proximity hard-zero — historical audit (Phase 2)\n\n")
        f.write(f"**Window:** 2026-05-16 → 2026-05-31 UTC | **N:** 157 rejections "
                f"(155 proximity_to_support sell + 2 proximity_to_resistance buy)\n\n")
        f.write("## Convention\n\n")
        f.write("- All deltas are signed so that **positive = the block was wrong** "
                "(price moved in the rejected direction).\n")
        f.write("- `delta_30m_pct` / `delta_60m_pct` = (decision_price - close_at_+Nm) / decision_price × 100, "
                "with sign flipped for buy-rejections.\n")
        f.write("- `mfe_sell_pct` = max favorable excursion for a sell entry (i.e., how far price dropped below "
                "decision_price within 60min). For buy-rejections this is repurposed as MFE_buy.\n")
        f.write("- `mae_sell_pct` = max adverse excursion for a sell entry (i.e., how far price rose above "
                "decision_price within 60min).\n")
        f.write("- **block_wrong_60m_loose** = price moved ≥0.10% in the rejected direction by +60min close. "
                "**strong** = ≥0.30% (would have cleared the typical fee floor with margin).\n\n")
        f.write("## Overall — proximity_to_support sells (N=155)\n\n")
        f.write(f"- N with valid +60m close: **{overall['n']}**\n")
        f.write(f"- Mean Δ+30m: **{overall['d30_mean']}%** (median {overall['d30_median']}%)\n")
        f.write(f"- Mean Δ+60m: **{overall['d60_mean']}%** (median {overall['d60_median']}%)\n")
        f.write(f"- Mean MFE (sell win available): **{overall['mfe_mean']}%**\n")
        f.write(f"- Mean MAE (sell adverse): **{overall['mae_mean']}%**\n")
        f.write(f"- **% block-wrong @ +60m, loose (≥0.10%):** {overall['block_wrong_60m_loose_pct']}%\n")
        f.write(f"- **% block-wrong @ +60m, strong (≥0.30%):** {overall['block_wrong_60m_strong_pct']}%\n\n")

        f.write("## By reason × regime × side × tier (matches Probe B)\n\n")
        f.write("| reason | regime | side | tier | N | Δ+30m | Δ+60m | MFE | MAE | wrong@60m loose | wrong@60m strong |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|---|\n")
        for key in sorted(cohorts.keys(), key=lambda k: -len(cohorts[k])):
            s = cell_stats(cohorts[key])
            if s["n"] == 0:
                continue
            f.write(f"| {key[0]} | {key[1]} | {key[2]} | {key[3]} | {s['n']} | "
                    f"{s['d30_mean']}% | {s['d60_mean']}% | {s['mfe_mean']}% | {s['mae_mean']}% | "
                    f"{s['block_wrong_60m_loose_pct']}% | {s['block_wrong_60m_strong_pct']}% |\n")

        f.write("\n## By distance bucket (proximity_to_support sells)\n\n")
        f.write("| dist bucket | N | Δ+30m | Δ+60m | MFE | MAE | wrong@60m loose | wrong@60m strong |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        for k in ["<0.10%", "0.10-0.20%", "0.20-0.30%", ">=0.30% (?)", "n/a"]:
            rs = dist_cohorts.get(k, [])
            if not rs: continue
            s = cell_stats(rs)
            f.write(f"| {k} | {s['n']} | {s['d30_mean']}% | {s['d60_mean']}% | "
                    f"{s['mfe_mean']}% | {s['mae_mean']}% | "
                    f"{s['block_wrong_60m_loose_pct']}% | {s['block_wrong_60m_strong_pct']}% |\n")

        f.write("\n## By regime (proximity_to_support sells, collapsing tier)\n\n")
        f.write("| regime | N | Δ+30m | Δ+60m | MFE | MAE | wrong@60m loose | wrong@60m strong |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        for k in sorted(regime_cohorts.keys()):
            s = cell_stats(regime_cohorts[k])
            f.write(f"| {k} | {s['n']} | {s['d30_mean']}% | {s['d60_mean']}% | "
                    f"{s['mfe_mean']}% | {s['mae_mean']}% | "
                    f"{s['block_wrong_60m_loose_pct']}% | {s['block_wrong_60m_strong_pct']}% |\n")

        f.write("\n## By trigger_signal (proximity_to_support sells)\n\n")
        f.write("| trigger | N | Δ+60m | MFE | MAE | wrong@60m loose |\n")
        f.write("|---|---|---|---|---|---|\n")
        for k in sorted(trigger_cohorts.keys(), key=lambda k: -len(trigger_cohorts[k])):
            s = cell_stats(trigger_cohorts[k])
            f.write(f"| {k} | {s['n']} | {s['d60_mean']}% | {s['mfe_mean']}% | "
                    f"{s['mae_mean']}% | {s['block_wrong_60m_loose_pct']}% |\n")

        f.write("\n## Resistance rejections (buys, N=2)\n\n")
        buy_resist = [r for r in enriched_rows if r["hard_zero_reason"] == "proximity_to_resistance"]
        f.write(f"Total: {len(buy_resist)}. Too few to analyze; row dump:\n\n")
        for r in buy_resist:
            f.write(f"- {r['ts']} {r['regime']} buy {r['tier']} "
                    f"dist_resist={r['dist_resist_pct']}% Δ+60m={r.get('delta_60m_pct')}%\n")

        # Sanity check: bars used vs missing
        no_bar = sum(1 for r in enriched_rows if r.get("decision_price") is None)
        no_60m = sum(1 for r in enriched_rows if r.get("delta_60m_pct") is None)
        f.write(f"\n## Data quality\n\n")
        f.write(f"- Rows with no decision bar: {no_bar}\n")
        f.write(f"- Rows with no +60m close: {no_60m}\n")

    print(f"Wrote {ANALYSIS}")
    print(f"Wrote {ENRICHED}")


if __name__ == "__main__":
    main()
