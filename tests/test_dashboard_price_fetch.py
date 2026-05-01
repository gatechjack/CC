"""Dashboard price-fetch shape tests.

Pins the {CODE}/USD ↔ {CODE}-USD translation in `web/data._fetch_prices_async`.

Origin: 2026-05-01 — Robinhood crypto positions started flowing into the
dashboard's Holdings table in unified `BTC/USD` form. The dashboard's
yfinance-backed price fetcher uses dash-form (`BTC-USD`); without this
translation, crypto rows would render with no Last / Mkt Value.
"""
from __future__ import annotations

import pytest

from trading_corp.web import data as data_mod


@pytest.mark.asyncio
async def test_slash_form_routed_through_dash_form_to_yfinance(monkeypatch):
    """`BTC/USD` in → yfinance receives `BTC-USD` → returned dict carries
    the original `BTC/USD` key (caller stays in unified land)."""
    captured: dict[str, list[str]] = {"calls": []}

    async def _fake_fetch(syms):
        captured["calls"].append(list(syms))
        # Yfinance answers with what was asked (dash form).
        return {s: 100.0 + i for i, s in enumerate(syms)}

    # Patch the inner PMCCAgent fetcher.
    from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent
    monkeypatch.setattr(PMCCAgent, "_fetch_prices", staticmethod(_fake_fetch))

    out = await data_mod._fetch_prices_async(["AAPL", "BTC/USD", "ETH/USD"])

    # Inner fetcher saw dash-form for crypto + untouched stock.
    assert captured["calls"] == [["AAPL", "BTC-USD", "ETH-USD"]]
    # Caller sees the original (slash) keys back.
    assert set(out.keys()) == {"AAPL", "BTC/USD", "ETH/USD"}
    assert out["AAPL"] == pytest.approx(100.0)
    assert out["BTC/USD"] == pytest.approx(101.0)
    assert out["ETH/USD"] == pytest.approx(102.0)


@pytest.mark.asyncio
async def test_no_crypto_no_translation(monkeypatch):
    """Pure stock list passes through unchanged — no false-positive
    rewrites on tickers that happen to contain `/`-shaped strings later."""
    seen: list[list[str]] = []

    async def _fake_fetch(syms):
        seen.append(list(syms))
        return {s: 50.0 for s in syms}

    from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent
    monkeypatch.setattr(PMCCAgent, "_fetch_prices", staticmethod(_fake_fetch))

    await data_mod._fetch_prices_async(["AAPL", "MSFT", "NVDA"])
    assert seen == [["AAPL", "MSFT", "NVDA"]]


@pytest.mark.asyncio
async def test_inner_fetch_failure_returns_empty(monkeypatch):
    """If the inner fetcher raises, _fetch_prices_async returns {} rather
    than propagating — pricing is best-effort on the dashboard."""
    async def _boom(syms):
        raise RuntimeError("yfinance down")

    from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent
    monkeypatch.setattr(PMCCAgent, "_fetch_prices", staticmethod(_boom))

    out = await data_mod._fetch_prices_async(["AAPL", "BTC/USD"])
    assert out == {}


@pytest.mark.asyncio
async def test_partial_yfinance_response_preserves_unified_keys(monkeypatch):
    """If yfinance only returns a subset (BTC found, ETH not),
    the caller still sees the BTC entry under its unified key."""
    async def _fake_fetch(syms):
        # Simulate yfinance: BTC-USD resolves, ETH-USD doesn't.
        return {"BTC-USD": 60_000.0}

    from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent
    monkeypatch.setattr(PMCCAgent, "_fetch_prices", staticmethod(_fake_fetch))

    out = await data_mod._fetch_prices_async(["BTC/USD", "ETH/USD"])
    assert out == {"BTC/USD": 60_000.0}
