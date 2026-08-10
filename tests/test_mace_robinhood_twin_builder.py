"""MANDATED twin-builder consistency test (plan § Additive robinhood.py change,
edit D condition).

The additive edit DUPLICATES the spread-payload build into a new
`place_multi_leg_resting` (GTC resting close) rather than extracting the shared
core — an accepted trade-off whose CONDITION is exactly this test: for the SAME
condor legs, `place_multi_leg` (the deterministic-ref_id submit path) and
`place_multi_leg_resting` must build a BYTE-IDENTICAL spread POST payload, with
ONLY `time_in_force` differing (gfd day-order vs gtc resting). If this ever
diverges, the two builders have drifted and the duplication is no longer safe.

FUTURE-EXTRACTION NOTE: the builders unify when the shared condor core is
extracted (plan § Future extraction seam) — at which point this test guards the
extraction instead of the duplication.

Red until the gated brokers/robinhood.py edit lands `place_multi_leg_resting`;
green immediately after (Checkpoint 3 gate).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import robin_stocks.robinhood as rs  # type: ignore

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from trading_corp.brokers.robinhood import RobinhoodBroker
from trading_corp.persistence.models import ProposedOrder


def _closing_condor_legs() -> list[ProposedOrder]:
    """The flatten payload for one SPY condor (buy back shorts, sell longs) —
    a net-debit close, the exact shape both builders receive."""
    def leg(side, otype, strike):
        return ProposedOrder(
            strategy="robinhood_mace", symbol="SPY", side=side, qty=1.0,
            order_type="limit", limit_price=2.04,
            extra={"is_option": True, "is_multi_leg": True, "combo_id": "mace-twin",
                   "combo_direction": "debit", "net_limit_price": 2.04, "underlying": "SPY",
                   "expiration": "2026-09-18", "strike": strike, "option_type": otype,
                   "position_effect": "close", "ratio_quantity": 1})
    return [leg("buy", "put", 585.0), leg("sell", "put", 582.0),
            leg("buy", "call", 615.0), leg("sell", "call", 618.0)]


def _fake_id_for_option(underlying, expiration, strike, option_type):
    return f"OID-{underlying}-{expiration}-{strike}-{option_type}"


def _make_broker() -> RobinhoodBroker:
    b = RobinhoodBroker(username="x", password="y", account_filter="joint")
    b._connected = True              # type: ignore[attr-defined]
    b._account_number = "ACCT-123"   # type: ignore[attr-defined]
    return b


async def _capture_submit_payload(coro_factory) -> dict:
    """Run a submit coroutine, capturing the single request_post payload."""
    cap: dict = {}

    def fake_request_post(url, payload, json=True, jsonify_data=True):
        cap["payload"] = payload
        return {"id": "RH-TWIN", "state": "filled",
                "legs": [{"price": "0.51"} for _ in range(4)]}

    with patch.object(rs.orders, "id_for_option", new=_fake_id_for_option), \
         patch.object(rs.orders, "option_instruments_url",
                      new=lambda oid: f"https://api.robinhood.com/options/instruments/{oid}/"), \
         patch.object(rs.orders, "load_account_profile",
                      new=lambda account_number=None, info=None:
                      f"https://api.robinhood.com/accounts/{account_number}/"), \
         patch.object(rs.orders, "option_orders_url",
                      new=lambda account_number=None: "https://api.robinhood.com/options/orders/"), \
         patch.object(rs.orders, "request_post", new=fake_request_post), \
         patch.object(rs.orders, "get_option_order_info",
                      new=lambda oid: {"id": oid, "state": "filled",
                                       "legs": [{"price": "0.51"} for _ in range(4)]}):
        await coro_factory()
    return cap["payload"]


@pytest.mark.asyncio
async def test_twin_builders_identical_payload_except_tif():
    legs = _closing_condor_legs()
    broker = _make_broker()

    payload_day = await _capture_submit_payload(
        lambda: broker.place_multi_leg(legs, ref_id="twin-1"))
    payload_rest = await _capture_submit_payload(
        lambda: broker.place_multi_leg_resting(legs, ref_id="twin-1", time_in_force="gtc"))

    # ONLY time_in_force may differ (gfd day-order vs gtc resting).
    assert payload_day["time_in_force"] == "gfd"
    assert payload_rest["time_in_force"] == "gtc"
    a = {k: v for k, v in payload_day.items() if k != "time_in_force"}
    b = {k: v for k, v in payload_rest.items() if k != "time_in_force"}
    assert a == b, "twin builders drifted — duplicated spread build is no longer safe"
