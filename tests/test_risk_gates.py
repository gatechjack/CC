"""Risk Agent deterministic-cap tests. These run without any LLM call."""
from __future__ import annotations

import pytest

from trading_corp.agents.risk import RiskAgent
from trading_corp.persistence.models import AccountState, ProposedOrder, StrategyState


def _account(equity=100_000, peak=100_000, halted=False) -> AccountState:
    return AccountState(account="paper", equity=equity, peak_equity=peak, halted=halted)


def _strategy(name="demo", halted=False, pnl=0.0) -> StrategyState:
    return StrategyState(strategy=name, halted=halted, realized_pnl=pnl)


def _order(qty=10, price=500.0, side="buy") -> ProposedOrder:
    return ProposedOrder(
        strategy="demo", symbol="SPY", side=side, qty=qty,
        order_type="limit", limit_price=price,
    )


def test_halted_strategy_rejected(tmp_risk_yaml):
    risk = RiskAgent(risk_yaml=tmp_risk_yaml, narrator_enabled=False)
    v = risk.evaluate(_order(), _account(), _strategy(halted=True))
    assert v.verdict == "reject"
    assert "halted" in v.reason


def test_halted_account_rejected(tmp_risk_yaml):
    risk = RiskAgent(risk_yaml=tmp_risk_yaml, narrator_enabled=False)
    v = risk.evaluate(_order(), _account(halted=True), _strategy())
    assert v.verdict == "reject"
    assert "halted" in v.reason


def test_max_drawdown_triggers_flatten(tmp_risk_yaml):
    risk = RiskAgent(risk_yaml=tmp_risk_yaml, narrator_enabled=False)
    # Equity is 80% of peak → 20% DD → exceeds 15% cap.
    acct = _account(equity=80_000, peak=100_000)
    v = risk.evaluate(_order(), acct, _strategy())
    assert v.verdict == "reject"
    assert v.flatten_account is True


def test_max_drawdown_disabled_flag_skips_cap(tmp_path):
    """Per-strategy `max_drawdown_disabled: true` opts a strategy out of the
    account-level auto-flatten — required for 100%-in/out strategies (e.g.
    coinbase_btc_donchian) whose edge needs to ride volatility to the next
    exit signal. With the flag set, a drawdown past the global cap must NOT
    trigger reject/flatten.
    """
    yaml_path = tmp_path / "risk.yaml"
    yaml_path.write_text(
        """
global:
  per_trade_risk_pct: 0.015
  per_strategy_daily_loss_pct: 0.03
  per_account_max_drawdown_pct: 0.15
trend_alignment:
  counter_trend_size_multiplier: 0.5
overrides:
  demo:
    max_drawdown_disabled: true
""".strip(),
        encoding="utf-8",
    )
    risk = RiskAgent(risk_yaml=yaml_path, narrator_enabled=False)
    # 20% DD — would normally trigger flatten/reject. With opt-out, approves.
    acct = _account(equity=80_000, peak=100_000)
    v = risk.evaluate(_order(qty=1, price=500), acct, _strategy())
    assert v.verdict == "approve"
    assert v.flatten_account is False


def test_per_trade_cap_triggers_resize(tmp_risk_yaml):
    risk = RiskAgent(risk_yaml=tmp_risk_yaml, narrator_enabled=False)
    # Equity 100k * 0.015 = 1500 risk cap.
    # Order: 10 @ 500 = 5000 notional → must resize.
    v = risk.evaluate(_order(qty=10, price=500), _account(), _strategy())
    assert v.verdict == "resize"
    assert v.new_qty is not None
    # 1500 / 500 = 3.0 contracts max
    assert v.new_qty == pytest.approx(3.0, rel=1e-6)


def test_within_cap_approved(tmp_risk_yaml):
    risk = RiskAgent(risk_yaml=tmp_risk_yaml, narrator_enabled=False)
    # 1 share @ $500 = $500 notional, well within $1500 cap.
    v = risk.evaluate(_order(qty=1, price=500), _account(), _strategy())
    assert v.verdict == "approve"


def test_counter_trend_resizes_down(tmp_risk_yaml):
    risk = RiskAgent(risk_yaml=tmp_risk_yaml, narrator_enabled=False)
    # 1 share @ $500 = $500 notional, far under cap. With counter-trend (buy in
    # downtrend), qty should be halved to 0.5.
    v = risk.evaluate(
        _order(qty=1, price=500, side="buy"),
        _account(),
        _strategy(),
        regime="downtrend",
    )
    assert v.verdict == "resize"
    assert v.new_qty == pytest.approx(0.5, rel=1e-6)


def test_daily_loss_cap_rejects_and_halts(tmp_risk_yaml):
    risk = RiskAgent(risk_yaml=tmp_risk_yaml, narrator_enabled=False)
    # 3% of 100k = 3000 daily loss cap.
    strat = _strategy(pnl=-3500)  # exceeded
    v = risk.evaluate(_order(qty=1, price=500), _account(), strat)
    assert v.verdict == "reject"
    assert v.halt_strategy is True


def test_no_price_reference_approves_provisionally(tmp_risk_yaml):
    """If neither limit_price nor a fetched mark is supplied, risk approves
    and the per-trade cap is enforced at fill time by the executor.
    """
    risk = RiskAgent(risk_yaml=tmp_risk_yaml, narrator_enabled=False)
    order = ProposedOrder(strategy="demo", symbol="BTC/USD", side="buy",
                          qty=1.0, order_type="market", limit_price=None)
    v = risk.evaluate(order, _account(), _strategy())
    assert v.verdict == "approve"
