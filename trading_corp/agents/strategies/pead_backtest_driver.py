"""Orchestration for the PEAD backtest — turns fetched data into `EventSignal`s,
splits in-sample / out-of-sample, and formats the report.

Pure (all data is passed in), so the whole pipeline is unit-testable offline.
The network CLI (`scripts/backtest_pead.py`) fetches bars + earnings and calls
`build_signals` -> `run_backtest` -> `format_report`.

Wave model: announcements are grouped by report DATE; within each wave the
top-quintile + SUE-threshold selection (`pead_signal.select_candidates`) runs,
so the same wave-relative ranking the live strategy will use is applied here.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date

from trading_corp.agents.strategies.pead_backtest import (
    Bar,
    BacktestParams,
    BacktestReport,
    EventSignal,
    _index_on_or_after,
    run_backtest,
)
from trading_corp.agents.strategies.pead_signal import (
    PeadCandidate,
    ScreenInputs,
    ScreenParams,
    SueParams,
    passes_screen,
    select_candidates,
    standardized_ue,
)
from trading_corp.data.earnings_provider import QuarterlyEPS


def _avg_volume_before(bars: Sequence[Bar], idx: int, n: int) -> float | None:
    lo = max(0, idx - n)
    window = [bars[i].volume for i in range(lo, idx)]
    if not window:
        return None
    return sum(window) / len(window)


def _trading_days_between(bars: Sequence[Bar], d1: date, d2: date | None) -> int | None:
    if d2 is None:
        return None
    i1 = _index_on_or_after(bars, d1)
    i2 = _index_on_or_after(bars, d2)
    if i1 is None or i2 is None:
        return None
    return i2 - i1


def build_signals(
    eps_by_symbol: Mapping[str, Sequence[QuarterlyEPS]],
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    info_by_symbol: Mapping[str, Mapping],
    *,
    sue_params: SueParams = SueParams(),
    screen_params: ScreenParams = ScreenParams(),
    window_start: date | None = None,
    window_end: date | None = None,
) -> list[EventSignal]:
    """Build the ranked list of `EventSignal`s to backtest.

    For each symbol's reported quarter within [window_start, window_end]:
    compute SUE from the EPS actuals up to that quarter, assemble screen
    inputs (price/volume from bars, market_cap/sector from info, days-to-next
    from the next report date), then group by report date and run the wave's
    top-quintile + threshold selection.
    """
    # date -> {symbol: sue}, and date -> {symbol: (screen, next_report_date)}
    wave_sue: dict[date, dict[str, float]] = defaultdict(dict)
    wave_meta: dict[date, dict[str, tuple[ScreenInputs, date | None]]] = defaultdict(dict)

    for sym, rows in eps_by_symbol.items():
        rows = list(rows)
        actuals = [r.actual_eps for r in rows]
        bars = bars_by_symbol.get(sym)
        if not bars:
            continue
        for i, r in enumerate(rows):
            d = r.report_date
            if window_start is not None and d < window_start:
                continue
            if window_end is not None and d > window_end:
                continue
            sue = standardized_ue(actuals[: i + 1], lookback=sue_params.lookback)
            if sue is None:
                continue
            ai = _index_on_or_after(bars, d)
            if ai is None or ai < 1:
                continue
            next_report = rows[i + 1].report_date if i + 1 < len(rows) else None
            info = info_by_symbol.get(sym, {})
            screen = ScreenInputs(
                symbol=sym,
                price=bars[ai].close,
                avg_daily_volume_30d=_avg_volume_before(bars, ai, 30),
                market_cap=info.get("market_cap"),
                sector=info.get("sector"),
                guidance_cut=None,
                days_to_next_earnings=_trading_days_between(bars, d, next_report),
            )
            wave_sue[d][sym] = sue
            wave_meta[d][sym] = (screen, next_report)

    signals: list[EventSignal] = []
    for d in sorted(wave_sue):
        candidates: list[PeadCandidate] = []
        for sym, sue in wave_sue[d].items():
            screen, _ = wave_meta[d][sym]
            ok, reason = passes_screen(screen, screen_params)
            candidates.append(PeadCandidate(sym, sue, ok, reason))
        for c in select_candidates(candidates, sue_params):
            _, next_report = wave_meta[d][c.symbol]
            signals.append(EventSignal(
                symbol=c.symbol,
                announcement_date=d,
                sue=float(c.sue),  # type: ignore[arg-type]
                bars=bars_by_symbol[c.symbol],
                next_earnings_date=next_report,
            ))
    return signals


def split_is_oos(
    signals: Sequence[EventSignal], split_date: date
) -> tuple[list[EventSignal], list[EventSignal]]:
    """Partition signals into (in-sample < split_date, out-of-sample >=)."""
    in_sample = [s for s in signals if s.announcement_date < split_date]
    oos = [s for s in signals if s.announcement_date >= split_date]
    return in_sample, oos


def run_split_backtest(
    signals: Sequence[EventSignal],
    params: BacktestParams,
    *,
    split_date: date | None = None,
    starting_equity: float = 100_000.0,
) -> dict[str, BacktestReport]:
    """Run the backtest on the full set and, if `split_date` given, on the
    in-sample and out-of-sample subsets too. Returns labelled reports."""
    out: dict[str, BacktestReport] = {
        "all": run_backtest(signals, params, starting_equity=starting_equity),
    }
    if split_date is not None:
        is_sig, oos_sig = split_is_oos(signals, split_date)
        out["in_sample"] = run_backtest(is_sig, params, starting_equity=starting_equity)
        out["out_of_sample"] = run_backtest(oos_sig, params, starting_equity=starting_equity)
    return out


def format_report(label: str, report: BacktestReport) -> str:
    m = report.metrics
    if m.get("n_trades", 0) == 0:
        return f"[{label}] no trades ({m.get('skipped_concurrency', 0)} skipped)"
    lines = [
        f"=== {label} ===",
        f"  trades            {m['n_trades']}  (skipped concurrency: {m['skipped_concurrency']})",
        f"  win rate          {m['win_rate']:.1%}",
        f"  avg return/trade  {m['avg_return_pct']:+.2%}  (median {m['median_return_pct']:+.2%})",
        f"  avg R multiple    {m['avg_r_multiple']:+.2f}",
        f"  avg holding days  {m['avg_holding_days']:.1f}",
        f"  total return      {m['total_return_pct']:+.2%}",
        f"  max drawdown      {m['max_drawdown_pct']:.2%}",
        f"  profit factor     {m['profit_factor']:.2f}",
        f"  exit reasons      {m['exit_reasons']}",
    ]
    return "\n".join(lines)
