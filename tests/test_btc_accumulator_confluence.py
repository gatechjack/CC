"""Unit tests for the Coinbase BTC Accumulator confluence scorer.

Pin behaviors that the backtest harness AND the (Phase 2) live
strategy both rely on:

  - TTL-windowed alert filtering matches between callers.
  - Buy/sell side routing for `directional` factors (signal-name
    suffix carries the bull/bear direction).
  - Guard penalties apply only on the "wrong" direction (sell-on-
    rush only penalizes sells; buy-on-fall only penalizes buys).
  - State-aware decisions: CASH never sells, BTC never buys.
  - The score breakdown is audit-grade — every contributing factor
    appears with its weight, so an audit reader can reconstruct the
    decision without re-running the scorer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trading_corp.agents.strategies.btc_accumulator import (
    AlertEvent,
    ConfluenceConfig,
    Decision,
    PriceContext,
    State,
    evaluate_confluence,
    filter_live_alerts,
)


# ── Test fixtures ────────────────────────────────────────────────────


def _config(min_buy: int = 6, min_sell: int = 6) -> ConfluenceConfig:
    """Mirror of the production strategies.yaml `btc_accumulator`
    block. Test-local copy so tests don't depend on YAML loading."""
    raw = {
        "confluence": {
            "min_score_buy": min_buy,
            "min_score_sell": min_sell,
            "factors": {
                "cypher_1d_bull":         {"weight": 4, "side": "buy",         "ttl_minutes": 1440},
                "cypher_1d_bear":         {"weight": 4, "side": "sell",        "ttl_minutes": 1440},
                "cypher_4h_bull":         {"weight": 3, "side": "buy",         "ttl_minutes": 240},
                "cypher_4h_bear":         {"weight": 3, "side": "sell",        "ttl_minutes": 240},
                "otter_diamond":          {"weight": 3, "side": "directional", "ttl_minutes": 15},
                "otter_premium":          {"weight": 2, "side": "directional", "ttl_minutes": 15},
                "otter_water_large":      {"weight": 2, "side": "directional", "ttl_minutes": 30},
                "otter_water_small":      {"weight": 1, "side": "directional", "ttl_minutes": 30},
                "otter_standard":         {"weight": 1, "side": "directional", "ttl_minutes": 15},
                "otter_money_bag":        {"weight": 1, "side": "directional", "ttl_minutes": 30},
                "otter_solo":             {"weight": 1, "side": "directional", "ttl_minutes": 15},
                "above_session_vwap":     {"weight": 1, "side": "buy"},
                "below_session_vwap":     {"weight": 1, "side": "sell"},
                "higher_highs_4h":        {"weight": 2, "side": "buy"},
                "lower_lows_4h":          {"weight": 2, "side": "sell"},
                "volume_above_20bar_avg": {"weight": 1, "side": "directional"},
            },
        },
        "guards": {
            "sell_on_rush": {
                "window_minutes": 60,
                "brackets": [
                    {"upto_pct": 1.0, "penalty": 0},
                    {"upto_pct": 3.0, "penalty": -1},
                    {"upto_pct": 5.0, "penalty": -2},
                    {"upto_pct": 999, "penalty": -3},
                ],
            },
            "buy_on_fall": {
                "window_minutes": 60,
                "brackets": [
                    {"upto_drop_pct": 1.0, "penalty": 0},
                    {"upto_drop_pct": 3.0, "penalty": -1},
                    {"upto_drop_pct": 5.0, "penalty": -2},
                    {"upto_drop_pct": 999, "penalty": -3},
                ],
            },
        },
        "audit": {"log_confluence_negative": True},
    }
    return ConfluenceConfig.from_dict(raw)


def _now() -> datetime:
    return datetime(2026, 5, 8, 22, 0, 0, tzinfo=timezone.utc)


def _quiet_price() -> PriceContext:
    """Price context with no rush, no fall, no PA factors active."""
    return PriceContext(
        current_price=80_000.0,
        pct_change_in_window_sell=0.0,
        pct_change_in_window_buy=0.0,
    )


# ── Buy-side scoring ────────────────────────────────────────────────


