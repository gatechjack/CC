"""Donchian Channel Breakout — walk-forward (out-of-sample) validator.

Mirrors scripts/walkforward_btc_accumulator.py but for the Donchian
strategy: sweeps entry/exit lookbacks on a training half, picks the
top-N by training pct_return, re-runs each on the test half. Output
shows train-vs-test alpha so we can spot overfitting.

The 6-month BTC corpus (Nov 2025 → May 2026, BTC -26.85%) shows ALL
parameter combos in the rough range beating HODL on the full window.
This script confirms whether the WIN comes from genuine trend-
following edge (out-of-sample) or from picking-the-right-params
hindsight.

Usage:
  python scripts/walkforward_donchian.py \\
    --start 2025-11-01 --end 2026-05-09 \\
    --split 2026-02-01 \\
    --top 10
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.backtest_donchian import fetch_ohlcv, run_donchian_backtest  # noqa: E402
from trading_corp.agents.strategies.donchian_btc import (  # noqa: E402
    DonchianConfig,
    State,
)


log = logging.getLogger("walkforward_donchian")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Sweep grid ─────────────────────────────────────────────────────


GRID = {
    "entry_lookback": [12, 20, 24, 30, 40, 50, 55, 75, 100, 150],
    "exit_lookback":  [6,  10, 12, 15, 20, 25, 30, 50],
    "trend_filter":   [None, 168, 336, 720],   # None / 1w / 2w / 30d SMA
}


def expand_grid() -> list[dict]:
    out = []
    for e, x, t in itertools.product(GRID["entry_lookback"], GRID["exit_lookback"], GRID["trend_filter"]):
        if x >= e:
            continue   # exit lookback should be shorter than entry
        out.append({"entry_lookback": e, "exit_lookback": x, "trend_filter": t})
    return out


# ── Walkforward ────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--split", required=True)
    p.add_argument("--starting-cash", type=float, default=10_000.0)
    p.add_argument("--starting-state", choices=["cash", "btc"], default="cash")
    p.add_argument("--granularity", type=int, default=3600)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--output", default=None)
    p.add_argument("--refresh", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    split = datetime.fromisoformat(args.split).replace(tzinfo=timezone.utc)
    if not (start < split < end):
        log.error("split must be between start and end")
        return 2

    bars = fetch_ohlcv(start, end, args.granularity, args.refresh)
    train_bars = [b for b in bars if b["ts"] < split]
    test_bars  = [b for b in bars if b["ts"] >= split]
    log.info("Split @ %s — train: %d bars, test: %d bars",
             split.isoformat(), len(train_bars), len(test_bars))

    starting_state = State.CASH if args.starting_state == "cash" else State.BTC
    combos = expand_grid()
    log.info("Sweep grid: %d combinations", len(combos))

    # Train sweep
    train_rows = []
    for i, combo in enumerate(combos, 1):
        config = DonchianConfig(
            entry_lookback=combo["entry_lookback"],
            exit_lookback=combo["exit_lookback"],
            trend_filter_lookback=combo["trend_filter"],
            granularity_seconds=args.granularity,
        )
        try:
            _, summary = run_donchian_backtest(
                bars=train_bars, config=config,
                starting_cash=args.starting_cash, starting_state=starting_state,
            )
            train_rows.append({"combo": combo, "summary": asdict(summary)})
        except Exception as e:
            log.warning("[train %d] failed: %s", i, e)
        if i % 50 == 0 or i == len(combos):
            log.info("Train sweep: %d/%d", i, len(combos))

    valid_train = [r for r in train_rows if r.get("summary")]
    valid_train.sort(key=lambda r: r["summary"]["pct_return"], reverse=True)
    top_train = valid_train[:args.top]

    # HODL benchmarks
    def _hodl(bars_subset):
        if len(bars_subset) < 2:
            return 0.0
        return (bars_subset[-1]["close"] - bars_subset[0]["close"]) / bars_subset[0]["close"] * 100

    hodl_train = _hodl(train_bars)
    hodl_test  = _hodl(test_bars)

    # Test re-run for top-N
    log.info("Re-running top-%d train configs on out-of-sample test half", len(top_train))
    rows = []
    for r in top_train:
        combo = r["combo"]
        config = DonchianConfig(
            entry_lookback=combo["entry_lookback"],
            exit_lookback=combo["exit_lookback"],
            trend_filter_lookback=combo["trend_filter"],
            granularity_seconds=args.granularity,
        )
        try:
            _, test_summary = run_donchian_backtest(
                bars=test_bars, config=config,
                starting_cash=args.starting_cash, starting_state=starting_state,
            )
            rows.append({"combo": combo, "train": r["summary"], "test": asdict(test_summary)})
        except Exception as e:
            log.warning("test re-run failed: %s", e)

    # Output
    if args.output:
        out_dir = Path(args.output)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        out_dir = _REPO_ROOT / "data" / "backtest_runs" / f"walkforward_donchian_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "results.json").write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")

    lines = [
        f"# Donchian Walk-Forward — Top {args.top} train configs vs out-of-sample",
        "",
        f"- Granularity: {args.granularity}s",
        f"- Split: `{split.isoformat()}`",
        f"- Train: {start.isoformat()} -> {split.isoformat()}  ({len(train_bars)} bars)",
        f"- Test:  {split.isoformat()} -> {end.isoformat()}  ({len(test_bars)} bars)",
        f"- HODL — train: {hodl_train:+.2f}%  ·  test: {hodl_test:+.2f}%",
        "",
        "| # | entry | exit | trend | Train Ret | Train RT | Train W% | Test Ret | Test RT | Test W% | Train α | Test α |",
        "|---|-------|------|-------|-----------|----------|----------|----------|---------|---------|---------|--------|",
    ]
    for i, row in enumerate(rows, 1):
        c = row["combo"]
        t = row["train"]
        x = row["test"]
        t_rt = t["round_trip_count"]
        x_rt = x["round_trip_count"]
        t_w = (t["win_count"] / t_rt * 100) if t_rt else 0
        x_w = (x["win_count"] / x_rt * 100) if x_rt else 0
        lines.append(
            f"| {i} | {c['entry_lookback']} | {c['exit_lookback']} | {c['trend_filter']} "
            f"| {t['pct_return']:+.2f}% | {t_rt} | {t_w:.0f}% "
            f"| {x['pct_return']:+.2f}% | {x_rt} | {x_w:.0f}% "
            f"| {t['pct_return'] - hodl_train:+.2f}% "
            f"| {x['pct_return'] - hodl_test:+.2f}% |"
        )
    lines.append("")
    if rows:
        test_alphas = [r["test"]["pct_return"] - hodl_test for r in rows]
        positives = sum(1 for a in test_alphas if a > 0)
        lines.append(
            f"## Out-of-sample alpha summary (top {len(rows)} train configs)"
        )
        lines.append(f"- median test α vs HODL: {sorted(test_alphas)[len(test_alphas) // 2]:+.2f}%")
        lines.append(f"- best test α vs HODL: {max(test_alphas):+.2f}%")
        lines.append(f"- worst test α vs HODL: {min(test_alphas):+.2f}%")
        lines.append(
            f"- configs that beat HODL on test: {positives}/{len(test_alphas)} "
            f"({positives / len(test_alphas) * 100:.0f}%)"
        )

    (out_dir / "results.md").write_text("\n".join(lines), encoding="utf-8")
    log.info("Wrote %s", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
