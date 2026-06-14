"""E1·5 — PolymarketBroker.quote consolidated onto the py_clob_client SDK
(`get_midpoint`, a Level-0 public read), replacing the unverified raw-httpx
`/last-trade-price` GET. Mocked/fundless: no live network, no creds, no funds.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from trading_corp.brokers.polymarket import PolymarketBroker


def _broker(*, stub: bool = False) -> PolymarketBroker:
    """Minimal broker instance for unit tests (bypasses __init__/network).
    Sets only the attrs quote()/_midpoint_via_sdk read."""
    b = PolymarketBroker.__new__(PolymarketBroker)
    b._stub = stub
    b._client = AsyncMock()      # async httpx (gamma)
    b._clob_sdk = None           # SDK injected per-test (no real ClobClient)
    return b


# ── _midpoint_via_sdk (sync; SDK get_midpoint mocked — no SDK import) ────────

def test_midpoint_parses_mid_field():
    b = _broker()
    b._clob_sdk = MagicMock()
    b._clob_sdk.get_midpoint.return_value = {"mid": "0.52"}
    assert b._midpoint_via_sdk("tok") == 0.52
    b._clob_sdk.get_midpoint.assert_called_once_with("tok")


def test_midpoint_parses_fallback_field():
    b = _broker()
    b._clob_sdk = MagicMock()
    b._clob_sdk.get_midpoint.return_value = {"midpoint": 0.4}
    assert b._midpoint_via_sdk("tok") == 0.4


def test_midpoint_empty_or_nondict_returns_zero():
    b = _broker()
    b._clob_sdk = MagicMock()
    b._clob_sdk.get_midpoint.return_value = {}
    assert b._midpoint_via_sdk("tok") == 0.0
    b._clob_sdk.get_midpoint.return_value = None
    assert b._midpoint_via_sdk("tok") == 0.0


def test_midpoint_exception_returns_zero():
    b = _broker()
    b._clob_sdk = MagicMock()
    b._clob_sdk.get_midpoint.side_effect = RuntimeError("clob down")
    assert b._midpoint_via_sdk("tok") == 0.0


def test_midpoint_non_numeric_returns_zero():
    b = _broker()
    b._clob_sdk = MagicMock()
    b._clob_sdk.get_midpoint.return_value = {"mid": "not-a-number"}
    assert b._midpoint_via_sdk("tok") == 0.0


# ── quote() end-to-end: gamma slug->token_id (mocked) + SDK midpoint ────────

def _gamma_resp(payload):
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json.return_value = payload
    return r


async def test_quote_resolves_correct_outcome_then_midpoint():
    b = _broker()
    b._clob_sdk = MagicMock()
    b._clob_sdk.get_midpoint.return_value = {"mid": "0.6"}
    # outcomes ordered No,Yes so the "Yes" token is t1 (not a default index-0).
    b._client.get.return_value = _gamma_resp(
        [{"clobTokenIds": '["t0", "t1"]', "outcomes": '["No", "Yes"]'}]
    )
    price = await b.quote("trump-2024:Yes")
    assert price == 0.6
    b._clob_sdk.get_midpoint.assert_called_once_with("t1")  # the Yes token


async def test_quote_stub_returns_zero():
    b = _broker(stub=True)
    assert await b.quote("slug:Yes") == 0.0


async def test_quote_no_market_returns_zero():
    b = _broker()
    b._client.get.return_value = _gamma_resp([])
    assert await b.quote("slug:Yes") == 0.0