def test_buy_fires_on_cypher_1d_plus_4h_bull():
    """Cypher 1D bull (4) + Cypher 4h bull (3) = 7 ≥ min_score_buy=6.
    Pure HTF confluence — no Otter needed."""
    cfg = _config(min_buy=6)
    alerts = [
        AlertEvent(ts=_now() - timedelta(hours=2), signal_name="cypher_1d_bull"),
        AlertEvent(ts=_now() - timedelta(minutes=30), signal_name="cypher_4h_bull"),
    ]
    v = evaluate_confluence(
        state=State.CASH, live_alerts=alerts, price_ctx=_quiet_price(),
        config=cfg, now=_now(),
    )
    assert v.decision == Decision.BUY
    assert v.breakdown.raw_buy_score == 7
    assert v.breakdown.final_buy_score == 7


def test_buy_skips_when_only_otter_solo():
    """Otter Solo alone (1) is well below min_score_buy=6. Confluence
    explicitly rejects the lonely-LTF case."""
    cfg = _config(min_buy=6)
    alerts = [
        AlertEvent(ts=_now() - timedelta(minutes=5), signal_name="otter_solo_bull"),
    ]
    v = evaluate_confluence(
        state=State.CASH, live_alerts=alerts, price_ctx=_quiet_price(),
        config=cfg, now=_now(),
    )
    assert v.decision == Decision.SKIP
    assert v.breakdown.raw_buy_score == 1


def test_buy_fires_with_directional_otter_diamond_plus_pa():
    """Otter Diamond Bull (3) + above_VWAP (1) + higher_highs_4h (2)
    = 6 — exactly at threshold, fires."""
    cfg = _config(min_buy=6)
    alerts = [
        AlertEvent(ts=_now() - timedelta(minutes=5), signal_name="otter_diamond_bull"),
    ]
    pc = PriceContext(
        current_price=80_000.0,
        pct_change_in_window_sell=0.0,
        pct_change_in_window_buy=0.0,
        above_session_vwap=True,
        higher_highs_4h=True,
    )
    v = evaluate_confluence(
        state=State.CASH, live_alerts=alerts, price_ctx=pc, config=cfg, now=_now(),
    )
    assert v.decision == Decision.BUY
    assert v.breakdown.final_buy_score == 6


# ── Sell-side scoring ───────────────────────────────────────────────


def test_sell_fires_on_cypher_bear_confluence():
    """Cypher 1D bear (4) + Cypher 4h bear (3) = 7. Mirror of the buy
    case. Only fires when state=BTC."""
    cfg = _config(min_sell=6)
    alerts = [
        AlertEvent(ts=_now() - timedelta(hours=2), signal_name="cypher_1d_bear"),
        AlertEvent(ts=_now() - timedelta(minutes=30), signal_name="cypher_4h_bear"),
    ]
    v = evaluate_confluence(
        state=State.BTC, live_alerts=alerts, price_ctx=_quiet_price(),
        config=cfg, now=_now(),
    )
    assert v.decision == Decision.SELL
    assert v.breakdown.final_sell_score == 7


def test_sell_skipped_when_state_is_cash():
    """Even with overwhelming sell confluence, CASH state means no
    sell — nothing to sell. State-aware decision invariant."""
    cfg = _config(min_sell=6)
    alerts = [
        AlertEvent(ts=_now() - timedelta(hours=2), signal_name="cypher_1d_bear"),
        AlertEvent(ts=_now() - timedelta(minutes=30), signal_name="cypher_4h_bear"),
        AlertEvent(ts=_now() - timedelta(minutes=5), signal_name="otter_diamond_bear"),
    ]
    v = evaluate_confluence(
        state=State.CASH, live_alerts=alerts, price_ctx=_quiet_price(),
        config=cfg, now=_now(),
    )
    assert v.decision == Decision.SKIP
    # The sell side score IS computed (audit visibility) but isn't
    # acted on because state=CASH evaluates only the buy side.
    assert v.breakdown.raw_sell_score >= 7


def test_buy_skipped_when_state_is_btc():
    """BTC state means we're already 100% in — buy signal is a no-op.
    Symmetric to the CASH-skip-sell case."""
    cfg = _config(min_buy=6)
    alerts = [
        AlertEvent(ts=_now() - timedelta(hours=2), signal_name="cypher_1d_bull"),
        AlertEvent(ts=_now() - timedelta(minutes=30), signal_name="cypher_4h_bull"),
    ]
    v = evaluate_confluence(
        state=State.BTC, live_alerts=alerts, price_ctx=_quiet_price(),
        config=cfg, now=_now(),
    )
    assert v.decision == Decision.SKIP


