"""ITEM A — utils/market_data.get_next_earnings: EODHD reportDate primary +
labeled yfinance fallback. Pins the swap that fixes the live earnings-avoidance
gate (PMCC + both ICs) without changing the public signature/contract.
"""
from __future__ import annotations

import inspect
from datetime import date, datetime, timezone

import trading_corp.utils.market_data as md
from trading_corp.data import earnings_provider as ep
from trading_corp.data.earnings_provider import EarningsProvider, _eodhd_next_earnings_date

# Synthetic EODHD Earnings.History (shape verified live on AAPL.US): the future
# quarter is an unreported row (epsActual=null) with reportDate populated.
HIST = {
    "2026-06-30": {"reportDate": "2026-07-30", "date": "2026-06-30", "epsActual": None, "epsEstimate": 1.9},
    "2026-03-31": {"reportDate": "2026-05-01", "date": "2026-03-31", "epsActual": 1.65, "epsEstimate": 1.6},
    "2025-12-31": {"reportDate": "2026-01-30", "date": "2025-12-31", "epsActual": 2.4, "epsEstimate": 2.35},
}


# ── pure helper ──────────────────────────────────────────────────────────
def test_picks_min_future_reportdate():
    assert _eodhd_next_earnings_date(HIST, date(2026, 6, 21)) == date(2026, 7, 30)


def test_none_when_all_past():
    assert _eodhd_next_earnings_date(HIST, date(2026, 8, 1)) is None


def test_picks_nearest_future_among_multiple():
    h = dict(HIST)
    h["2026-09-30"] = {"reportDate": "2026-10-29", "epsActual": None}
    assert _eodhd_next_earnings_date(h, date(2026, 6, 21)) == date(2026, 7, 30)
    assert _eodhd_next_earnings_date(h, date(2026, 8, 1)) == date(2026, 10, 29)


def test_empty_and_malformed_rows():
    assert _eodhd_next_earnings_date({}, date(2026, 6, 21)) is None
    assert _eodhd_next_earnings_date({"x": {"reportDate": "garbage"}}, date(2026, 6, 21)) is None
    assert _eodhd_next_earnings_date({"x": {"reportDate": None}}, date(2026, 6, 21)) is None
    assert _eodhd_next_earnings_date({"x": "not-a-dict"}, date(2026, 6, 21)) is None


# ── provider method (reuses the 24h fundamentals cache) ──────────────────
def test_provider_get_next_earnings_date(monkeypatch):
    monkeypatch.setattr(EarningsProvider, "_get_fundamentals",
                        lambda self, s: {"Earnings": {"History": HIST}})
    p = EarningsProvider(api_key="x")
    assert p.get_next_earnings_date("AAPL", asof=date(2026, 6, 21)) == date(2026, 7, 30)


def test_provider_none_when_no_data(monkeypatch):
    monkeypatch.setattr(EarningsProvider, "_get_fundamentals", lambda self, s: None)
    p = EarningsProvider(api_key="x")
    assert p.get_next_earnings_date("AAPL") is None
    assert p.get_next_earnings_date("") is None


# ── market_data wrapper: EODHD primary + yfinance fallback ───────────────
def test_eodhd_primary_used_yfinance_not_called(monkeypatch):
    md._EARNINGS_CACHE.clear()
    called = {"yf": False}
    monkeypatch.setattr(md, "_eodhd_next_earnings",
                        lambda s: datetime(2026, 7, 30, 23, 59, 59, tzinfo=timezone.utc))
    monkeypatch.setattr(md, "_yfinance_next_earnings",
                        lambda s: called.__setitem__("yf", True) or datetime(2099, 1, 1, tzinfo=timezone.utc))
    out = md.get_next_earnings("AAPL")
    assert out == datetime(2026, 7, 30, 23, 59, 59, tzinfo=timezone.utc)
    assert called["yf"] is False   # no auto-failover when EODHD succeeds


def test_yfinance_fallback_when_eodhd_none(monkeypatch):
    md._EARNINGS_CACHE.clear()
    monkeypatch.setattr(md, "_eodhd_next_earnings", lambda s: None)
    monkeypatch.setattr(md, "_yfinance_next_earnings",
                        lambda s: datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc))
    assert md.get_next_earnings("ZZZ") == datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def test_both_none_returns_none(monkeypatch):
    md._EARNINGS_CACHE.clear()
    monkeypatch.setattr(md, "_eodhd_next_earnings", lambda s: None)
    monkeypatch.setattr(md, "_yfinance_next_earnings", lambda s: None)
    assert md.get_next_earnings("ZZZ") is None


def test_end_of_report_day_utc_convention(monkeypatch):
    # _eodhd_next_earnings converts the EODHD date → end-of-report-day UTC.
    monkeypatch.setattr(ep.EarningsProvider, "get_next_earnings_date",
                        lambda self, s, asof=None: date(2026, 7, 30))
    assert md._eodhd_next_earnings("AAPL") == datetime(2026, 7, 30, 23, 59, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(ep.EarningsProvider, "get_next_earnings_date",
                        lambda self, s, asof=None: None)
    assert md._eodhd_next_earnings("AAPL") is None


# ── contract preserved (callers unaffected) ──────────────────────────────
def test_signature_and_empty_symbol_contract():
    sig = inspect.signature(md.get_next_earnings)
    assert list(sig.parameters) == ["symbol", "force_refresh"]
    assert md.get_next_earnings("") is None
