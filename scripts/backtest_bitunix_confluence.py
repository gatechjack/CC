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
    _resample_to_1h,
    _resample_to_4h,
    build_price_context,
    fetch_alerts_from_prod,
    fetch_ohlcv_from_coinbase,
    find_bar_at,
)
# PRE-EXISTING BREAKAGE (surfaced 2026-06-14, filed BACKLOG): _resample_to_3m/_5m/_15m
# are imported + called (coinbase path, ~lines 480-482) but defined NOWHERE in the repo
# (only _4h/_1h exist). The module was UNIMPORTABLE on main 32e7fb4 and the five-factor
# test was red at collection. Guarded so the bybit_hybrid path (which supplies bars_3m/5m/15m
# overrides and never calls these) imports + runs; the coinbase path stays broken-but-LOUD.
try:
    from scripts.backtest_btc_accumulator import (  # noqa: E402
        _resample_to_3m,
        _resample_to_5m,
        _resample_to_15m,
    )
except ImportError:
    def _resample_missing(_name):
        def _raise(*_a, **_k):
            raise NotImplementedError(
                f"{_name} is not defined in backtest_btc_accumulator (pre-existing "
                "breakage; see BACKLOG 2026-06-14). The bybit_hybrid path supplies "
                "bars_3m/5m/15m overrides and does not call these."
            )
        return _raise
    _resample_to_3m = _resample_missing("_resample_to_3m")
    _resample_to_5m = _resample_missing("_resample_to_5m")
    _resample_to_15m = _resample_missing("_resample_to_15m")
from trading_corp.agents.strategies.bitunix_confluence import (  # noqa: E402
    AlertEvent,
    BitUnixConfluenceConfig,
    BitUnixVerdict,
    Side,
    Tier,
    evaluate_confluence_futures,
    filter_live_alerts_with_dedupe,
)
# PRE-EXISTING BREAKAGE (surfaced 2026-06-14, filed BACKLOG): the whole FIVE-FACTOR
# gate machinery is absent from git — `bitunix_confluence_gate` (module) and
# `bitunix_price_context.build_gate_inputs` are imported but defined NOWHERE in the
# repo (prod-vs-git drift; same class as the _resample_3m/5m/15m gap above). Guarded
# so the PA + bybit_hybrid path (which never touches the 5f machinery) imports + runs;
# the five_factor arm stays broken-but-LOUD.
try:
    from trading_corp.agents.strategies.bitunix_confluence_gate import (  # noqa: E402
        ConfluenceGateConfig,
        GateDecision,
        evaluate_confluence_gate,
    )
except ImportError:
    class ConfluenceGateConfig:  # type: ignore[no-redef]
        def __init__(self, *a, **k):
            raise NotImplementedError(
                "bitunix_confluence_gate is absent from the repo (pre-existing "
                "breakage; see BACKLOG 2026-06-14). The five_factor arm is unavailable; "
                "use --gate pa_validation (the redeem-cap engine is PA-based)."
            )
    GateDecision = None  # type: ignore[assignment]
    def evaluate_confluence_gate(*a, **k):  # type: ignore[misc]
        raise NotImplementedError("bitunix_confluence_gate absent — see BACKLOG 2026-06-14.")
from trading_corp.agents.strategies.bitunix_pa_validation import (  # noqa: E402
    PAValidationConfig,
    PAValidationDecision,
    evaluate_pa_validation,
)
from trading_corp.agents.strategies.btc_accumulator import (  # noqa: E402
    PriceContext,
)
try:
    from trading_corp.data.bitunix_price_context import (  # noqa: E402
        build_gate_inputs,
    )
except ImportError:
    def build_gate_inputs(*a, **k):  # type: ignore[misc]
        raise NotImplementedError(
            "build_gate_inputs is absent from bitunix_price_context (pre-existing "
            "breakage; see BACKLOG 2026-06-14). Five_factor arm only; PA path unaffected."
        )
# PA-redeem-cap engine graft (2026-06-14): the REAL v2 trade-plan + fee gate,
# plus swing/HTF-level/ATR-14 recompute (mirrors observer._build_proposal_v2),
# plus the entry-timing harness bar-walk. Reused in run_redeem_cap_backtest below.
from trading_corp.agents.strategies.trade_plan import (  # noqa: E402
    FeeConfig,
    StrategyConfig,
    build_trade_plan,
)
from trading_corp.agents.strategies.swing import get_recent_swing  # noqa: E402
from trading_corp.agents.strategies.levels import get_htf_levels  # noqa: E402
from trading_corp.data.live_bar_cache import Bar as _LBar  # noqa: E402
from trading_corp.data.live_bar_cache import LiveBarCache  # noqa: E402


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

    # PR-PA-backtest 2026-05-18 — PA validator arm tracking
    arm_name: str = ""                          # "4h_baseline" | "1h_with_4h_bonus"
    structure_tf: str = "4h"                    # "4h" | "1h"
    pa_4h_bonus_multiplier: float = 1.0
    n_pa_rejected: int = 0
    n_pa_passed: int = 0
    n_pa_passed_with_4h_bonus: int = 0          # 1h-arm only: how many fires got 4h-aligned bonus
    n_fires_premium_4h_aligned: int = 0
    n_fires_standard_4h_aligned: int = 0

    # Confluence-gate Phase C — 5-factor arm tracking. Populated only
    # when `gate="five_factor"`; PA-arm runs leave these at zero.
    gate_kind: str = "pa_validation"            # "pa_validation" | "five_factor"
    n_gate_rejected: int = 0
    n_gate_passed: int = 0
    n_gate_disabled: int = 0
    per_factor_pass_counts: dict = field(default_factory=dict)
    per_factor_eval_counts: dict = field(default_factory=dict)
    cvd_fallback_evals: int = 0
    gate_evals_total: int = 0

    # Phase C profit-factor (gross winners / gross losers) — added so
    # the comparison report can apply the pre-committed PF threshold.
    profit_factor: float = 0.0


# ── Confluence-gate adapters (Phase C) ──────────────────────────────


@dataclass
class _ShimBar:
    """Mimics `trading_corp.data.live_bar_cache.Bar` for the gate's
    `build_gate_inputs`. The backtest works with bar dicts (`ts`, `open`,
    `high`, `low`, `close`, `volume`); the gate consumer needs `ts_ms`.
    """
    ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class _ShimCache:
    """Mimics the slice of `LiveBarCache` that `build_gate_inputs` reads."""
    bars: list
    timeframe_seconds: int


def _shim_cache_at(
    resampled_bars: list[dict], ts: datetime, timeframe_seconds: int,
    *, max_bars: int,
) -> _ShimCache:
    """Build an as-of-`ts` cache slice from the resampled backtest bars.

    Excludes the in-progress bucket (the one whose start <= ts < end)
    to mirror live-prod behavior — `LiveBarCache.refresh()` drops the
    partial latest bar. Returns at most `max_bars` of completed bars.
    """
    out: list[_ShimBar] = []
    ts_ms = int(ts.timestamp() * 1000)
    for b in resampled_bars:
        bar_ts_ms = int(b["ts"].timestamp() * 1000)
        bar_end_ms = bar_ts_ms + timeframe_seconds * 1000
        # Skip the in-progress bucket — the bar whose interval contains ts
        if bar_ts_ms <= ts_ms < bar_end_ms:
            continue
        # Skip future bars
        if bar_ts_ms > ts_ms:
            break
        out.append(_ShimBar(
            ts_ms=bar_ts_ms,
            open=b["open"], high=b["high"], low=b["low"],
            close=b["close"], volume=b["volume"],
        ))
    if len(out) > max_bars:
        out = out[-max_bars:]
    return _ShimCache(bars=out, timeframe_seconds=timeframe_seconds)


# ── Trade open / resolve ────────────────────────────────────────────


