"""Coinbase BTC Accumulator — parameter sweep harness.

Wraps `scripts/backtest_btc_accumulator.py`'s `run_backtest()` in a
parameter grid loop. For each combination, runs the backtest and
records summary metrics. Outputs a sorted table so the highest-
returning (or any other primary metric) configurations are visible
at a glance.

Today the sweep dimensions are:
  - min_score_buy
  - min_score_sell
  - cypher_1d_ttl_minutes
  - cypher_4h_ttl_minutes
  - cypher_weight_scalar (multiplies all cypher_* factor weights)
  - otter_weight_scalar (multiplies all otter-LTF factor weights)
  - pa_weight_scalar    (multiplies all price-action factor weights)

Pass --dimensions to control which dimensions vary; defaults to a
small grid (~30 combinations) suitable for an overnight or
mid-session run on the cached corpus.

Usage:
  python scripts/sweep_btc_accumulator.py \\
    --start 2026-04-30 --end 2026-05-09 \\
    --starting-cash 10000

  python scripts/sweep_btc_accumulator.py --grid wide --top 20

Output:
  - data/backtest_runs/sweep_<utc-timestamp>/
      results.json   — every config + summary metric
      results.md     — sorted table (default sort: pct_return desc)
      best.json      — top-N configurations
"""
from __future__ import annotations

import argparse
import copy
import itertools
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
from trading_corp.agents.strategies.btc_accumulator import (  # noqa: E402
    ConfluenceConfig,
    State,
)


