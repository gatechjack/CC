"""B-ARM #2: 401/429 on the LIVE combo submit → re-auth (401) / back-off (429),
then RECONCILE by ref_id (fallback: legs+qty+recency). Book ONLY a confirmed fill;
never synthesize, never blind-retry (no double-place).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from trading_corp.brokers.robinhood import (
    RobinhoodBroker,
    RobinhoodComboPending,
    RobinhoodOrderError,
)
from trading_corp.persistence.models import ProposedOrder


def _make_broker() -> RobinhoodBroker:
    b = RobinhoodBroker(username="x", password="y", account_filter="joint")
    b._connected = True                    # type: ignore[attr-defined]
    b._account_number = "ACCT-123"         # type: ignore[attr-defined]
    b._RATE_LIMIT_BACKOFF_S = 0.0          # no real sleep in tests
    return b


def _roll_legs(combo_id: str = "roll-1"):
    def _leg(side, effect, strike, exp, limit):
        return ProposedOrder(
            strategy="robinhood_pmcc", symbol="RKLB", side=side,  # type: ignore[arg-type]
            qty=1.0, order_type="limit", limit_price=limit,
            extra={"is_option": True, "is_multi_leg": True, "combo_id": combo_id,
                   "combo_direction": "credit", "net_limit_price": 1.14,
                   "underlying": "RKLB", "expiration": exp, "strike": strike,
                   "option_type": "call", "position_effect": effect, "ratio_quantity": 1},
        )
    return [_leg("buy", "close", 74.0, "2026-07-24", 0.03),
            _leg("sell", "open", 75.0, "2026-07-31", 1.25)]


def _rh_order(ref_id="REF-1", state="filled", created_at=None):
    """A recent RH option order that matches _roll_legs (legs REVERSED vs submit,
    exercising identity attribution). created_at defaults to 'now' for recency."""
    return {
        "id": "RH-1", "ref_id": ref_id, "state": state, "quantity": "1.00000",
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "legs": [
            {"option_type": "call", "expiration_date": "2026-07-31",
             "strike_price": "75.0000", "price": "1.20"},     # sell leg, first
            {"option_type": "call", "expiration_date": "2026-07-24",
             "strike_price": "74.0000", "price": "0.03"},     # buy leg, second
        ],
    }


@pytest.mark.asyncio
async def test_401_reauth_then_reconcile_finds_and_books():
    b = _make_broker()
    order = _rh_order("REF-1", "filled")
    with patch.object(b, "_submit_spread_with_ref_id", return_value={}), \
         patch.object(b, "_auth_is_401", new=AsyncMock(return_value=True)), \
         patch.object(b, "_attempt_reauth", new=AsyncMock(return_value=True)) as reauth, \
         patch.object(b, "_recent_option_orders", new=AsyncMock(return_value=[order])), \
         patch.object(b, "_await_terminal_option_order", new=AsyncMock(return_value=order)):
        fills = await b.place_multi_leg(_roll_legs(), ref_id="REF-1")
    reauth.assert_awaited()                          # re-authed BEFORE reconcile
    assert {f.side: f.price for f in fills} == {"buy": 0.03, "sell": 1.20}


@pytest.mark.asyncio
async def test_401_reconcile_not_found_books_nothing_and_raises_pending():
    b = _make_broker()
    with patch.object(b, "_submit_spread_with_ref_id", return_value={}), \
         patch.object(b, "_auth_is_401", new=AsyncMock(return_value=True)), \
         patch.object(b, "_attempt_reauth", new=AsyncMock(return_value=True)), \
         patch.object(b, "_recent_option_orders", new=AsyncMock(return_value=[])):
        with pytest.raises(RobinhoodComboPending):
            await b.place_multi_leg(_roll_legs(), ref_id="REF-1")


@pytest.mark.asyncio
async def test_429_backoff_then_reconcile_books_confirmed_fill():
    b = _make_broker()
    throttled = {"detail": "Request was throttled. Expected available in 1 second."}
    order = _rh_order("REF-1", "filled")
    with patch.object(b, "_submit_spread_with_ref_id", return_value=throttled), \
         patch.object(b, "_auth_is_401", new=AsyncMock(return_value=False)), \
         patch.object(b, "_recent_option_orders", new=AsyncMock(return_value=[order])), \
         patch.object(b, "_await_terminal_option_order", new=AsyncMock(return_value=order)):
        fills = await b.place_multi_leg(_roll_legs(), ref_id="REF-1")
    assert {f.side: f.price for f in fills} == {"buy": 0.03, "sell": 1.20}


@pytest.mark.asyncio
async def test_no_double_place_submit_fires_exactly_once():
    b = _make_broker()
    with patch.object(b, "_submit_spread_with_ref_id", return_value={}) as submit, \
         patch.object(b, "_auth_is_401", new=AsyncMock(return_value=True)), \
         patch.object(b, "_attempt_reauth", new=AsyncMock(return_value=True)), \
         patch.object(b, "_recent_option_orders", new=AsyncMock(return_value=[])):
        with pytest.raises(RobinhoodComboPending):
            await b.place_multi_leg(_roll_legs(), ref_id="REF-1")
    submit.assert_called_once()                      # NEVER re-submitted (no double-place)


@pytest.mark.asyncio
async def test_genuine_reject_still_raises_order_error():
    """No id + session alive + not throttled = a real RH reject → RobinhoodOrderError
    (unchanged), NOT the reconcile path."""
    b = _make_broker()
    with patch.object(b, "_submit_spread_with_ref_id", return_value={"detail": "invalid strike"}), \
         patch.object(b, "_auth_is_401", new=AsyncMock(return_value=False)):
        with pytest.raises(RobinhoodOrderError):
            await b.place_multi_leg(_roll_legs(), ref_id="REF-1")


@pytest.mark.asyncio
async def test_reconcile_fallback_matches_by_legs_when_ref_id_absent():
    """If RH doesn't surface ref_id on the order payload, reconcile still matches by
    (leg identity + qty + recency)."""
    b = _make_broker()
    order = _rh_order(ref_id=None, state="filled")
    order.pop("ref_id", None)                        # RH omitted ref_id
    with patch.object(b, "_submit_spread_with_ref_id", return_value={}), \
         patch.object(b, "_auth_is_401", new=AsyncMock(return_value=True)), \
         patch.object(b, "_attempt_reauth", new=AsyncMock(return_value=True)), \
         patch.object(b, "_recent_option_orders", new=AsyncMock(return_value=[order])), \
         patch.object(b, "_await_terminal_option_order", new=AsyncMock(return_value=order)):
        fills = await b.place_multi_leg(_roll_legs(), ref_id="REF-1")
    assert {f.side: f.price for f in fills} == {"buy": 0.03, "sell": 1.20}


@pytest.mark.asyncio
async def test_reconcile_found_but_not_filled_books_nothing():
    """Matched order exists but is not terminal-filled → book NOTHING (raise pending)."""
    b = _make_broker()
    order = _rh_order("REF-1", "confirmed")          # placed, not filled
    with patch.object(b, "_submit_spread_with_ref_id", return_value={}), \
         patch.object(b, "_auth_is_401", new=AsyncMock(return_value=True)), \
         patch.object(b, "_attempt_reauth", new=AsyncMock(return_value=True)), \
         patch.object(b, "_recent_option_orders", new=AsyncMock(return_value=[order])), \
         patch.object(b, "_await_terminal_option_order", new=AsyncMock(return_value=order)):
        with pytest.raises(RobinhoodComboPending):
            await b.place_multi_leg(_roll_legs(), ref_id="REF-1")


def test_looks_rate_limited_signatures():
    f = RobinhoodBroker._looks_rate_limited
    assert f({"detail": "Request was throttled."}) is True
    assert f("HTTP 429 Too Many Requests") is True
    assert f({"detail": "invalid strike"}) is False
    assert f({}) is False
