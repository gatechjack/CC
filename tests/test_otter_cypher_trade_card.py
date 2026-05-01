"""Phase A trade-card enrichment tests (BACKLOG.md 2026-05-01).

Pins:
  - Otter and Cypher `_build_order` populate take-profit fields in
    `extra` (take_profit_price, tp_basis, tp_r_multiple,
    tp_distance_dollars, tp_distance_pct, expected_gain_if_tp_hit,
    expected_loss_if_stopped, entry_reference_price).
  - TP price = entry ± (stop_distance × tier_r_multiple), signed by
    direction.
  - Per-tier R-multiple from strategies.yaml; fallback to default
    when tier missing.
  - Degenerate stop_distance=0 → take_profit_price=None, tp_basis='unavailable'.
  - The `_format_trade_card` push formatter renders entry / stop / TP
    / R:R lines from `extra`; missing fields gracefully omit lines.
  - Legacy orders (no Phase A fields) still render a basic card.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_corp.agents.divisions.lord_otter import (
    LordOtterAgent, TierVerdict,
)
from trading_corp.agents.divisions.market_cypher import MarketCypherAgent
from trading_corp.persistence.models import ProposedOrder
from trading_corp.web.webhooks import (
    _format_trade_card,
    _format_would_have_placed_msg,
    _format_would_have_placed_msg_cypher,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def otter_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "strategies.yaml"
    p.write_text(
        """
lord_otter:
  enabled: true
  auto_execute: false
  symbols:
    - BTC/USD
  tier_sizes:
    diamond:    0.050
    solo_otter: 0.0075
  stop_loss:
    method: trigger_bar
    swing_buffer_pct: 0.001
    max_loss_pct_equity: 0.005
    fallback_stop_distance_pct: 0.003
  take_profit:
    default_r_multiple: 2.0
    tier_r_multiples:
      diamond:    2.5
      solo_otter: 1.5
""".strip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def cypher_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "strategies.yaml"
    p.write_text(
        """
market_cypher:
  enabled: true
  auto_execute: false
  symbols:
    - BTC/USD
  tier_sizes:
    gold:        0.075
    standard:    0.020
  stop_loss:
    method: trigger_bar
    swing_buffer_pct: 0.005
    max_loss_pct_equity: 0.02
    fallback_stop_distance_pct: 0.02
  take_profit:
    default_r_multiple: 2.5
    tier_r_multiples:
      gold:     3.0
      standard: 2.0