# ── Guard penalties ─────────────────────────────────────────────────


def test_sell_on_rush_penalty_blocks_marginal_sell():
    """Sell score 7 (marginal pass) into a 4% rush (-2 penalty) →
    final 5, doesn't clear min_score_sell=6. Guard does its job."""
    cfg = _config(min_sell=6)
    alerts = [
        AlertEvent(ts=_now() - timedelta(hours=2), signal_name="cypher_1d_bear"),
        AlertEvent(ts=_now() - timedelta(minutes=30), signal_name="cypher_4h_bear"),
    ]
    pc = PriceContext(
        current_price=80_000.0,
        pct_change_in_window_sell=4.0,   # +4% in last 60min — on a heater
        pct_change_in_window_buy=4.0,
    )
    v = evaluate_confluence(
        state=State.BTC, live_alerts=alerts, price_ctx=pc, config=cfg, now=_now(),
    )
    assert v.decision == Decision.SKIP
    assert v.breakdown.sell_guard_penalty == -2
    assert v.breakdown.final_sell_score == 5


def test_sell_on_rush_does_not_block_overwhelming_sell():
    """Sell score 9 into a 4% rush (-2) → final 7, still clears
    min_score_sell=6. Guard expresses 'wait unless conviction is
    overwhelming' rather than a binary veto."""
    cfg = _config(min_sell=6)
    alerts = [
        AlertEvent(ts=_now() - timedelta(hours=2), signal_name="cypher_1d_bear"),    # 4
        AlertEvent(ts=_now() - timedelta(minutes=30), signal_name="cypher_4h_bear"), # 3
        AlertEvent(ts=_now() - timedelta(minutes=5), signal_name="otter_premium_bear"),  # 2
    ]
    pc = PriceContext(
        current_price=80_000.0,
        pct_change_in_window_sell=4.0,
        pct_change_in_window_buy=4.0,
    )
    v = evaluate_confluence(
        state=State.BTC, live_alerts=alerts, price_ctx=pc, config=cfg, now=_now(),
    )
    assert v.decision == Decision.SELL
    assert v.breakdown.raw_sell_score == 9
    assert v.breakdown.sell_guard_penalty == -2
    assert v.breakdown.final_sell_score == 7


def test_buy_on_fall_penalty_blocks_marginal_buy():
    """Falling-knife protection: buy score 7 into a 4% fall (-2) →
    final 5, doesn't clear. Symmetric to sell-on-rush."""
    cfg = _config(min_buy=6)
    alerts = [
        AlertEvent(ts=_now() - timedelta(hours=2), signal_name="cypher_1d_bull"),
        AlertEvent(ts=_now() - timedelta(minutes=30), signal_name="cypher_4h_bull"),
    ]
    pc = PriceContext(
        current_price=80_000.0,
        pct_change_in_window_sell=-4.0,
        pct_change_in_window_buy=-4.0,   # -4% in last 60min — falling
    )
    v = evaluate_confluence(
        state=State.CASH, live_alerts=alerts, price_ctx=pc, config=cfg, now=_now(),
    )
    assert v.decision == Decision.SKIP
    assert v.breakdown.buy_guard_penalty == -2
    assert v.breakdown.final_buy_score == 5


def test_sell_on_rush_does_not_penalize_buy_side():
    """A fast rise should NOT block a buy — only sells get penalized
    on rises. Cross-contamination is the exact bug we're testing
    against."""
    cfg = _config(min_buy=6)
    alerts = [
        AlertEvent(ts=_now() - timedelta(hours=2), signal_name="cypher_1d_bull"),
        AlertEvent(ts=_now() - timedelta(minutes=30), signal_name="cypher_4h_bull"),
    ]
    pc = PriceContext(
        current_price=80_000.0,
        pct_change_in_window_sell=5.5,   # rush (sell-only penalty domain)
        pct_change_in_window_buy=5.5,    # but window_buy is positive (rose)
                                          # → no buy penalty (we only penalize falls)
    )
    v = evaluate_confluence(
        state=State.CASH, live_alerts=alerts, price_ctx=pc, config=cfg, now=_now(),
    )
    assert v.decision == Decision.BUY
    assert v.breakdown.buy_guard_penalty == 0


