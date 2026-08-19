"""Unit tests for KalshiBroker.quote() — the pykalshi 1.0.6 MarketModel top-of-book
fix (2026-08-18). The old get_orderbook().yes_bids/yes_asks parse returned 0.0 for
every market (OrderbookResponse has no such attrs); quote() now reads the MarketModel
yes_bid_dollars/yes_ask_dollars — the same source proven live by main._pk_quote_fn.
No network: a fake pykalshi client stands in for AsyncKalshiClient."""
from __future__ import annotations

import asyncio

import pytest

from trading_corp.brokers.kalshi import KalshiBroker, _dollar_price


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeMarket:
    def __init__(self, yes_bid_dollars, yes_ask_dollars):
        self.yes_bid_dollars = yes_bid_dollars
        self.yes_ask_dollars = yes_ask_dollars


class _FakeClient:
    def __init__(self, market=None, boom=None):
        self._m = market
        self._boom = boom

    async def get_market(self, symbol):
        if self._boom is not None:
            raise self._boom
        return self._m


def _broker(market=None, boom=None):
    b = KalshiBroker(api_key_id="k", private_key_pem="pem")   # non-stub (creds present)
    b._client = _FakeClient(market, boom)
    return b


# ── the fix: full book -> real mid (the live-observed values) ────────────────
def test_quote_full_book_returns_mid():
    # live probe values 2026-08-18: yes_bid 0.26 / yes_ask 0.36 -> mid 0.31
    b = _broker(_FakeMarket("0.2600", "0.3600"))
    assert _run(b.quote("KXMLBGAME-26AUG212210PITLAD-PIT")) == pytest.approx(0.31)


def test_quote_one_sided_returns_present_side():
    assert _run(_broker(_FakeMarket("0.2600", None)).quote("T")) == pytest.approx(0.26)
    assert _run(_broker(_FakeMarket(None, "0.3600")).quote("T")) == pytest.approx(0.36)


def test_quote_settled_or_missing_returns_zero():
    assert _run(_broker(_FakeMarket("1.00", "1.00")).quote("T")) == 0.0   # settled boundary -> not priceable
    assert _run(_broker(_FakeMarket("0.00", "0.00")).quote("T")) == 0.0
    assert _run(_broker(_FakeMarket(None, None)).quote("T")) == 0.0       # missing fields
    assert _run(_broker(_FakeMarket("", "")).quote("T")) == 0.0
    assert _run(_broker(_FakeMarket("junk", "0.3600")).quote("T")) == pytest.approx(0.36)  # bad bid ignored


def test_quote_stub_returns_zero():
    assert _run(KalshiBroker().quote("T")) == 0.0                          # no creds -> stub


def test_quote_get_market_error_returns_zero():
    assert _run(_broker(boom=RuntimeError("api down")).quote("T")) == 0.0  # error -> 0.0 (contract)


# ── the _dollar_price helper ─────────────────────────────────────────────────
def test_dollar_price_parses_band_only():
    assert _dollar_price("0.2600") == pytest.approx(0.26)
    assert _dollar_price(0.5) == pytest.approx(0.5)
    for bad in (None, "", "abc", "1.00", "0.00", "1.5", "-0.1"):
        assert _dollar_price(bad) is None
