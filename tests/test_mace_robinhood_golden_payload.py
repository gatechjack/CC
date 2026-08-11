"""PMCC/IC golden-payload regression for the additive brokers/robinhood.py change
(MACE Phase 3, plan § Additive robinhood.py change spec).

The MACE additive edit adds optional combo-TIF / fill-timeout reads (from leg-0
extra) + two new methods. Existing PMCC/IC callers set NONE of the new extra
keys and never call the new methods, so their spread POST payload MUST be
byte-identical pre/post edit. This test captures the exact payload for
representative PMCC (2-leg roll) and IC (4-leg) call-shapes on BOTH submit paths
(legacy order_option_spread + deterministic-ref_id _submit_spread_with_ref_id)
and asserts equality against committed golden JSON.

The goldens were captured against the PRE-EDIT robinhood.py (LF-md5 5862d2e8).
Regenerate deliberately with:  python tests/test_mace_robinhood_golden_payload.py
(only when an intended payload change is being baselined — never to paper over a
regression). A missing golden fails the test (it does NOT self-seed at test time).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

# Repo root on sys.path so a bare `python tests/...py` generation run resolves
# trading_corp (pytest adds this via its rootdir; a __main__ run does not).
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
import robin_stocks.robinhood as rs  # type: ignore

from trading_corp.brokers.robinhood import RobinhoodBroker
from trading_corp.persistence.models import ProposedOrder

GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "mace"


# ── deterministic fakes for the ref_id payload path ──────────────────────

def _fake_id_for_option(underlying, expiration, strike, option_type):
    return f"OID-{underlying}-{expiration}-{strike}-{option_type}"


def _fake_instruments_url(oid):
    return f"https://api.robinhood.com/options/instruments/{oid}/"


def _fake_load_account_profile(account_number=None, info=None):
    return f"https://api.robinhood.com/accounts/{account_number}/"


def _fake_option_orders_url(account_number=None):
    return "https://api.robinhood.com/options/orders/"


def _make_broker() -> RobinhoodBroker:
    b = RobinhoodBroker(username="x", password="y", account_filter="joint")
    b._connected = True              # type: ignore[attr-defined]
    b._account_number = "ACCT-123"   # type: ignore[attr-defined]
    return b


# ── representative PMCC + IC call-shapes (no MACE extra keys set) ─────────

def _ic_legs() -> list[ProposedOrder]:
    def leg(role, side, otype, strike):
        return ProposedOrder(
            strategy="robinhood_joint_iron_condor", symbol="SPY", side=side,
            qty=1.0, order_type="limit", limit_price=0.50,
            extra={"is_option": True, "is_multi_leg": True, "combo_id": "ic-golden",
                   "combo_role": role, "combo_direction": "credit",
                   "net_limit_price": 1.20, "underlying": "SPY",
                   "expiration": "2026-06-19", "strike": strike, "option_type": otype,
                   "position_effect": "open", "ratio_quantity": 1})
    return [leg("short_put", "sell", "put", 430.0), leg("long_put", "buy", "put", 427.0),
            leg("short_call", "sell", "call", 470.0), leg("long_call", "buy", "call", 473.0)]


def _pmcc_legs() -> list[ProposedOrder]:
    def leg(side, effect, strike, exp, limit):
        return ProposedOrder(
            strategy="robinhood_pmcc", symbol="RKLB", side=side, qty=1.0,
            order_type="limit", limit_price=limit,
            extra={"is_option": True, "is_multi_leg": True, "combo_id": "roll-golden",
                   "combo_direction": "credit", "net_limit_price": 1.14,
                   "underlying": "RKLB", "expiration": exp, "strike": strike,
                   "option_type": "call", "position_effect": effect, "ratio_quantity": 1})
    return [leg("buy", "close", 74.0, "2026-07-24", 0.03),
            leg("sell", "open", 75.0, "2026-07-31", 1.25)]


# ── capture on both submit paths ─────────────────────────────────────────

async def _capture_legacy(legs) -> dict:
    """order_option_spread args (ref_id=None path)."""
    cap: dict = {}

    def fake_spread(direction, price, symbol, quantity, spread,
                    account_number=None, timeInForce="gfd", **kw):
        cap.update(direction=direction, price=price, symbol=symbol, quantity=quantity,
                   spread=spread, account_number=account_number, timeInForce=timeInForce)
        return {"id": "RH-GOLDEN", "state": "filled",
                "legs": [{"price": "0.50"} for _ in legs]}

    with patch.object(rs.orders, "order_option_spread", new=fake_spread):
        await _make_broker().place_multi_leg(legs)
    return cap


async def _capture_ref_id(legs) -> dict:
    """request_post payload (deterministic-ref_id path)."""
    cap: dict = {}

    def fake_request_post(url, payload, json=True, jsonify_data=True):
        cap["url"] = url
        cap["payload"] = payload
        return {"id": "RH-GOLDEN", "state": "filled",
                "legs": [{"price": "0.50"} for _ in legs]}

    with patch.object(rs.orders, "id_for_option", new=_fake_id_for_option), \
         patch.object(rs.orders, "option_instruments_url", new=_fake_instruments_url), \
         patch.object(rs.orders, "load_account_profile", new=_fake_load_account_profile), \
         patch.object(rs.orders, "option_orders_url", new=_fake_option_orders_url), \
         patch.object(rs.orders, "request_post", new=fake_request_post), \
         patch.object(rs.orders, "get_option_order_info",
                      new=lambda oid: {"id": oid, "state": "filled"}):
        await _make_broker().place_multi_leg(legs, ref_id="golden-ref-1")
    return cap


_SHAPES = {
    "ic_legacy": (_ic_legs, _capture_legacy),
    "pmcc_legacy": (_pmcc_legs, _capture_legacy),
    "ic_ref_id": (_ic_legs, _capture_ref_id),
    "pmcc_ref_id": (_pmcc_legs, _capture_ref_id),
}


def _golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


@pytest.mark.parametrize("name", sorted(_SHAPES))
@pytest.mark.asyncio
async def test_golden_payload_byte_identical(name):
    legs_fn, cap_fn = _SHAPES[name]
    gp = _golden_path(name)
    assert gp.exists(), (f"golden {gp} missing — regenerate deliberately with "
                         f"`python tests/test_mace_robinhood_golden_payload.py`")
    captured = await cap_fn(legs_fn())
    golden = json.loads(gp.read_text(encoding="utf-8"))
    # round-trip captured through JSON so tuple/float normalization matches the golden
    captured = json.loads(json.dumps(captured))
    assert captured == golden, f"{name}: payload drifted from golden {gp}"


def _write_goldens() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, (legs_fn, cap_fn) in _SHAPES.items():
        captured = asyncio.run(cap_fn(legs_fn()))
        _golden_path(name).write_text(
            json.dumps(captured, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {_golden_path(name)}")


if __name__ == "__main__":
    _write_goldens()
