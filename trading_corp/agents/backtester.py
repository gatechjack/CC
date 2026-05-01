"""Backtesting Agent — Phase 2 SKELETON.

The full backtest engine ships in Phase 3 (vectorized OHLCV replay with
slippage/fees, walk-forward, parameter sweeps). For now this class exposes
the gate API that the rest of the system uses: `validate_strategy(name)`
returns a Pass/Fail with metrics. A new strategy cannot deploy to paper or
live until this gate returns Pass.

Phase 2 default: returns Pass with `note='skeleton'` ONLY when the env var
ALLOW_SKELETON_BACKTEST=1 is set. Otherwise returns Fail to enforce the
"backtest before deploy" invariant.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    strategy: str
    passed: bool
    metrics: dict
    note: str = ""


class BacktesterAgent:
    def validate_strategy(self, strategy: str) -> BacktestResult:
        if os.getenv("ALLOW_SKELETON_BACKTEST") == "1":
            log.warning(
                "BacktesterAgent: skeleton-pass for %s (ALLOW_SKELETON_BACKTEST=1). "
                "Phase 3 will replace this with a real backtest engine.",
                strategy,
            )
            return BacktestResult(
                strategy=strategy, passed=True, metrics={}, note="skeleton",
            )
        return BacktestResult(
            strategy=strategy,
            passed=False,
            metrics={},
            note=(
                "Phase 2 backtester is a skeleton. Set ALLOW_SKELETON_BACKTEST=1 "
                "to bypass for development; Phase 3 ships the real engine."
            ),
        )
