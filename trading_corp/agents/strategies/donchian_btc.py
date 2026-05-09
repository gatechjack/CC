"""Coinbase BTC Donchian Channel Breakout — decision module.

Pure-function decision engine implementing the canonical Donchian
Channel breakout (a/k/a "Turtle Lite") for the Coinbase BTC
Accumulator division. No alert intake, no LLM, no broker calls —
just OHLCV → BUY / SELL / SKIP.

Strategy spec:
  - Long when current close > max(high) over the last `entry_lookback`
    bars (excluding the current bar, to avoid intra-bar look-ahead).
  - Flat (sell) when current close < min(low) over the last
    `exit_lookback` bars (excluding the current bar).
  - Optional trend filter: only allow the long entry if current close
    > simple moving average over `trend_filter_lookback` bars.

Defaults (`DonchianConfig` below) are tuned for hourly bars:
  entry_lookback = 50    (~2 days of hourly bars)
  exit_lookback  = 20    (~20 hours)
  trend_filter   = None  (disabled — BTC's recent regime makes a
                          long-window SMA filter potentially
                          counter-productive on short corpora)

Both backtest harness AND (forthcoming) live strategy import this
same module so the decision function is byte-identical between
research and production.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Decision(str, Enum):
    BUY = "buy"
    SELL = "sell"
    SKIP = "skip"


class State(str, Enum):
    CASH = "cash"
    BTC = "btc"


@dataclass(frozen=True)
class DonchianConfig:
    """Configuration knobs. Hard-coded defaults match the most-cited
    Donchian breakout parameterization for hourly BTC bars; tunable
    via the backtest harness CLI for sweeps."""
    entry_lookback: int = 50           # bars-back for the breakout high
    exit_lookback: int = 20            # bars-back for the breakdown low
    trend_filter_lookback: int | None = None   # None = no SMA filter
    granularity_seconds: int = 3600    # bar duration (1h default)


@dataclass
class DonchianBreakdown:
    """Audit-grade detail of one decision. Same role as the
    confluence engine's `ScoreBreakdown` — every reader of an audit
    row should be able to reconstruct WHY the strategy did or
    didn't fire."""
    current_close: float
    donchian_high: float | None        # None during warmup
    donchian_low: float | None
    trend_filter_sma: float | None     # None when filter disabled
    trend_filter_passed: bool
    bars_considered: int               # how many bars we had at decision time


@dataclass
class DonchianVerdict:
    decision: Decision
    breakdown: DonchianBreakdown
    reason: str


def evaluate_donchian(
    *,
    state: State,
    bars_window: list[dict],
    config: DonchianConfig,
    now: datetime,
) -> DonchianVerdict:
    """Evaluate one decision point.

    Args:
        state: current portfolio state (CASH or BTC).
        bars_window: chronologically-sorted OHLCV bars up to AND
            INCLUDING the bar at `now`. Caller is responsible for
            ensuring no future bars leak in. Each bar is a dict with
            keys: ts, open, high, low, close, volume.
        config: Donchian parameters.
        now: evaluation timestamp (used in the verdict reason).

    Returns:
        DonchianVerdict — decision (BUY/SELL/SKIP) + audit-grade
        breakdown of the channel highs/lows the decision was based on.
    """
    needed = max(
        config.entry_lookback,
        config.exit_lookback,
        config.trend_filter_lookback or 0,
    )
    if len(bars_window) < needed + 1:
        return DonchianVerdict(
            decision=Decision.SKIP,
            breakdown=DonchianBreakdown(
                current_close=bars_window[-1]["close"] if bars_window else 0.0,
                donchian_high=None,
                donchian_low=None,
                trend_filter_sma=None,
                trend_filter_passed=False,
                bars_considered=len(bars_window),
            ),
            reason=(
                f"warmup @ {now.isoformat()}: need {needed + 1} bars, "
                f"have {len(bars_window)}"
            ),
        )

    current = bars_window[-1]
    current_close = current["close"]

    # Channels — exclude the current bar from both the high and low
    # window so a single intra-bar tick can't simultaneously set the
    # threshold AND trigger the breakout.
    entry_window = bars_window[-(config.entry_lookback + 1):-1]
    exit_window  = bars_window[-(config.exit_lookback + 1):-1]
    donchian_high = max(b["high"] for b in entry_window)
    donchian_low  = min(b["low"]  for b in exit_window)

    # Trend filter (optional)
    trend_sma: float | None = None
    trend_ok = True
    if config.trend_filter_lookback:
        sma_window = bars_window[-config.trend_filter_lookback:]
        trend_sma = sum(b["close"] for b in sma_window) / len(sma_window)
        trend_ok = current_close > trend_sma

    breakdown = DonchianBreakdown(
        current_close=current_close,
        donchian_high=donchian_high,
        donchian_low=donchian_low,
        trend_filter_sma=trend_sma,
        trend_filter_passed=trend_ok,
        bars_considered=len(bars_window),
    )

    # Decision tree
    if state == State.CASH:
        if current_close > donchian_high and trend_ok:
            return DonchianVerdict(
                decision=Decision.BUY,
                breakdown=breakdown,
                reason=(
                    f"breakout @ {now.isoformat()}: close={current_close:.2f} "
                    f"> {config.entry_lookback}-bar high={donchian_high:.2f}"
                    + (f" AND close > SMA({config.trend_filter_lookback})="
                       f"{trend_sma:.2f}" if config.trend_filter_lookback else "")
                ),
            )
        # Why didn't we fire?
        if current_close <= donchian_high:
            reason = (
                f"no breakout @ {now.isoformat()}: close={current_close:.2f} "
                f"<= {config.entry_lookback}-bar high={donchian_high:.2f}"
            )
        else:
            # Breakout passed but trend filter failed
            reason = (
                f"breakout blocked by trend filter @ {now.isoformat()}: "
                f"close={current_close:.2f} <= "
                f"SMA({config.trend_filter_lookback})={trend_sma:.2f}"
            )
        return DonchianVerdict(decision=Decision.SKIP, breakdown=breakdown, reason=reason)

    # state == BTC
    if current_close < donchian_low:
        return DonchianVerdict(
            decision=Decision.SELL,
            breakdown=breakdown,
            reason=(
                f"breakdown @ {now.isoformat()}: close={current_close:.2f} "
                f"< {config.exit_lookback}-bar low={donchian_low:.2f}"
            ),
        )
    return DonchianVerdict(
        decision=Decision.SKIP,
        breakdown=breakdown,
        reason=(
            f"hold @ {now.isoformat()}: close={current_close:.2f} "
            f">= {config.exit_lookback}-bar low={donchian_low:.2f}"
        ),
    )
