#!/usr/bin/env python
"""CLI driver for the PEAD (post-earnings-announcement-drift) backtest.

Thin network layer over the tested orchestration in
`trading_corp.agents.strategies.pead_backtest_driver`: fetches daily bars
(yfinance) + quarterly EPS history + company facts (EarningsProvider: EODHD
primary), builds the ranked `EventSignal`s, runs the backtest with explicit
friction, and prints in-sample / out-of-sample reports.

Screen + signal params are CONFIG-DRIVEN from `config/strategies.yaml`
(the `robinhood_pead` block) so the floors can be retuned without a code
change; --lookback / --sue-threshold / --no-quintile override the signal side.

REQUIRES (to run on real data):
  - EODHD_API_KEY in the environment (primary — fundamentals JSON supplies
    announcement dates, EPS history, market cap, and sector in one call).
    Falls back to yfinance for EPS actuals only if key is absent.
  - A universe of tickers (--universe AAPL,MSFT,... or --universe @nasdaq_composite.txt).

NOTE: daily OHLCV bars stay on yfinance/Tastytrade — only the EPS + company
facts layer moved to EODHD.

This is a VALIDATION tool — it places no orders and touches no broker.
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from datetime import date, timedelta

import yaml

from trading_corp.agents.strategies.pead_backtest import Bar, BacktestParams
from trading_corp.agents.strategies.pead_backtest_driver import (
    build_signals,
    format_report,
    run_split_backtest,
)
from trading_corp.agents.strategies.pead_signal import (
    screen_params_from_config,
    sue_params_from_config,
)
from trading_corp.data.earnings_provider import EarningsProvider
from trading_corp.utils.secrets import load_secrets

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


def fetch_info(symbol: str, provider: "EarningsProvider") -> dict:
    """Static sector + market-cap snapshot via EarningsProvider.get_company_facts.

    Delegates to EODHD fundamentals JSON (same fetch already done for EPS —
    the shared 24h cache means no extra HTTP call).  Returns {} on failure.

    NOTE: point-in-time, not historical — a documented backtest simplification
    for the sector/size screen.
    """
    result = provider.get_company_facts(symbol)
    return result if result is not None else {"market_cap": None, "sector": None}


def load_pead_config(path: str) -> dict:
    """Read the `robinhood_pead` block from strategies.yaml ({} if absent)."""
    try:
        with open(path, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("robinhood_pead", {}) or {}
    except FileNotFoundError:
        log.warning("strategies.yaml not found at %s — using built-in defaults", path)
        return {}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PEAD backtest (validation only).")
    p.add_argument("--universe", required=True, help='"AAPL,MSFT" or "@tickers.txt"')
    p.add_argument("--start", required=True, type=date.fromisoformat)
    p.add_argument("--end", required=True, type=date.fromisoformat)
    p.add_argument("--oos-split", type=date.fromisoformat, default=None,
                   help="in-sample < date <= out-of-sample")
    p.add_argument("--strategies-yaml", default="config/strategies.yaml",
                   help="source of the robinhood_pead screen/signal params")
    # signal overrides (default: read from the strategies.yaml robinhood_pead block)
    p.add_argument("--lookback", type=int, default=None)
    p.add_argument("--sue-threshold", type=float, default=None)
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

    # --- config-driven screen + signal params (CLI overrides the signal) ---
    cfg = load_pead_config(args.strategies_yaml)
    screen_params = screen_params_from_config(cfg.get("screen", {}) or {})
    sue_params = sue_params_from_config(cfg.get("signal", {}) or {})
    if args.lookback is not None:
        sue_params = replace(sue_params, lookback=args.lookback)
    if args.sue_threshold is not None:
        sue_params = replace(sue_params, sue_threshold=args.sue_threshold)
    if args.no_quintile:
        sue_params = replace(sue_params, top_quintile=False)
    log.info("screen=%s", screen_params)
    log.info("signal=%s", sue_params)

    universe = load_universe(args.universe)
    log.info("universe: %d symbols", len(universe))

    # Resolve EODHD key via the STANDARD secrets path: load_secrets() loads
    # .env and, if KEY_VAULT_URI is set, pulls from Azure Key Vault (secret
    # name EODHD-API-KEY) using DefaultAzureCredential (Managed Identity on
    # the prod VM; `az login` locally). No local key file.
    secrets = load_secrets()
    provider = EarningsProvider(api_key=secrets.eodhd_api_key)
    if secrets.eodhd_api_key is None:
        log.warning(
            "EODHD key not resolved (.env + Azure KV 'EODHD-API-KEY'); "
            "EPS history will use yfinance fallback (actuals only, no estimates, "
            "no company facts). Add the KV secret and run where the vault is "
            "reachable (KEY_VAULT_URI + Azure auth)."
        )

    fetch_start = args.start - timedelta(days=_LEAD_DAYS)
    fetch_end = args.end + timedelta(days=_TRAIL_DAYS)

    eps_by, bars_by, info_by = {}, {}, {}
    for i, sym in enumerate(universe):
        eps = provider.get_quarterly_eps(sym)
        if not eps:
            continue
        bars = fetch_bars(sym, fetch_start, fetch_end)
        if not bars:
            continue
        eps_by[sym], bars_by[sym], info_by[sym] = eps, bars, fetch_info(sym, provider)
        if (i + 1) % 100 == 0:
            log.info("  ...%d/%d symbols scanned, %d with data", i + 1, len(universe), len(eps_by))
    log.info("fetched data for %d symbols", len(eps_by))

    params = BacktestParams(
        atr_period=14, max_hold_trading_days=args.max_hold,
        slippage_bps=args.slippage_bps, half_spread_bps=args.half_spread_bps,
        position_pct=args.position_pct, max_concurrent=args.max_concurrent,
        exit_mode=args.exit_mode,
    )

    signals = build_signals(
        eps_by, bars_by, info_by,
        sue_params=sue_params, screen_params=screen_params,
        window_start=args.start, window_end=args.end,
    )
    log.info("built %d signals", len(signals))

    reports = run_split_backtest(
        signals, params, split_date=args.oos_split,
        starting_equity=args.starting_equity,
    )
    print()
    print(f"PEAD backtest — exit_mode={args.exit_mode} "
          f"lookback={sue_params.lookback} sue>{sue_params.sue_threshold} "
          f"quintile={sue_params.top_quintile} friction="
          f"{args.slippage_bps + args.half_spread_bps:.0f}bps/side")
    for label in ("all", "in_sample", "out_of_sample"):
        if label in reports:
            print(format_report(label, reports[label]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
