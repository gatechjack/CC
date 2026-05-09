"""Coinbase BTC Accumulator — walk-forward (out-of-sample) validator.

Splits the corpus into a training half and a test half. Runs the
parameter sweep on training only, picks the top-N configs by
pct_return, then re-runs each of them on the test half. Compares
train-vs-test returns: configs that win on training but lose on
test are overfit; configs that win on both are robust.

This is the load-bearing tool for "are our weights generalizable?"
The thresholds_only sweep on the full 8.5-day corpus found
min_buy=4/min_sell=16 → +6.31% (vs HODL +5.79% in same window).
That's training-set performance. Walk-forward reveals whether the
pattern survives on data the tuner didn't see.

Usage:
  python scripts/walkforward_btc_accumulator.py \\
    --start 2026-04-30 --end 2026-05-09 \\
    --split 2026-05-04 \\
    --grid thresholds_only --top 10

  --split is the boundary timestamp. Alerts before split = training,
  alerts at-or-after split = test. Roughly 50/50 by default.

Output:
  - data/backtest_runs/walkforward_<utc-timestamp>/
      results.json    — full ranked train+test rows
      results.md      — comparison table sorted by training return,
                        with test return next to it for spot-check
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.backtest_btc_accumulator import (  # noqa: E402
    fetch_alerts_from_prod,
    fetch_ohlcv_from_coinbase,
    run_backtest,
)
from scripts.sweep_btc_accumulator import (  # noqa: E402
    GRIDS,
    build_config_for_combo,
    expand_grid,
)
from trading_corp.agents.strategies.btc_accumulator import State  # noqa: E402


log = logging.getLogger("walkforward_btc_accumulator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _split_alerts(alerts: list, split_ts: datetime) -> tuple[list, list]:
    train = [a for a in alerts if a.ts < split_ts]
    test  = [a for a in alerts if a.ts >= split_ts]
    return train, test


def _split_bars(bars: list, split_ts: datetime) -> tuple[list, list]:
    train = [b for b in bars if b["ts"] < split_ts]
    test  = [b for b in bars if b["ts"] >= split_ts]
    return train, test


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument(
        "--split", required=True,
        help="UTC date (YYYY-MM-DD) — alerts/bars before this timestamp are training; "
             "from this timestamp onward are out-of-sample test",
    )
    p.add_argument("--starting-cash", type=float, default=10_000.0)
    p.add_argument("--starting-state", choices=["cash", "btc"], default="cash")
    p.add_argument("--grid", choices=list(GRIDS.keys()), default="thresholds_only")
    p.add_argument("--config", default=str(_REPO_ROOT / "config" / "strategies.yaml"))
    p.add_argument("--top", type=int, default=10,
                   help="Re-run this many top-train configs on the test half")
    p.add_argument("--output", default=None)
    p.add_argument("--refresh", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    split = datetime.fromisoformat(args.split).replace(tzinfo=timezone.utc)
    if not (start < split < end):
        log.error("split must be strictly between start and end")
        return 2

    raw_yaml = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    base = raw_yaml["btc_accumulator"]

    alerts = fetch_alerts_from_prod(start, end, args.refresh)
    bars = fetch_ohlcv_from_coinbase(start, end, args.refresh)

    train_alerts, test_alerts = _split_alerts(alerts, split)
    train_bars, test_bars = _split_bars(bars, split)
    log.info(
        "Split @ %s — train: %d alerts / %d bars  ·  test: %d alerts / %d bars",
        split.isoformat(), len(train_alerts), len(train_bars),
        len(test_alerts), len(test_bars),
    )

    starting_state = State.CASH if args.starting_state == "cash" else State.BTC

    grid = GRIDS[args.grid]
    combos = expand_grid(grid)
    log.info("Sweep grid: %d combinations", len(combos))

    # Stage 1 — sweep on training half
    train_results = []
    for i, combo in enumerate(combos, 1):
        config = build_config_for_combo(base, combo)
        try:
            _, summary = run_backtest(
                alerts=train_alerts, bars=train_bars, config=config,
                starting_cash=args.starting_cash, starting_state=starting_state,
            )
            train_results.append({"combo": combo, "summary": asdict(summary)})
        except Exception as e:
            log.warning("[train %d/%d] failed: %s", i, len(combos), e)
        if i % 25 == 0 or i == len(combos):
            log.info("Train sweep: %d/%d", i, len(combos))

    # Sort by training pct_return desc, take top-N
    valid_train = [r for r in train_results if r.get("summary")]
    valid_train.sort(key=lambda r: r["summary"]["pct_return"], reverse=True)
    top_train = valid_train[:args.top]

    # Stage 2 — re-run top-N on test half
    log.info("Re-running top-%d train configs on out-of-sample test half", len(top_train))
    rows = []
    for rank, r in enumerate(top_train, 1):
        combo = r["combo"]
        train_summary = r["summary"]
        config = build_config_for_combo(base, combo)
        try:
            _, test_summary = run_backtest(
                alerts=test_alerts, bars=test_bars, config=config,
                starting_cash=args.starting_cash, starting_state=starting_state,
            )
            rows.append({
                "rank_train": rank,
                "combo": combo,
                "train": train_summary,
                "test": asdict(test_summary),
            })
        except Exception as e:
            log.warning("[test %d] failed: %s", rank, e)

    # HODL benchmarks per half
    def _hodl(bars_subset):
        if len(bars_subset) < 2:
            return 0.0
        return (bars_subset[-1]["close"] - bars_subset[0]["close"]) / bars_subset[0]["close"] * 100

    hodl_train = _hodl(train_bars)
    hodl_test  = _hodl(test_bars)

    # Output
    if args.output:
        out_dir = Path(args.output)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        out_dir = _REPO_ROOT / "data" / "backtest_runs" / f"walkforward_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "results.json").write_text(
        json.dumps(rows, indent=2, default=str), encoding="utf-8",
    )

    lines = [
        f"# BTC Accumulator Walk-Forward — Top {args.top} train configs vs out-of-sample test",
        "",
        f"- Split: `{split.isoformat()}`",
        f"- Train range: `{start.isoformat()}` → `{split.isoformat()}` ({len(train_alerts)} alerts)",
        f"- Test  range: `{split.isoformat()}` → `{end.isoformat()}` ({len(test_alerts)} alerts)",
        f"- HODL benchmark — train: {hodl_train:+.2f}%  ·  test: {hodl_test:+.2f}%",
        "",
        "| Rank | min_buy | min_sell | Train Ret | Train RT | Train Win% | Test Ret | Test RT | Test Win% | Train α vs HODL | Test α vs HODL |",
        "|------|---------|----------|-----------|----------|------------|----------|---------|-----------|-----------------|-----------------|",
    ]
    for row in rows:
        c = row["combo"]
        t = row["train"]
        x = row["test"]
        t_rt = t["round_trip_count"]
        x_rt = x["round_trip_count"]
        t_winpct = (t["win_count"] / t_rt * 100) if t_rt else 0
        x_winpct = (x["win_count"] / x_rt * 100) if x_rt else 0
        train_alpha = t["pct_return"] - hodl_train
        test_alpha  = x["pct_return"] - hodl_test
        lines.append(
            f"| {row['rank_train']} "
            f"| {c['min_score_buy']} "
            f"| {c['min_score_sell']} "
            f"| {t['pct_return']:+.2f}% "
            f"| {t_rt} "
            f"| {t_winpct:.0f}% "
            f"| {x['pct_return']:+.2f}% "
            f"| {x_rt} "
            f"| {x_winpct:.0f}% "
            f"| {train_alpha:+.2f}% "
            f"| {test_alpha:+.2f}% |"
        )

    lines.append("")
    # Aggregate
    test_alphas = [(row["test"]["pct_return"] - hodl_test) for row in rows]
    if test_alphas:
        lines.append(
            f"## Out-of-sample alpha summary (top {len(rows)} train configs)"
        )
        lines.append(f"- median test α vs HODL: {sorted(test_alphas)[len(test_alphas) // 2]:+.2f}%")
        lines.append(f"- best test α vs HODL: {max(test_alphas):+.2f}%")
        lines.append(f"- worst test α vs HODL: {min(test_alphas):+.2f}%")
        positives = sum(1 for a in test_alphas if a > 0)
        lines.append(
            f"- configs that beat HODL on test: {positives}/{len(test_alphas)} "
            f"({positives / len(test_alphas) * 100:.0f}%)"
        )

    (out_dir / "results.md").write_text("\n".join(lines), encoding="utf-8")
    log.info("Wrote walkforward results to %s", out_dir)
    print()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
