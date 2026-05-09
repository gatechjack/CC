"""Coinbase BTC Accumulator — Phase 1 backtest harness.

Pulls historical TV alerts from prod's audit_event log, fetches
matching Coinbase BTC/USD OHLCV bars, walks the alert stream forward
through the confluence engine, simulates 100%-in/out fills at bar
mid, outputs a trade ledger + summary.

Usage:
  python scripts/backtest_btc_accumulator.py \\
    --start 2026-04-30 \\
    --end   2026-05-08 \\
    --starting-cash 10000 \\
    --starting-state cash

  python scripts/backtest_btc_accumulator.py --help

Source-of-truth for inputs:
  - Alerts: prod's audit_event WHERE actor IN ('lord_otter','market_cypher')
    AND kind='webhook_received'. Pulled via SSH + sqlite3.
  - OHLCV: Coinbase Exchange public REST (no auth needed).
  - Config: `btc_accumulator` block in `config/strategies.yaml`.

Caching:
  - Alerts cached to `data/historical_alerts/cache_<start>_<end>.json`
    after first SSH pull. Subsequent runs skip the SSH if cache exists
    AND was created in the last 6 hours.
  - OHLCV cached to `data/historical_alerts/ohlcv_<start>_<end>.json`
    similarly.
  - Pass `--refresh` to bypass caches.

Output:
  - `data/backtest_runs/<timestamp>/ledger.json` — full event-by-event
    trade ledger (every alert, decision, score breakdown, fill).
  - `data/backtest_runs/<timestamp>/summary.md` — human-readable
    summary (round-trip count, P&L, max DD, time in BTC vs cash,
    sensitivity to config).
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

# Repo root resolves so the script is runnable from anywhere.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from trading_corp.agents.strategies.btc_accumulator import (  # noqa: E402
    AlertEvent,
    ConfluenceConfig,
    Decision,
    PriceContext,
    State,
    evaluate_confluence,
    filter_live_alerts,
)


log = logging.getLogger("backtest_btc_accumulator")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


SSH_TARGET = "azureuser@trading.jacksumner.com"
PROD_DB = "/home/azureuser/trading_corp/data/trading_corp.db"
COINBASE_CANDLES_URL = (
    "https://api.exchange.coinbase.com/products/BTC-USD/candles"
)
COINBASE_GRANULARITY_SEC = 60   # 1m bars
COINBASE_MAX_CANDLES_PER_REQ = 300

CACHE_TTL_HOURS = 6


# ── Data ingestion ──────────────────────────────────────────────────


def _alert_cache_path(start: datetime, end: datetime) -> Path:
    return _REPO_ROOT / "data" / "historical_alerts" / (
        f"cache_alerts_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.json"
    )


def _ohlcv_cache_path(start: datetime, end: datetime) -> Path:
    return _REPO_ROOT / "data" / "historical_alerts" / (
        f"cache_ohlcv_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.json"
    )


def _is_cache_fresh(p: Path) -> bool:
    if not p.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)
    return age < timedelta(hours=CACHE_TTL_HOURS)


def fetch_alerts_from_prod(start: datetime, end: datetime, refresh: bool) -> list[AlertEvent]:
    """Pull `webhook_received` rows from prod's audit_event for both
    Otter and Cypher webhooks within the date range. Caches result."""
    cache = _alert_cache_path(start, end)
    cache.parent.mkdir(parents=True, exist_ok=True)

    if not refresh and _is_cache_fresh(cache):
        log.info("Using cached alerts at %s", cache)
        rows = json.loads(cache.read_text(encoding="utf-8"))
    else:
        log.info("Pulling alerts from prod via SSH (%s → %s)…", start.date(), end.date())
        # NOTE: -separator '|' chosen because audit payloads contain
        # commas; CSV mode would require careful quote handling.
        sql = (
            "SELECT ts, actor, json_extract(payload_json,'$.signal') AS signal, "
            "json_extract(payload_json,'$.symbol') AS symbol, "
            "json_extract(payload_json,'$.price') AS price "
            "FROM audit_event "
            "WHERE actor IN ('lord_otter','market_cypher') "
            "AND kind='webhook_received' "
            f"AND ts >= '{start.isoformat()}' "
            f"AND ts <  '{end.isoformat()}' "
            "ORDER BY ts"
        )
        cmd = ["ssh", SSH_TARGET, f"sqlite3 -separator '|' {PROD_DB} \"{sql}\""]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        rows = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            ts, actor, signal, symbol, price = line.split("|", 4)
            if not signal or signal == "":
                continue   # smoke tests / malformed payloads
            rows.append({
                "ts": ts,
                "actor": actor,
                "signal": signal.lower(),
                "symbol": symbol,
                "price": float(price) if price and price != "" else None,
            })
        cache.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        log.info("Cached %d alert rows to %s", len(rows), cache)

    alerts = [
        AlertEvent(
            ts=datetime.fromisoformat(r["ts"].replace("+00:00", "+00:00")),
            signal_name=r["signal"],
        )
        for r in rows
    ]
    log.info("Loaded %d alerts (Otter+Cypher) for backtest range", len(alerts))
    return alerts


def fetch_ohlcv_from_coinbase(start: datetime, end: datetime, refresh: bool) -> list[dict]:
    """Fetch BTC/USD 1m candles from Coinbase Exchange's public REST.
    Coinbase returns max 300 candles per request, so we paginate by
    300-minute windows.

    Returns: list of {ts: datetime, open, high, low, close, volume}.
    Sorted ascending by ts.
    """
    cache = _ohlcv_cache_path(start, end)
    cache.parent.mkdir(parents=True, exist_ok=True)

    if not refresh and _is_cache_fresh(cache):
        log.info("Using cached OHLCV at %s", cache)
        bars = json.loads(cache.read_text(encoding="utf-8"))
    else:
        log.info("Fetching Coinbase OHLCV (%s → %s)…", start.date(), end.date())
        bars: list[dict] = []
        cursor = start
        while cursor < end:
            window_end = min(
                cursor + timedelta(minutes=COINBASE_MAX_CANDLES_PER_REQ),
                end,
            )
            url = (
                f"{COINBASE_CANDLES_URL}"
                f"?start={cursor.isoformat()}"
                f"&end={window_end.isoformat()}"
                f"&granularity={COINBASE_GRANULARITY_SEC}"
            )
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "trading-corp-backtest/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                data = json.load(resp)
            # Coinbase returns: [time, low, high, open, close, volume]
            # Newest first. We reverse to get chronological.
            for row in reversed(data):
                bars.append({
                    "ts": datetime.fromtimestamp(row[0], tz=timezone.utc).isoformat(),
                    "low": row[1],
                    "high": row[2],
                    "open": row[3],
                    "close": row[4],
                    "volume": row[5],
                })
            cursor = window_end
        # Deduplicate (window edges may overlap by 1 bar)
        seen: set[str] = set()
        deduped: list[dict] = []
        for b in bars:
            if b["ts"] not in seen:
                seen.add(b["ts"])
                deduped.append(b)
        bars = sorted(deduped, key=lambda b: b["ts"])
        cache.write_text(json.dumps(bars, indent=2), encoding="utf-8")
        log.info("Cached %d OHLCV bars to %s", len(bars), cache)

    # Re-parse cached datetimes
    for b in bars:
        if isinstance(b["ts"], str):
            b["ts"] = datetime.fromisoformat(b["ts"])
    log.info("Loaded %d 1m OHLCV bars", len(bars))
    return bars


# ── Price context ──────────────────────────────────────────────────


def find_bar_at(bars: list[dict], ts: datetime) -> dict | None:
    """Binary-search the bar containing ts. Bars are 1m so the bar
    `b` containing ts has `b['ts'] <= ts < b['ts'] + 1min`."""
    if not bars:
        return None
    lo, hi = 0, len(bars) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        bar_start = bars[mid]["ts"]
        bar_end = bar_start + timedelta(seconds=COINBASE_GRANULARITY_SEC)
        if bar_start <= ts < bar_end:
            return bars[mid]
        if ts < bar_start:
            hi = mid - 1
        else:
            lo = mid + 1
    return None


def fill_price_at(bars: list[dict], ts: datetime) -> float | None:
    """Bar-mid for the bar containing ts. Per design lock #3."""
    bar = find_bar_at(bars, ts)
    if bar is None:
        return None
    return (bar["open"] + bar["close"]) / 2.0


