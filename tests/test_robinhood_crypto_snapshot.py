"""Regression tests for Robinhood crypto positions appearing on the snapshot.

Origin: 2026-05-01 dashboard bug — a real-money crypto holding (qty=4.022,
likely BTC or ETH) was completely invisible on the Robinhood pane because
`RobinhoodBroker._snapshot_for_account()` only queried the stocks +
options endpoints. The fix added a third query branch
(`rs.crypto.get_crypto_positions()`) emitted only on the Individual
account instance — Robinhood holds crypto in one account-wide wallet, so
emitting from IRA / Joint instances would triple-count.

These tests pin:
  1. Individual instance includes crypto positions in its snapshot.
  2. IRA Roth and Joint instances do NOT (no triple-counting).
  3. Symbols are formatted as `{CODE}/USD` matching Coinbase's unified format.
  4. avg_price = cost_basis / quantity.
  5. quote("BTC/USD") routes to rs.crypto.get_crypto_quote, not the equities
     endpoint that would 404 on the slash-bearing symbol.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_corp.brokers.robinhood import RobinhoodBroker


def _install_fake_rs(crypto_positions, crypto_quote=None):
    """Install a minimal fake `robin_stocks.robinhood` module in sys.modules.

    Real robin_stocks may or may not be installed in the test environment,
    so we shim only the surface area `snapshot()` and `quote()` touch. We
    tear down any pre-existing module first so each test gets a clean slate.
    """
    rs = types.ModuleType("robin_stocks.robinhood")
    rs_pkg = types.ModuleType("robin_stocks")

    rs.profiles = types.SimpleNamespace(
        load_portfolio_profile=lambda *_a, **_kw: {"equity": "10000", "buying_power": "5000"},
    )
    rs.account = types.SimpleNamespace(
        get_open_stock_positions=lambda *_a, **_kw: [],
    )
    rs.options = types.SimpleNamespace(
        get_open_option_positions=lambda *_a, **_kw: [],
    )
    rs.stocks = types.SimpleNamespace(
        get_latest_price=lambda *_a, **_kw: ["0"],
        get_symbol_by_url=lambda *_a, **_kw: "",
    )
    rs.crypto = types.SimpleNamespace(
        get_crypto_positions=lambda *_a, **_kw: crypto_positions,
        get_crypto_quote=lambda symbol, *_a, **_kw: crypto_quote or {},
    )

    rs_pkg.robinhood = rs
    sys.modules["robin_stocks"] = rs_pkg
    sys.modules["robin_stocks.robinhood"] = rs
    return rs


def _make_broker(account_type: str) -> RobinhoodBroker:
    """Build a connected broker bound to the given account_type, no real login."""
    b = RobinhoodBroker(username="u", password="p")
    b._connected = True
    b._account_type = account_type
    b._account_label = account_type
    b._account_number = "X1234"
    return b


# ── snapshot crypto branch ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_individual_snapshot_includes_crypto():
    _install_fake_rs(
        crypto_positions=[
            {
                "currency": {"code": "BTC", "name": "Bitcoin"},
                "quantity": "0.5",
                "cost_basis": "20000.00",
                "id": "abc",
                "created_at": "2025-01-01T00:00:00Z",
            },
            {
                "currency": {"code": "ETH"},
                "quantity": "4.022",
                "cost_basis": "8044.00",
                "id": "def",
                "created_at": "",
            },
        ],
    )
    b = _make_broker("individual")
    snap = await b.snapshot()

    crypto = [p for p in snap.positions if p.symbol.endswith("/USD")]
    assert len(crypto) == 2

    btc = next(p for p in crypto if p.symbol == "BTC/USD")
    assert btc.qty == pytest.approx(0.5)
    # cost_basis 20000 / qty 0.5 = 40000 avg
    assert btc.avg_price == pytest.approx(40000.0)
    assert btc.extra == {"asset": "BTC", "venue": "robinhood", "asset_type": "crypto"}

    eth = next(p for p in crypto if p.symbol == "ETH/USD")
    assert eth.qty == pytest.approx(4.022)
    assert eth.avg_price == pytest.approx(8044.00 / 4.022)


@pytest.mark.asyncio
async def test_ira_snapshot_excludes_crypto():
    """IRA accounts must NOT emit crypto — Robinhood doesn't support crypto
    in IRAs, and crypto is account-wide; emitting here would triple-count."""
    _install_fake_rs(
        crypto_positions=[{
            "currency": {"code": "BTC"},
            "quantity": "0.5",
            "cost_basis": "20000.00",
        }],
    )
    b = _make_broker("ira_roth")
    snap = await b.snapshot()
    assert not any(p.symbol.endswith("/USD") for p in snap.positions)


@pytest.mark.asyncio
async def test_joint_snapshot_excludes_crypto():
    _install_fake_rs(
        crypto_positions=[{
            "currency": {"code": "BTC"},
            "quantity": "0.5",
            "cost_basis": "20000.00",
        }],
    )
    b = _make_broker("joint")
    snap = await b.snapshot()
    assert not any(p.symbol.endswith("/USD") for p in snap.positions)


@pytest.mark.asyncio
async def test_zero_qty_crypto_skipped():
    _install_fake_rs(
        crypto_positions=[
            {"currency": {"code": "DOGE"}, "quantity": "0", "cost_basis": "0"},
            {"currency": {"code": "BTC"}, "quantity": "0.5", "cost_basis": "20000"},
        ],
    )
    b = _make_broker("individual")
    snap = await b.snapshot()
    crypto = [p for p in snap.positions if p.symbol.endswith("/USD")]
    assert [p.symbol for p in crypto] == ["BTC/USD"]


@pytest.mark.asyncio
async def test_currency_as_bare_string_handled():
    """Some Robinhood API variants return `currency` as a bare string. The
    snapshot must not crash on either shape."""
    _install_fake_rs(
        crypto_positions=[
            {"currency": "BTC", "quantity": "0.5", "cost_basis": "20000"},
        ],
    )
    b = _make_broker("individual")
    snap = await b.snapshot()
    syms = [p.symbol for p in snap.positions if p.symbol.endswith("/USD")]
    assert syms == ["BTC/USD"]


@pytest.mark.asyncio
async def test_missing_currency_skipped_silently():
    _install_fake_rs(
        crypto_positions=[
            {"currency": None, "quantity": "0.5", "cost_basis": "20000"},
            {"quantity": "0.5", "cost_basis": "20000"},  # no currency key
            {"currency": {"code": "BTC"}, "quantity": "0.5", "cost_basis": "20000"},
        ],
    )
    b = _make_broker("individual")
    snap = await b.snapshot()
    syms = [p.symbol for p in snap.positions if p.symbol.endswith("/USD")]
    assert syms == ["BTC/USD"]


@pytest.mark.asyncio
async def test_crypto_api_failure_does_not_break_snapshot():
    """If get_crypto_positions throws, the rest of the snapshot must still
    return — losing crypto is bad, losing the whole division is worse."""
    rs = _install_fake_rs(crypto_positions=[])

    def boom(*_a, **_kw):
        raise RuntimeError("RH crypto API down")
    rs.crypto.get_crypto_positions = boom

    b = _make_broker("individual")
    snap = await b.snapshot()  # must not raise
    assert snap.equity == pytest.approx(10000.0)


# ── quote() crypto routing ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quote_btc_usd_routes_to_crypto_endpoint():
    _install_fake_rs(
        crypto_positions=[],
        crypto_quote={"mark_price": "40123.45", "ask_price": "40150.00"},
    )
    b = _make_broker("individual")
    px = await b.quote("BTC/USD")
    assert px == pytest.approx(40123.45)


@pytest.mark.asyncio
async def test_quote_eth_usd_falls_through_to_ask_when_no_mark():
    _install_fake_rs(
        crypto_positions=[],
        crypto_quote={"ask_price": "2500.00"},
    )
    b = _make_broker("individual")
    px = await b.quote("ETH/USD")
    assert px == pytest.approx(2500.00)


@pytest.mark.asyncio
async def test_quote_crypto_failure_returns_zero():
    rs = _install_fake_rs(crypto_positions=[])

    def boom(*_a, **_kw):
        raise RuntimeError("crypto quote API down")
    rs.crypto.get_crypto_quote = boom

    b = _make_broker("individual")
    px = await b.quote("BTC/USD")
    assert px == 0.0


@pytest.mark.asyncio
async def test_quote_stock_symbol_unchanged():
    """Pre-existing equity-quote path must keep working."""
    rs = _install_fake_rs(crypto_positions=[])
    rs.stocks.get_latest_price = lambda symbols, *_a, **_kw: ["123.45"]
    b = _make_broker("individual")
    px = await b.quote("AAPL")
    assert px == pytest.approx(123.45)
