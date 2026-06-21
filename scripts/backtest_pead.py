#!/usr/bin/env python
"""CLI driver for the PEAD (post-earnings-announcement-drift) backtest.

Thin network layer over the tested orchestration in
`trading_corp.agents.strategies.pead_backtest_driver`: fetches daily bars
(yfinance) + quarterly EPS history (EarningsProvider: Finnhub primary) + a
sector/market-cap snapshot (yfinance .info), builds the ranked `EventSignal`s,
runs the backtest with explicit friction, and prints in-sample / out-of-sample
reports.

REQUIRES (to run on real data):
  - FINNHUB_API_KEY in the environment (the yfinance EPS fallback is unreliable
    on current yfinance; Finnhub is the working primary).
  - A universe of tickers (--universe AAPL,MSFT,... or --universe @sp500.txt).

Example:
  FINNHUB_API_KEY=xxx python -m scripts.backtest_pead \\
    --universe @sp500.txt --start 2021-01-01 --end 2024-12-31 \\
    --oos-split 2024-01-01 --exit-mode pure_hold

This is a VALIDATION tool — it places no orders and touches no broker. The
kill-gate decision (proceed to the live division vs. shelve) is read off these
reports by the Board.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

from trading_corp.agents.strategies.pead_backtest import Bar, BacktestParams
from trading_corp.agents.strategies.pead_backtest_driver import (
    build_signals,
    format_report,
    run_split_backtest,
)
from trading_corp.agents.strategies.pead_signal import ScreenParams, SueParams
from trading_corp.data.earnings_provider import EarningsProvider

log = logging.getLogger("backtest_pead")

# Lead bars before the window (ATR + pre-earnings close) and trailing bars
# after it (the max hold window) that must be fetched.
_LEAD_DAYS = 120
_TRAIL_DAYS = 150


def load_universe(spec: str) -> list[str]:
    """Parse --universe: a comma list ("AAPL,MSFT") or "@path" to a file with
    one ticker per line (blank lines / '#' comments ignored)."""
    if spec.startswith("@"):
        out: list[str] = []
        with open(spec[1:], encoding="utf-8") as f:
            for line in f:
                t = line.strip().upper()
                if t and not t.startswith("#"):
                    out.append(t)
        return out
    return [t.strip().upper() for t in spec.split(",") if t.strip()]


def fetch_bars(symbol: str, start: date, end: date) -> list[Bar]:
    """Daily OHLCV bars via yfinance, oldest -> newest. [] on failure."""
    try:
        import yfinance as yf  # type: ignore
        df = yf.download(
            symbol, start=start.isoformat(), end=end.isoformat(),
            progress=False, auto_adjust=False,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_bars(%s) failed: %s", symbol, e)
        return []
    if df is None or getattr(df, "empty", True):
        return []

    def _cell(row, col):
        v = row[col]
        return float(v.iloc[0]) if hasattr(v, "iloc") else float(v)

    bars: list[Bar] = []
    for idx, row in df.iterrows():
        try:
            d = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
            bars.append(Bar(
                d, _cell(row, "Open"), _cell(row, "High"),
                _cell(row, "Low"), _cell(row, "Close"), _cell(row, "Volume"),
            ))
        except Exception:  # noqa: BLE001
            continue
    return bars


def fetch_info(symbol: str) -> dict:
    """Static sector + market-cap snapshot via yfinance .info (best-effort).
    NOTE: this is point-in-time, not historical — a documented backtest
    simplification for the sector/size screen."""
    try:
        import yfinance as yf  # type: ignore
        info = yf.Ticker(symbol).info or {}
    except Exception:  # noqa: BLE001
        info = {}
    return {"market_cap": info.get("marketCap"), "sector": info.get("sector")}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PEAD backtest (validation only).")
    p.add_argument("--universe", required=True, help='"AAPL,MSFT" or "@tickers.txt"')
    p.add_argument("--start", required=True, type=date.fromisoformat)
    p.add_argument("--end", required=True, type=date.fromisoformat)
    p.add_argument("--oos-split", type=date.fromisoformat, default=None,
                   help="in-sample < date <= out-of-sample")
    # signal
    p.add_argument("--lookback", type=int, default=8)
    p.add_argument("--sue-threshold", type=float, default=1.5)
    p.add_argument("--no-quintile", action="store_true")
    # exits / friction / sizing
    p.add_argument("--exit-mode", choices=["pure_hold", "partial_trail"], default="pure_hold")
    p.add_argument("--max-hold", type=int, default=60)
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--half-spread-bps", type=float, default=5.0)
    p.add_argument("--position-pct", type=float, default=0.05)
    p.add_argument("--max-concurrent", type=int, default=5)
    p.add_argument("--starting-equity", type=float, default=100_000.0)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    universe = load_universe(args.universe)
    log.info("universe: %d symbols", len(universe))

    provider = EarningsProvider()
    if provider._api_key is None:  # noqa: SLF001 — intentional pre-flight check
        log.warning(
            "FINNHUB_API_KEY not set — EPS history will be unreliable; "
            "results may be empty. Set the key for a real run."
        )

    fetch_start = args.start - timedelta(days=_LEAD_DAYS)
    fetch_end = args.end + timedelta(days=_TRAIL_DAYS)

    eps_by, bars_by, info_by = {}, {}, {}
    for sym in universe:
        eps = provider.get_quarterly_eps(sym)
        if not eps:
            continue
        bars = fetch_bars(sym, fetch_start, fetch_end)
        if not bars:
            continue
        eps_by[sym], bars_by[sym], info_by[sym] = eps, bars, fetch_info(sym)
    log.info("fetched data for %d symbols", len(eps_by))

    sue_params = SueParams(
        lookback=args.lookback,
        sue_threshold=args.sue_threshold,
        top_quintile=not args.no_quintile,
    )
    params = BacktestParams(
        atr_period=14, max_hold_trading_days=args.max_hold,
        slippage_bps=args.slippage_bps, half_spread_bps=args.half_spread_bps,
        position_pct=args.position_pct, max_concurrent=args.max_concurrent,
        exit_mode=args.exit_mode,
    )

    signals = build_signals(
        eps_by, bars_by, info_by,
        sue_params=sue_params, screen_params=ScreenParams(),
        window_start=args.start, window_end=args.end,
    )
    log.info("built %d signals", len(signals))

    reports = run_split_backtest(
        signals, params, split_date=args.oos_split,
        starting_equity=args.starting_equity,
    )
    print()
    print(f"PEAD backtest — exit_mode={args.exit_mode} "
          f"lookback={args.lookback} sue>{args.sue_threshold} "
          f"quintile={not args.no_quintile} friction="
          f"{args.slippage_bps + args.half_spread_bps:.0f}bps/side")
    for label in ("all", "in_sample", "out_of_sample"):
        if label in reports:
            print(format_report(label, reports[label]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