def pct_change_in_window(
    bars: list[dict], ts: datetime, window_minutes: int,
) -> float:
    """% change from the bar `window_minutes` before ts to the bar
    containing ts. Positive = rose. Returns 0.0 if data unavailable."""
    now_bar = find_bar_at(bars, ts)
    then_bar = find_bar_at(bars, ts - timedelta(minutes=window_minutes))
    if now_bar is None or then_bar is None:
        return 0.0
    if then_bar["close"] == 0:
        return 0.0
    return (now_bar["close"] - then_bar["close"]) / then_bar["close"] * 100.0


def session_vwap_at(bars: list[dict], ts: datetime) -> float | None:
    """VWAP since 00:00 UTC of ts's date, computed from typical-price
    × volume / cumulative volume. Returns None if no bars in window."""
    day_start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    sum_pv = 0.0
    sum_v = 0.0
    for b in bars:
        if b["ts"] < day_start:
            continue
        if b["ts"] > ts:
            break
        typical = (b["high"] + b["low"] + b["close"]) / 3.0
        sum_pv += typical * b["volume"]
        sum_v += b["volume"]
    if sum_v == 0:
        return None
    return sum_pv / sum_v


def _resample_to_4h(bars: list[dict]) -> list[dict]:
    """Cheap 4h resample. Returns list of {ts, open, high, low,
    close, volume} aligned to UTC 00:00, 04:00, 08:00, 12:00, 16:00,
    20:00 boundaries."""
    if not bars:
        return []
    out: list[dict] = []
    cur: dict | None = None
    cur_bucket: datetime | None = None
    for b in bars:
        bucket = b["ts"].replace(
            hour=(b["ts"].hour // 4) * 4, minute=0, second=0, microsecond=0,
        )
        if cur is None or bucket != cur_bucket:
            if cur is not None:
                out.append(cur)
            cur_bucket = bucket
            cur = {
                "ts": bucket,
                "open": b["open"], "high": b["high"], "low": b["low"],
                "close": b["close"], "volume": b["volume"],
            }
        else:
            cur["high"] = max(cur["high"], b["high"])
            cur["low"] = min(cur["low"], b["low"])
            cur["close"] = b["close"]
            cur["volume"] += b["volume"]
    if cur is not None:
        out.append(cur)
    return out


def hh_ll_4h_at(
    bars_4h: list[dict], ts: datetime,
) -> tuple[bool, bool]:
    """Returns (higher_highs_4h, lower_lows_4h) flags relative to the
    PRIOR completed 4h bar. The current (in-progress) 4h bar is
    excluded from the comparison — only completed bars count, to
    avoid look-ahead within the bar."""
    bucket = ts.replace(
        hour=(ts.hour // 4) * 4, minute=0, second=0, microsecond=0,
    )
    # Find the index of the bar matching `bucket` (the in-progress bar)
    cur_idx = None
    for i, b in enumerate(bars_4h):
        if b["ts"] == bucket:
            cur_idx = i
            break
    if cur_idx is None or cur_idx < 2:
        return False, False
    # Compare the most-recently-COMPLETED 4h bar to the one before it
    last_completed = bars_4h[cur_idx - 1]
    prior         = bars_4h[cur_idx - 2]
    return (
        last_completed["high"] > prior["high"],
        last_completed["low"] < prior["low"],
    )


def volume_above_20bar_avg_at(bars: list[dict], ts: datetime) -> bool:
    """True iff the bar containing ts has volume > 20-bar trailing
    average (computed from the 20 PRIOR bars; current bar's volume
    is the comparison target, not in the average)."""
    bar = find_bar_at(bars, ts)
    if bar is None:
        return False
    # Find the 20 bars BEFORE this one
    idx = bars.index(bar)
    if idx < 20:
        return False
    prior20 = bars[idx - 20:idx]
    avg = sum(b["volume"] for b in prior20) / 20.0
    return bar["volume"] > avg


def build_price_context(
    bars: list[dict], ts: datetime, config: ConfluenceConfig,
    bars_4h: list[dict] | None = None,
) -> PriceContext | None:
    """Compose `PriceContext` for a given timestamp. Returns None if
    no bar exists for ts (alert outside OHLCV range).

    `bars_4h` (resampled 4h bars) is precomputed once by the caller
    and threaded in for HH/LL detection — re-resampling per alert
    would be wasteful."""
    fill = fill_price_at(bars, ts)
    if fill is None:
        return None

    vwap = session_vwap_at(bars, ts)
    above_vwap = vwap is not None and fill > vwap
    below_vwap = vwap is not None and fill < vwap

    if bars_4h:
        hh4h, ll4h = hh_ll_4h_at(bars_4h, ts)
    else:
        hh4h = ll4h = False

    return PriceContext(
        current_price=fill,
        pct_change_in_window_sell=pct_change_in_window(
            bars, ts, config.sell_on_rush.window_minutes,
        ),
        pct_change_in_window_buy=pct_change_in_window(
            bars, ts, config.buy_on_fall.window_minutes,
        ),
        above_session_vwap=above_vwap,
        below_session_vwap=below_vwap,
        higher_highs_4h=hh4h,
        lower_lows_4h=ll4h,
        volume_above_20bar_avg=volume_above_20bar_avg_at(bars, ts),
    )


# ── Backtest engine ────────────────────────────────────────────────


@dataclass
class LedgerEntry:
    ts: str
    signal_name: str
    decision: str
    raw_buy_score: int
    final_buy_score: int
    raw_sell_score: int
    final_sell_score: int
    buy_guard_penalty: int
    sell_guard_penalty: int
    buy_contributions: list  # list[tuple[str, int]]
    sell_contributions: list
    fill_price: float | None
    state_after: str
    cash_after: float
    btc_after: float
    cost_basis: float | None
    equity_after: float    # cash + btc * fill_price
    realized_pnl_round_trip: float | None  # only on SELL


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
    max_drawdown_pct: float   # peak-to-trough on equity curve
    pct_time_in_btc: float
    fires_buy: int
    fires_sell: int
    skips: int
    skips_with_reason_breakdown: dict[str, int]


def run_backtest(
    *,
    alerts: list[AlertEvent],
    bars: list[dict],
    config: ConfluenceConfig,
    starting_cash: float,
    starting_state: State,
) -> tuple[list[LedgerEntry], BacktestResult]:
    """Walk forward through chronologically-sorted alerts. At each
    alert: filter live window, compute price context, evaluate
    confluence, simulate fill at bar mid. Build ledger + summary."""
    bars_4h = _resample_to_4h(bars)
    ledger: list[LedgerEntry] = []

    state = starting_state
    cash = starting_cash if state == State.CASH else 0.0
    btc = 0.0 if state == State.CASH else (starting_cash / bars[0]["close"] if bars else 0.0)
    cost_basis: float | None = (
        bars[0]["close"] if state == State.BTC and bars else None
    )

    last_buy_price: float | None = cost_basis

    skip_reasons: dict[str, int] = {}
    fires_buy = fires_sell = skips = 0
    win_count = loss_count = breakeven_count = 0
    round_trip_pnls: list[float] = []

    # Equity curve for drawdown calculation
    equity_curve: list[tuple[datetime, float]] = []

    for alert in sorted(alerts, key=lambda a: a.ts):
        # Filter to live alerts (TTL-aware) at this moment
        live = filter_live_alerts(alerts, config, alert.ts)

        # Price context at this moment
        ctx = build_price_context(bars, alert.ts, config, bars_4h=bars_4h)
        if ctx is None:
            # Alert outside OHLCV coverage; record but don't act
            log.debug("No OHLCV at %s; skipping alert %s", alert.ts, alert.signal_name)
            continue

        verdict = evaluate_confluence(
            state=state, live_alerts=live, price_ctx=ctx,
            config=config, now=alert.ts,
        )

        # Apply decision
        realized_pnl: float | None = None
        if verdict.decision == Decision.BUY:
            fires_buy += 1
            btc = cash / ctx.current_price
            cost_basis = ctx.current_price
            last_buy_price = ctx.current_price
            cash = 0.0
            state = State.BTC
        elif verdict.decision == Decision.SELL:
            fires_sell += 1
            sell_proceeds = btc * ctx.current_price
            if last_buy_price is not None:
                # Per Board direction: "cost basis = price when sold"
                # for the next round-trip's reference. Realized P&L
                # for THIS round-trip is sell_proceeds - (btc * last_buy_price).
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
        else:
            skips += 1
            skip_reasons[verdict.reason.split(":")[0]] = (
                skip_reasons.get(verdict.reason.split(":")[0], 0) + 1
            )

        equity_after = cash + btc * ctx.current_price
        equity_curve.append((alert.ts, equity_after))

        ledger.append(LedgerEntry(
            ts=alert.ts.isoformat(),
            signal_name=alert.signal_name,
            decision=verdict.decision.value,
            raw_buy_score=verdict.breakdown.raw_buy_score,
            final_buy_score=verdict.breakdown.final_buy_score,
            raw_sell_score=verdict.breakdown.raw_sell_score,
            final_sell_score=verdict.breakdown.final_sell_score,
            buy_guard_penalty=verdict.breakdown.buy_guard_penalty,
            sell_guard_penalty=verdict.breakdown.sell_guard_penalty,
            buy_contributions=verdict.breakdown.buy_contributions,
            sell_contributions=verdict.breakdown.sell_contributions,
            fill_price=ctx.current_price,
            state_after=state.value,
            cash_after=cash,
            btc_after=btc,
            cost_basis=cost_basis,
            equity_after=equity_after,
            realized_pnl_round_trip=realized_pnl,
        ))

    # Final equity (mark-to-market at last bar close if still in BTC)
    if bars and state == State.BTC:
        final_equity = cash + btc * bars[-1]["close"]
    else:
        final_equity = cash + btc * (bars[-1]["close"] if bars else 0)

    pct_return = (final_equity - starting_cash) / starting_cash * 100.0

    # Max drawdown on equity curve
    peak = starting_cash
    max_dd_pct = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100.0
        max_dd_pct = max(max_dd_pct, dd)

    # % time in BTC: count alert intervals where state was BTC
    pct_time_in_btc = 0.0
    if len(equity_curve) >= 2:
        # Reconstruct state per ledger row
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
        skips=skips,
        skips_with_reason_breakdown=skip_reasons,
    )
    return ledger, summary


# ── Output ─────────────────────────────────────────────────────────


def write_outputs(
    ledger: list[LedgerEntry],
    summary: BacktestResult,
    output_dir: Path,
    config: ConfluenceConfig,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Ledger as JSON
    ledger_path = output_dir / "ledger.json"
    ledger_path.write_text(
        json.dumps([asdict(e) for e in ledger], indent=2, default=str),
        encoding="utf-8",
    )

    # Summary as markdown
    summary_path = output_dir / "summary.md"
    md = [
        "# BTC Accumulator Backtest — Summary",
        "",
        f"- **Starting cash:** ${summary.starting_cash:,.2f}",
        f"- **Starting state:** {summary.starting_state}",
        f"- **Final equity:** ${summary.final_equity:,.2f}",
        f"- **Return:** {summary.pct_return:+.2f}%",
        f"- **Max drawdown:** {summary.max_drawdown_pct:.2f}%",
        f"- **Time in BTC:** {summary.pct_time_in_btc:.1f}%",
        "",
        "## Decisions",
        f"- BUY fires: {summary.fires_buy}",
        f"- SELL fires: {summary.fires_sell}",
        f"- SKIPs: {summary.skips}",
        "",
        "## Round-trips",
        f"- Count: {summary.round_trip_count}",
        f"- Wins: {summary.win_count}",
        f"- Losses: {summary.loss_count}",
        f"- Breakeven: {summary.breakeven_count}",
        f"- Avg P&L per round-trip: ${summary.avg_round_trip_pnl:+,.2f}",
        "",
        "## Config used",
        f"- min_score_buy: {config.min_score_buy}",
        f"- min_score_sell: {config.min_score_sell}",
        f"- factors: {len(config.factors)} configured",
        f"- sell_on_rush window: {config.sell_on_rush.window_minutes}m",
        f"- buy_on_fall window: {config.buy_on_fall.window_minutes}m",
    ]
    summary_path.write_text("\n".join(md), encoding="utf-8")

    log.info("Wrote ledger to %s", ledger_path)
    log.info("Wrote summary to %s", summary_path)
    print()
    print("\n".join(md))


# ── CLI ────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", required=True, help="UTC date (YYYY-MM-DD)")
    p.add_argument("--end",   required=True, help="UTC date (YYYY-MM-DD), exclusive")
    p.add_argument("--starting-cash", type=float, default=10_000.0)
    p.add_argument("--starting-state", choices=["cash", "btc"], default="cash")
    p.add_argument(
        "--config", default=str(_REPO_ROOT / "config" / "strategies.yaml"),
        help="Path to strategies.yaml",
    )
    p.add_argument(
        "--output", default=None,
        help="Output dir (default: data/backtest_runs/<utc-timestamp>/)",
    )
    p.add_argument("--refresh", action="store_true",
                   help="Bypass alert + OHLCV caches")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    if end <= start:
        log.error("end must be after start")
        return 2

    # Load config
    raw_yaml = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if "btc_accumulator" not in raw_yaml:
        log.error("config %s lacks `btc_accumulator` block", args.config)
        return 2
    config = ConfluenceConfig.from_dict(raw_yaml["btc_accumulator"])
    log.info("Loaded config: %d factors, min_buy=%d, min_sell=%d",
             len(config.factors), config.min_score_buy, config.min_score_sell)

    # Ingest
    alerts = fetch_alerts_from_prod(start, end, args.refresh)
    bars = fetch_ohlcv_from_coinbase(start, end, args.refresh)

    # Sanity report on signal coverage (warn if alerts have signals not in YAML)
    known_signals = set(config.factors.keys())
    seen_signals = {a.signal_name for a in alerts}
    unknown = seen_signals - known_signals
    # Allow `_bull/_bear` directional variants
    if unknown:
        from trading_corp.agents.strategies.btc_accumulator import (
            _strip_directional_suffix,
        )
        truly_unknown = {
            s for s in unknown if _strip_directional_suffix(s) not in known_signals
        }
        if truly_unknown:
            log.warning("Alerts contain signals NOT in YAML factors (will be ignored): %s",
                        sorted(truly_unknown))

    # Run
    starting_state = State.CASH if args.starting_state == "cash" else State.BTC
    ledger, summary = run_backtest(
        alerts=alerts, bars=bars, config=config,
        starting_cash=args.starting_cash, starting_state=starting_state,
    )

    # Output
    if args.output:
        out_dir = Path(args.output)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        out_dir = _REPO_ROOT / "data" / "backtest_runs" / ts
    write_outputs(ledger, summary, out_dir, config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