def test_buy_on_fall_does_not_penalize_sell_side():
    """A fast fall should NOT block a sell — only buys get penalized
    on falls. Symmetric to the cross-contamination test above."""
    cfg = _config(min_sell=6)
    alerts = [
        AlertEvent(ts=_now() - timedelta(hours=2), signal_name="cypher_1d_bear"),
        AlertEvent(ts=_now() - timedelta(minutes=30), signal_name="cypher_4h_bear"),
    ]
    pc = PriceContext(
        current_price=80_000.0,
        pct_change_in_window_sell=-5.5,
        pct_change_in_window_buy=-5.5,
    )
    v = evaluate_confluence(
        state=State.BTC, live_alerts=alerts, price_ctx=pc, config=cfg, now=_now(),
    )
    assert v.decision == Decision.SELL
    assert v.breakdown.sell_guard_penalty == 0


def test_guard_bracket_boundaries():
    """Verify each bracket's `upto_pct` upper edge maps to the
    expected penalty. Off-by-one in the bracket selection would
    silently miscompute every guard."""
    cfg = _config()
    # sell_on_rush brackets: 0/-1/-2/-3 at 1.0/3.0/5.0/999
    alerts = [
        AlertEvent(ts=_now() - timedelta(hours=2), signal_name="cypher_1d_bear"),
        AlertEvent(ts=_now() - timedelta(minutes=30), signal_name="cypher_4h_bear"),
    ]
    for pct, expected_penalty in [
        (0.5, 0),     # in 0-1.0 bracket
        (1.0, 0),     # at 1.0 boundary (inclusive)
        (1.01, -1),   # just over 1.0 → next bracket
        (3.0, -1),    # at 3.0 boundary
        (3.5, -2),    # in 3-5 bracket
        (5.0, -2),    # at 5.0 boundary
        (8.0, -3),    # over 5 → max penalty
    ]:
        pc = PriceContext(
            current_price=80_000.0,
            pct_change_in_window_sell=pct,
            pct_change_in_window_buy=pct,
        )
        v = evaluate_confluence(
            state=State.BTC, live_alerts=alerts, price_ctx=pc,
            config=cfg, now=_now(),
        )
        assert v.breakdown.sell_guard_penalty == expected_penalty, (
            f"pct={pct}: got penalty={v.breakdown.sell_guard_penalty}, "
            f"expected {expected_penalty}"
        )


# ── Score breakdown audit detail ────────────────────────────────────


def test_breakdown_lists_every_contributing_factor():
    """Audit consumers reconstruct the decision from the breakdown,
    so every contributing factor must appear with its weight."""
    cfg = _config(min_buy=4)
    alerts = [
        AlertEvent(ts=_now() - timedelta(hours=2), signal_name="cypher_1d_bull"),
        AlertEvent(ts=_now() - timedelta(minutes=5), signal_name="otter_diamond_bull"),
    ]
    pc = PriceContext(
        current_price=80_000.0,
        pct_change_in_window_sell=0.0,
        pct_change_in_window_buy=0.0,
        above_session_vwap=True,
    )
    v = evaluate_confluence(
        state=State.CASH, live_alerts=alerts, price_ctx=pc,
        config=cfg, now=_now(),
    )
    contrib_names = {name for name, _ in v.breakdown.buy_contributions}
    assert "cypher_1d_bull" in contrib_names
    assert "otter_diamond_bull" in contrib_names
    assert "above_session_vwap" in contrib_names
    # Total weight should match raw score
    total_weight = sum(w for _, w in v.breakdown.buy_contributions)
    assert total_weight == v.breakdown.raw_buy_score == 8


def test_directional_volume_factor_contributes_to_both_sides():
    """volume_above_20bar_avg is `directional` — it's a strength-of-
    move indicator, not direction-of-move. So it adds to both buy
    and sell scores; the directional signals (cypher/otter) supply
    the actual direction."""
    cfg = _config(min_buy=2, min_sell=2)
    pc = PriceContext(
        current_price=80_000.0,
        pct_change_in_window_sell=0.0,
        pct_change_in_window_buy=0.0,
        volume_above_20bar_avg=True,
    )
    v = evaluate_confluence(
        state=State.CASH, live_alerts=[], price_ctx=pc, config=cfg, now=_now(),
    )
    assert v.breakdown.raw_buy_score == 1
    assert v.breakdown.raw_sell_score == 1


