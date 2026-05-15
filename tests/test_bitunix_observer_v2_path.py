"""Integration tests for the PR 4 adaptive trade-plan path in
BitunixFuturesObserver._build_proposal_v2 + _log_trade_plan_decision.

These exercise the v2 dispatch end-to-end without going through the
full webhook flow — just construct an observer with the new configs
and call the method directly.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
)
from trading_corp.agents.strategies.trade_plan import (
    FeeConfig,
    StrategyConfig,
    TradePlan,
)
from trading_corp.data.live_bar_cache import Bar
from trading_corp.persistence import db


def _bar(
    price: float,
    *,
    high: float | None = None,
    low: float | None = None,
    ts_ms: int = 0,
) -> Bar:
    return Bar(
        ts_ms=ts_ms,
        open=price,
        high=high if high is not None else price,
        low=low if low is not None else price,
        close=price,
        volume=1.0,
    )


class _FakeBarCache:
    """Minimal LiveBarCache stand-in for v2-path unit tests."""

    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars

    def get_atr(self, period: int = 14) -> float:
        return 50.0


def _make_observer(
    tmp_path: Path,
    bars: list[Bar] | None = None,
    *,
    trade_plan_config: StrategyConfig | None = None,
    fee_config: FeeConfig | None = None,
) -> BitunixFuturesObserver:
    db_path = tmp_path / "v2.db"
    db_url = f"sqlite:///{db_path}"
    db.init_db(db_url)
    return BitunixFuturesObserver(
        db_url=db_url,
        bar_cache=_FakeBarCache(bars or []),
        trade_plan_config=trade_plan_config or StrategyConfig(),
        fee_config=fee_config or FeeConfig(),
    )


def test_v2_uses_swing_when_distance_in_range(tmp_path: Path):
    # ATR=200 (realistic BTC 3m). Bar 12 swings to low=79700 = 300 from entry
    # 80000 = 1.5×ATR, within [0.5, 2.5] × ATR → SL method "swing".
    base = 80_000.0
    bars = []
    for i in range(50):
        if i == 35:
            bars.append(_bar(price=base, low=base - 300.0, high=base + 5.0, ts_ms=i * 180_000))
        else:
            bars.append(_bar(price=base, low=base - 5.0, high=base + 5.0, ts_ms=i * 180_000))

    obs = _make_observer(tmp_path, bars)
    proposal, plan, structural = obs._build_proposal_v2(
        tier="STANDARD",
        trigger_side="bull",
        trigger_signal="spoon_bull",
        entry_price=base,
        account_equity=5_000.0,
        atr_3m=200.0,
    )
    assert proposal.proposed_order is not None
    assert plan.should_trade
    assert plan.sl_method == "swing"
    tp_plan = proposal.proposed_order.extra["tp_plan"]
    assert len(tp_plan) == 3
    assert proposal.proposed_order.extra["tp_plan_version"] == "v2"
    assert tp_plan[0]["stop_action"] == "move_to_breakeven"
    assert tp_plan[1]["stop_action"] == "move_to_tp1"
    assert tp_plan[2]["stop_action"] == "trail_atr"
    assert sum(leg["fraction"] for leg in tp_plan) == pytest.approx(1.0)
    assert structural["swing_low"] == pytest.approx(base - 300.0)


def test_v2_falls_back_to_atr_when_no_swing(tmp_path: Path):
    bars = [_bar(80_000.0, ts_ms=i * 180_000) for i in range(50)]
    obs = _make_observer(tmp_path, bars)
    proposal, plan, _ = obs._build_proposal_v2(
        tier="STANDARD",
        trigger_side="bull",
        trigger_signal="spoon_bull",
        entry_price=80_000.0,
        account_equity=5_000.0,
        atr_3m=200.0,
    )
    assert proposal.proposed_order is not None
    assert plan.sl_method == "atr_fallback"


def test_v2_returns_skip_when_swing_too_close(tmp_path: Path):
    # ATR=200. Swing distance < 100 (0.5×ATR) → swing_too_close.
    base = 80_000.0
    bars = []
    for i in range(50):
        if i == 35:
            bars.append(_bar(price=base, low=base - 50.0, high=base + 5.0, ts_ms=i * 180_000))
        else:
            bars.append(_bar(price=base, low=base - 5.0, high=base + 5.0, ts_ms=i * 180_000))
    obs = _make_observer(tmp_path, bars)
    proposal, plan, _ = obs._build_proposal_v2(
        tier="STANDARD",
        trigger_side="bull",
        trigger_signal="spoon_bull",
        entry_price=base,
        account_equity=5_000.0,
        atr_3m=200.0,
    )
    assert proposal.proposed_order is None
    assert plan.skip_reason == "swing_too_close"


def test_v2_unknown_tier_returns_skip(tmp_path: Path):
    obs = _make_observer(tmp_path, [])
    proposal, plan, _ = obs._build_proposal_v2(
        tier="MYSTERY", trigger_side="bull", trigger_signal="x",
        entry_price=80_000.0, account_equity=5_000.0, atr_3m=50.0,
    )
    assert proposal.proposed_order is None
    assert plan.skip_reason == "tier_not_sized"


def test_v2_zero_equity_returns_skip(tmp_path: Path):
    obs = _make_observer(tmp_path, [])
    proposal, plan, _ = obs._build_proposal_v2(
        tier="STANDARD", trigger_side="bull", trigger_signal="x",
        entry_price=80_000.0, account_equity=0.0, atr_3m=50.0,
    )
    assert proposal.proposed_order is None
    assert plan.skip_reason == "account_equity_le_0"


def test_log_trade_plan_decision_writes_audit_row(tmp_path: Path):
    db_path = tmp_path / "audit.db"
    db_url = f"sqlite:///{db_path}"
    db.init_db(db_url)
    obs = BitunixFuturesObserver(
        db_url=db_url,
        trade_plan_config=StrategyConfig(),
        fee_config=FeeConfig(),
    )

    plan = TradePlan(
        entry=80_000.0, stop_loss=0.0, tp1=0.0, tp2=0.0, tp3=0.0,
        sl_method="", tp2_method="", risk_per_unit=0.0,
        skip_reason="swing_too_close",
    )

    class _Side:
        value = "buy"

    class _Tier:
        value = "STANDARD"

    class _Verdict:
        side = _Side()
        tier = _Tier()

    obs._log_trade_plan_decision(
        payload={"signal": "spoon_bull"},
        plan=plan,
        structural_inputs={"swing_low": 79_900.0},
        verdict_score=_Verdict(),
    )

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT kind, payload_json FROM audit_event WHERE kind = ?",
        ("trade_plan_decision",),
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    payload = json.loads(rows[0][1])
    assert payload["skip_reason"] == "swing_too_close"
    assert payload["should_trade"] is False
    assert payload["inputs"]["swing_low"] == 79_900.0
