"""E1·6 — PolymarketLiveBroker(Broker) assembly + factory live-vs-paper resolution.

Mocked/fundless: no real CLOB, no creds, no funds. Verifies the WIRING (the
delegated pieces — mapping/sign/place/poll/cancel/quote — are tested in E1·2-5):
  - PolymarketLiveBroker is a placement-legal Broker (NOT a ReadOnlyBroker);
  - connect() L2-authorizes the client (create_or_derive_api_creds -> set_api_creds);
  - place_order/cancel_order delegate to the E1·2-4 module fns with the L2 client;
  - snapshot/quote delegate to the read adapter (E1·5 quote);
  - the main.py factory returns the LIVE broker when LIVE+selected, else read-only
    (the anti-half-flip).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import trading_corp.brokers.polymarket_live as pl
from trading_corp.brokers.base import Broker, ReadOnlyBroker
from trading_corp.brokers.polymarket import PolymarketBroker
from trading_corp.brokers.polymarket_live import PolymarketLiveBroker


def _live():
    return PolymarketLiveBroker(
        private_key="0xkey", funder_address="0xfunder", polygon_rpc_url="http://rpc",
    )


# ── Broker-ABC conformance: placement-legal, NOT read-only ──────────────────

def test_live_broker_is_placement_legal_broker():
    b = _live()
    assert isinstance(b, Broker)          # placement-legal (has place_order)
    assert b.paper is False               # live, not paper
    assert hasattr(b, "place_order") and hasattr(b, "cancel_order")


def test_read_adapter_is_readonly_not_broker():
    rb = PolymarketBroker(funder_address="0xf", polygon_rpc_url="http://rpc")
    assert isinstance(rb, ReadOnlyBroker)
    assert not isinstance(rb, Broker)     # cannot place — the key distinction


# ── connect(): L2 authorization ─────────────────────────────────────────────

async def test_connect_l2_authorizes_client():
    b = _live()
    b._read = AsyncMock()                  # read-adapter connect mocked
    clob = MagicMock()
    clob.create_or_derive_api_creds.return_value = "CREDS"
    b._build_clob_client = lambda: clob    # inject mock client (no real SDK)

    await b.connect()

    b._read.connect.assert_awaited_once()
    clob.create_or_derive_api_creds.assert_called_once()
    clob.set_api_creds.assert_called_once_with("CREDS")
    assert b._clob is clob
    assert b._connected is True


async def test_disconnect_clears_state():
    b = _live()
    b._read = AsyncMock()
    b._clob = MagicMock()
    b._connected = True
    await b.disconnect()
    b._read.disconnect.assert_awaited_once()
    assert b._clob is None and b._connected is False


# ── place/cancel delegate to the (E1·2-4) module fns with the L2 client ─────

async def test_place_order_delegates_to_module_fn(monkeypatch):
    b = _live()
    b._clob = MagicMock()
    b._connected = True
    fill = object()
    fn = AsyncMock(return_value=fill)
    monkeypatch.setattr(pl, "_place_order_fn", fn)
    result = await b.place_order("ORDER")
    assert result is fill
    fn.assert_awaited_once_with(b._clob, "ORDER")


async def test_cancel_order_delegates_to_module_fn(monkeypatch):
    b = _live()
    b._clob = MagicMock()
    b._connected = True
    fn = AsyncMock(return_value=True)
    monkeypatch.setattr(pl, "_cancel_order_fn", fn)
    result = await b.cancel_order("0xOID")
    assert result is True
    fn.assert_awaited_once_with(b._clob, "0xOID")


async def test_place_and_cancel_require_connected():
    b = _live()                            # not connected (_clob=None)
    with pytest.raises(RuntimeError):
        await b.place_order("O")
    with pytest.raises(RuntimeError):
        await b.cancel_order("O")


# ── snapshot/quote delegate to the read adapter (E1·5 SDK quote) ────────────

async def test_quote_and_snapshot_delegate_to_read_adapter():
    b = _live()
    b._read = AsyncMock()
    b._read.quote.return_value = 0.55
    b._read.snapshot.return_value = "SNAP"
    assert await b.quote("slug:Yes") == 0.55
    b._read.quote.assert_awaited_once_with("slug:Yes")
    assert await b.snapshot() == "SNAP"


# ── factory: live-vs-paper resolution (the anti-half-flip) ──────────────────

def _div():
    return SimpleNamespace(broker="polymarket", slug="polymarket_copy_trading", account_filter=None)


def _secrets():
    return SimpleNamespace(
        polymarket_wallets={
            "polymarket_copy_trading": SimpleNamespace(private_key="0xk", funder_address="0xf"),
        },
        polygon_rpc_url="http://rpc",
    )


def test_factory_live_and_selected_returns_live_broker():
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(_div(), _secrets(), "LIVE", ["polymarket"])
    assert isinstance(b, PolymarketLiveBroker)   # the anti-half-flip: live, not read-only


def test_factory_paper_returns_readonly():
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(_div(), _secrets(), "PAPER", ["polymarket"])
    assert isinstance(b, PolymarketBroker)
    assert not isinstance(b, PolymarketLiveBroker)


def test_factory_live_but_not_selected_returns_readonly():
    from trading_corp.main import _build_broker_for_division
    b = _build_broker_for_division(_div(), _secrets(), "LIVE", [])
    assert isinstance(b, PolymarketBroker)
    assert not isinstance(b, PolymarketLiveBroker)
