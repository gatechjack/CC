"""Pure backtest engine for the `robinhood_pead` (Post-Earnings-Announcement-
Drift) division — the kill-gate's load-bearing simulator.

Decoupled from data-fetching by design: it takes daily OHLC bars + earnings
events as plain inputs, so it is independent of the earnings/price adapters
and fully unit-testable. The CLI driver (`scripts/backtest_pead.py`) fetches
bars (yfinance / Tasty) + earnings (Finnhub / yfinance), turns them into
`EventSignal`s, and calls `run_backtest`.

FRICTION IS EXPLICIT (the kill-gate must be friction-honest — $0 commissions
≠ $0 cost). Every fill pays `slippage_bps + half_spread_bps` against itself:
a buy fills higher, a sell fills lower. Returns are therefore net of friction.

FILL CONVENTIONS (conservative, documented so the methodology is auditable):
  - Entry: at the OPEN of the bar `entry_delay_days` trading days after the
    announcement bar (Day 0 skipped), plus buy-side friction.
  - Intrabar STOP (triggered by a bar's low): fill at the stop level, BUT if the
    bar gapped through it (open already below), fill at the open — models
    gap-through slippage. Then sell-side friction.
  - CLOSE-based exits (drift-dead, next-earnings guard, time): fill at that bar's
    close, plus sell-side friction. (Drift is a daily-CLOSE rule — parity with the
    live engine — so it never fires on an intrabar low.)

EXIT PRECEDENCE — evaluated each bar top-down, FIRST MATCH WINS:
  (1) HARD STOP   price ≤ max(entry − atr_mult·ATR14, post-earnings swing low)
  (2) DRIFT-DEAD  price gives back ≥ `drift_dead_giveback` of the earnings gap
  (3) NEXT-EARNINGS GUARD  ≤ guard_days trading days before next earnings → flat
  (4) TIME        held ≥ max_hold_trading_days → close
Optional `exit_mode="partial_trail"` adds: at +partial_gain_trigger with
held>partial_hold_min_days, sell `partial_fraction`; trail the remainder out
when close < its `trail_ma_period`-day moving average (the 4 hard rules still
bind the remainder). Resolved against pure_hold by the head-to-head backtest.

All thresholds are parameters (literature priors) — the backtest tunes them.
Long-only v1.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from trading_corp.agents.strategies.pead_signal import reaction_index


# ---------------------------------------------------------------------------
# Inputs / params
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Bar:
    """One daily OHLCV bar (a trading day)."""
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class EventSignal:
    """A qualifying post-earnings long candidate to simulate.

    `bars` is the symbol's chronological daily bars, covering enough
    pre-announcement history (>= atr_period+1 bars before the announcement
    for ATR + the pre-earnings close) through the max hold window after it.
    """
    symbol: str
    announcement_date: date
    sue: float
    bars: Sequence[Bar]
    next_earnings_date: date | None = None
    report_time: str | None = None   # 'BeforeMarket' | 'AfterMarket' | None (BMO/AMC slot; None = unknown)


@dataclass(frozen=True)
class BacktestParams:
    # entry
    entry_delay_days: int = 1            # trading days after announcement (skip Day 0); 1-2
    confirmation_gate: bool = False      # post-reaction confirmation gate; DEFAULT OFF. When ON,
    #                                      entry is slot-aware (a+1 BMO / a+2 AMC) — see simulate_trade.
    # exits
    atr_period: int = 14
    hard_stop_atr_mult: float = 2.5
    drift_dead_giveback: float = 0.50    # exit if >=50% of the earnings gap is given back
    next_earnings_guard_days: int = 2    # flatten this many trading days before next earnings
    max_hold_trading_days: int = 60
    # friction (per side, basis points)
    slippage_bps: float = 5.0
    half_spread_bps: float = 5.0
    # sizing / portfolio
    position_pct: float = 0.05           # fraction of equity per name (plumbing-validation value)
    max_concurrent: int = 5
    # exit mode
    exit_mode: str = "pure_hold"         # "pure_hold" | "partial_trail"
    partial_gain_trigger: float = 0.15
    partial_hold_min_days: int = 3
    partial_fraction: float = 1.0 / 3.0
    trail_ma_period: int = 20


@dataclass
class TradeResult:
    symbol: str
    entry_date: date
    entry_price: float          # net of buy friction
    exit_date: date
    exit_price: float           # net of sell friction (blended if partial)
    exit_reason: str
    holding_days: int
    return_pct: float           # net of friction, position-weighted if partial
    r_multiple: float
    sue: float
    partials: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _friction(price: float, side: str, p: BacktestParams) -> float:
    """Apply per-side slippage + half-spread. Buy fills up, sell fills down."""
    cost = (p.slippage_bps + p.half_spread_bps) / 10_000.0
    return price * (1.0 + cost) if side == "buy" else price * (1.0 - cost)


def _index_on_or_after(bars: Sequence[Bar], d: date) -> int | None:
    for i, b in enumerate(bars):
        if b.trade_date >= d:
            return i
    return None


def compute_atr(bars: Sequence[Bar], end_idx: int, period: int) -> float | None:
    """Simple-average True Range over `period` bars ending at `end_idx`
    (inclusive). Needs `period` TR values, i.e. bars[end_idx-period .. end_idx]
    with a prior close → end_idx >= period. Returns None if insufficient."""
    if end_idx < period:
        return None
    trs: list[float] = []
    for i in range(end_idx - period + 1, end_idx + 1):
        prev_close = bars[i - 1].close
        tr = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - prev_close),
            abs(bars[i].low - prev_close),
        )
        trs.append(tr)
    atr = statistics.fmean(trs)
    return atr if math.isfinite(atr) and atr > 0 else None


def _sma_close(bars: Sequence[Bar], end_idx: int, period: int) -> float | None:
    if end_idx < period - 1:
        return None
    window = [bars[i].close for i in range(end_idx - period + 1, end_idx + 1)]
    return statistics.fmean(window)


# ---------------------------------------------------------------------------
# Single-trade simulation
# ---------------------------------------------------------------------------

def simulate_trade(signal: EventSignal, p: BacktestParams) -> TradeResult | None:
    """Simulate one PEAD trade. Returns None if the trade can't be set up
    (insufficient history, entry bar missing, no ATR)."""
    bars = signal.bars
    a = _index_on_or_after(bars, signal.announcement_date)
    if a is None or a < 1:
        return None  # need a pre-earnings close
    if p.confirmation_gate:
        # gate ON: enter at the OPEN after the post-reaction session CLOSES —
        # a+1 for BeforeMarket, a+2 for AfterMarket. Matches the live entry and
        # avoids look-ahead. Un-slotted names are excluded upstream (build_signals).
        ri = reaction_index(signal.report_time, a)
        if ri is None:
            return None  # no slot -> not tradeable (defensive; excluded upstream)
        e = ri + 1
    else:
        e = a + p.entry_delay_days
    if e >= len(bars):
        return None  # no entry bar available
    atr = compute_atr(bars, e, p.atr_period)
    if atr is None:
        return None

    entry_raw = bars[e].open
    entry_price = _friction(entry_raw, "buy", p)
    entry_date = bars[e].trade_date

    # DRIFT baseline: slot-aware bar0 (AMC=a, BMO=a-1) via the SAME reaction_index the
    # gate uses — identical to the live _build_primitives fix, so live and backtest
    # measure the drift gap from the same pre-earnings close. Unknown slot -> a-1.
    _bar1 = reaction_index(signal.report_time, a)
    _bar0 = (_bar1 - 1) if _bar1 is not None else (a - 1)
    pre_earnings_close = bars[_bar0].close
    earnings_gap = entry_raw - pre_earnings_close            # the reaction we entered after
    # post-earnings swing low = lowest low from announcement through entry
    swing_low = min(bars[i].low for i in range(a, e + 1))
    stop_level = max(entry_raw - p.hard_stop_atr_mult * atr, swing_low)
    # drift-dead: give back `giveback` of a POSITIVE gap (long-only)
    drift_dead_level = (
        entry_raw - p.drift_dead_giveback * earnings_gap if earnings_gap > 0 else None
    )
    next_earn_idx = (
        _index_on_or_after(bars, signal.next_earnings_date)
        if signal.next_earnings_date is not None else None
    )

    risk_per_share = max(entry_price - stop_level, 1e-9)

    # ---- partial-trail bookkeeping ----
    partials: list[dict] = []
    remaining = 1.0
    realized_weighted_ret = 0.0  # sum of (fraction * net_return) for closed pieces

    def _record_exit(exit_raw_price: float, idx: int, reason: str) -> TradeResult:
        exit_price = _friction(exit_raw_price, "sell", p)
        piece_ret = (exit_price - entry_price) / entry_price
        total_ret = realized_weighted_ret + remaining * piece_ret
        # blended exit price implied by the position-weighted return
        blended_exit = entry_price * (1.0 + total_ret)
        return TradeResult(
            symbol=signal.symbol,
            entry_date=entry_date,
            entry_price=entry_price,
            exit_date=bars[idx].trade_date,
            exit_price=blended_exit,
            exit_reason=reason,
            holding_days=idx - e,
            return_pct=total_ret,
            r_multiple=(blended_exit - entry_price) / risk_per_share,
            sue=signal.sue,
            partials=partials,
        )

    last_idx = min(len(bars) - 1, e + p.max_hold_trading_days)
    for i in range(e + 1, last_idx + 1):
        bar = bars[i]
        held = i - e

        # (1) HARD STOP
        if bar.low <= stop_level:
            fill = bar.open if bar.open <= stop_level else stop_level
            return _record_exit(fill, i, "hard_stop")
        # (2) DRIFT-DEAD — CLOSE-only, daily-bar granularity (parity with the live
        # engine: drift evaluates a completed daily bar's CLOSE, never an intrabar
        # low, and never on the entry day — the loop already starts at e+1). The
        # STOP above stays low-triggered (the intraday risk layer).
        if drift_dead_level is not None and bar.close <= drift_dead_level:
            return _record_exit(bar.close, i, "drift_dead")
        # (3) NEXT-EARNINGS GUARD
        if next_earn_idx is not None and (next_earn_idx - i) <= p.next_earnings_guard_days:
            return _record_exit(bar.close, i, "next_earnings_guard")
        # (4) TIME
        if held >= p.max_hold_trading_days:
            return _record_exit(bar.close, i, "time")

        # ---- optional partial-and-trail on the remainder ----
        if p.exit_mode == "partial_trail":
            gain = (bar.close - entry_price) / entry_price
            if (not partials) and gain >= p.partial_gain_trigger and held > p.partial_hold_min_days:
                part_price = _friction(bar.close, "sell", p)
                part_ret = (part_price - entry_price) / entry_price
                realized_weighted_ret += p.partial_fraction * part_ret
                remaining -= p.partial_fraction
                partials.append({
                    "date": bar.trade_date, "fraction": p.partial_fraction,
                    "price": part_price, "return_pct": part_ret, "reason": "partial_gain",
                })
            if partials:  # trail the remainder on the MA
                ma = _sma_close(bars, i, p.trail_ma_period)
                if ma is not None and bar.close < ma:
                    return _record_exit(bar.close, i, "trail_ma")

    # ran out of bars before any rule fired
    return _record_exit(bars[last_idx].close, last_idx, "data_end")


# ---------------------------------------------------------------------------
# Portfolio backtest
# ---------------------------------------------------------------------------

@dataclass
class BacktestReport:
    trades: list[TradeResult]
    skipped_concurrency: int
    starting_equity: float
    ending_equity: float
    equity_curve: list[tuple[date, float]]
    metrics: dict


def run_backtest(
    signals: Sequence[EventSignal],
    params: BacktestParams,
    *,
    starting_equity: float = 100_000.0,
) -> BacktestReport:
    """Run the portfolio backtest over `signals` (one window — the driver
    splits in-sample vs out-of-sample and calls this per subset).

    Concurrency: signals are taken in announcement-date order; a signal is
    SKIPPED if `max_concurrent` trades are already open at its entry. Sizing
    is fixed-fractional off REALIZED equity at entry (realized P&L from
    already-closed trades; unrealized intra-trade drawdown is not marked to
    market — a documented simplification, conservative for the kill-gate's
    realized-equity max-DD).
    """
    # 1. Pre-simulate each trade independently (entry/exit/return).
    sims: list[TradeResult] = []
    for sig in sorted(signals, key=lambda s: s.announcement_date):
        tr = simulate_trade(sig, params)
        if tr is not None:
            sims.append(tr)
    sims.sort(key=lambda t: t.entry_date)

    # 2. Walk entries; compound realized equity at each exit; enforce concurrency.
    equity = starting_equity
    open_trades: list[tuple[date, float, TradeResult]] = []  # (exit_date, dollars, trade)
    equity_curve: list[tuple[date, float]] = []
    taken: list[TradeResult] = []
    skipped = 0

    def _close_due(up_to: date) -> None:
        nonlocal equity
        still: list[tuple[date, float, TradeResult]] = []
        for exit_date, dollars, tr in sorted(open_trades, key=lambda x: x[0]):
            if exit_date <= up_to:
                equity += dollars * tr.return_pct
                equity_curve.append((exit_date, equity))
            else:
                still.append((exit_date, dollars, tr))
        open_trades[:] = still

    for tr in sims:
        _close_due(tr.entry_date)
        currently_open = sum(1 for ed, _, _ in open_trades if ed > tr.entry_date)
        if currently_open >= params.max_concurrent:
            skipped += 1
            continue
        dollars = equity * params.position_pct
        open_trades.append((tr.exit_date, dollars, tr))
        taken.append(tr)
    # flush remaining exits
    for exit_date, dollars, tr in sorted(open_trades, key=lambda x: x[0]):
        equity += dollars * tr.return_pct
        equity_curve.append((exit_date, equity))

    return BacktestReport(
        trades=taken,
        skipped_concurrency=skipped,
        starting_equity=starting_equity,
        ending_equity=equity,
        equity_curve=equity_curve,
        metrics=_metrics(taken, skipped, starting_equity, equity, equity_curve),
    )


def _max_drawdown(equity_curve: Sequence[tuple[date, float]], start: float) -> float:
    peak = start
    max_dd = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    return max_dd


def _metrics(
    trades: Sequence[TradeResult], skipped: int, start: float, end: float,
    equity_curve: Sequence[tuple[date, float]],
) -> dict:
    n = len(trades)
    if n == 0:
        return {"n_trades": 0, "skipped_concurrency": skipped, "note": "no trades"}
    rets = [t.return_pct for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    return {
        "n_trades": n,
        "skipped_concurrency": skipped,
        "win_rate": len(wins) / n,
        "avg_return_pct": statistics.fmean(rets),
        "median_return_pct": statistics.median(rets),
        "avg_r_multiple": statistics.fmean(t.r_multiple for t in trades),
        "avg_holding_days": statistics.fmean(t.holding_days for t in trades),
        "total_return_pct": (end / start - 1.0) if start > 0 else 0.0,
        "max_drawdown_pct": _max_drawdown(equity_curve, start),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "exit_reasons": reasons,
    }
