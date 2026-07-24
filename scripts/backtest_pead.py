#!/usr/bin/env python
"""CLI driver for the PEAD (post-earnings-announcement-drift) backtest.

Thin network layer over the tested orchestration in
`trading_corp.agents.strategies.pead_backtest_driver`: fetches daily bars
(Robinhood, split-adjusted, via data.rh_bars) + quarterly EPS history + company facts (EarningsProvider: EODHD
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

NOTE: daily OHLCV bars come from Robinhood (SPLIT-ADJUSTED, via the shared
data.rh_bars fetcher — IDENTICAL to the live engine's bars, no yfinance). Only
the EPS + company facts layer is EODHD.

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
from trading_corp.data.rh_bars import RHBarsError, fetch_rh_daily_bars

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
    """Daily SPLIT-ADJUSTED OHLCV bars via Robinhood (the shared data.rh_bars
    fetcher — IDENTICAL to the live engine's bars, no yfinance), filtered to
    [start, end], oldest -> newest. [] on failure; the fetcher raises rather than
    fall back to a banned source."""
    try:
        rows = fetch_rh_daily_bars(symbol, span="5year", bounds="regular")
    except RHBarsError as e:
        log.warning("fetch_bars(%s): Robinhood fetch failed — skipping (%s)", symbol, e)
        return []
    return [Bar(r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"])
            for r in rows if start <= r["date"] <= end]


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
    p.add_argument("--confirmation-gate", action="store_true",
                   help="enable the post-reaction confirmation gate (default OFF)")
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

    # Robinhood auth for SPLIT-ADJUSTED daily bars (data.rh_bars.fetch_rh_daily_bars).
    # Offline/backtest-side: rs.login REUSES the shared session pickle — creds/MFA are
    # used only if the pickle is stale. Separate from the LIVE engine's in-process
    # session (a backtest process cannot share it). Bars NEVER fall back to yfinance;
    # a stale/absent RH session simply yields empty bars for the affected symbols.
    import robin_stocks.robinhood as rs  # noqa: E402
    _mfa = None
    if secrets.robinhood_mfa_secret:
        try:
            import pyotp
            _mfa = pyotp.TOTP(secrets.robinhood_mfa_secret).now()
        except ImportError:
            log.warning("pyotp not installed — RH MFA skipped (valid-pickle reuse still works)")
    try:
        rs.login(secrets.robinhood_username, secrets.robinhood_password,
                 mfa_code=_mfa, store_session=True)
    except Exception as e:  # noqa: BLE001
        log.error("Robinhood login failed (%s) — bars will be empty; NOT falling back to yfinance", e)

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
        exit_mode=args.exit_mode, confirmation_gate=args.confirmation_gate,
    )

    signals = build_signals(
        eps_by, bars_by, info_by,
        sue_params=sue_params, screen_params=screen_params,
        window_start=args.start, window_end=args.end,
        confirmation_gate=args.confirmation_gate,
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