def open_trade(
    *, verdict: BitUnixVerdict, alert_ts: datetime, entry_price: float,
    account_equity: float, size_multiplier: float = 1.0,
) -> PaperTrade | None:
    """Build a paper trade from a fired verdict. Returns None if
    sizing math rejects (qty rounds to 0 or R:R below floor).

    `size_multiplier` scales `size_pct_target` (default 1.0 = no bonus).
    Added 2026-05-18 for the PA structure-TF backtest's
    `pa_4h_aligned → 1.25× size` bonus arm."""
    tier_cfg = TIER_SIZING.get(verdict.tier)
    if tier_cfg is None:
        return None
    if entry_price <= 0 or account_equity <= 0:
        return None

    size_pct_target = float(tier_cfg["size_pct"]) * float(size_multiplier)
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
    structure_tf: str = "4h",
    pa_config: PAValidationConfig | None = None,
    pa_4h_bonus_multiplier: float = 1.0,
    arm_name: str = "",
    gate: str = "pa_validation",
    gate_config: ConfluenceGateConfig | None = None,
    # Hybrid bar source — supply pre-computed 3m/5m/15m bars to skip
    # resampling from `bars`. Used by --bar-source bybit_hybrid where
    # the bar sources are native (Bybit 3m+15m from btc_scalping.db,
    # Bitunix 5m from cache) rather than resampled from 1m Coinbase.
    bars_3m_override: list[dict] | None = None,
    bars_5m_override: list[dict] | None = None,
    bars_15m_override: list[dict] | None = None,
    # 1m trade-resolution arm (v3 addendum Branch A 2026-05-18). When set,
    # `resolve_trade` walks these bars instead of `bars`, while entry-price
    # context (`build_price_context(bars, ...)`) still uses `bars`. Default
    # (None) preserves the existing semantics where the same bar series
    # serves both price-context and trade-resolution.
    resolution_bars: list[dict] | None = None,
) -> tuple[list[LedgerEntry], list[PaperTrade], BacktestResult]:
    """Walk alerts chronologically, evaluate scorer at each, open/resolve
    paper trades.

    Position model for v1: at most ONE open trade at a time.
    - New same-direction signal during open trade → cooldown should
      block (handled in scorer) OR ignore here.
    - New opposite-direction signal during open trade → close current
      trade at current bar price, then evaluate new fire normally.
    - Daily-risk-kill caps cumulative-at-risk per UTC day at 3% equity.

    PR-PA-backtest 2026-05-18 — PA validator arms:
    - `structure_tf` = "4h" (current prod) or "1h" (proposal). When "1h",
      the validator's `structure_alignment` check reads the 1h HH/LL
      from PriceContext instead of the 4h fields (achieved by swapping
      the values on a ctx copy before calling evaluate_pa_validation).
    - `pa_4h_bonus_multiplier` > 1.0 enables 4h-as-size-bonus: when the
      proposal-arm trade fires AND the 4h structure is also aligned with
      the trade side, scale size_pct by the multiplier.

    Phase C — confluence-gate arm:
    - `gate="pa_validation"` (default): legacy PA-only arm; unchanged.
    - `gate="five_factor"`: PA replaced with the 5-factor confluence
      gate (`evaluate_confluence_gate`). Per-factor pass counts +
      CVD-fallback usage rate tracked on the result. `pa_4h_bonus`
      and `structure_tf` are ignored on this arm.
    """
    if gate not in ("pa_validation", "five_factor"):
        raise ValueError(f"unknown gate {gate!r}")
    bars_4h = _resample_to_4h(bars)
    bars_1h = _resample_to_1h(bars) if structure_tf == "1h" else None
    # _walk_bars is what resolve_trade walks. Defaults to `bars` (the
    # legacy single-source path); the 1m arm passes a finer-resolution
    # series here while leaving entry-price context on `bars`.
    _walk_bars = resolution_bars if resolution_bars is not None else bars

    # Phase C — resample once up front for the 5f arm. Backtest uses
    # 1m Coinbase data; live prod feeds the gate native 3m / 5m / 15m
    # BitUnix klines. Apples-to-apples for the comparison purpose; the
    # report's caveat section names the data-fidelity gap.
    bars_3m: list[dict] = []
    bars_5m: list[dict] = []
    bars_15m: list[dict] = []
    if gate == "five_factor":
        bars_3m = bars_3m_override if bars_3m_override is not None else _resample_to_3m(bars)
        bars_5m = bars_5m_override if bars_5m_override is not None else _resample_to_5m(bars)
        bars_15m = bars_15m_override if bars_15m_override is not None else _resample_to_15m(bars)
        if gate_config is None:
            gate_config = ConfluenceGateConfig(enabled=True, min_gate_score=3)

    if pa_config is None:
        pa_config = PAValidationConfig(
            enabled=True, require_all=True,
            validators=("vwap_alignment", "volume_confirmation", "structure_alignment"),
            rush_fall_enabled=True,
            reject_buy_on_60m_drop_pct=5.0,
            reject_sell_on_60m_rise_pct=5.0,
        )

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
    n_pa_rejected = 0
    n_pa_passed = 0
    n_pa_passed_with_4h_bonus = 0
    n_fires_premium_4h_aligned = 0
    n_fires_standard_4h_aligned = 0
    # Phase C — 5f arm tracking
    n_gate_rejected = 0
    n_gate_passed = 0
    n_gate_disabled = 0
    gate_evals_total = 0
    cvd_fallback_evals = 0
    per_factor_pass_counts: dict[str, int] = {}
    per_factor_eval_counts: dict[str, int] = {}

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
            resolve_trade(open_trade_obj, _walk_bars, datetime.fromisoformat(open_trade_obj.open_ts))
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
        ctx = build_price_context(
            bars, a.ts, ctx_config(config),
            bars_4h=bars_4h, bars_1h=bars_1h,
        )
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
            side_str = "buy" if verdict.side == Side.BUY else "sell"

            # ── Gate (PA validator OR 5-factor confluence) ──
            gate_reject_reason: str | None = None
            if gate == "pa_validation":
                # For 1h-arm, swap the 4h fields with 1h values on a ctx
                # copy so evaluate_pa_validation's structure_alignment
                # check reads 1h HH/LL without modifying the live validator
                # code.
                if structure_tf == "1h":
                    from dataclasses import replace
                    pa_ctx = replace(
                        ctx,
                        higher_highs_4h=ctx.higher_highs_1h,
                        lower_lows_4h=ctx.lower_lows_1h,
                    )
                else:
                    pa_ctx = ctx
                pa_result = evaluate_pa_validation(
                    side=side_str, price_ctx=pa_ctx, config=pa_config,
                )
                if pa_result.decision == PAValidationDecision.REJECT:
                    n_pa_rejected += 1
                    gate_reject_reason = f"pa_reject: {pa_result.reason}"
                else:
                    n_pa_passed += 1
            else:  # gate == "five_factor"
                # Build as-of-ts shim caches with max_bars matching prod.
                # 5m/15m max_bars chosen to cover gate's longest input;
                # 3m at 500 mirrors `bitunix_bar_cache.max_bars`.
                shim_3m = _shim_cache_at(bars_3m, a.ts, 180, max_bars=500)
                shim_5m = _shim_cache_at(bars_5m, a.ts, 300, max_bars=300)
                shim_15m = _shim_cache_at(bars_15m, a.ts, 900, max_bars=250)
                gate_inputs = build_gate_inputs(
                    shim_3m, shim_5m, shim_15m,
                    side=side_str, config=gate_config,
                )
                gate_result = evaluate_confluence_gate(
                    side=side_str, inputs=gate_inputs, config=gate_config,
                )
                gate_evals_total += 1
                if gate_result.cvd_fallback_used:
                    cvd_fallback_evals += 1
                for f in gate_result.factors:
                    per_factor_eval_counts[f.name] = (
                        per_factor_eval_counts.get(f.name, 0) + 1
                    )
                    if f.passed:
                        per_factor_pass_counts[f.name] = (
                            per_factor_pass_counts.get(f.name, 0) + 1
                        )
                if gate_result.decision == GateDecision.REJECT:
                    n_gate_rejected += 1
                    gate_reject_reason = (
                        f"five_factor_reject: {gate_result.reason}"
                    )
                elif gate_result.decision == GateDecision.DISABLED:
                    n_gate_disabled += 1
                else:
                    n_gate_passed += 1

            if gate_reject_reason is not None:
                # Mirror prod: gate reject short-circuits before sizing.
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
                    fired=False, trade_id=None,
                    reason=gate_reject_reason,
                ))
                equity_curve.append((a.ts, equity))
                continue

            # Compute 4h alignment for size bonus (1h-arm only). The
            # ORIGINAL ctx's 4h fields are what matters here (not the
            # swapped pa_ctx).
            size_multiplier = 1.0
            if structure_tf == "1h" and pa_4h_bonus_multiplier > 1.0:
                if side_str == "buy" and ctx.higher_highs_4h:
                    size_multiplier = pa_4h_bonus_multiplier
                    n_pa_passed_with_4h_bonus += 1
                    if verdict.tier == Tier.PREMIUM:
                        n_fires_premium_4h_aligned += 1
                    elif verdict.tier == Tier.STANDARD:
                        n_fires_standard_4h_aligned += 1
                elif side_str == "sell" and ctx.lower_lows_4h:
                    size_multiplier = pa_4h_bonus_multiplier
                    n_pa_passed_with_4h_bonus += 1
                    if verdict.tier == Tier.PREMIUM:
                        n_fires_premium_4h_aligned += 1
                    elif verdict.tier == Tier.STANDARD:
                        n_fires_standard_4h_aligned += 1

            # Daily kill check
            day_key = a.ts.date().isoformat()
            day_at_risk = daily_at_risk.get(day_key, 0.0)
            tier_cfg = TIER_SIZING[verdict.tier]
            # Conservative estimate of effective risk for the kill check.
            # The actual eff_risk is computed in open_trade(); we use the
            # tier's nominal target as a ceiling. Slight over-blocking
            # is acceptable. Multiply by size_multiplier for the 4h-bonus arm
            # so 1.25x sizing correctly trips the daily kill sooner.
            stop_distance_pct = max(STOP_FLOOR_PCT, ATR_MULTIPLIER * ATR_FALLBACK_PCT)
            estimated_eff_risk = min(
                tier_cfg["size_pct"] * size_multiplier
                * tier_cfg["leverage"] * stop_distance_pct,
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
                        size_multiplier=size_multiplier,
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
        resolve_trade(open_trade_obj, _walk_bars, datetime.fromisoformat(open_trade_obj.open_ts))
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
    # Profit factor = gross winners / gross losers (in R units). Used by
    # the Phase C acceptance gate. Returns 0.0 when there are no losers
    # (degenerate; the acceptance bar's `n>=20` requirement guards this).
    gross_win_r = sum(t.realized_r for t in resolved if (t.realized_r or 0) > 0)
    gross_loss_r = sum(-t.realized_r for t in resolved if (t.realized_r or 0) < 0)
    profit_factor = (gross_win_r / gross_loss_r) if gross_loss_r > 0 else 0.0

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
        arm_name=arm_name,
        structure_tf=structure_tf,
        pa_4h_bonus_multiplier=pa_4h_bonus_multiplier,
        n_pa_rejected=n_pa_rejected,
        n_pa_passed=n_pa_passed,
        n_pa_passed_with_4h_bonus=n_pa_passed_with_4h_bonus,
        n_fires_premium_4h_aligned=n_fires_premium_4h_aligned,
        n_fires_standard_4h_aligned=n_fires_standard_4h_aligned,
        gate_kind=gate,
        n_gate_rejected=n_gate_rejected,
        n_gate_passed=n_gate_passed,
        n_gate_disabled=n_gate_disabled,
        per_factor_pass_counts=per_factor_pass_counts,
        per_factor_eval_counts=per_factor_eval_counts,
        cvd_fallback_evals=cvd_fallback_evals,
        gate_evals_total=gate_evals_total,
        profit_factor=profit_factor,
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

    cvd_fb_pct = (
        (summary.cvd_fallback_evals / summary.gate_evals_total * 100.0)
        if summary.gate_evals_total > 0 else 0.0
    )
    factor_lines: list[str] = []
    for name, evals in sorted(summary.per_factor_eval_counts.items()):
        passes = summary.per_factor_pass_counts.get(name, 0)
        rate = (passes / evals * 100.0) if evals > 0 else 0.0
        factor_lines.append(f"  - {name}: {passes}/{evals} ({rate:.1f}%)")

    md = [
        "# BitUnix Futures — Phase 3.2 Confluence Score Backtest",
        "",
        f"**Arm:** `{summary.arm_name or '(unnamed)'}` · structure_tf=`{summary.structure_tf}` · pa_4h_bonus={summary.pa_4h_bonus_multiplier:.2f}x · gate=`{summary.gate_kind}`",
        "",
        "## Verdict",
        f"- **Starting equity:** ${summary.starting_equity:,.2f}",
        f"- **Final equity:** ${summary.final_equity:,.2f}",
        f"- **Return:** {summary.pct_return:+.2f}%",
        f"- **Max drawdown:** {summary.max_drawdown_pct:.2f}%",
        f"- **Profit factor:** {summary.profit_factor:.2f}",
        "",
        "## Decisions",
        f"- Alerts processed: {summary.n_alerts}",
        f"- Fires: **{summary.n_fires}**  ({sum(summary.fires_by_tier.values())} non-SKIP tiers)",
        f"- SKIPs: {summary.n_skips}",
        f"- Cooldown-blocked: {summary.n_cooldown_blocked}",
        f"- Daily-kill-blocked: {summary.n_daily_kill_blocked}",
        f"- **PA rejected: {summary.n_pa_rejected}** ({(summary.n_pa_rejected / max(1, summary.n_pa_rejected + summary.n_pa_passed)) * 100:.1f}% of score-PASS evals)",
        f"- PA passed: {summary.n_pa_passed}",
        f"- PA passed WITH 4h-bonus: {summary.n_pa_passed_with_4h_bonus}  (PREMIUM aligned: {summary.n_fires_premium_4h_aligned} · STANDARD aligned: {summary.n_fires_standard_4h_aligned})",
        f"- **5f-gate rejected: {summary.n_gate_rejected}** · passed: {summary.n_gate_passed} · disabled: {summary.n_gate_disabled}",
        f"- CVD fallback used: {summary.cvd_fallback_evals}/{summary.gate_evals_total} ({cvd_fb_pct:.1f}%)",
        "",
        "### Per-factor pass rates (5f arm only)",
        *(factor_lines or ["  - (no 5f evals on this arm)"]),
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


def _load_bybit_hybrid_inputs(
    db_path: Path, bitunix_5m_cache: Path, prod_alerts_cache: Path,
    start: datetime, end: datetime,
) -> tuple[list[AlertEvent], list[dict], list[dict], list[dict], list[dict]]:
    """Load prod alerts + Bybit 3m/15m DB bars + cached Bitunix 5m bars.

    Returns (alerts, bars_3m_for_resolution, bars_3m_for_gate, bars_5m, bars_15m).

    bars_3m_for_resolution is what the trade-resolution code walks; it's the
    same as bars_3m_for_gate in this mode (no separate 1m source).
    """
    import sqlite3
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    start_s = int(start.timestamp())
    end_s = int(end.timestamp())

    def _load(table: str) -> list[dict]:
        rows = cur.execute(
            f"SELECT ts, open, high, low, close, volume FROM {table} "
            f"WHERE ts >= ? AND ts < ? ORDER BY ts",
            (start_s, end_s),
        ).fetchall()
        return [{
            "ts": datetime.fromtimestamp(ts, tz=timezone.utc),
            "open": o, "high": h, "low": l, "close": c, "volume": v or 0.0,
        } for ts, o, h, l, c, v in rows]
    bars_3m = _load("bars_3m")
    bars_15m = _load("bars_15m")
    con.close()
    log.info("Loaded %d 3m + %d 15m Bybit bars from %s",
             len(bars_3m), len(bars_15m), db_path)

    # Bitunix 5m cache — JSON list of {ts: ISO string, ...}
    bx5m_raw = json.loads(bitunix_5m_cache.read_text(encoding="utf-8"))
    bars_5m: list[dict] = []
    for b in bx5m_raw:
        ts_dt = datetime.fromisoformat(b["ts"])
        if start <= ts_dt < end:
            bars_5m.append({
                "ts": ts_dt,
                "open": b["open"], "high": b["high"], "low": b["low"],
                "close": b["close"], "volume": b["volume"],
            })
    log.info("Loaded %d Bitunix 5m bars from cache (windowed)", len(bars_5m))

    # Prod alerts (already pink_box-filtered at ingest)
    prod_raw = json.loads(prod_alerts_cache.read_text(encoding="utf-8"))
    alerts: list[AlertEvent] = []
    for r in prod_raw:
        ts_dt = datetime.fromisoformat(r["ts"])
        if not (start <= ts_dt < end):
            continue
        alerts.append(AlertEvent(
            ts=ts_dt, signal_name=r["signal"], tf=r.get("tf"),
        ))
    log.info("Loaded %d prod alerts from %s (windowed, pink_box already filtered)",
             len(alerts), prod_alerts_cache)
    return alerts, bars_3m, bars_3m, bars_5m, bars_15m


def _run_one_arm(
    *, alerts, bars, config, args, gate: str, arm_label: str,
    ts_stamp: str,
    bars_3m_override=None, bars_5m_override=None, bars_15m_override=None,
    resolution_bars=None,
) -> tuple[Path, BacktestResult, list[LedgerEntry]]:
    ledger, trades, summary = run_backtest(
        alerts=alerts, bars=bars, config=config,
        starting_equity=args.starting_equity,
        structure_tf=args.structure_tf,
        pa_4h_bonus_multiplier=args.pa_4h_bonus,
        arm_name=arm_label,
        gate=gate,
        bars_3m_override=bars_3m_override,
        bars_5m_override=bars_5m_override,
        bars_15m_override=bars_15m_override,
        resolution_bars=resolution_bars,
    )
    if args.output_dir is None:
        output_dir = (
            _REPO_ROOT / "data" / "backtest_runs"
            / f"bitunix_{ts_stamp}_{arm_label}"
        )
    else:
        output_dir = Path(args.output_dir) / arm_label
    write_outputs(ledger, trades, summary, output_dir)
    return output_dir, summary, ledger


def _crosstab_2x2(
    pa_ledger: list[LedgerEntry], gate_ledger: list[LedgerEntry],
) -> dict[str, int]:
    """Cross-tab fire outcomes by (PA-fire, 5f-fire) at each alert ts."""
    pa_fire_by_ts = {e.ts: e.fired for e in pa_ledger}
    gate_fire_by_ts = {e.ts: e.fired for e in gate_ledger}
    tab = {
        "both_fire": 0, "pa_only": 0, "gate_only": 0, "neither": 0,
    }
    all_ts = set(pa_fire_by_ts) | set(gate_fire_by_ts)
    for ts in all_ts:
        p = pa_fire_by_ts.get(ts, False)
        g = gate_fire_by_ts.get(ts, False)
        if p and g:
            tab["both_fire"] += 1
        elif p:
            tab["pa_only"] += 1
        elif g:
            tab["gate_only"] += 1
        else:
            tab["neither"] += 1
    return tab


# Phase C pre-committed acceptance thresholds (Board mod #1, locked
# before running the backtest). Tightening / loosening these AFTER
# seeing the numbers is the explicit failure mode this commit blocks.
ACCEPTANCE_THRESHOLDS = {
    "min_profit_factor": 1.20,
    "min_win_rate_pct": 45.0,
    "min_round_trips": 20,
    "fire_rate_pct_range": (5.0, 50.0),
    # Relative bar reported but only blocking when PA n>=20.
    "relative_total_r_floor_vs_pa": True,
}


def _evaluate_acceptance(
    gate_summary: BacktestResult, pa_summary: BacktestResult,
) -> dict:
    """Apply pre-committed thresholds to the 5f arm. Returns a dict of
    {check: (passed_bool, value, threshold_str)} plus a top-level
    `passed_all` bool."""
    t = ACCEPTANCE_THRESHOLDS
    fire_rate = (
        (gate_summary.n_fires / gate_summary.n_alerts * 100.0)
        if gate_summary.n_alerts > 0 else 0.0
    )
    checks = {
        "profit_factor": (
            gate_summary.profit_factor >= t["min_profit_factor"],
            gate_summary.profit_factor,
            f">= {t['min_profit_factor']:.2f}",
        ),
        "win_rate": (
            gate_summary.win_rate_pct >= t["min_win_rate_pct"],
            gate_summary.win_rate_pct,
            f">= {t['min_win_rate_pct']:.1f}%",
        ),
        "round_trips": (
            gate_summary.n_round_trips >= t["min_round_trips"],
            gate_summary.n_round_trips,
            f">= {t['min_round_trips']}",
        ),
        "fire_rate": (
            t["fire_rate_pct_range"][0]
            <= fire_rate
            <= t["fire_rate_pct_range"][1],
            fire_rate,
            f"in [{t['fire_rate_pct_range'][0]:.1f}%, "
            f"{t['fire_rate_pct_range'][1]:.1f}%]",
        ),
    }
    # Relative gate (informational if PA n<20)
    pa_n = pa_summary.n_round_trips
    rel_passed = (
        gate_summary.total_r >= pa_summary.total_r
        if pa_n >= 20 else True
    )
    rel_note = (
        f"5f total R {gate_summary.total_r:+.2f} vs PA "
        f"{pa_summary.total_r:+.2f}"
        + (f" (informational; PA n={pa_n} < 20)" if pa_n < 20 else "")
    )
    checks["relative_total_r"] = (rel_passed, rel_note, ">= PA total R")
    passed_all = all(v[0] for v in checks.values())
    return {"checks": checks, "passed_all": passed_all}


def _write_comparison_report(
    *, output_path: Path,
    pa_summary: BacktestResult, gate_summary: BacktestResult,
    pa_ledger: list[LedgerEntry], gate_ledger: list[LedgerEntry],
    start: datetime, end: datetime,
) -> Path:
    """Write `reports/gate_backtest_<end_date>.md` — the §11 Backtester
    deliverable. Applies the pre-committed acceptance thresholds and
    surfaces the recommendation."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tab = _crosstab_2x2(pa_ledger, gate_ledger)
    accept = _evaluate_acceptance(gate_summary, pa_summary)

    def _fmt_check(name: str, tup: tuple) -> str:
        passed, val, thresh = tup
        mark = "PASS" if passed else "FAIL"
        if isinstance(val, float):
            val_s = f"{val:.2f}"
        else:
            val_s = str(val)
        return f"- **{mark}** · {name}: {val_s}  (target: {thresh})"

    cvd_fb_pct = (
        (gate_summary.cvd_fallback_evals / gate_summary.gate_evals_total
         * 100.0)
        if gate_summary.gate_evals_total > 0 else 0.0
    )
    factor_rows: list[str] = []
    for name in sorted(gate_summary.per_factor_eval_counts):
        evals = gate_summary.per_factor_eval_counts[name]
        passes = gate_summary.per_factor_pass_counts.get(name, 0)
        rate = (passes / evals * 100.0) if evals > 0 else 0.0
        factor_rows.append(f"| {name} | {passes}/{evals} | {rate:.1f}% |")

    tier_rows: list[str] = []
    for tier in ("PREMIUM", "STANDARD", "WEAK"):
        pa_n = pa_summary.fires_by_tier.get(tier, 0)
        gn = gate_summary.fires_by_tier.get(tier, 0)
        tier_rows.append(f"| {tier} | {pa_n} | {gn} |")

    md = [
        "# BitUnix Confluence-Gate Backtest — PA vs 5-Factor",
        "",
        f"**Window:** {start.date().isoformat()} → {end.date().isoformat()}  ·  "
        f"**Alerts:** {pa_summary.n_alerts}",
        "",
        "## Pre-committed acceptance thresholds (Board mod #1)",
        "",
        "These were locked before the backtest ran. Moving them after seeing the",
        "numbers is the explicit failure mode this report blocks.",
        "",
        f"- Profit factor ≥ **{ACCEPTANCE_THRESHOLDS['min_profit_factor']:.2f}**",
        f"- Win rate ≥ **{ACCEPTANCE_THRESHOLDS['min_win_rate_pct']:.1f}%**",
        f"- Round-trips ≥ **{ACCEPTANCE_THRESHOLDS['min_round_trips']}** "
        "(statistical floor)",
        f"- Fire rate ∈ **[{ACCEPTANCE_THRESHOLDS['fire_rate_pct_range'][0]:.1f}%, "
        f"{ACCEPTANCE_THRESHOLDS['fire_rate_pct_range'][1]:.1f}%]** of alerts",
        "- Total R ≥ PA's total R (informational only if PA n < 20)",
        "",
        "## Acceptance evaluation (5-factor arm)",
        "",
        *(_fmt_check(k, v) for k, v in accept["checks"].items()),
        "",
        f"**OVERALL: {'PASS' if accept['passed_all'] else 'FAIL'}**",
        "",
        "## Side-by-side summary",
        "",
        "| Metric | PA arm | 5-factor arm |",
        "|---|---|---|",
        f"| Fires | {pa_summary.n_fires} | {gate_summary.n_fires} |",
        f"| Round-trips | {pa_summary.n_round_trips} | {gate_summary.n_round_trips} |",
        f"| Win rate | {pa_summary.win_rate_pct:.1f}% | {gate_summary.win_rate_pct:.1f}% |",
        f"| Avg R | {pa_summary.avg_r:+.3f} | {gate_summary.avg_r:+.3f} |",
        f"| Total R | {pa_summary.total_r:+.2f} | {gate_summary.total_r:+.2f} |",
        f"| Profit factor | {pa_summary.profit_factor:.2f} | {gate_summary.profit_factor:.2f} |",
        f"| Return % | {pa_summary.pct_return:+.2f}% | {gate_summary.pct_return:+.2f}% |",
        f"| Max DD % | {pa_summary.max_drawdown_pct:.2f}% | {gate_summary.max_drawdown_pct:.2f}% |",
        "",
        "## 2×2 outcome cross-tab",
        "",
        "| | 5f fires | 5f rejects |",
        "|---|---|---|",
        f"| **PA fires** | {tab['both_fire']} (both agree, fire) | {tab['pa_only']} (PA fires alone) |",
        f"| **PA rejects** | {tab['gate_only']} (5f catches PA misses) | {tab['neither']} (both agree, skip) |",
        "",
        "## Per-factor pass rates (5f arm)",
        "",
        "| Factor | Passes / Evals | Rate |",
        "|---|---|---|",
        *(factor_rows or ["| (no 5f evals) | — | — |"]),
        "",
        f"CVD tick-rule fallback used in **{gate_summary.cvd_fallback_evals}/"
        f"{gate_summary.gate_evals_total} ({cvd_fb_pct:.1f}%)** of 5f evals "
        "(expected = 100% for v1; flag flips False only when a future "
        "trade-stream consumer lands).",
        "",
        "## Per-tier fire breakdown",
        "",
        "| Tier | PA arm | 5-factor arm |",
        "|---|---|---|",
        *tier_rows,
        "",
        "## Methodology + caveats",
        "",
        "- Alerts pulled from prod `audit_event` `webhook_received` rows over",
        f"  the window above (resolution: per-alert timestamp).",
        "- OHLCV: Coinbase BTC/USD 1m (NOT BitUnix futures). Live prod feeds",
        "  the gate native BitUnix 3m/5m/15m kline. Apples-to-apples for the",
        "  PA-vs-5f relative comparison; absolute trade outcomes carry a",
        "  cross-venue volatility-profile fidelity gap.",
        "- CVD: tick-rule fallback (close-direction sign × bar volume).",
        "  Aggressor-side data is not available from BitUnix public; v1 of",
        "  the gate accepts this as a known coarse signal. The dashboard",
        "  banner (Phase D) surfaces `cvd_fallback_used` to operators.",
        "- Sizing: per-tier nominal × leverage; effective risk capped at",
        "  0.5%/trade; daily kill at 3% cumulative.",
        "- Position model: one open trade at a time; opposite-side signal",
        "  flips. No funding / fees modeled.",
        "",
        "## Recommendation",
        "",
        "**Cutover criterion:** all four absolute acceptance checks above must",
        "PASS, and the relative-R check must PASS (or be informational with",
        "PA n < 20). The Board records the final cutover decision in",
        "`runbooks/deploy_log.md`; this report is input, not the decision.",
        "",
        f"Status from this run: **{'PASS — proceed to Phase D' if accept['passed_all'] else 'FAIL — hold; iterate gate config or tighten factor inputs before cutover'}**.",
        "",
    ]
    output_path.write_text("\n".join(md), encoding="utf-8")
    log.info("Wrote comparison report to %s", output_path)
    return output_path


# ════════════════════════════════════════════════════════════════════════
# PA-REDEEM-CAP ENGINE (2026-06-14, branch bitunix-redeem-cap-backtest-tooling)
# EXTENDS this backtest — reuses its corpus loader + evaluate_confluence_futures
# + evaluate_pa_validation + build_price_context — and adds:
#   (1) v2 economics: the REAL build_trade_plan (3-leg + fee gate) with
#       swing/HTF-level/ATR-14 recomputed from the 3m corpus (mirrors
#       observer._build_proposal_v2), plus the entry-timing harness bar-walk
#       (SL-first tie, ordered TP fills, BE-after-TP1 / TP1-after-TP2 ratchet)
#       and net-of-cost at VIP3 taker 0.09%rt / maker 0.064%rt. This REPLACES
#       the single-TP fixed-ATR open_trade/resolve_trade for the redeem arms.
#   (2) the PA-REDEEM loop with a --redeem-cap knob (the mechanism this test
#       varies): on a PA-reject, re-evaluate score+PA per subsequent 3m bar up
#       to cap N (mirrors observer.run_pa_redeem_loop: re-run until PA passes
#       OR score decays to SKIP OR cap exhausted) and FIRE AT THE PASS-BAR with
#       the late entry priced at the FIRE bar (NOT the stale signal price — the
#       entry-timing report showed stale pricing overstates redeem economics).
#       cap 0 = no-redeem; 1 = cap@1bar; REDEEM_CAP_CURRENT = current (decay-bounded).
# Decision metric = NET-OF-COST EXPECTANCY PER FIRE (never fire-rate). Fires are
# walked independently (per-fire expectancy = the §4 metric), NOT compounded into
# an equity curve; one-open-at-a-time portfolio effects are out of scope here.
# NOT a new engine; NOT the et/fg harnesses reused whole — the v2 walk is grafted
# onto this file's corpus/gate loop.
# ════════════════════════════════════════════════════════════════════════

_SCFG = StrategyConfig()
_FEES_TK = FeeConfig()                       # taker exits (prod): 0.00090 round-trip
_FEES_MK = FeeConfig(tp_is_maker=True)       # maker exits:        0.00064 round-trip
_RT_TK = _FEES_TK.round_trip_cost_pct()
_RT_MK = _FEES_MK.round_trip_cost_pct()
REDEEM_CAP_CURRENT = 30                       # "current" cap = 30 3m bars (90 min). Covers
#  the observed max bars_waited=25 from the entry-timing analysis; in prod score-decay
#  (Tier.SKIP) usually breaks the redeem far sooner. Bounds the per-fire redeem walk for
#  tractable runs (raise for a fuller §4 run if score-decay is rare on the corpus).


def _bars_to_objs(bars_3m: list[dict]) -> list:
    out = []
    for b in bars_3m:
        ts = b["ts"]
        ms = int(ts.timestamp() * 1000) if isinstance(ts, datetime) else int(ts)
        out.append(_LBar(ts_ms=ms, open=float(b["open"]), high=float(b["high"]),
                         low=float(b["low"]), close=float(b["close"]),
                         volume=float(b.get("volume", 0.0) or 0.0)))
    return out


def _bar_idx_at(bar_objs: list, ts: datetime) -> int:
    """Index of the last 3m bar with ts_ms <= ts (the bar in force at ts)."""
    ms = int(ts.timestamp() * 1000)
    lo, hi, ans = 0, len(bar_objs) - 1, -1
    while lo <= hi:
        m = (lo + hi) // 2
        if bar_objs[m].ts_ms <= ms:
            ans = m; lo = m + 1
        else:
            hi = m - 1
    return ans


def _atr14_at(bar_objs: list, idx: int) -> float | None:
    lc = LiveBarCache()
    lc.bars = bar_objs[max(0, idx - 59): idx + 1]
    return lc.get_atr(14)


def build_v2_plan(side: str, entry: float, bar_objs: list, idx: int):
    """Real v2 3-leg plan via the strategy's OWN build_trade_plan, with swings /
    HTF levels / ATR-14 recomputed from the 3m corpus as-of bar `idx` (mirrors
    observer._build_proposal_v2). Returns a TradePlan — check `.should_trade`
    (a fee-gate skip returns should_trade=False with skip_reason set)."""
    if idx < 0:
        return None
    atr = _atr14_at(bar_objs, idx)
    if atr is None or atr <= 0:
        atr = entry * ATR_FALLBACK_PCT
    swl = get_recent_swing(bar_objs, idx, side="low", n=_SCFG.swing_n,
                           max_lookback=_SCFG.swing_max_lookback)
    swh = get_recent_swing(bar_objs, idx, side="high", n=_SCFG.swing_n,
                           max_lookback=_SCFG.swing_max_lookback)
    res, sup = get_htf_levels(bar_objs, idx, htf_minutes=_SCFG.htf_minutes,
                              lookback_bars_htf=_SCFG.htf_lookback_bars, n=_SCFG.swing_n)
    return build_trade_plan(entry=entry, side=side, atr=atr, swing_low=swl,
                            swing_high=swh, resistance=res, support=sup,
                            cfg=_SCFG, fees=_FEES_TK)


# ── bar walk grafted from etharness.py (SL-first tie, ordered TP, BE/TP1 ratchet) ──
def _r_at(side, e, osl, px):
    risk = abs(e - osl)
    return 0.0 if risk <= 0 else (1.0 if side == "buy" else -1.0) * (px - e) / risk


def _agg_r(side, e, osl, legs, filled, exitpx):
    tot = ff = 0.0
    for lg in ("tp1", "tp2", "tp3"):
        if lg in filled:
            tot += legs[lg]["r"] * legs[lg]["f"]; ff += legs[lg]["f"]
    unf = max(0.0, 1.0 - ff)
    if unf > 0:
        tot += _r_at(side, e, osl, exitpx) * unf
    return tot


def _ratchet_sl(side, e, osl, csl, filled, legs):
    f = set(filled)
    if "tp1" not in f:
        return csl
    if "tp2" not in f:
        return e if ((side == "buy" and e > csl) or (side == "sell" and e < csl)) else csl
    c = legs["tp1"]["px"]
    return c if ((side == "buy" and c > csl) or (side == "sell" and c < csl)) else csl


def walk_v2(side, entry, legs, bar_objs, start_idx, max_bars=480):
    """legs = {"_sl": float, "tp1": {"px","r","f"}, ...}. Returns
    (outcome, gross_r, n_ambiguous, filled_legs)."""
    osl = legs["_sl"]
    filled, csl, amb = [], osl, 0
    tgt = {lg: legs[lg]["px"] for lg in ("tp1", "tp2", "tp3")}
    end = min(len(bar_objs), start_idx + max_bars)
    if start_idx < 0 or start_idx >= len(bar_objs):
        return ("no_bars", None, 0, [])
    for idx in range(start_idx, end):
        hi, lo = bar_objs[idx].high, bar_objs[idx].low
        sl = (side == "buy" and lo <= csl) or (side == "sell" and hi >= csl)
        hit = []
        for lg in ("tp1", "tp2", "tp3"):
            if lg in filled:
                continue
            t = tgt[lg]
            if (side == "buy" and hi >= t) or (side == "sell" and lo <= t):
                hit.append(lg)
            else:
                break
        if sl and hit:
            amb += 1
        if sl:
            r = _agg_r(side, entry, osl, legs, filled, csl)
            return ("win" if r > 0 else "loss", r, amb, list(filled))
        for lg in hit:
            filled.append(lg); csl = _ratchet_sl(side, entry, osl, csl, filled, legs)
        if "tp3" in filled:
            return ("win", _agg_r(side, entry, osl, legs, filled, tgt["tp3"]), amb, list(filled))
    return ("open", None, amb, list(filled))


def _plan_to_legs(tp, entry: float) -> dict:
    """TradePlan (3-leg) → {_sl, tp1:{px,r,f}, ...} for walk_v2."""
    rpu = tp.risk_per_unit
    out = {"_sl": tp.stop_loss}
    for lg, px, f in (("tp1", tp.tp1, tp.tp1_qty_fraction),
                      ("tp2", tp.tp2, tp.tp2_qty_fraction),
                      ("tp3", tp.tp3, tp.tp3_qty_fraction)):
        out[lg] = {"px": px, "r": abs(px - entry) / rpu, "f": f}
    return out


@dataclass
class RedeemFire:
    ts: str
    side: str
    redeemed: bool
    bars_waited: int
    entry: float
    risk: float
    gross_r: float | None
    net_r_taker: float | None
    net_r_maker: float | None
    outcome: str               # win | loss | open | plan_skip
    filled: str
    n_amb: int
    skip_reason: str | None = None


def _simulate_redeem(*, config, pa_config, structure_tf, bars, bars_4h, bars_1h,
                     bar_objs, reject_idx, cap, sorted_alerts,
                     last_fire_ts_buy, last_fire_ts_sell):
    """Mirror observer.run_pa_redeem_loop: re-evaluate score+PA on each
    subsequent 3m bar up to `cap`. Returns (fire_idx, fire_ts, fire_price,
    bars_waited, side) on a redeem PASS, else None (score-decay or cap)."""
    for k in range(1, cap + 1):
        fidx = reject_idx + k
        if fidx >= len(bar_objs):
            return None
        ts_k = datetime.fromtimestamp(bar_objs[fidx].ts_ms / 1000, tz=timezone.utc)
        ctx_k = build_price_context(bars, ts_k, ctx_config(config),
                                    bars_4h=bars_4h, bars_1h=bars_1h)
        if ctx_k is None:
            continue
        live_k = filter_live_alerts_with_dedupe(sorted_alerts, config, ts_k)
        v_k = evaluate_confluence_futures(
            live_alerts=live_k, price_ctx=ctx_k, config=config, now=ts_k,
            last_fire_ts_buy=last_fire_ts_buy, last_fire_ts_sell=last_fire_ts_sell,
        )
        if v_k.tier == Tier.SKIP:
            return None                       # score decayed → redeem clears (prod parity)
        side_k = "buy" if v_k.side == Side.BUY else "sell"
        pa_ctx_k = ctx_k
        if structure_tf == "1h":
            from dataclasses import replace
            pa_ctx_k = replace(ctx_k, higher_highs_4h=ctx_k.higher_highs_1h,
                               lower_lows_4h=ctx_k.lower_lows_1h)
        pa_k = evaluate_pa_validation(side=side_k, price_ctx=pa_ctx_k, config=pa_config)
        if pa_k.decision != PAValidationDecision.REJECT:
            return (fidx, ts_k, bar_objs[fidx].close, k, side_k)
    return None


def run_redeem_cap_backtest(*, alerts, bars, config, pa_config, redeem_cap,
                            structure_tf="4h", arm_name=""):
    """v2-economics + PA-redeem-cap engine. `bars` is the 3m series (= bars in
    bybit_hybrid mode). redeem_cap: 0=no-redeem, N=cap@N bars, REDEEM_CAP_CURRENT
    =current. Returns (fires, summary_dict). Decision metric = net-of-cost
    expectancy per fire."""
    if pa_config is None:
        pa_config = PAValidationConfig(
            enabled=True, require_all=True,
            validators=("vwap_alignment", "volume_confirmation", "structure_alignment"),
            rush_fall_enabled=True,
            reject_buy_on_60m_drop_pct=5.0, reject_sell_on_60m_rise_pct=5.0,
        )
    bar_objs = _bars_to_objs(bars)
    bars_4h = _resample_to_4h(bars)
    bars_1h = _resample_to_1h(bars)
    sorted_alerts = sorted(alerts, key=lambda a: a.ts)

    fires: list[RedeemFire] = []
    last_fire_ts_buy: datetime | None = None
    last_fire_ts_sell: datetime | None = None
    n_score_fire = n_pa_pass = n_pa_reject = 0
    n_first_pass = n_redeem_fire = n_redeem_drop = n_plan_skip = 0

    def _open(side: str, fire_idx: int, entry: float, redeemed: bool, bars_waited: int):
        nonlocal last_fire_ts_buy, last_fire_ts_sell, n_plan_skip
        tp = build_v2_plan(side, entry, bar_objs, fire_idx)
        ts_iso = datetime.fromtimestamp(
            bar_objs[fire_idx].ts_ms / 1000, tz=timezone.utc).isoformat()
        if tp is None:
            return
        if not tp.should_trade:
            n_plan_skip += 1
            fires.append(RedeemFire(
                ts=ts_iso, side=side, redeemed=redeemed, bars_waited=bars_waited,
                entry=entry, risk=0.0, gross_r=None, net_r_taker=None,
                net_r_maker=None, outcome="plan_skip", filled="", n_amb=0,
                skip_reason=tp.skip_reason))
            return
        legs = _plan_to_legs(tp, entry)
        o, gr, amb, fl = walk_v2(side, entry, legs, bar_objs, fire_idx + 1)
        risk = tp.risk_per_unit
        fires.append(RedeemFire(
            ts=ts_iso, side=side, redeemed=redeemed, bars_waited=bars_waited,
            entry=entry, risk=risk, gross_r=gr,
            net_r_taker=(gr - _RT_TK * entry / risk) if gr is not None else None,
            net_r_maker=(gr - _RT_MK * entry / risk) if gr is not None else None,
            outcome=o, filled=",".join(fl), n_amb=amb))
        if side == "buy":
            last_fire_ts_buy = datetime.fromisoformat(ts_iso)
        else:
            last_fire_ts_sell = datetime.fromisoformat(ts_iso)

    for a in sorted_alerts:
        ctx = build_price_context(bars, a.ts, ctx_config(config),
                                  bars_4h=bars_4h, bars_1h=bars_1h)
        if ctx is None:
            continue
        live = filter_live_alerts_with_dedupe(sorted_alerts, config, a.ts)
        verdict = evaluate_confluence_futures(
            live_alerts=live, price_ctx=ctx, config=config, now=a.ts,
            last_fire_ts_buy=last_fire_ts_buy, last_fire_ts_sell=last_fire_ts_sell)
        if verdict.cooldown_blocked or verdict.tier == Tier.SKIP:
            continue
        n_score_fire += 1
        side_str = "buy" if verdict.side == Side.BUY else "sell"
        pa_ctx = ctx
        if structure_tf == "1h":
            from dataclasses import replace
            pa_ctx = replace(ctx, higher_highs_4h=ctx.higher_highs_1h,
                             lower_lows_4h=ctx.lower_lows_1h)
        pa_result = evaluate_pa_validation(side=side_str, price_ctx=pa_ctx, config=pa_config)
        alert_idx = _bar_idx_at(bar_objs, a.ts)
        if alert_idx < 0:
            continue
        if pa_result.decision != PAValidationDecision.REJECT:
            # first-pass fire — enter at the signal/alert bar (no latency)
            n_pa_pass += 1
            n_first_pass += 1
            _open(side_str, alert_idx, bar_objs[alert_idx].close, redeemed=False, bars_waited=0)
            continue
        # PA reject
        n_pa_reject += 1
        if redeem_cap <= 0:
            n_redeem_drop += 1                # no-redeem arm: PA-reject drops
            continue
        rd = _simulate_redeem(
            config=config, pa_config=pa_config, structure_tf=structure_tf,
            bars=bars, bars_4h=bars_4h, bars_1h=bars_1h, bar_objs=bar_objs,
            reject_idx=alert_idx, cap=redeem_cap, sorted_alerts=sorted_alerts,
            last_fire_ts_buy=last_fire_ts_buy, last_fire_ts_sell=last_fire_ts_sell)
        if rd is None:
            n_redeem_drop += 1                # score-decay or cap exhausted → abandon
            continue
        fire_idx, _ts_k, fire_px, bars_waited, side_k = rd
        n_redeem_fire += 1
        _open(side_k, fire_idx, fire_px, redeemed=True, bars_waited=bars_waited)

    walked = [f for f in fires if f.gross_r is not None]
    redeem_walked = [f for f in walked if f.redeemed]

    def _mean(xs):
        return (sum(xs) / len(xs)) if xs else 0.0
    summary = {
        "arm_name": arm_name,
        "redeem_cap": redeem_cap,
        "n_score_fire": n_score_fire,
        "n_pa_pass": n_pa_pass,
        "n_pa_reject": n_pa_reject,
        "n_first_pass_fire": n_first_pass,
        "n_redeem_fire": n_redeem_fire,
        "n_redeem_drop": n_redeem_drop,
        "n_plan_skip": n_plan_skip,
        "n_walked": len(walked),
        # DECISION METRIC — net-of-cost expectancy per fire (never fire-rate):
        "exp_gross_per_fire": _mean([f.gross_r for f in walked]),
        "exp_net_taker_per_fire": _mean([f.net_r_taker for f in walked]),
        "exp_net_maker_per_fire": _mean([f.net_r_maker for f in walked]),
        "redeem_exp_net_taker": _mean([f.net_r_taker for f in redeem_walked]),
        "redeem_exp_net_maker": _mean([f.net_r_maker for f in redeem_walked]),
        "max_bars_waited": max([f.bars_waited for f in fires], default=0),
    }
    return fires, summary


def _write_redeem_comparison(arms: list[dict], output_path: Path, *,
                             start, end) -> Path:
    """Write the three-arm comparison. EXPLICITLY engine validation, NOT a §4 verdict."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in arms:
        rows.append(
            f"| {s['arm_name']} | {s['n_first_pass_fire']} | {s['n_redeem_fire']} | "
            f"{s['n_redeem_drop']} | {s['n_plan_skip']} | {s['n_walked']} | "
            f"{s['exp_gross_per_fire']:+.4f} | **{s['exp_net_taker_per_fire']:+.4f}** | "
            f"{s['exp_net_maker_per_fire']:+.4f} | {s['redeem_exp_net_taker']:+.4f} |")
    md = [
        "# BitUnix PA-redeem-cap backtest — ENGINE VALIDATION (NOT a §4 verdict)",
        "",
        "> **THIS IS ENGINE VALIDATION / A SMOKE RUN — NOT THE §4 REDEEM-CAP VERDICT.**",
        "> The corpus here (`btc_scalping.db` bars_3m) is only a modest ~1.9x vol",
        "> gradient (Mar→May 2026); a defensible §4 verdict REQUIRES a high-vol 3m",
        "> regime (separate data-ingest task) for regime robustness. Do not cite these",
        "> numbers as the redeem-cap decision.",
        "",
        f"Window: {start.date()} → {end.date()}  ·  3m corpus  ·  VIP3 taker 0.09%rt / maker 0.064%rt",
        "",
        "Decision metric = **net-of-cost expectancy per fire** (NEVER fire-rate).",
        "",
        "| arm | first-pass | redeem | dropped | plan-skip | walked | gross/fire | **net-taker/fire** | net-maker/fire | redeem net-taker |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        *rows,
        "",
        "## Arms",
        "- **no-redeem** (`--redeem-cap 0`): PA-reject drops; no deferred entry.",
        "- **cap@1bar** (`--redeem-cap 1`): re-evaluate PA for 1 bar; fire-or-abandon.",
        f"- **current** (`--redeem-cap {REDEEM_CAP_CURRENT}`): re-evaluate until PA pass / score-decay / cap.",
        "",
        "## Methodology",
        "- v2 economics: the real `build_trade_plan` (3-leg + fee gate) + the entry-timing",
        "  harness bar-walk (SL-first tie, ordered TP, BE-after-TP1 / TP1-after-TP2 ratchet).",
        "- Redeem fires priced at the **FIRE bar** (not the stale signal price).",
        "- Per-fire independent walks; net-of-cost expectancy per fire is the metric",
        "  (not a compounded equity curve; one-open-at-a-time effects out of scope).",
        "- Cooldown threaded via last_fire_ts; redeem look-ahead introduces minor",
        "  ordering imperfection vs prod — acceptable for engine validation.",
        "",
    ]
    output_path.write_text("\n".join(md), encoding="utf-8")
    return output_path


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
    parser.add_argument("--structure-tf", choices=["4h", "1h"], default="4h",
                        help="PA validator structure-alignment timeframe. "
                             "4h matches current prod; 1h is the proposal.")
    parser.add_argument("--pa-4h-bonus", type=float, default=1.0,
                        help="Size multiplier applied when PA passes AND 4h "
                             "structure also aligns with trade side. Default "
                             "1.0 = no bonus. Proposal uses 1.25.")
    parser.add_argument("--arm-name", default="",
                        help="Free-form label written into the summary "
                             "(e.g. '4h_baseline' or '1h_with_4h_bonus').")
    parser.add_argument(
        "--bar-source", choices=["coinbase", "bybit_hybrid"], default="coinbase",
        help="coinbase = 1m Coinbase + resample (legacy v1.0/v1.1 path). "
             "bybit_hybrid = Bybit 3m/15m from data/btc_scalping.db + "
             "cached Bitunix 5m, prod alerts from cache_alerts_prod_filtered_*.json. "
             "Used by the v1.1 hostile-regime backtest.",
    )
    parser.add_argument(
        "--alert-source", choices=["prod_cache", "synth"], default="prod_cache",
        help="prod_cache = filtered prod webhook alerts (default; Block A). "
             "synth = synthesized alerts from btc_scalping.db DB columns via "
             "scripts/research_scoring/synth_ledger.py (Block B internal-"
             "consistency check; inherits May 16 alertcondition-gap caveats).",
    )
    parser.add_argument(
        "--prod-alerts-cache", default=None,
        help="Path to filtered prod-alert JSON cache (--bar-source bybit_hybrid only).",
    )
    parser.add_argument(
        "--bybit-db", default=None,
        help="Path to data/btc_scalping.db (--bar-source bybit_hybrid only).",
    )
    parser.add_argument(
        "--bitunix-5m-cache", default=None,
        help="Path to Bitunix 5m OHLCV JSON cache (--bar-source bybit_hybrid only).",
    )
    parser.add_argument(
        "--gate", choices=["pa_validation", "five_factor", "both"],
        default="pa_validation",
        help="Gate to evaluate. 'both' runs each arm and writes a "
             "side-by-side comparison report applying the pre-committed "
             "Phase C acceptance thresholds.",
    )
    parser.add_argument(
        "--report-path", default=None,
        help="When --gate=both, the comparison report is written here. "
             "Default: reports/gate_backtest_<end>.md",
    )
    parser.add_argument(
        "--resolution-tf", choices=["3m", "1m"], default="3m",
        help="Trade-resolution bar source. Default 3m preserves the v3 "
             "Block A behavior (uses --bar-source's primary bar series for "
             "both entry-price context and resolve_trade walk). When 1m "
             "AND --bar-source=bybit_hybrid, loads bars_1m (Bitunix native "
             "via scripts/ingest_bitunix_1m_to_db.py) for resolve_trade only; "
             "entry-price context still uses Bybit 3m. Branch A of the v3 "
             "addendum 2026-05-18.",
    )
    parser.add_argument(
        "--redeem-arms", action="store_true",
        help="PA-redeem-cap ENGINE VALIDATION: run no-redeem / cap@1bar / current "
             "arms on the 3m corpus (v2 economics, net-of-cost expectancy per fire). "
             "NOT the §4 verdict (needs high-vol 3m data). Requires bybit_hybrid.",
    )
    parser.add_argument(
        "--redeem-cap", type=int, default=None,
        help="Single redeem-cap arm with this cap (0=no-redeem, N=cap@N bars, "
             f"{REDEEM_CAP_CURRENT}=current). Alternative to --redeem-arms.",
    )
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
    log.info(
        "Arm: name=%r  structure_tf=%s  pa_4h_bonus=%.2f  gate=%s",
        args.arm_name, args.structure_tf, args.pa_4h_bonus, args.gate,
    )

    # Bar source: legacy 1m Coinbase OR Bybit-hybrid (DB 3m/15m + cached Bitunix 5m)
    bars_3m_override = None
    bars_5m_override = None
    bars_15m_override = None
    if args.bar_source == "bybit_hybrid":
        need = [args.bybit_db, args.bitunix_5m_cache]
        if args.alert_source == "prod_cache":
            need.append(args.prod_alerts_cache)
        if not all(need):
            parser.error(
                "--bar-source bybit_hybrid requires --bybit-db, "
                "--bitunix-5m-cache; prod_cache alert source also needs --prod-alerts-cache"
            )
        # bars come from DB regardless of alert source
        # Reuse hybrid loader for bars; substitute alerts based on alert_source
        if args.alert_source == "prod_cache":
            alerts, bars, bars_3m_override, bars_5m_override, bars_15m_override = (
                _load_bybit_hybrid_inputs(
                    Path(args.bybit_db), Path(args.bitunix_5m_cache),
                    Path(args.prod_alerts_cache), start, end,
                )
            )
        else:  # synth
            # Load bars via a dummy alert path; substitute synth alerts after
            _, bars, bars_3m_override, bars_5m_override, bars_15m_override = (
                _load_bybit_hybrid_inputs(
                    Path(args.bybit_db), Path(args.bitunix_5m_cache),
                    # Dummy: use any valid prod cache path; alerts get overwritten below
                    Path(args.prod_alerts_cache) if args.prod_alerts_cache
                    else Path(_REPO_ROOT / "data" / "historical_alerts"
                              / "cache_alerts_20260430_20260517.json"),
                    start, end,
                )
            )
            # Load synth alerts via research_scoring/synth_ledger
            import sys as _sys
            _sys.path.insert(0, str(_REPO_ROOT / "scripts" / "research_scoring"))
            from synth_ledger import load_synth_ledger  # noqa: E402
            synth_alerts = load_synth_ledger(Path(args.bybit_db))
            alerts = [
                AlertEvent(ts=a.ts, signal_name=a.signal_name, tf=a.tf)
                for a in synth_alerts if start <= a.ts < end
            ]
            log.info(
                "Loaded %d synth alerts (windowed) — May 16 alertcondition-gap "
                "caveats apply (reports/scoring_backtest_results.md L15-25)",
                len(alerts),
            )
    else:
        alerts = fetch_alerts_from_prod(start, end, refresh=args.refresh)
        bars = fetch_ohlcv_from_coinbase(start, end, refresh=args.refresh)

    # --resolution-tf 1m: load bars_1m for the resolve_trade walk while
    # leaving `bars` (entry-price context) untouched. Only supported in
    # bybit_hybrid mode today; the Coinbase path is already at 1m so the
    # arm is meaningless there.
    resolution_bars = None
    if args.resolution_tf == "1m":
        if args.bar_source != "bybit_hybrid":
            parser.error(
                "--resolution-tf 1m requires --bar-source bybit_hybrid "
                "(Coinbase path is already at 1m resolution)"
            )
        import sqlite3
        con = sqlite3.connect(args.bybit_db)
        try:
            cur = con.cursor()
            try:
                rows = cur.execute(
                    "SELECT ts, open, high, low, close, volume "
                    "FROM bars_1m WHERE ts >= ? AND ts < ? ORDER BY ts",
                    (int(start.timestamp()), int(end.timestamp())),
                ).fetchall()
            except sqlite3.OperationalError as e:
                parser.error(
                    f"bars_1m table missing or unreadable in {args.bybit_db}: {e}\n"
                    "Run scripts/ingest_bitunix_1m_to_db.py first."
                )
        finally:
            con.close()
        resolution_bars = [{
            "ts": datetime.fromtimestamp(ts, tz=timezone.utc),
            "open": o, "high": h, "low": l, "close": c, "volume": v or 0.0,
        } for ts, o, h, l, c, v in rows]
        log.info(
            "Loaded %d bars_1m (Bitunix native) for trade resolution "
            "(entry price still from %d %s bars)",
            len(resolution_bars), len(bars), args.bar_source,
        )

    ts_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    # PA-redeem-cap ENGINE VALIDATION (2026-06-14) — NOT a §4 verdict.
    if args.redeem_arms or args.redeem_cap is not None:
        if args.bar_source != "bybit_hybrid":
            parser.error("--redeem-arms/--redeem-cap require --bar-source bybit_hybrid (3m corpus)")
        if args.redeem_arms:
            plan = [("no_redeem", 0), ("cap_1bar", 1), ("current", REDEEM_CAP_CURRENT)]
        else:
            plan = [(f"cap_{args.redeem_cap}", args.redeem_cap)]
        arms = []
        for label, cap in plan:
            log.info("redeem-cap arm START: %s (cap=%d)", label, cap)
            _fires, _sum = run_redeem_cap_backtest(
                alerts=alerts, bars=bars, config=config, pa_config=None,
                redeem_cap=cap, structure_tf=args.structure_tf, arm_name=label,
            )
            arms.append(_sum)
            log.info("redeem-cap arm DONE: %s  fires(fp=%d redeem=%d drop=%d skip=%d) "
                     "walked=%d  net-taker/fire=%+.4f",
                     label, _sum["n_first_pass_fire"], _sum["n_redeem_fire"],
                     _sum["n_redeem_drop"], _sum["n_plan_skip"], _sum["n_walked"],
                     _sum["exp_net_taker_per_fire"])
        rp = (Path(args.report_path) if args.report_path else
              _REPO_ROOT / "reports" / f"redeem_cap_engine_validation_{end.date().isoformat()}.md")
        written = _write_redeem_comparison(arms, rp, start=start, end=end)
        print("\n=== PA-REDEEM-CAP ENGINE VALIDATION (NOT a §4 verdict — high-vol 3m data pending) ===")
        for s in arms:
            print(f"  {s['arm_name']:10} fires(fp={s['n_first_pass_fire']} rd={s['n_redeem_fire']} "
                  f"drop={s['n_redeem_drop']} skip={s['n_plan_skip']}) walked={s['n_walked']}  "
                  f"net-taker/fire={s['exp_net_taker_per_fire']:+.4f}  "
                  f"net-maker/fire={s['exp_net_maker_per_fire']:+.4f}  "
                  f"max_bw={s['max_bars_waited']}")
        print(f"  Comparison: {written}")
        return

    if args.gate == "both":
        pa_dir, pa_summary, pa_ledger = _run_one_arm(
            alerts=alerts, bars=bars, config=config, args=args,
            gate="pa_validation", arm_label=args.arm_name or "pa",
            ts_stamp=ts_stamp,
            bars_3m_override=bars_3m_override,
            bars_5m_override=bars_5m_override,
            bars_15m_override=bars_15m_override,
            resolution_bars=resolution_bars,
        )
        gate_dir, gate_summary, gate_ledger = _run_one_arm(
            alerts=alerts, bars=bars, config=config, args=args,
            gate="five_factor", arm_label="five_factor",
            ts_stamp=ts_stamp,
            bars_3m_override=bars_3m_override,
            bars_5m_override=bars_5m_override,
            bars_15m_override=bars_15m_override,
            resolution_bars=resolution_bars,
        )
        if args.report_path:
            report_path = Path(args.report_path)
        else:
            report_path = (
                _REPO_ROOT / "reports"
                / f"gate_backtest_{end.date().isoformat()}.md"
            )
        written = _write_comparison_report(
            output_path=report_path,
            pa_summary=pa_summary, gate_summary=gate_summary,
            pa_ledger=pa_ledger, gate_ledger=gate_ledger,
            start=start, end=end,
        )
        print(f"\nPA arm     : {pa_dir / 'summary.md'}")
        print(f"5f arm     : {gate_dir / 'summary.md'}")
        print(f"Comparison : {written}")
        return

    ledger, trades, summary = run_backtest(
        alerts=alerts, bars=bars, config=config,
        starting_equity=args.starting_equity,
        structure_tf=args.structure_tf,
        pa_4h_bonus_multiplier=args.pa_4h_bonus,
        arm_name=args.arm_name,
        gate=args.gate,
        bars_3m_override=bars_3m_override,
        bars_5m_override=bars_5m_override,
        bars_15m_override=bars_15m_override,
        resolution_bars=resolution_bars,
    )

    if args.output_dir is None:
        suffix = f"_{args.arm_name}" if args.arm_name else f"_{args.gate}"
        output_dir = _REPO_ROOT / "data" / "backtest_runs" / f"bitunix_{ts_stamp}{suffix}"
    else:
        output_dir = Path(args.output_dir)

    write_outputs(ledger, trades, summary, output_dir)
    print(f"\nVerdict written to {output_dir / 'summary.md'}")
    print(f"  Arm: {summary.arm_name}  |  gate={summary.gate_kind}  |  structure_tf={summary.structure_tf}")
    print(f"  Trades: {summary.n_fires}  |  Round-trips: {summary.n_round_trips}")
    print(f"  Win rate: {summary.win_rate_pct:.1f}%  |  PF: {summary.profit_factor:.2f}  |  Total R: {summary.total_r:+.2f}")
    print(f"  Return: {summary.pct_return:+.2f}%  |  Max DD: {summary.max_drawdown_pct:.2f}%")


if __name__ == "__main__":
    main()
