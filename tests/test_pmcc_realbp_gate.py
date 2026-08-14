"""#4 (2026-07-24): the combo risk gate sees REAL buying power for a genuinely
BP-consuming OPEN, but a defensive/credit roll or protective close is NOT blocked by
low BP (carve-out). Tiny-BP account still allows a protective/credit roll; a
BP-consuming open is gated.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from trading_corp.agents.strategies._pmcc_combo import (
    _combo_is_bp_consuming,
    propose_pmcc_combo,
)
from trading_corp.persistence.models import ProposedOrder


def _leg(side, effect, direction="credit", combo_id="c1"):
    return ProposedOrder(
        strategy="robinhood_pmcc", symbol="RKLB", side=side,  # type: ignore[arg-type]
        qty=1.0, order_type="limit", limit_price=1.0,
        extra={"is_option": True, "is_multi_leg": True, "combo_id": combo_id,
               "combo_direction": direction, "net_limit_price": 1.0,
               "underlying": "RKLB", "expiration": "2026-07-31", "strike": 75.0,
               "option_type": "call", "position_effect": effect, "ratio_quantity": 1,
               "action": effect},
    )


def _roll_short():   # buy-to-close + sell-to-open, net credit (defensive)
    return [_leg("buy", "close", "credit"), _leg("sell", "open", "credit")]


def _open_debit():   # a fresh net-debit OPEN with no close leg (BP-consuming)
    return [_leg("buy", "open", "debit"), _leg("sell", "open", "debit")]


def _close_all():    # all-close protective exit
    return [_leg("buy", "close", "debit"), _leg("sell", "close", "debit")]


def test_bp_consuming_classification():
    assert _combo_is_bp_consuming(_roll_short()) is False    # has close -> defensive
    assert _combo_is_bp_consuming(_close_all()) is False     # all close -> protective
    assert _combo_is_bp_consuming(_open_debit()) is True     # fresh debit open


class _Verdict:
    def __init__(self, verdict, reason=""):
        self.verdict = verdict
        self.reason = reason


class _RiskAgent:
    def __init__(self, reject_below=None):
        self.reject_below = reject_below
        self.seen_equity = None

    def evaluate(self, leg, account, strat_state, regime, x, db_url=None):
        self.seen_equity = account.equity
        if self.reject_below is not None and account.equity < self.reject_below:
            return _Verdict("reject", "insufficient BP")
        return _Verdict("ok")


class _Logger:
    def log_event(self, *a, **k):
        pass


class _Registry:
    def __init__(self):
        self.proposed = []

    def propose(self, combo_id, combo, **k):
        self.proposed.append(combo_id)


@pytest.mark.asyncio
async def test_roll_short_uses_permissive_equity_not_blocked_on_tiny_bp():
    ra = _RiskAgent(reject_below=5000)          # would reject at real $1,160
    reg = _Registry()
    with patch("trading_corp.agents.strategies._pmcc_combo.StrategyState") as SS:
        SS.from_persistence.return_value = object()
        ok = await propose_pmcc_combo(
            _roll_short(), risk_agent=ra, logger_agent=_Logger(),
            pending_combo_registry=reg, account_equity=1160.0)
    assert ra.seen_equity == 100_000.0          # carve-out: permissive, NOT $1,160
    assert ok is True and reg.proposed == ["c1"]


@pytest.mark.asyncio
async def test_bp_consuming_open_gated_on_real_equity():
    ra = _RiskAgent(reject_below=5000)
    reg = _Registry()
    with patch("trading_corp.agents.strategies._pmcc_combo.StrategyState") as SS:
        SS.from_persistence.return_value = object()
        ok = await propose_pmcc_combo(
            _open_debit(), risk_agent=ra, logger_agent=_Logger(),
            pending_combo_registry=reg, account_equity=1160.0)
    assert ra.seen_equity == 1160.0             # real equity for a BP-consuming open
    assert ok is False and reg.proposed == []   # gated by the risk reject


@pytest.mark.asyncio
async def test_bp_consuming_open_allowed_when_equity_sufficient():
    ra = _RiskAgent(reject_below=5000)
    reg = _Registry()
    with patch("trading_corp.agents.strategies._pmcc_combo.StrategyState") as SS:
        SS.from_persistence.return_value = object()
        ok = await propose_pmcc_combo(
            _open_debit(), risk_agent=ra, logger_agent=_Logger(),
            pending_combo_registry=reg, account_equity=50_000.0)
    assert ra.seen_equity == 50_000.0 and ok is True
