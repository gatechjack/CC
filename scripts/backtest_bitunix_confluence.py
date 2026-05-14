"""BitUnix Futures Phase 3.2 — confluence score accumulator backtest.

Walks prod's `audit_event` log of `webhook_received` rows (Otter +
Cypher webhooks) through the new scoring engine in
`trading_corp/agents/strategies/bitunix_confluence.py`. At every alert,
the scorer evaluates the current TTL-filtered + deduped signal window
plus a `PriceContext` computed from Coinbase BTC/USD OHLCV.

When the verdict fires (PREMIUM / STANDARD / WEAK), the harness:
  - Opens a paper long or short at the bar-mid fill price
  - Sets a structural stop (1.5 × ATR_fallback or 0.3% floor) and a 2R
    take-profit (same defaults as the Phase 3.1 observer)
  - Walks 1m bars forward to resolve: TP hit, SL hit, or timeout
  - Applies the 0.5% per-trade effective-risk cap (downsizes pct → qty)

Output:
  - `data/backtest_runs/bitunix_<ts>/ledger.json` — every alert +
    every paper round-trip
  - `data/backtest_runs/bitunix_<ts>/summary.md` — verdict doc:
    win rate, avg R, max DD, tier breakdown, 16:42 scenario check.

Usage:
    python scripts/backtest_bitunix_confluence.py \
        --start 2026-04-30 --end 2026-05-12 \
        --starting-equity 10000

Re-uses ALL the alert / OHLCV / price-context machinery from
`backtest_btc_accumulator.py` — only the decision engine + position
state model are different.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.backtest_btc_accumulator import (  # noqa: E402
    _ohlcv_cache_path,
    _resample_to_4h,
    build_price_context,
    fetch_alerts_from_prod,
    fetch_ohlcv_from_coinbase,
    find_bar_at,
)
from trading_corp.agents.strategies.bitunix_confluence import (  # noqa: E402
    AlertEvent,
    BitUnixConfluenceConfig,
    BitUnixVerdict,
    Side,
    Tier,
    evaluate_confluence_futures,
    filter_live_alerts_with_dedupe,
)
from trading_corp.agents.strategies.btc_accumulator import (  # noqa: E402
    PriceContext,
)


log = logging.getLogger("backtest_bitunix_confluence")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


# Phase 3.1 risk caps + stop/TP defaults (mirrored from
# `trading_corp/agents/divisions/bitunix_futures_observer.py`).
EFFECTIVE_RISK_PER_TRADE_PCT = 0.005   # 0.5% account equity per trade
DAILY_RISK_KILL_PCT = 0.03             # 3% per UTC day cumulative at-risk
ATR_FALLBACK_PCT = 0.0004              # 0.04% × price (placeholder ATR)
ATR_MULTIPLIER = 1.5
STOP_FLOOR_PCT = 0.003                 # 0.3% absolute stop floor
DEFAULT_TP_R = 2.0
MIN_RR_RATIO = 1.5

TIER_SIZING = {
    Tier.PREMIUM:  {"size_pct": 0.04, "leverage": 8.0},
    Tier.STANDARD: {"size_pct": 0.02, "leverage": 5.0},
    Tier.WEAK:     {"size_pct": 0.01, "leverage": 2.0},
}

# Max bars to walk forward looking for SL/TP resolution. 1m bars × 24h
# = 1440. If neither hits in 24h, mark `timeout` and close at last close.
MAX_HOLD_BARS = 24 * 60


@dataclass
class PaperTrade:
    open_ts: str
    side: str                  # "buy" or "sell"
    tier: str
    entry_price: float
    stop_price: float
    tp_price: float
    qty: float
    leverage: float
    size_pct: float
    effective_risk_pct: float
    net_score: int
    raw_buy_score: int
    raw_sell_score: int

    # Resolution (filled by walk-forward)
    close_ts: str | None = None
    close_price: float | None = None
    bars_held: int | None = None
    outcome: str | None = None    # "tp" | "sl" | "timeout"
    realized_pnl: float | None = None
    realized_r: float | None = None


@dataclass
class LedgerEntry:
    ts: str
    signal_name: str
    tier: str
    side: str
    cooldown_blocked: bool
    net_score: int
    final_buy_score: int
    final_sell_score: int
    buy_contributions: list
    sell_contributions: list
    fired: bool
    trade_id: int | None
    reason: str


@dataclass
class BacktestResult:
    starting_equity: float
    final_equity: float
    pct_return: float
    max_drawdown_pct: float

    n_alerts: int
    n_fires: int
    n_skips: int
    n_cooldown_blocked: int
    n_daily_kill_blocked: int

    fires_by_tier: dict[str, int]
    fires_by_side: dict[str, int]

    n_round_trips: int
    n_tp: int
    n_sl: int
    n_timeout: int
    win_rate_pct: float
    avg_r: float
    total_r: float
    avg_bars_held: float


# ── Trade open / resolve ────────────────────────────────────────────


def open_trade(
    *, verdict: BitUnixVerdict, alert_ts: datetime, entry_price: float,
    account_equity: float,
) -> PaperTrade | None:
    """Build a paper trade from a fired verdict. Returns None if
    sizing math rejects (qty rounds to 0 or R:R below floor)."""
    tier_cfg = TIER_SIZING.get(verdict.tier)
    if tier_cfg is None:
        return None
    if entry_price <= 0 or account_equity <= 0:
        return None

    size_pct_target = float(tier_cfg["size_pct"])
    leverage = float(tier_cfg["leverage"])

    atr = entry_price * ATR_FALLBACK_PCT
    stop_distance = max(ATR_MULTIPLIER * atr, STOP_FLOOR_PCT * entry_price)
    stop_distance_pct = stop_distance / entry_price
    tp_distance = DEFAULT_TP_R * stop_distance
    rr = tp_distance / stop_distance
    if rr < MIN_RR_RATIO:
        return None

    # Effective-risk downsize
    denom = leverage * stop_distance_pct
    if denom <= 0:
        return None
    max_pct = EFFECTIVE_RISK_PER_TRADE_PCT / denom
    size_pct = min(size_pct_target, max_pct)
    eff_risk = size_pct * leverage * stop_distance_pct

    notional = account_equity * size_pct * leverage
    qty = notional / entry_price
    if qty <= 0:
        return None

    if verdict.side == Side.BUY:
        side_str = "buy"
        stop_price = entry_price - stop_distance
        tp_price = entry_price + tp_distance
    else:
        side_str = "sell"
        stop_price = entry_price + stop_distance
        tp_price = entry_price - tp_distance

    return PaperTrade(
        open_ts=alert_ts.isoformat(),
        side=side_str,
        tier=verdict.tier.value,
        entry_price=entry_price,
        stop_price=stop_price,
        tp_price=tp_price,
        qty=qty,
        leverage=leverage,
        size_pct=size_pct,
        effective_risk_pct=eff_risk,
        net_score=verdict.breakdown.net_score,
        raw_buy_score=verdict.breakdown.raw_buy_score,
        raw_sell_score=verdict.breakdown.raw_sell_score,
    )


def resolve_trade(
    trade: PaperTrade, bars: list[dict], open_ts: datetime,
) -> None:
    """Walk bars forward from `open_ts` to find TP or SL hit. Marks the
    trade in-place. Conservative: if both stop+TP fall inside the SAME
    bar, assume STOP hits first (we can't see intra-bar ordering)."""
    # Locate the bar containing open_ts
    start_bar = find_bar_at(bars, open_ts)
    if start_bar is None:
        trade.outcome = "no_data"
        return
    start_idx = bars.index(start_bar)

    is_long = trade.side == "buy"
    for i in range(start_idx + 1, min(start_idx + 1 + MAX_HOLD_BARS, len(bars))):
        bar = bars[i]
        high = bar["high"]
        low = bar["low"]
        if is_long:
            sl_hit = low <= trade.stop_price
            tp_hit = high >= trade.tp_price
        else:
            sl_hit = high >= trade.stop_price
            tp_hit = low <= trade.tp_price

        if sl_hit and tp_hit:
            # Both in same bar — assume worst case (stop first)
            trade.close_ts = bar["ts"].isoformat() if isinstance(bar["ts"], datetime) else bar["ts"]
            trade.close_price = trade.stop_price
            trade.bars_held = i - start_idx
            trade.outcome = "sl"
            break
        if sl_hit:
            trade.close_ts = bar["ts"].isoformat() if isinstance(bar["ts"], datetime) else bar["ts"]
            trade.close_price = trade.stop_price
            trade.bars_held = i - start_idx
            trade.outcome = "sl"
            break
        if tp_hit:
            trade.close_ts = bar["ts"].isoformat() if isinstance(bar["ts"], datetime) else bar["ts"]
            trade.close_price = trade.tp_price
            trade.bars_held = i - start_idx
            trade.outcome = "tp"
            break
    else:
        # Walked MAX_HOLD_BARS without resolution → timeout at last seen close
        last = bars[min(start_idx + MAX_HOLD_BARS, len(bars) - 1)]
        trade.close_ts = last["ts"].isoformat() if isinstance(last["ts"], datetime) else last["ts"]
        trade.close_price = last["close"]
        trade.bars_held = min(MAX_HOLD_BARS, len(bars) - 1 - start_idx)
        trade.outcome = "timeout"

    # P&L in equity terms
    if trade.close_price is not None:
        price_move = trade.close_price - trade.entry_price
        if not is_long:
            price_move = -price_move
        # qty is sized so eff_risk × equity = qty × stop_distance (in price)
        # so realized_r = price_move / stop_distance
        stop_distance = abs(trade.entry_price - trade.stop_price)
        if stop_distance > 0:
            trade.realized_r = price_move / stop_distance
        # P&L = qty × price_move (no funding/fees modeled here)
        trade.realized_pnl = trade.qty * price_move


# ── Backtest loop ───────────────────────────────────────────────────


def run_backtest(
    *,
    alerts: list[AlertEvent],
    bars: list[dict],
    config: BitUnixConfluenceConfig,
    starting_equity: float,
) -> tuple[list[LedgerEntry], list[PaperTrade], BacktestResult]:
    """Walk alerts chronologically, evaluate scorer at each, open/resolve
    paper trades.

    Position model for v1: at most ONE open trade at a time.
    - New same-direction signal during open trade → cooldown should
      block (handled in scorer) OR ignore here.
    - New opposite-direction signal during open trade → close current
      trade at current bar price, then evaluate new fire normally.
    - Daily-risk-kill caps cumulative-at-risk per UTC day at 3% equity.
    """
    bars_4h = _resample_to_4h(bars)

    ledger: list[LedgerEntry] = []
    trades: list[PaperTrade] = []
    equity = starting_equity
    equity_curve: list[tuple[datetime, float]] = []

    open_trade_obj: PaperTrade | None = None
    open_trade_idx: int | None = None
    last_fire_ts_buy: datetime | None = None
    last_fire_ts_sell: datetime | None = None

    # Daily kill ledger
    daily_at_risk: dict[str, float] = {}
    n_cooldown_blocked = 0
    n_daily_kill_blocked = 0
    n_skips = 0

    fires_by_tier = {t.value: 0 for t in Tier if t != Tier.SKIP}
    fires_by_side = {"buy": 0, "sell": 0}

    sorted_alerts = sorted(alerts, key=lambda a: a.ts)

    for a in sorted_alerts:
        # If there's an open trade, walk price forward and see if it
        # would have resolved before this alert. (Simple model: we only
        # check resolution at alert times — close the open trade if SL
        # or TP would have hit by now.)
        if open_trade_obj is not None and open_trade_obj.outcome is None:
            # Snapshot pre-alert resolution
            resolve_trade(open_trade_obj, bars, datetime.fromisoformat(open_trade_obj.open_ts))
            if open_trade_obj.outcome and open_trade_obj.close_ts:
                close_dt = datetime.fromisoformat(open_trade_obj.close_ts)
                if close_dt <= a.ts:
                    # Trade resolved before this alert; bank P&L
                    equity += (open_trade_obj.realized_pnl or 0.0)
                    open_trade_obj = None
                    open_trade_idx = None
                else:
                    # Trade hasn't resolved yet — leave it open (it'll
                    # resolve later). For this iteration, reset its
                    # outcome to None so we don't double-close.
                    open_trade_obj.outcome = None
                    open_trade_obj.close_ts = None
                    open_trade_obj.close_price = None
                    open_trade_obj.bars_held = None
                    open_trade_obj.realized_pnl = None
                    open_trade_obj.realized_r = None

        # Pre-filter + dedupe live alerts
        live = filter_live_alerts_with_dedupe(sorted_alerts, config, a.ts)
        ctx = build_price_context(bars, a.ts, ctx_config(config), bars_4h=bars_4h)
        if ctx is None:
            continue

        verdict = evaluate_confluence_futures(
            live_alerts=live, price_ctx=ctx, config=config, now=a.ts,
            last_fire_ts_buy=last_fire_ts_buy,
            last_fire_ts_sell=last_fire_ts_sell,
        )

        fired = False
        trade_id: int | None = None

        if verdict.cooldown_blocked:
            n_cooldown_blocked += 1
        elif verdict.tier != Tier.SKIP:
            # Daily kill check
            day_key = a.ts.date().isoformat()
            day_at_risk = daily_at_risk.get(day_key, 0.0)
            tier_cfg = TIER_SIZING[verdict.tier]
            # Conservative estimate of effective risk for the kill check.
            # The actual eff_risk is computed in open_trade(); we use the
            # tier's nominal target as a ceiling. Slight over-blocking
            # is acceptable.
            stop_distance_pct = max(STOP_FLOOR_PCT, ATR_MULTIPLIER * ATR_FALLBACK_PCT)
            estimated_eff_risk = min(
                tier_cfg["size_pct"] * tier_cfg["leverage"] * stop_distance_pct,
                EFFECTIVE_RISK_PER_TRADE_PCT,
            )
            if day_at_risk + estimated_eff_risk > DAILY_RISK_KILL_PCT:
                n_daily_kill_blocked += 1
            else:
                # Handle opposite-side close
                if open_trade_obj is not None:
                    if open_trade_obj.side != ("buy" if verdict.side == Side.BUY else "sell"):
                        # Close current at this alert's bar price
                        close_price = ctx.current_price
                        is_long = open_trade_obj.side == "buy"
                        move = close_price - open_trade_obj.entry_price
                        if not is_long:
                            move = -move
                        sd = abs(open_trade_obj.entry_price - open_trade_obj.stop_price)
                        open_trade_obj.close_ts = a.ts.isoformat()
                        open_trade_obj.close_price = close_price
                        open_trade_obj.outcome = "flipped"
                        open_trade_obj.realized_r = (move / sd) if sd > 0 else None
                        open_trade_obj.realized_pnl = open_trade_obj.qty * move
                        equity += (open_trade_obj.realized_pnl or 0.0)
                        open_trade_obj = None
                        open_trade_idx = None

                if open_trade_obj is None:
                    new_trade = open_trade(
                        verdict=verdict, alert_ts=a.ts,
                        entry_price=ctx.current_price, account_equity=equity,
                    )
                    if new_trade is not None:
                        trades.append(new_trade)
                        open_trade_obj = new_trade
                        open_trade_idx = len(trades) - 1
                        trade_id = open_trade_idx
                        fired = True
                        fires_by_tier[verdict.tier.value] += 1
                        fires_by_side[new_trade.side] += 1
                        daily_at_risk[day_key] = day_at_risk + (new_trade.effective_risk_pct or 0.0)
                        if verdict.side == Side.BUY:
                            last_fire_ts_buy = a.ts
                        else:
                            last_fire_ts_sell = a.ts
        else:
            n_skips += 1

        ledger.append(LedgerEntry(
            ts=a.ts.isoformat(),
            signal_name=a.signal_name,
            tier=verdict.tier.value,
            side=verdict.side.value,
            cooldown_blocked=verdict.cooldown_blocked,
            net_score=verdict.breakdown.net_score,
            final_buy_score=verdict.breakdown.final_buy_score,
            final_sell_score=verdict.breakdown.final_sell_score,
            buy_contributions=verdict.breakdown.buy_contributions,
            sell_contributions=verdict.breakdown.sell_contributions,
            fired=fired,
            trade_id=trade_id,
            reason=verdict.reason,
        ))

        equity_curve.append((a.ts, equity))

    # Resolve any still-open trade at the last bar
    if open_trade_obj is not None and open_trade_obj.outcome is None:
        resolve_trade(open_trade_obj, bars, datetime.fromisoformat(open_trade_obj.open_ts))
        equity += (open_trade_obj.realized_pnl or 0.0)

    # Stats
    n_tp = sum(1 for t in trades if t.outcome == "tp")
    n_sl = sum(1 for t in trades if t.outcome == "sl")
    n_timeout = sum(1 for t in trades if t.outcome in ("timeout", "flipped"))
    resolved = [t for t in trades if t.realized_r is not None]
    avg_r = sum(t.realized_r for t in resolved) / len(resolved) if resolved else 0.0
    total_r = sum(t.realized_r for t in resolved)
    avg_bars = (
        sum(t.bars_held for t in trades if t.bars_held is not None)
        / max(1, sum(1 for t in trades if t.bars_held is not None))
    )

    # Drawdown
    peak = starting_equity
    max_dd_pct = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            dd = (peak - eq) / peak * 100.0
            max_dd_pct = max(max_dd_pct, dd)

    summary = BacktestResult(
        starting_equity=starting_equity,
        final_equity=equity,
        pct_return=(equity - starting_equity) / starting_equity * 100.0,
        max_drawdown_pct=max_dd_pct,
        n_alerts=len(sorted_alerts),
        n_fires=len(trades),
        n_skips=n_skips,
        n_cooldown_blocked=n_cooldown_blocked,
        n_daily_kill_blocked=n_daily_kill_blocked,
        fires_by_tier=fires_by_tier,
        fires_by_side=fires_by_side,
        n_round_trips=len(resolved),
        n_tp=n_tp,
        n_sl=n_sl,
        n_timeout=n_timeout,
        win_rate_pct=(n_tp / len(resolved) * 100.0) if resolved else 0.0,
        avg_r=avg_r,
        total_r=total_r,
        avg_bars_held=avg_bars,
    )
    return ledger, trades, summary


# ── Adapter: BitUnixConfluenceConfig → btc_accumulator's price-context
# helper signature (it expects a ConfluenceConfig but only reads
# `sell_on_rush.window_minutes` and `buy_on_fall.window_minutes`).


@dataclass
class _CtxConfigShim:
    sell_on_rush: object
    buy_on_fall: object


def ctx_config(c: BitUnixConfluenceConfig) -> "_CtxConfigShim":
    return _CtxConfigShim(sell_on_rush=c.sell_on_rush, buy_on_fall=c.buy_on_fall)


# ── Output ──────────────────────────────────────────────────────────


def write_outputs(
    ledger: list[LedgerEntry],
    trades: list[PaperTrade],
    summary: BacktestResult,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "ledger.json").write_text(
        json.dumps([asdict(e) for e in ledger], indent=2, default=str),
        encoding="utf-8",
    )
    (output_dir / "trades.json").write_text(
        json.dumps([asdict(t) for t in trades], indent=2, default=str),
        encoding="utf-8",
    )

    md = [
        "# BitUnix Futures — Phase 3.2 Confluence Score Backtest",
        "",
        "## Verdict",
        f"- **Starting equity:** ${summary.starting_equity:,.2f}",
        f"- **Final equity:** ${summary.final_equity:,.2f}",
        f"- **Return:** {summary.pct_return:+.2f}%",
        f"- **Max drawdown:** {summary.max_drawdown_pct:.2f}%",
        "",
        "## Decisions",
        f"- Alerts processed: {summary.n_alerts}",
        f"- Fires: **{summary.n_fires}**  ({sum(summary.fires_by_tier.values())} non-SKIP tiers)",
        f"- SKIPs: {summary.n_skips}",
        f"- Cooldown-blocked: {summary.n_cooldown_blocked}",
        f"- Daily-kill-blocked: {summary.n_daily_kill_blocked}",
        "",
        "### Fires by tier",
        *[f"- {tier}: {n}" for tier, n in summary.fires_by_tier.items()],
        "",
        "### Fires by side",
        *[f"- {side}: {n}" for side, n in summary.fires_by_side.items()],
        "",
        "## Round-trips",
        f"- Resolved: {summary.n_round_trips}",
        f"- TP hits: **{summary.n_tp}**",
        f"- SL hits: {summary.n_sl}",
        f"- Timeouts/Flips: {summary.n_timeout}",
        f"- Win rate: **{summary.win_rate_pct:.1f}%**",
        f"- Avg R per trade: {summary.avg_r:+.3f}",
        f"- Total R: {summary.total_r:+.2f}",
        f"- Avg bars held (1m): {summary.avg_bars_held:.0f}",
        "",
        "## Notes",
        "- Bar mid is the fill price; SL/TP checked against high/low of subsequent 1m bars.",
        "- When SL and TP fall in the same bar, assumes SL hit first (worst case).",
        "- No funding cost / fees modeled. Add these in a future iteration.",
        "- Daily-kill ceiling: 3% equity at-risk per UTC day.",
        "- Trades are paper; cap of ONE open at a time (opposite-side signal flips).",
        "",
    ]
    (output_dir / "summary.md").write_text("\n".join(md), encoding="utf-8")
    log.info("Wrote outputs to %s", output_dir)


# ── CLI ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="UTC start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="UTC end date YYYY-MM-DD")
    parser.add_argument("--starting-equity", type=float, default=10000.0)
    parser.add_argument("--config-path", default=str(_REPO_ROOT / "config" / "strategies.yaml"))
    parser.add_argument("--refresh", action="store_true",
                        help="bypass alert + OHLCV caches")
    parser.add_argument("--output-dir", default=None,
                        help="override output dir; default data/backtest_runs/bitunix_<ts>/")
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start + "T00:00:00+00:00")
    end = datetime.fromisoformat(args.end + "T00:00:00+00:00")

    with open(args.config_path) as f:
        raw = yaml.safe_load(f)
    config = BitUnixConfluenceConfig.from_dict(raw["bitunix_futures"])
    log.info(
        "Config: factors=%d  premium>=%d  standard>=%d  weak>=%d  min_fire=%d",
        len(config.factors), config.premium_threshold,
        config.standard_threshold, config.weak_threshold,
        config.min_score_to_fire,
    )

    alerts = fetch_alerts_from_prod(start, end, refresh=args.refresh)
    bars = fetch_ohlcv_from_coinbase(start, end, refresh=args.refresh)

    ledger, trades, summary = run_backtest(
        alerts=alerts, bars=bars, config=config,
        starting_equity=args.starting_equity,
    )

    if args.output_dir is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        output_dir = _REPO_ROOT / "data" / "backtest_runs" / f"bitunix_{ts}"
    else:
        output_dir = Path(args.output_dir)

    write_outputs(ledger, trades, summary, output_dir)
    print(f"\nVerdict written to {output_dir / 'summary.md'}")
    print(f"  Trades: {summary.n_fires}  |  Round-trips: {summary.n_round_trips}")
    print(f"  Win rate: {summary.win_rate_pct:.1f}%  |  Avg R: {summary.avg_r:+.3f}  |  Total R: {summary.total_r:+.2f}")
    print(f"  Return: {summary.pct_return:+.2f}%  |  Max DD: {summary.max_drawdown_pct:.2f}%")


if __name__ == "__main__":
    main()
