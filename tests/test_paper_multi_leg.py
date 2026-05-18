"""Tests for PaperExecutionBroker.place_multi_leg and .get_option_greeks.

The simulator runs entirely in-process — no network, no real broker.
We construct a fake `live` broker that mocks the Greeks lookup, and a
fresh PaperBroker as the executor (its place_order is unused for combos,
but the wrapper still calls connect on it).

Coverage parallels the user's step-6 checklist:
- happy-path fill at exactly the limit
- fill better than the limit (slippage works in our favor)
- no-fill when slippage pushes net past the limit
- combo cohesion rejection (sample of the 8 cases — full coverage lives
  in test_robinhood_multi_leg.py since the validator is shared)
- missing mid data falls back to limit_price
- get_option_greeks happy path + fallback when live lacks the method
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.brokers.base import Broker
from trading_corp.brokers.paper import PaperBroker, PaperExecutionBroker
from trading_corp.persistence.models import ProposedOrder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeLiveBroker(Broker):
    """Minimal stand-in for a live broker that supplies option Greeks.

    We bypass Broker abstract methods we don't exercise (connect /
    disconnect / snapshot / quote / place_order / cancel_order) by
    providing async no-op stubs. `get_option_greeks` is the only method
    the multi-leg simulator actually calls.
    """
    name = "fake-live"
    paper = False

    def __init__(self, greeks_by_id: dict[str, dict] | None = None,
                 raise_on_ids: set[str] | None = None) -> None:
        self._greeks = greeks_by_id or {}
        self._raise_on = raise_on_ids or set()

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def snapshot(self): ...
    async def quote(self, symbol: str) -> float:
        return 0.0
    async def place_order(self, order: ProposedOrder):
        raise NotImplementedError
    async def cancel_order(self, order_id: str) -> bool:
        return False

    async def get_option_greeks(self, option_id: str) -> dict:
        if option_id in self._raise_on:
            raise ConnectionError(f"forced failure for {option_id}")
        return self._greeks.get(option_id, {
            "delta": None, "gamma": None, "theta": None,
            "vega": None,  "iv": None,    "mark_price": None,
        })


def _make_broker(greeks: dict[str, dict] | None = None,
                 raise_on_ids: set[str] | None = None) -> PaperExecutionBroker:
    live = _FakeLiveBroker(greeks_by_id=greeks, raise_on_ids=raise_on_ids)
    paper = PaperBroker(starting_equity=100_000.0)
    return PaperExecutionBroker(live=live, paper=paper)


def _leg(
    *,
    role: str,
    side: str,
    strike: float,
    option_type: str,
    option_id: str | None = None,
    combo_id: str = "combo-1",
    net_limit: float = 1.20,
    direction: str = "credit",
    underlying: str = "SPY",
    qty: int = 1,
    limit_price: float = 0.50,
    expiration: str = "2026-06-19",
    effect: str = "open",
    slippage: float | None = None,
) -> ProposedOrder:
    extra = {
        "is_option": True,
        "is_multi_leg": True,
        "combo_id": combo_id,
        "combo_role": role,
        "combo_direction": direction,
        "net_limit_price": net_limit,
        "underlying": underlying,
        "expiration": expiration,
        "strike": strike,
        "option_type": option_type,
        "position_effect": effect,
        "ratio_quantity": 1,
    }
    if option_id is not None:
        extra["option_id"] = option_id
    if slippage is not None:
        extra["paper_per_leg_slippage_dollars"] = slippage
    return ProposedOrder(
        strategy="robinhood_joint_iron_condor",
        symbol=underlying,
        side=side,    # type: ignore[arg-type]
        qty=float(qty),
        order_type="limit",
        limit_price=limit_price,
        extra=extra,
    )


def _standard_ic_legs(
    *,
    combo_id: str = "combo-1",
    net_limit: float = 1.20,
    mids: tuple[float, float, float, float] = (0.60, 0.25, 0.65, 0.20),
    slippage: float | None = None,
) -> tuple[list[ProposedOrder], dict[str, dict]]:
    """Return (legs, greeks_dict) for a standard 4-leg SPY iron condor.

    Order: short put (sell), long put (buy), short call (sell), long call (buy).
    mids supplies the live mark_price for each leg in that order.
    """
    legs = [
        _leg(role="short_put",  side="sell", option_type="put",
             strike=430.0, option_id="OPT-SP", combo_id=combo_id,
             net_limit=net_limit, slippage=slippage),
        _leg(role="long_put",   side="buy",  option_type="put",
             strike=427.0, option_id="OPT-LP", combo_id=combo_id,
             net_limit=net_limit, slippage=slippage),
        _leg(role="short_call", side="sell", option_type="call",
             strike=470.0, option_id="OPT-SC", combo_id=combo_id,
             net_limit=net_limit, slippage=slippage),
        _leg(role="long_call",  side="buy",  option_type="call",
             strike=473.0, option_id="OPT-LC", combo_id=combo_id,
             net_limit=net_limit, slippage=slippage),
    ]
    greeks = {
        "OPT-SP": {"mark_price": mids[0]},
        "OPT-LP": {"mark_price": mids[1]},
        "OPT-SC": {"mark_price": mids[2]},
        "OPT-LC": {"mark_price": mids[3]},
    }
    return legs, greeks


# ---------------------------------------------------------------------------
# place_multi_leg — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_combo_fills_exactly_at_limit(caplog):
    """Mids chosen so that adverse slippage lands net == limit exactly.

    Sell shorts at 0.60 and 0.65 mid → fill 0.57 and 0.62 (mid - 0.03).
    Buy longs at 0.25 and 0.20 mid → fill 0.28 and 0.23 (mid + 0.03).
    Net credit = (0.57 + 0.62) - (0.28 + 0.23) = 0.68.
    """
    legs, greeks = _standard_ic_legs(
        net_limit=0.68,
        mids=(0.60, 0.25, 0.65, 0.20),
    )
    b = _make_broker(greeks=greeks)
    await b.connect()

    with caplog.at_level("INFO"):
        fills = await b.place_multi_leg(legs)

    assert len(fills) == 4
    assert [round(f.price, 2) for f in fills] == [0.57, 0.28, 0.62, 0.23]
    assert all(f.venue == "paper-exec" for f in fills)
    assert any("paper_combo_filled" in r.message for r in caplog.records)
    # actual == limit → slippage-vs-limit is 0.
    assert any("actual_vs_limit_slippage_dollars=0.0000" in r.message
               for r in caplog.records)


@pytest.mark.asyncio
async def test_paper_combo_fills_better_than_limit(caplog):
    """Mids favor us: simulated net credit > limit. Fill happens.

    Net credit at mids = (0.80 + 0.85) - (0.20 + 0.15) = 1.30 gross.
    With $0.03 slippage on each leg: (0.77 + 0.82) - (0.23 + 0.18) = 1.18.
    Limit is 1.00 — we collect 1.18, exceeding the limit by 0.18.
    """
    legs, greeks = _standard_ic_legs(
        net_limit=1.00,
        mids=(0.80, 0.20, 0.85, 0.15),
    )
    b = _make_broker(greeks=greeks)
    await b.connect()

    with caplog.at_level("INFO"):
        fills = await b.place_multi_leg(legs)

    assert len(fills) == 4
    assert any("paper_combo_filled" in r.message for r in caplog.records)
    # actual_net is 1.18; limit 1.00; slippage_vs_limit = 0.18.
    assert any("actual_vs_limit_slippage_dollars=0.1800" in r.message
               for r in caplog.records)


@pytest.mark.asyncio
async def test_paper_combo_does_not_fill_when_slippage_eats_credit(caplog):
    """Slippage pushes net under the limit → no fill.

    Net at mid = (0.50 + 0.55) - (0.30 + 0.25) = 0.50 gross.
    With $0.03 slippage per leg: 0.47 + 0.52 - 0.33 - 0.28 = 0.38.
    Limit is 0.50; 0.38 < 0.50 → no fill.
    """
    legs, greeks = _standard_ic_legs(
        net_limit=0.50,
        mids=(0.50, 0.30, 0.55, 0.25),
    )
    b = _make_broker(greeks=greeks)
    await b.connect()

    with caplog.at_level("INFO"):
        fills = await b.place_multi_leg(legs)

    assert fills == []
    assert any("paper_combo_unfilled" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_paper_combo_empty_input_returns_empty():
    b = _make_broker()
    assert await b.place_multi_leg([]) == []


# ---------------------------------------------------------------------------
# Debit-direction combo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_combo_debit_fills_when_paid_under_limit(caplog):
    """Debit combo (long spread close): limit is max we'll pay.

    Legs: buy short_put @ 0.30 mid, sell long_put @ 0.10 mid
    (closing a put credit spread → buy back the short, sell off the long).
    With slippage: buy fills at 0.33, sell fills at 0.07.
    Cashflow = 0.07 - 0.33 = -0.26 (we pay 0.26).
    Debit-as-positive = 0.26. Limit = 0.40 → 0.26 ≤ 0.40 → fills.
    """
    legs = [
        ProposedOrder(
            strategy="robinhood_joint_iron_condor", symbol="SPY",
            side="buy", qty=1.0, order_type="limit", limit_price=0.30,
            extra={
                "is_option": True, "is_multi_leg": True,
                "combo_id": "combo-debit",
                "combo_direction": "debit",
                "net_limit_price": 0.40,
                "underlying": "SPY", "expiration": "2026-06-19",
                "strike": 430.0, "option_type": "put",
                "position_effect": "close", "option_id": "OPT-A",
            },
        ),
        ProposedOrder(
            strategy="robinhood_joint_iron_condor", symbol="SPY",
            side="sell", qty=1.0, order_type="limit", limit_price=0.10,
            extra={
                "is_option": True, "is_multi_leg": True,
                "combo_id": "combo-debit",
                "combo_direction": "debit",
                "net_limit_price": 0.40,
                "underlying": "SPY", "expiration": "2026-06-19",
                "strike": 427.0, "option_type": "put",
                "position_effect": "close", "option_id": "OPT-B",
            },
        ),
    ]
    greeks = {"OPT-A": {"mark_price": 0.30}, "OPT-B": {"mark_price": 0.10}}
    b = _make_broker(greeks=greeks)
    await b.connect()

    with caplog.at_level("INFO"):
        fills = await b.place_multi_leg(legs)

    assert len(fills) == 2
    # Buy fills at 0.33, sell at 0.07.
    assert fills[0].price == pytest.approx(0.33)
    assert fills[1].price == pytest.approx(0.07)
    assert any("paper_combo_filled" in r.message and "direction=debit" in r.message
               for r in caplog.records)


@pytest.mark.asyncio
async def test_paper_combo_debit_unfilled_when_pays_more_than_limit(caplog):
    """Same shape, tighter limit → no fill."""
    legs = [
        ProposedOrder(
            strategy="ic", symbol="SPY", side="buy", qty=1.0,
            order_type="limit", limit_price=0.30,
            extra={
                "is_option": True, "is_multi_leg": True,
                "combo_id": "c2", "combo_direction": "debit",
                "net_limit_price": 0.20, "underlying": "SPY",
                "expiration": "2026-06-19", "strike": 430.0,
                "option_type": "put", "position_effect": "close",
                "option_id": "X",
            },
        ),
        ProposedOrder(
            strategy="ic", symbol="SPY", side="sell", qty=1.0,
            order_type="limit", limit_price=0.10,
            extra={
                "is_option": True, "is_multi_leg": True,
                "combo_id": "c2", "combo_direction": "debit",
                "net_limit_price": 0.20, "underlying": "SPY",
                "expiration": "2026-06-19", "strike": 427.0,
                "option_type": "put", "position_effect": "close",
                "option_id": "Y",
            },
        ),
    ]
    greeks = {"X": {"mark_price": 0.30}, "Y": {"mark_price": 0.10}}
    b = _make_broker(greeks=greeks)
    await b.connect()

    with caplog.at_level("INFO"):
        fills = await b.place_multi_leg(legs)

    assert fills == []
    assert any("paper_combo_unfilled" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Mid-fetch fallbacks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_combo_falls_back_to_limit_price_when_option_id_missing():
    """Legs without `extra.option_id` use `order.limit_price` as mid."""
    legs, _ = _standard_ic_legs(net_limit=0.60)
    for o in legs:
        o.extra.pop("option_id")
        # Use distinct limit_prices to verify per-leg fallback works.
    legs[0].limit_price = 0.60
    legs[1].limit_price = 0.25
    legs[2].limit_price = 0.65
    legs[3].limit_price = 0.20
    b = _make_broker(greeks={})       # no live greeks at all
    await b.connect()

    fills = await b.place_multi_leg(legs)
    # cashflow: (0.57 + 0.62) - (0.28 + 0.23) = 0.68 ≥ limit 0.60 → fill.
    assert len(fills) == 4
    assert [round(f.price, 2) for f in fills] == [0.57, 0.28, 0.62, 0.23]


@pytest.mark.asyncio
async def test_paper_combo_falls_back_when_live_get_greeks_raises(caplog):
    """ConnectionError from get_option_greeks → fall back to limit_price."""
    legs, _ = _standard_ic_legs(net_limit=0.60)
    legs[0].limit_price = 0.60
    legs[1].limit_price = 0.25
    legs[2].limit_price = 0.65
    legs[3].limit_price = 0.20

    b = _make_broker(
        greeks={},
        raise_on_ids={"OPT-SP", "OPT-LP", "OPT-SC", "OPT-LC"},
    )
    await b.connect()
    with caplog.at_level("WARNING"):
        fills = await b.place_multi_leg(legs)
    assert len(fills) == 4
    # Warnings logged for each leg.
    warns = [r for r in caplog.records if "get_option_greeks" in r.message
             and "falling back" in r.message]
    assert len(warns) == 4


@pytest.mark.asyncio
async def test_paper_combo_falls_back_when_mid_is_none():
    """Live broker returns mark_price=None → fall back to limit_price."""
    legs, _ = _standard_ic_legs(net_limit=0.60)
    legs[0].limit_price = 0.60
    legs[1].limit_price = 0.25
    legs[2].limit_price = 0.65
    legs[3].limit_price = 0.20

    none_greeks = {
        opt: {"mark_price": None}
        for opt in ("OPT-SP", "OPT-LP", "OPT-SC", "OPT-LC")
    }
    b = _make_broker(greeks=none_greeks)
    await b.connect()
    fills = await b.place_multi_leg(legs)
    assert len(fills) == 4


@pytest.mark.asyncio
async def test_paper_combo_falls_back_when_mid_is_zero():
    """Live returns mark_price=0 → treated as missing, fall back to limit."""
    legs, _ = _standard_ic_legs(net_limit=0.60)
    legs[0].limit_price = 0.60
    legs[1].limit_price = 0.25
    legs[2].limit_price = 0.65
    legs[3].limit_price = 0.20

    zero_greeks = {
        opt: {"mark_price": 0.0}
        for opt in ("OPT-SP", "OPT-LP", "OPT-SC", "OPT-LC")
    }
    b = _make_broker(greeks=zero_greeks)
    await b.connect()
    fills = await b.place_multi_leg(legs)
    assert len(fills) == 4


# ---------------------------------------------------------------------------
# Slippage knob respected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_combo_respects_custom_slippage_per_leg():
    """When extra.paper_per_leg_slippage_dollars is set, that value is used."""
    legs, greeks = _standard_ic_legs(
        net_limit=0.60,
        mids=(0.60, 0.25, 0.65, 0.20),
        slippage=0.10,           # bigger slippage than the 0.03 default
    )
    b = _make_broker(greeks=greeks)
    await b.connect()
    fills = await b.place_multi_leg(legs)
    # Net = (0.50 + 0.55) - (0.35 + 0.30) = 0.40 < limit 0.60 → no fill.
    assert fills == []


# ---------------------------------------------------------------------------
# Combo cohesion rejection — sample (full coverage in robinhood test file)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_combo_rejects_mixed_combo_ids():
    legs, greeks = _standard_ic_legs()
    legs[2].extra["combo_id"] = "other"
    b = _make_broker(greeks=greeks)
    await b.connect()
    with pytest.raises(ValueError, match="mixed combo_ids"):
        await b.place_multi_leg(legs)


@pytest.mark.asyncio
async def test_paper_combo_rejects_invalid_direction():
    legs, greeks = _standard_ic_legs()
    for o in legs:
        o.extra["combo_direction"] = "wat"
    b = _make_broker(greeks=greeks)
    await b.connect()
    with pytest.raises(ValueError, match="combo_direction must be"):
        await b.place_multi_leg(legs)


# ---------------------------------------------------------------------------
# get_option_greeks delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_option_greeks_delegates_to_live():
    b = _make_broker(greeks={"OPT-1": {
        "delta": 0.16, "gamma": 0.008, "theta": -0.05,
        "vega": 0.12, "iv": 0.23, "mark_price": 1.47,
    }})
    await b.connect()
    out = await b.get_option_greeks("OPT-1")
    assert out["delta"] == 0.16
    assert out["mark_price"] == 1.47


@pytest.mark.asyncio
async def test_get_option_greeks_returns_all_none_when_live_lacks_method():
    """If wrapped live broker doesn't implement Greeks, return all-None."""

    class _NoGreeksLive(Broker):
        name = "no-greeks"
        paper = False
        async def connect(self): ...
        async def disconnect(self): ...
        async def snapshot(self): ...
        async def quote(self, symbol): return 0.0
        async def place_order(self, order): raise NotImplementedError
        async def cancel_order(self, order_id): return False
        # NOT overriding get_option_greeks — inherits the ABC default
        # which raises. Wrapper's hasattr check sees the inherited method
        # is present, so this test forces a different shape: simulate
        # a "doesn't have it" broker by deleting the attr.

    live = _NoGreeksLive()
    # Delete the inherited method on this instance so hasattr returns False.
    # (Class still has it; instance-level delete via __dict__ doesn't work
    # on methods, so we use a different approach — set to a non-callable
    # is brittle. Cleaner: monkey-patch a class without the method via
    # MagicMock(spec_set=[...]) on the broker side.)
    fake_live = MagicMock(spec=["connect", "disconnect", "snapshot", "quote",
                                "place_order", "cancel_order"])
    fake_live.connect = AsyncMock()
    fake_live.disconnect = AsyncMock()
    fake_live.snapshot = AsyncMock()
    fake_live.quote = AsyncMock(return_value=0.0)
    paper = PaperBroker(starting_equity=100_000.0)
    b = PaperExecutionBroker(live=fake_live, paper=paper)
    await b.connect()

    out = await b.get_option_greeks("OPT-X")
    assert out == {
        "delta": None, "gamma": None, "theta": None,
        "vega": None,  "iv": None,    "mark_price": None,
    }
