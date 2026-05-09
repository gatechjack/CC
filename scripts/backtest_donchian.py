"""Coinbase BTC Donchian Channel Breakout — backtest harness.

Walks bar-by-bar through Coinbase BTC/USD OHLCV, evaluates the
Donchian decision at each bar's close, simulates 100%-in/out fills
at next-bar open (one-bar latency = realistic + look-ahead-safe).

Reuses Coinbase OHLCV cache from scripts/backtest_btc_accumulator.py
when present. Granularity is configurable; default 1h is the sweet
spot for Donchian on a multi-day corpus.

Usage:
  python scripts/backtest_donchian.py \\
    --start 2026-04-30 --end 2026-05-09 \\
    --starting-cash 10000 \\
    --entry-lookback 50 --exit-lookback 20 \\
    --granularity 3600

  python scripts/backtest_donchian.py --help
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import urllib.request

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from trading_corp.agents.strategies.donchian_btc import (  # noqa: E402
    Decision,
    DonchianConfig,
    DonchianVerdict,
    State,
    evaluate_donchian,
)


log = logging.getLogger("backtest_donchian")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
COINBASE_MAX_CANDLES_PER_REQ = 300
CACHE_TTL_HOURS = 6


def _ohlcv_cache_path(start: datetime, end: datetime, granularity: int) -> Path:
    return _REPO_ROOT / "data" / "historical_alerts" / (
        f"cache_ohlcv_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}_g{granularity}.json"
    )


def _is_cache_fresh(p: Path) -> bool:
    if not p.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)
    return age < timedelta(hours=CACHE_TTL_HOURS)


def fetch_ohlcv(start: datetime, end: datetime, granularity: int, refresh: bool) -> list[dict]:
    """Fetch BTC/USD OHLCV from Coinbase Exchange public REST.
    Same shape as scripts/backtest_btc_accumulator.py but parameterized
    on granularity (Coinbase supports 60, 300, 900, 3600, 21600, 86400)."""
    cache = _ohlcv_cache_path(start, end, granularity)
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not refresh and _is_cache_fresh(cache):
        log.info("Using cached OHLCV at %s", cache)
        bars = json.loads(cache.read_text(encoding="utf-8"))
    else:
        log.info("Fetching Coinbase OHLCV (granularity=%ds, %s → %s)…",
                 granularity, start.date(), end.date())
        bars: list[dict] = []
        bar_seconds = granularity
        # Each REST call returns up to 300 candles for the granularity asked.
        # Step in window-size = 300 * granularity seconds.
        cursor = start
        step = timedelta(seconds=COINBASE_MAX_CANDLES_PER_REQ * bar_seconds)
        while cursor < end:
            window_end = min(cursor + step, end)
            url = (
                f"{COINBASE_CANDLES_URL}"
                f"?start={cursor.isoformat()}&end={window_end.isoformat()}"
                f"&granularity={granularity}"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "trading-corp-backtest/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                data = json.load(resp)
            for row in reversed(data):
                bars.append({
                    "ts": datetime.fromtimestamp(row[0], tz=timezone.utc).isoformat(),
                    "low": row[1], "high": row[2], "open": row[3],
                    "close": row[4], "volume": row[5],
                })
            cursor = window_end
        # Dedup
        seen: set[str] = set()
        deduped: list[dict] = []
        for b in bars:
            if b["ts"] not in seen:
                seen.add(b["ts"])
                deduped.append(b)
        bars = sorted(deduped, key=lambda b: b["ts"])
        cache.write_text(json.dumps(bars, indent=2), encoding="utf-8")
        log.info("Cached %d bars to %s", len(bars), cache)
    for b in bars:
        if isinstance(b["ts"], str):
            b["ts"] = datetime.fromisoformat(b["ts"])
    log.info("Loaded %d OHLCV bars (granularity=%ds)", len(bars), granularity)
    return bars


# ── Backtest ───────────────────────────────────────────────────────


@dataclass
class LedgerEntry:
    ts: str
    decision: str
    current_close: float
    donchian_high: float | None
    donchian_low: float | None
    trend_filter_sma: float | None
    trend_filter_passed: bool
    fill_price: float | None         # next-bar open (None on SKIP)
    state_after: str
    cash_after: float
    btc_after: float
    cost_basis: float | None
    equity_after: float
    realized_pnl_round_trip: float | None


@dataclass
class BacktestResult:
    starting_cash: float
    starting_state: str
    final_cash: float
    final_btc: float
    final_equity: float
    pct_return: float
    round_trip_count: int
    win_count: int
    loss_count: int
    breakeven_count: int
    avg_round_trip_pnl: float
    max_drawdown_pct: float
    pct_time_in_btc: float
    fires_buy: int
    fires_sell: int
    decisions_evaluated: int


def run_donchian_backtest(
    *,
    bars: list[dict],
    config: DonchianConfig,
    starting_cash: float,
    starting_state: State,
) -> tuple[list[LedgerEntry], BacktestResult]:
    """Walk bar-by-bar. At each bar's close, evaluate Donchian using
    the bars-up-to-and-including-current window. If a decision fires
    (BUY/SELL), simulate fill at NEXT bar's open — one-bar latency
    captures the realistic "you can't trade on the close you just
    saw" constraint AND eliminates intra-bar look-ahead."""
    state = starting_state
    cash = starting_cash if state == State.CASH else 0.0
    btc = 0.0 if state == State.CASH else (starting_cash / bars[0]["close"] if bars else 0.0)
    cost_basis: float | None = bars[0]["close"] if state == State.BTC and bars else None
    last_buy_price: float | None = cost_basis

    ledger: list[LedgerEntry] = []
    fires_buy = fires_sell = 0
    win_count = loss_count = breakeven_count = 0
    round_trip_pnls: list[float] = []
    equity_curve: list[tuple[datetime, float]] = []

    for i in range(len(bars) - 1):  # leave room for next-bar open as fill
        bar_now = bars[i]
        bar_next = bars[i + 1]
        window = bars[: i + 1]

        verdict = evaluate_donchian(
            state=state, bars_window=window, config=config, now=bar_now["ts"],
        )
        realized_pnl: float | None = None
        fill: float | None = None

        if verdict.decision == Decision.BUY:
            fill = bar_next["open"]
            btc = cash / fill
            cost_basis = fill
            last_buy_price = fill
            cash = 0.0
            state = State.BTC
            fires_buy += 1
        elif verdict.decision == Decision.SELL:
            fill = bar_next["open"]
            sell_proceeds = btc * fill
            if last_buy_price is not None:
                realized_pnl = sell_proceeds - (btc * last_buy_price)
                round_trip_pnls.append(realized_pnl)
                if realized_pnl > 0:
                    win_count += 1
                elif realized_pnl < 0:
                    loss_count += 1
                else:
                    breakeven_count += 1
            cash = sell_proceeds
            btc = 0.0
            cost_basis = None
            state = State.CASH
            fires_sell += 1

        equity_after = cash + btc * bar_now["close"]
        equity_curve.append((bar_now["ts"], equity_after))

        ledger.append(LedgerEntry(
            ts=bar_now["ts"].isoformat(),
            decision=verdict.decision.value,
            current_close=verdict.breakdown.current_close,
            donchian_high=verdict.breakdown.donchian_high,
            donchian_low=verdict.breakdown.donchian_low,
            trend_filter_sma=verdict.breakdown.trend_filter_sma,
            trend_filter_passed=verdict.breakdown.trend_filter_passed,
            fill_price=fill,
            state_after=state.value,
            cash_after=cash,
            btc_after=btc,
            cost_basis=cost_basis,
            equity_after=equity_after,
            realized_pnl_round_trip=realized_pnl,
        ))

    final_close = bars[-1]["close"] if bars else 0
    final_equity = cash + btc * final_close
    pct_return = (final_equity - starting_cash) / starting_cash * 100.0

    peak = starting_cash
    max_dd_pct = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100.0
        max_dd_pct = max(max_dd_pct, dd)

    pct_time_in_btc = 0.0
    if len(equity_curve) >= 2:
        time_in_btc = timedelta(0)
        time_total = timedelta(0)
        for i in range(len(ledger) - 1):
            dt = datetime.fromisoformat(ledger[i + 1].ts) - datetime.fromisoformat(ledger[i].ts)
            time_total += dt
            if ledger[i].state_after == "btc":
                time_in_btc += dt
        if time_total > timedelta(0):
            pct_time_in_btc = time_in_btc.total_seconds() / time_total.total_seconds() * 100.0

    summary = BacktestResult(
        starting_cash=starting_cash,
        starting_state=starting_state.value,
        final_cash=cash,
        final_btc=btc,
        final_equity=final_equity,
        pct_return=pct_return,
        round_trip_count=len(round_trip_pnls),
        win_count=win_count,
        loss_count=loss_count,
        breakeven_count=breakeven_count,
        avg_round_trip_pnl=(
            sum(round_trip_pnls) / len(round_trip_pnls) if round_trip_pnls else 0.0
        ),
        max_drawdown_pct=max_dd_pct,
        pct_time_in_btc=pct_time_in_btc,
        fires_buy=fires_buy,
        fires_sell=fires_sell,
        decisions_evaluated=len(ledger),
    )
    return ledger, summary