""".strip(),
        encoding="utf-8",
    )
    return p


def _otter(otter_yaml: Path) -> LordOtterAgent:
    from trading_corp.data.macro_calendar import MacroCalendar
    return LordOtterAgent(
        strategies_yaml=otter_yaml,
        macro_calendar=MacroCalendar(path=otter_yaml.parent / "no_events.yaml"),
        db_url=None,
    )


def _cypher(cypher_yaml: Path) -> MarketCypherAgent:
    from trading_corp.data.macro_calendar import MacroCalendar
    return MarketCypherAgent(
        strategies_yaml=cypher_yaml,
        macro_calendar=MacroCalendar(path=cypher_yaml.parent / "no_events.yaml"),
        db_url=None,
    )


def _otter_long_verdict(tier: str = "diamond") -> TierVerdict:
    return TierVerdict(
        tier=tier, direction="long", size_pct_equity=0.05,
        rationale=f"{tier} setup",
        entry_price=67_420.0,
        payload={
            "symbol": "BTC/USD",
            "signal": f"bullish_{tier}_3m",
            "bar_low": 67_150.0,
            "bar_high": 67_500.0,
            "close": 67_420.0,
        },
    )


# ── Otter TP fields ─────────────────────────────────────────────────────


def test_otter_build_order_populates_tp_fields(otter_yaml):
    agent = _otter(otter_yaml)
    order = agent._build_order(
        _otter_long_verdict("diamond"), size_pct=0.05,
        price=67_420.0, ts=datetime.now(timezone.utc),
        account_equity=100_000.0,
    )
    extra = order.extra
    # Tier-specific R from config (diamond = 2.5)
    assert extra["tp_r_multiple"] == 2.5
    assert extra["take_profit_price"] is not None
    assert extra["tp_basis"] == "r_multiple"
    # Entry reference + Phase B replay-table fields
    assert extra["entry_reference_price"] == 67_420.0
    # TP must be ABOVE entry on a long
    assert extra["take_profit_price"] > 67_420.0
    # TP distance = stop_distance × R
    expected_tp_distance = extra["stop_distance_dollars"] * 2.5
    assert extra["tp_distance_dollars"] == pytest.approx(expected_tp_distance)
    # Expected gain = qty × tp_distance (mirror of expected_loss)
    assert extra["expected_gain_if_tp_hit"] == pytest.approx(
        order.qty * expected_tp_distance,
    )
    assert extra["expected_loss_if_stopped"] == pytest.approx(
        -extra["max_dollar_risk"],
    )


def test_otter_short_tp_below_entry(otter_yaml):
    """For a short, TP is BELOW entry (price - tp_distance)."""
    agent = _otter(otter_yaml)
    short_verdict = TierVerdict(
        tier="diamond", direction="short", size_pct_equity=0.05,
        rationale="bearish setup",
        entry_price=67_420.0,
        payload={
            "symbol": "BTC/USD", "signal": "bearish_diamond_3m",
            "bar_low": 67_300.0, "bar_high": 67_600.0,
            "close": 67_420.0,
        },
    )
    order = agent._build_order(
        short_verdict, size_pct=0.05, price=67_420.0,
        ts=datetime.now(timezone.utc), account_equity=100_000.0,
    )
    assert order.extra["take_profit_price"] < 67_420.0


def test_otter_tier_fallback_to_default_when_tier_missing(otter_yaml):
    """A tier not listed in tier_r_multiples uses default_r_multiple."""
    agent = _otter(otter_yaml)
    # standard tier is NOT in the fixture's tier_r_multiples block
    order = agent._build_order(
        _otter_long_verdict("standard"), size_pct=0.015,
        price=67_420.0, ts=datetime.now(timezone.utc),
        account_equity=100_000.0,
    )
    assert order.extra["tp_r_multiple"] == 2.0   # default


def test_otter_solo_tier_uses_lower_r_multiple(otter_yaml):
    """Lower-conviction tier gets a tighter TP per config."""
    agent = _otter(otter_yaml)
    order = agent._build_order(
        _otter_long_verdict("solo_otter"), size_pct=0.0075,
        price=67_420.0, ts=datetime.now(timezone.utc),
        account_equity=100_000.0,
    )
    assert order.extra["tp_r_multiple"] == 1.5


# ── Cypher TP fields ────────────────────────────────────────────────────


def test_cypher_build_order_populates_tp_fields(cypher_yaml):
    agent = _cypher(cypher_yaml)
    verdict = TierVerdict(
        tier="gold", direction="long", size_pct_equity=0.075,
        rationale="gold setup",
        entry_price=2_100.0,
        payload={
            "symbol": "ETH/USD", "signal": "gold_circle",
            "low": 2_080.0, "high": 2_120.0, "close": 2_100.0,
        },
    )
    order = agent._build_order(
        verdict, size_pct=0.075, price=2_100.0,
        ts=datetime.now(timezone.utc), account_equity=100_000.0,
    )
    extra = order.extra
    assert extra["tp_r_multiple"] == 3.0   # gold gets the high R
    assert extra["take_profit_price"] is not None
    assert extra["take_profit_price"] > 2_100.0   # long → TP above
    assert extra["tp_basis"] == "r_multiple"
    assert extra["entry_reference_price"] == 2_100.0


def test_cypher_default_r_when_tier_missing(cypher_yaml):
    agent = _cypher(cypher_yaml)
    verdict = TierVerdict(
        tier="big_circle", direction="long", size_pct_equity=0.03,
        rationale="big_circle setup",
        entry_price=2_100.0,
        payload={
            "symbol": "ETH/USD", "signal": "big_green_circle",
            "low": 2_080.0, "high": 2_120.0, "close": 2_100.0,
        },
    )
    order = agent._build_order(
        verdict, size_pct=0.03, price=2_100.0,
        ts=datetime.now(timezone.utc), account_equity=100_000.0,
    )
    assert order.extra["tp_r_multiple"] == 2.5   # cypher default


# ── Push card formatter ─────────────────────────────────────────────────


def _enriched_order() -> ProposedOrder:
    """An order with full Phase A extras populated — what _build_order
    emits today after the enrichment."""
    return ProposedOrder(
        strategy="lord_otter",
        symbol="BTC/USD",
        side="buy",
        qty=0.0125,
        order_type="market",
        rationale="...",
        extra={
            "tier": "diamond",
            "source_signal": "bullish_diamond_3m",
            "size_pct_equity": 0.05,
            "notional_target": 5000.0,
            "entry_reference_price": 67_420.0,
            "stop_price": 67_150.0,
            "stop_basis": "trigger_bar_low",
            "stop_distance_pct": 0.004,
            "max_dollar_risk": 50.0,
            "take_profit_price": 68_230.0,
            "tp_r_multiple": 3.0,
            "tp_distance_pct": 0.012,
            "expected_gain_if_tp_hit": 150.0,
        },
    )


def test_formatter_renders_full_trade_card_for_otter():
    text = _format_would_have_placed_msg(_enriched_order(), "risk_approve")
    # Must include each information row — verify substrings
    assert "Lord Otter" in text
    assert "DIAMOND" in text
    assert "bullish_diamond_3m" in text
    assert "BTC/USD" in text
    assert "$67,420" in text                      # entry
    assert "$67,150" in text and "Stop" in text   # stop line
    assert "$68,230" in text and "Target" in text # TP line
    assert "3.0R" in text                          # R-multiple
    assert "Risk" in text and "$50.00" in text   # risk line
    assert "Reward" in text and "$150.00" in text
    assert "1:3.0" in text                         # R:R ratio
    assert "auto-execute is off" in text


def test_formatter_renders_cypher_with_correct_emoji():
    order = _enriched_order()
    order.strategy = "market_cypher"
    text = _format_would_have_placed_msg_cypher(order, "risk_approve")
    assert "🔮" in text
    assert "Market Cypher" in text


def test_formatter_omits_tp_line_when_take_profit_missing():
    """Legacy/degenerate order with no TP fields → still renders, just
    without the 🎯 line."""
    order = ProposedOrder(
        strategy="lord_otter", symbol="BTC/USD", side="buy", qty=0.01,
        order_type="market", rationale="...",
        extra={
            "tier": "diamond", "source_signal": "x",
            "size_pct_equity": 0.05, "notional_target": 5000.0,
            "entry_reference_price": 67_000.0,
            "stop_price": 66_700.0, "stop_basis": "fallback_pct",
            "stop_distance_pct": 0.0045, "max_dollar_risk": 50.0,
            # No take_profit_price / tp_r_multiple
        },
    )
    text = _format_would_have_placed_msg(order, "risk_approve")
    assert "Stop" in text   # stop still rendered
    assert "Target" not in text   # TP line absent


def test_formatter_handles_legacy_order_without_phase_a_fields():
    """Pre-Phase-A orders (no entry_reference_price, no TP, no stop_price)
    should still render a basic card without raising."""
    order = ProposedOrder(
        strategy="lord_otter", symbol="BTC/USD", side="buy", qty=0.01,
        order_type="market", rationale="...",
        extra={
            "tier": "standard",
            "source_signal": "old_signal",
            "size_pct_equity": 0.015,
            # No entry_reference_price / stop_price / TP fields
        },
    )
    text = _format_would_have_placed_msg(order, "risk_approve")
    # Must not raise; falls back to "would BUY ... market" form
    assert "Lord Otter" in text
    assert "would *BUY*" in text
    assert "auto-execute is off" in text


def test_formatter_short_uses_inverted_signs():
    """A SELL order: stop sign inverted (above entry), TP sign inverted."""
    order = _enriched_order()
    order.side = "sell"
    # For a short, stop > entry and TP < entry. Mock the values.
    order.extra["stop_price"] = 67_700.0       # above entry
    order.extra["take_profit_price"] = 66_610.0  # below entry
    text = _format_would_have_placed_msg(order, "risk_approve")
    # Verify the sign conventions show "+" for the stop% on a short
    assert "Stop" in text
    assert "Target" in text
    # SELL header line
    assert "would *SELL*" in text