# ── TTL filter ──────────────────────────────────────────────────────


def test_ttl_filter_drops_expired_otter_alerts():
    """Otter Diamond TTL is 15min. An alert from 60min ago should be
    filtered out by `filter_live_alerts` before reaching the scorer."""
    cfg = _config()
    alerts = [
        AlertEvent(ts=_now() - timedelta(hours=2), signal_name="cypher_1d_bull"),  # 1440min TTL — keep
        AlertEvent(ts=_now() - timedelta(minutes=60), signal_name="otter_diamond_bull"),  # 15min TTL — drop
        AlertEvent(ts=_now() - timedelta(minutes=10), signal_name="otter_diamond_bull"),  # within TTL — keep
    ]
    live = filter_live_alerts(alerts, cfg, _now())
    signal_names = [a.signal_name for a in live]
    assert "cypher_1d_bull" in signal_names
    assert signal_names.count("otter_diamond_bull") == 1
    assert len(live) == 2


def test_ttl_filter_keeps_cypher_1d_for_a_full_day():
    """Cypher 1D TTL is 1440min (24h) — alerts within that window
    must be retained."""
    cfg = _config()
    alerts = [
        AlertEvent(ts=_now() - timedelta(hours=23), signal_name="cypher_1d_bull"),    # within 24h — keep
        AlertEvent(ts=_now() - timedelta(hours=25), signal_name="cypher_1d_bull"),    # past 24h — drop
    ]
    live = filter_live_alerts(alerts, cfg, _now())
    assert len(live) == 1


def test_ttl_filter_drops_future_alerts():
    """Look-ahead guard: alerts with ts > now must be dropped, even
    when the negative `age` would compare ≤ TTL by Python's
    timedelta semantics. Pre-fix this silently passed future alerts
    through, giving the backtest look-ahead bias."""
    cfg = _config()
    alerts = [
        AlertEvent(ts=_now() - timedelta(minutes=5), signal_name="cypher_1d_bull"),    # past — keep
        AlertEvent(ts=_now() + timedelta(minutes=10), signal_name="cypher_1d_bull"),   # future — drop
        AlertEvent(ts=_now() + timedelta(hours=2), signal_name="cypher_1d_bull"),      # future — drop
    ]
    live = filter_live_alerts(alerts, cfg, _now())
    assert len(live) == 1
    assert live[0].ts < _now()


def test_ttl_filter_drops_unknown_signal_names():
    """Signal name not in the factors block: silently dropped at
    filter time. Backtest harness logs warnings at ingest, so the
    hot path stays quiet."""
    cfg = _config()
    alerts = [
        AlertEvent(ts=_now() - timedelta(minutes=5), signal_name="some_unknown_signal"),
    ]
    live = filter_live_alerts(alerts, cfg, _now())
    assert live == []


# ── Edge cases ──────────────────────────────────────────────────────


def test_empty_alerts_and_no_price_action_skips_cleanly():
    """No inputs at all → SKIP with zero scores. Should not crash."""
    cfg = _config()
    v = evaluate_confluence(
        state=State.CASH, live_alerts=[], price_ctx=_quiet_price(),
        config=cfg, now=_now(),
    )
    assert v.decision == Decision.SKIP
    assert v.breakdown.raw_buy_score == 0
    assert v.breakdown.final_buy_score == 0


def test_directional_signal_without_bull_or_bear_suffix_dropped():
    """A directional factor needs the signal name to indicate side
    (bull/bear). Without it: silent ignore (defensive — the alert
    pipeline upstream should canonicalize names but this is the
    fallback)."""
    cfg = _config()
    alerts = [
        AlertEvent(ts=_now() - timedelta(minutes=5), signal_name="otter_diamond"),  # no _bull / _bear
    ]
    v = evaluate_confluence(
        state=State.CASH, live_alerts=alerts, price_ctx=_quiet_price(),
        config=cfg, now=_now(),
    )
    # No contribution to either side
    assert v.breakdown.raw_buy_score == 0
    assert v.breakdown.raw_sell_score == 0
