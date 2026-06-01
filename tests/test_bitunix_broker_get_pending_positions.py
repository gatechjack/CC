"""Tests for `BitunixBroker.get_pending_positions` — public extraction.

Decision 6.4 (a): the broker exposes a public `get_pending_positions()`
method wrapping the existing inline endpoint call from `snapshot()`.
The position-state reconciler (commit 4) consumes this. Validates:

- Returns `list[Position]` parsed from the BitUnix futures position
  endpoint response.
- SHORT positions render with negative qty (signed-qty convention
  preserved from `snapshot()`).
- qty=0 positions filtered out (closed positions still on the
  endpoint).
- Stub mode + missing creds → empty list (no exception).
- Transient `_request` failures → empty list + log warning (the
  reconciler's "verdict = missing" branch handles).
- Method is positions-only — does NOT call the per-coin balance
  endpoints `snapshot()` hits.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.brokers.bitunix import BitunixBroker
from trading_corp.persistence.models import Position


def _stub_broker(
    *,
    request_returns=None,
    request_raises: Exception | None = None,
    stub_mode: bool = False,
    missing_creds: bool = False,
) -> BitunixBroker:
    if missing_creds:
        # Constructor auto-sets self._stub=True when either credential
        # missing; we explicitly verify the missing-creds short-circuit
        # below by overriding the flag back to False.
        broker = BitunixBroker(api_key="", api_secret="")
        broker._stub = False
        broker._client = MagicMock()
    elif stub_mode:
        broker = BitunixBroker(api_key="", api_secret="")
        # self._stub is already True via the constructor's missing-creds path
        broker._client = None
    else:
        broker = BitunixBroker(api_key="test_key", api_secret="test_secret")
        broker._client = MagicMock()
        if request_raises is not None:
            broker._request = AsyncMock(side_effect=request_raises)
        else:
            broker._request = AsyncMock(return_value=request_returns)
    return broker


# ─── happy path ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_returns_list_of_positions_from_endpoint():
    broker = _stub_broker(request_returns=[
        {
            "symbol": "BTCUSDT",
            "qty": "0.005",
            "avgOpenPrice": "80000.5",
            "ctime": "1717200000000",
            "leverage": "10",
            "marginMode": "ISOLATED",
            "unrealizedPNL": "0.1",
            "liqPrice": "78000.0",
            "side": "LONG",
        },
    ])
    positions = await broker.get_pending_positions()
    assert len(positions) == 1
    p = positions[0]
    assert isinstance(p, Position)
    assert p.symbol == "BTCUSDT"
    assert p.qty == 0.005
    assert p.avg_price == 80000.5
    assert p.extra["leverage"] == "10"
    assert p.extra["marginMode"] == "ISOLATED"
    assert p.extra["side"] == "LONG"


@pytest.mark.asyncio
async def test_short_positions_render_with_negative_qty():
    """Mirror `snapshot()`'s signed-qty convention so the dashboard's
    downstream PnL math is consistent across both call sites."""
    broker = _stub_broker(request_returns=[
        {
            "symbol": "BTCUSDT",
            "qty": "0.005",
            "avgOpenPrice": "80000.0",
            "ctime": "1717200000000",
            "side": "SHORT",
        },
    ])
    positions = await broker.get_pending_positions()
    assert len(positions) == 1
    assert positions[0].qty == -0.005


@pytest.mark.asyncio
async def test_zero_qty_positions_filtered_out():
    broker = _stub_broker(request_returns=[
        {"symbol": "BTCUSDT", "qty": "0", "side": "LONG"},
        {"symbol": "ETHUSDT", "qty": "0.5", "side": "LONG",
         "avgOpenPrice": "3000", "ctime": "1717200000000"},
    ])
    positions = await broker.get_pending_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "ETHUSDT"


@pytest.mark.asyncio
async def test_empty_response_returns_empty_list():
    broker = _stub_broker(request_returns=[])
    positions = await broker.get_pending_positions()
    assert positions == []


@pytest.mark.asyncio
async def test_none_response_returns_empty_list():
    """Defensive: BitUnix occasionally returns `data: null` when no
    positions exist; the parser must tolerate without crashing."""
    broker = _stub_broker(request_returns=None)
    positions = await broker.get_pending_positions()
    assert positions == []


# ─── degraded modes (no exception leaks) ────────────────────────────────


@pytest.mark.asyncio
async def test_stub_mode_returns_empty_list():
    broker = _stub_broker(stub_mode=True)
    positions = await broker.get_pending_positions()
    assert positions == []


@pytest.mark.asyncio
async def test_missing_creds_returns_empty_list():
    broker = _stub_broker(missing_creds=True)
    positions = await broker.get_pending_positions()
    assert positions == []


@pytest.mark.asyncio
async def test_request_exception_returns_empty_list():
    """Transient `_request` failures must not crash the reconciler.
    The reconciler's "verdict = missing" branch interprets the empty
    list correctly (no broker positions known this tick)."""
    broker = _stub_broker(request_raises=RuntimeError("network down"))
    positions = await broker.get_pending_positions()
    assert positions == []


# ─── isolation: does not call snapshot's balance endpoints ──────────────


@pytest.mark.asyncio
async def test_does_not_call_account_balance_endpoints():
    """Cheap-and-fast positions-only contract: this method MUST NOT
    fetch the N×marginCoin balance lookups that `snapshot()` does.
    Reconciler runs every tick; a stray per-coin loop would cost
    multiple API round-trips per check."""
    request_mock = AsyncMock(return_value=[])
    broker = _stub_broker()
    broker._request = request_mock
    await broker.get_pending_positions()
    # Exactly one _request call — for the position-list endpoint.
    assert request_mock.await_count == 1
    args, kwargs = request_mock.await_args
    assert args[0] == "GET"
    assert args[1] == "/api/v1/futures/position/get_pending_positions"