# ── Output ─────────────────────────────────────────────────────────


def write_outputs(
    ledger: list[LedgerEntry],
    summary: BacktestResult,
    output_dir: Path,
    config: DonchianConfig,
    hodl_pct: float,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "ledger.json").write_text(
        json.dumps([asdict(e) for e in ledger], indent=2, default=str), encoding="utf-8",
    )
    md = [
        "# Donchian Backtest Summary",
        "",
        f"- Strategy: Donchian Channel Breakout (entry={config.entry_lookback}, exit={config.exit_lookback}, "
        f"trend_filter={config.trend_filter_lookback})",
        f"- Granularity: {config.granularity_seconds}s ({config.granularity_seconds // 60}min)",
        f"- Decisions evaluated: {summary.decisions_evaluated}",
        f"- Starting cash: ${summary.starting_cash:,.2f}  (state={summary.starting_state})",
        f"- Final equity: ${summary.final_equity:,.2f}",
        f"- Strategy return: {summary.pct_return:+.2f}%",
        f"- HODL benchmark: {hodl_pct:+.2f}%",
        f"- Alpha vs HODL: {summary.pct_return - hodl_pct:+.2f}%",
        f"- Max drawdown: {summary.max_drawdown_pct:.2f}%",
        f"- Time in BTC: {summary.pct_time_in_btc:.1f}%",
        "",
        "## Decisions",
        f"- BUY fires: {summary.fires_buy}",
        f"- SELL fires: {summary.fires_sell}",
        f"- Round-trips: {summary.round_trip_count}",
        f"- Wins: {summary.win_count}  Losses: {summary.loss_count}  Breakeven: {summary.breakeven_count}",
        f"- Avg P&L per round-trip: ${summary.avg_round_trip_pnl:+,.2f}",
    ]
    (output_dir / "summary.md").write_text("\n".join(md), encoding="utf-8")
    log.info("Wrote outputs to %s", output_dir)
    print()
    print("\n".join(md))