log = logging.getLogger("sweep_btc_accumulator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Sweep grids ─────────────────────────────────────────────────────


GRIDS = {
    "small": {
        "min_score_buy":            [4, 6, 8, 10],
        "min_score_sell":           [4, 6, 8, 10],
        "cypher_1d_ttl_minutes":    [1440],
        "cypher_4h_ttl_minutes":    [240],
        "cypher_weight_scalar":     [1.0],
        "otter_weight_scalar":      [1.0],
        "pa_weight_scalar":         [1.0],
    },
    "wide": {
        "min_score_buy":            [4, 6, 8, 10, 12, 14],
        "min_score_sell":           [4, 6, 8, 10, 12, 14],
        "cypher_1d_ttl_minutes":    [240, 720, 1440],
        "cypher_4h_ttl_minutes":    [120, 240, 480],
        "cypher_weight_scalar":     [0.5, 1.0, 1.5],
        "otter_weight_scalar":      [0.5, 1.0, 1.5],
        "pa_weight_scalar":         [0.0, 1.0, 2.0],
    },
    "thresholds_only": {
        "min_score_buy":            [4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16],
        "min_score_sell":           [4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16],
        "cypher_1d_ttl_minutes":    [1440],
        "cypher_4h_ttl_minutes":    [240],
        "cypher_weight_scalar":     [1.0],
        "otter_weight_scalar":      [1.0],
        "pa_weight_scalar":         [1.0],
    },
}


# ── Config mutation ─────────────────────────────────────────────────


def _is_cypher(name: str) -> bool:
    return name.startswith("mc_") or name.startswith("cypher_")


def _is_otter(name: str) -> bool:
    """Otter-LTF: everything that's not Cypher and not a PA factor."""
    if _is_cypher(name) or _is_pa(name):
        return False
    return True   # otter_buy/sell, pink_box_*, spoon_*, cvd_*, etc.


def _is_pa(name: str) -> bool:
    return name in {
        "above_session_vwap", "below_session_vwap",
        "higher_highs_4h", "lower_lows_4h",
        "volume_above_20bar_avg",
    }


def build_config_for_combo(base_yaml: dict, combo: dict) -> ConfluenceConfig:
    """Apply a sweep combo to a copy of the YAML config and parse
    into a ConfluenceConfig."""
    raw = copy.deepcopy(base_yaml)
    raw["confluence"]["min_score_buy"] = combo["min_score_buy"]
    raw["confluence"]["min_score_sell"] = combo["min_score_sell"]

    factors = raw["confluence"]["factors"]
    for name, body in factors.items():
        if _is_cypher(name):
            # Cypher TTL split: 1D vs 4h. We detect 1D by the `mc_a_*`
            # prefix today (see strategies.yaml comment); `mc_b_*` is 4h.
            # Robust to other futures conventions: anything with a TTL
            # >= 1000 minutes is treated as 1D; otherwise 4h.
            if name.startswith("mc_a_") or body.get("ttl_minutes", 0) >= 1000:
                body["ttl_minutes"] = combo["cypher_1d_ttl_minutes"]
            else:
                body["ttl_minutes"] = combo["cypher_4h_ttl_minutes"]
            body["weight"] = max(0, int(round(
                body["weight"] * combo["cypher_weight_scalar"]
            )))
        elif _is_otter(name):
            body["weight"] = max(0, int(round(
                body["weight"] * combo["otter_weight_scalar"]
            )))
        elif _is_pa(name):
            body["weight"] = max(0, int(round(
                body["weight"] * combo["pa_weight_scalar"]
            )))
    return ConfluenceConfig.from_dict(raw)


def expand_grid(grid: dict[str, list]) -> list[dict]:
    """Cartesian product of all dimensions → list of combo dicts."""
    keys = list(grid.keys())
    values = [grid[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


# ── Sweep loop ──────────────────────────────────────────────────────


def run_sweep(
    *,
    base_yaml: dict,
    grid: dict,
    alerts: list,
    bars: list,
    starting_cash: float,
    starting_state: State,
) -> list[dict]:
    """Run backtest for each combination in the expanded grid.
    Returns list of {combo: dict, summary: dict}."""
    combos = expand_grid(grid)
    log.info("Sweep grid expanded to %d combinations", len(combos))
    results: list[dict] = []
    for i, combo in enumerate(combos, 1):
        config = build_config_for_combo(base_yaml, combo)
        try:
            _, summary = run_backtest(
                alerts=alerts, bars=bars, config=config,
                starting_cash=starting_cash, starting_state=starting_state,
            )
            results.append({"combo": combo, "summary": asdict(summary)})
        except Exception as e:
            log.warning("[%d/%d] combo failed: %s — %s", i, len(combos), combo, e)
            results.append({"combo": combo, "summary": None, "error": str(e)})
        if i % 20 == 0 or i == len(combos):
            log.info("Sweep progress: %d/%d", i, len(combos))
    return results


# ── Output ──────────────────────────────────────────────────────────


def write_sweep_outputs(
    results: list[dict],
    output_dir: Path,
    grid_name: str,
    sort_key: str,
    top_n: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    valid = [r for r in results if r.get("summary")]
    valid.sort(key=lambda r: r["summary"].get(sort_key, float("-inf")), reverse=True)

    # Full results JSON
    (output_dir / "results.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8",
    )
    # Top-N JSON
    (output_dir / "best.json").write_text(
        json.dumps(valid[:top_n], indent=2, default=str), encoding="utf-8",
    )

    # Markdown summary
    lines = [
        f"# BTC Accumulator Sweep — Top {top_n} by {sort_key}",
        "",
        f"- Grid: `{grid_name}`",
        f"- Total combinations: {len(results)}",
        f"- Successful runs: {len(valid)}",
        "",
        "| Rank | min_buy | min_sell | cy_1d_ttl | cy_4h_ttl | cy_w | ot_w | pa_w | RT | %Return | %MaxDD | Win% | %Time BTC |",
        "|------|---------|----------|-----------|-----------|------|------|------|-----|---------|--------|------|-----------|",
    ]
    for rank, r in enumerate(valid[:top_n], 1):
        c = r["combo"]
        s = r["summary"]
        wins = s.get("win_count", 0)
        losses = s.get("loss_count", 0)
        rt = s.get("round_trip_count", 0)
        win_pct = (wins / rt * 100) if rt else 0.0
        lines.append(
            f"| {rank} "
            f"| {c['min_score_buy']} "
            f"| {c['min_score_sell']} "
            f"| {c['cypher_1d_ttl_minutes']} "
            f"| {c['cypher_4h_ttl_minutes']} "
            f"| {c['cypher_weight_scalar']} "
            f"| {c['otter_weight_scalar']} "
            f"| {c['pa_weight_scalar']} "
            f"| {rt} "
            f"| {s['pct_return']:+.2f}% "
            f"| {s['max_drawdown_pct']:.2f}% "
            f"| {win_pct:.0f}% "
            f"| {s['pct_time_in_btc']:.1f}% |"
        )
    lines.append("")
    lines.append(f"## Median across all {len(valid)} runs:")
    if valid:
        rets = sorted([r["summary"]["pct_return"] for r in valid])
        median = rets[len(rets) // 2]
        lines.append(f"- median pct_return: {median:+.2f}%")
        lines.append(f"- best  pct_return: {rets[-1]:+.2f}%")
        lines.append(f"- worst pct_return: {rets[0]:+.2f}%")

    (output_dir / "results.md").write_text("\n".join(lines), encoding="utf-8")
    log.info("Wrote sweep results to %s", output_dir)
    print()
    print("\n".join(lines))


# ── CLI ────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--starting-cash", type=float, default=10_000.0)
    p.add_argument("--starting-state", choices=["cash", "btc"], default="cash")
    p.add_argument("--grid", choices=list(GRIDS.keys()), default="small")
    p.add_argument("--config", default=str(_REPO_ROOT / "config" / "strategies.yaml"))
    p.add_argument("--output", default=None)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--sort-key", default="pct_return",
                   help="Summary metric to sort by (e.g. pct_return, max_drawdown_pct)")
    p.add_argument("--top", type=int, default=20)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    raw_yaml = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    base = raw_yaml["btc_accumulator"]

    alerts = fetch_alerts_from_prod(start, end, args.refresh)
    bars = fetch_ohlcv_from_coinbase(start, end, args.refresh)

    starting_state = State.CASH if args.starting_state == "cash" else State.BTC

    grid = GRIDS[args.grid]
    results = run_sweep(
        base_yaml=base, grid=grid,
        alerts=alerts, bars=bars,
        starting_cash=args.starting_cash, starting_state=starting_state,
    )

    if args.output:
        out_dir = Path(args.output)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        out_dir = _REPO_ROOT / "data" / "backtest_runs" / f"sweep_{ts}"
    write_sweep_outputs(results, out_dir, args.grid, args.sort_key, args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
