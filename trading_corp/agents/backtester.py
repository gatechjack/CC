"""Backtesting Agent — gates new-strategy deploys.

`validate_strategy(name)` returns Pass/Fail with metrics. A new
strategy must not deploy to paper or live until this gate returns
Pass — that's the "backtest before deploy" invariant codified in
PROJECT_CONTEXT.md §11 and CLAUDE.md §6.

Status of validations (registry below):
  - `coinbase_btc_donchian`: PASSED via `scripts/walkforward_donchian.py`
    on 2026-05-08. Evidence: 24mo Coinbase BTC/USD full corpus +56.30%
    vs HODL +30.42% (+25.89% alpha); 12mo walk-forward 8/10 top configs
    beat HODL out-of-sample, median test α +12.86%. Locked config
    (entry=20, exit=6, trend_filter=168, granularity=21600s) is in
    `config/strategies.yaml`. See commit 0eb7692.

Strategies not in the registry below default to Fail. `ALLOW_SKELETON_BACKTEST=1`
preserves the dev escape hatch.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    strategy: str
    passed: bool
    metrics: dict
    note: str = ""


# Strategies that have passed walk-forward + Board review. Each entry
# documents the validation in `note` so a future reader can audit the
# evidence trail without external references.
_VALIDATED_STRATEGIES: dict[str, BacktestResult] = {
    "coinbase_btc_donchian": BacktestResult(
        strategy="coinbase_btc_donchian",
        passed=True,
        metrics={
            "full_24mo_pct_return": 56.30,
            "full_24mo_hodl_pct": 30.42,
            "full_24mo_alpha": 25.89,
            "walkforward_12mo_oos_median_alpha": 12.86,
            "walkforward_12mo_oos_best_alpha": 27.21,
            "walkforward_12mo_oos_worst_alpha": -0.02,
            "walkforward_12mo_top10_beat_hodl": 8,
            "round_trips_24mo": 35,
            "win_rate": 0.49,
            "max_drawdown_pct": 16.49,
            "pct_time_in_btc": 25.3,
        },
        note=(
            "Donchian Channel Breakout on 6h Coinbase BTC/USD bars. "
            "Validated 2026-05-08 via scripts/walkforward_donchian.py. "
            "Locked config: entry_lookback=20, exit_lookback=6, "
            "trend_filter_lookback=168 (~42d SMA), granularity=21600s. "
            "See commits 072a484 (backtest infra) + 0eb7692 (config lock)."
        ),
    ),
}


class BacktesterAgent:
    def validate_strategy(self, strategy: str) -> BacktestResult:
        # First: registry check. If the strategy has a documented
        # validation, return it.
        if strategy in _VALIDATED_STRATEGIES:
            log.info("BacktesterAgent: %s validated — %s",
                     strategy, _VALIDATED_STRATEGIES[strategy].note)
            return _VALIDATED_STRATEGIES[strategy]

        # Second: dev escape hatch.
        if os.getenv("ALLOW_SKELETON_BACKTEST") == "1":
            log.warning(
                "BacktesterAgent: skeleton-pass for %s (ALLOW_SKELETON_BACKTEST=1). "
                "Strategy is NOT in the validated registry; intended for dev only.",
                strategy,
            )
            return BacktestResult(
                strategy=strategy, passed=True, metrics={}, note="skeleton",
            )

        # Default: fail closed.
        return BacktestResult(
            strategy=strategy,
            passed=False,
            metrics={},
            note=(
                f"Strategy '{strategy}' is not in the validated registry. "
                "Run a full backtest + walk-forward, then register the result "
                "here. Set ALLOW_SKELETON_BACKTEST=1 to bypass for dev."
            ),
        )