# ── CLI ────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--starting-cash", type=float, default=10_000.0)
    p.add_argument("--starting-state", choices=["cash", "btc"], default="cash")
    p.add_argument("--entry-lookback", type=int, default=50)
    p.add_argument("--exit-lookback", type=int, default=20)
    p.add_argument("--trend-filter", type=int, default=None,
                   help="SMA lookback for trend filter (None = disabled)")
    p.add_argument("--granularity", type=int, default=3600,
                   help="Bar duration in seconds (60/300/900/3600/21600/86400)")
    p.add_argument("--output", default=None)
    p.add_argument("--refresh", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    bars = fetch_ohlcv(start, end, args.granularity, args.refresh)
    if len(bars) < 2:
        log.error("Need ≥2 bars to backtest, have %d", len(bars))
        return 2

    config = DonchianConfig(
        entry_lookback=args.entry_lookback,
        exit_lookback=args.exit_lookback,
        trend_filter_lookback=args.trend_filter,
        granularity_seconds=args.granularity,
    )
    starting_state = State.CASH if args.starting_state == "cash" else State.BTC

    ledger, summary = run_donchian_backtest(
        bars=bars, config=config,
        starting_cash=args.starting_cash, starting_state=starting_state,
    )

    hodl_pct = (bars[-1]["close"] - bars[0]["close"]) / bars[0]["close"] * 100.0

    if args.output:
        out_dir = Path(args.output)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        out_dir = _REPO_ROOT / "data" / "backtest_runs" / f"donchian_{ts}"
    write_outputs(ledger, summary, out_dir, config, hodl_pct)
    return 0


if __name__ == "__main__":
    sys.exit(main())
