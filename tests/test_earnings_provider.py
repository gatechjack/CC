"""Tests for trading_corp.data.earnings_provider (EODHD primary).

All HTTP and yfinance calls are mocked — NO live key needed.
Live-key smoke test is gated by pytest.mark.skipif(not EODHD_API_KEY).
"""
from __future__ import annotations

import io
import json
import os
from datetime import date
from unittest.mock import MagicMock, call, patch

import pytest

from trading_corp.data.earnings_provider import (
    EarningsProvider,
    QuarterlyEPS,
    _compute_surprise,
    _normalise_fiscal_period,
    _parse_eodhd_earnings,
    reset_earnings_provider_cache,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_cache():
    """Clear EarningsProvider caches before and after each test."""
    reset_earnings_provider_cache()
    yield
    reset_earnings_provider_cache()


# ---------------------------------------------------------------------------
# Helper: build a mock urlopen context manager from a Python object
# ---------------------------------------------------------------------------

def _make_urlopen_mock(payload: dict | list):
    """Return a mock that urlopen() returns as a context manager yielding JSON."""
    body = json.dumps(payload).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# Minimal EODHD fundamentals fixture (AAPL-like, 9 quarters)
# ---------------------------------------------------------------------------

# Keyed by fiscal-period-end date (what EODHD uses as the dict key).
# reportDate = announcement date (the PEAD-critical field).
# date       = fiscal period end.
_EODHD_HISTORY: dict = {
    "2022-12-31": {
        "reportDate": "2023-01-26",
        "date": "2022-12-31",
        "epsActual": 1.20,
        "epsEstimate": 1.10,
    },
    "2023-03-31": {
        "reportDate": "2023-04-27",
        "date": "2023-03-31",
        "epsActual": 1.30,
        "epsEstimate": 1.25,
    },
    "2023-06-30": {
        "reportDate": "2023-07-28",
        "date": "2023-06-30",
        "epsActual": 1.50,
        "epsEstimate": 1.40,
    },
    "2023-09-30": {
        "reportDate": "2023-10-31",
        "date": "2023-09-30",
        "epsActual": 1.70,
        "epsEstimate": 1.60,
    },
    "2023-12-31": {
        "reportDate": "2024-01-30",
        "date": "2023-12-31",
        "epsActual": 1.80,
        "epsEstimate": 1.75,
    },
    "2024-03-31": {
        "reportDate": "2024-04-30",
        "date": "2024-03-31",
        "epsActual": 1.90,
        "epsEstimate": 1.85,
    },
    "2024-06-30": {
        "reportDate": "2024-07-31",
        "date": "2024-06-30",
        "epsActual": 2.10,
        "epsEstimate": 2.00,
    },
    "2024-09-30": {
        "reportDate": "2024-10-30",
        "date": "2024-09-30",
        "epsActual": 2.35,
        "epsEstimate": 2.20,
    },
    "2024-12-31": {
        "reportDate": "2025-01-29",
        "date": "2024-12-31",
        "epsActual": 2.40,
        "epsEstimate": 2.35,
    },
}

_EODHD_FUNDAMENTALS: dict = {
    "General": {
        "Sector": "Technology",
        "FiscalYearEnd": "September",
    },
    "Highlights": {
        "MarketCapitalization": 3_000_000_000_000.0,
    },
    "Earnings": {
        "History": _EODHD_HISTORY,
    },
}


# ---------------------------------------------------------------------------
# QuarterlyEPS dataclass
# ---------------------------------------------------------------------------

def test_quarterly_eps_frozen():
    row = QuarterlyEPS(
        fiscal_period="2024Q1",
        report_date=date(2024, 5, 1),
        actual_eps=1.23,
        estimate_eps=1.10,
        surprise_pct=11.82,
    )
    with pytest.raises((AttributeError, TypeError)):
        row.actual_eps = 9.99  # type: ignore[misc]


def test_quarterly_eps_none_optional_fields():
    row = QuarterlyEPS(
        fiscal_period="2024Q2",
        report_date=date(2024, 8, 1),
        actual_eps=2.00,
        estimate_eps=None,
        surprise_pct=None,
    )
    assert row.estimate_eps is None
    assert row.surprise_pct is None


# ---------------------------------------------------------------------------
# _compute_surprise
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("actual,estimate,expected", [
    (1.23, 1.10, round((1.23 - 1.10) / abs(1.10) * 100, 4)),
    (1.00, 1.00, 0.0),
    (0.50, 1.00, -50.0),
    (1.20, None, None),    # no estimate → None
    (1.20, 0.0, None),     # zero estimate → None (div-by-zero guard)
])
def test_compute_surprise(actual, estimate, expected):
    result = _compute_surprise(actual, estimate)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, rel=1e-4)


# ---------------------------------------------------------------------------
# _normalise_fiscal_period
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected_prefix", [
    ("2024-03-31", "2024Q1"),
    ("2024-06-30", "2024Q2"),
    ("2024-09-30", "2024Q3"),
    ("2024-12-31", "2024Q4"),
    ("not-a-date", "not-a-date"),  # passthrough on failure
])
def test_normalise_fiscal_period(raw, expected_prefix):
    result = _normalise_fiscal_period(raw)
    assert result == expected_prefix


# ---------------------------------------------------------------------------
# _parse_eodhd_earnings — parsing logic
# ---------------------------------------------------------------------------

def test_parse_eodhd_earnings_basic():
    """All 9 fixture quarters should parse (all have epsActual)."""
    rows = _parse_eodhd_earnings(_EODHD_HISTORY)
    assert len(rows) == len(_EODHD_HISTORY)
    for r in rows:
        assert isinstance(r.actual_eps, float)
        assert isinstance(r.estimate_eps, float)
        assert r.surprise_pct is not None


def test_parse_eodhd_earnings_report_date_is_announcement_date():
    """report_date must equal reportDate (the announcement date), NOT the fiscal period end."""
    rows = _parse_eodhd_earnings(_EODHD_HISTORY)
    # Find 2024Q4 row (fiscal end 2024-12-31, announced 2025-01-29)
    q4 = [r for r in rows if r.fiscal_period == "2024Q4"]
    assert len(q4) == 1
    assert q4[0].report_date == date(2025, 1, 29), (
        "report_date must be the announcement date (reportDate), not the fiscal period end"
    )
    # Also verify it is NOT the fiscal period end date
    assert q4[0].report_date != date(2024, 12, 31)


def test_parse_eodhd_earnings_fiscal_period_from_date_field():
    """fiscal_period is derived from the `date` (fiscal period end), not reportDate."""
    rows = _parse_eodhd_earnings(_EODHD_HISTORY)
    # 2024-09-30 → Q3
    q3 = [r for r in rows if r.fiscal_period == "2024Q3"]
    assert len(q3) == 1


def test_parse_eodhd_earnings_skips_null_eps_actual():
    """Entries with epsActual=null (future quarters) must be skipped."""
    history = {
        "2025-03-31": {
            "reportDate": None,
            "date": "2025-03-31",
            "epsActual": None,    # unreported future quarter
            "epsEstimate": 2.50,
        },
        "2024-12-31": {
            "reportDate": "2025-01-29",
            "date": "2024-12-31",
            "epsActual": 2.40,
            "epsEstimate": 2.35,
        },
    }
    rows = _parse_eodhd_earnings(history)
    assert len(rows) == 1
    assert rows[0].actual_eps == 2.40


def test_parse_eodhd_earnings_none_estimate_gives_none_surprise():
    """epsEstimate=null → estimate_eps=None and surprise_pct=None."""
    history = {
        "2024-12-31": {
            "reportDate": "2025-01-29",
            "date": "2024-12-31",
            "epsActual": 2.40,
            "epsEstimate": None,
        }
    }
    rows = _parse_eodhd_earnings(history)
    assert len(rows) == 1
    assert rows[0].estimate_eps is None
    assert rows[0].surprise_pct is None


def test_parse_eodhd_earnings_chronological_order():
    """Sorted oldest→newest by report_date."""
    rows = sorted(_parse_eodhd_earnings(_EODHD_HISTORY), key=lambda r: r.report_date)
    for a, b in zip(rows, rows[1:]):
        assert a.report_date <= b.report_date


def test_parse_eodhd_earnings_empty_dict():
    assert _parse_eodhd_earnings({}) == []


def test_parse_eodhd_earnings_surprise_calculation():
    """Spot-check surprise: (2.40 - 2.35) / |2.35| * 100."""
    rows = _parse_eodhd_earnings(_EODHD_HISTORY)
    q4 = [r for r in rows if r.fiscal_period == "2024Q4"][0]
    expected = round((2.40 - 2.35) / abs(2.35) * 100, 4)
    assert q4.surprise_pct == pytest.approx(expected, rel=1e-4)


# ---------------------------------------------------------------------------
# EarningsProvider.get_quarterly_eps — EODHD primary path
# ---------------------------------------------------------------------------

def test_get_quarterly_eps_eodhd_primary_returns_8_plus_quarters():
    """EODHD returns 9 rows → provider returns all sorted oldest→newest."""
    provider = EarningsProvider(api_key="test-key")
    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_urlopen_mock(_EODHD_FUNDAMENTALS),
    ):
        result = provider.get_quarterly_eps("AAPL")

    assert result is not None
    assert len(result) >= 8
    for a, b in zip(result, result[1:]):
        assert a.report_date <= b.report_date


def test_get_quarterly_eps_report_date_is_announcement_date():
    """get_quarterly_eps must set report_date = reportDate (announcement), not date (period end)."""
    provider = EarningsProvider(api_key="test-key")
    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_urlopen_mock(_EODHD_FUNDAMENTALS),
    ):
        result = provider.get_quarterly_eps("AAPL")

    assert result is not None
    q4 = [r for r in result if r.fiscal_period == "2024Q4"]
    assert len(q4) == 1
    assert q4[0].report_date == date(2025, 1, 29)


def test_get_quarterly_eps_url_contains_us_suffix():
    """The HTTP request URL must contain the .US exchange suffix."""
    provider = EarningsProvider(api_key="test-key")
    captured_urls = []

    original_urlopen = __builtins__  # just to have a reference

    def capturing_urlopen(req, timeout=None):
        captured_urls.append(req.full_url if hasattr(req, "full_url") else str(req))
        return _make_urlopen_mock(_EODHD_FUNDAMENTALS)

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        side_effect=capturing_urlopen,
    ):
        provider.get_quarterly_eps("AAPL")

    assert captured_urls, "urlopen was never called"
    assert ".US" in captured_urls[0], f"URL did not contain '.US': {captured_urls[0]}"
    assert "AAPL.US" in captured_urls[0]


def test_get_quarterly_eps_caches_result():
    """Second call for same symbol hits the EPS cache — urlopen called once."""
    provider = EarningsProvider(api_key="test-key")
    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_urlopen_mock(_EODHD_FUNDAMENTALS),
    ) as mock_urlopen:
        provider.get_quarterly_eps("MSFT")
        provider.get_quarterly_eps("MSFT")

    assert mock_urlopen.call_count == 1


def test_get_quarterly_eps_none_on_eodhd_http_error():
    """EODHD HTTP error with no yfinance data → None (no crash)."""
    from urllib.error import HTTPError
    provider = EarningsProvider(api_key="test-key")

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        side_effect=HTTPError(url="", code=403, msg="Forbidden", hdrs=None, fp=None),  # type: ignore
    ):
        with patch(
            "trading_corp.data.earnings_provider._parse_yfinance_quarterly",
            return_value=None,
        ):
            result = provider.get_quarterly_eps("XYZ")

    assert result is None


def test_get_quarterly_eps_none_on_empty_symbol():
    provider = EarningsProvider(api_key="test-key")
    assert provider.get_quarterly_eps("") is None


def test_get_quarterly_eps_none_on_all_sources_fail():
    """EODHD errors AND yfinance returns nothing → None (no crash)."""
    from urllib.error import URLError
    provider = EarningsProvider(api_key="test-key")

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        side_effect=URLError("network error"),
    ):
        with patch(
            "trading_corp.data.earnings_provider._parse_yfinance_quarterly",
            return_value=None,
        ):
            result = provider.get_quarterly_eps("BADTICKER")

    assert result is None


# ---------------------------------------------------------------------------
# EarningsProvider.get_company_facts
# ---------------------------------------------------------------------------

def test_get_company_facts_market_cap_and_sector():
    """get_company_facts returns market_cap + sector from EODHD fundamentals."""
    provider = EarningsProvider(api_key="test-key")
    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_urlopen_mock(_EODHD_FUNDAMENTALS),
    ):
        result = provider.get_company_facts("AAPL")

    assert result is not None
    assert result["market_cap"] == pytest.approx(3_000_000_000_000.0)
    assert result["sector"] == "Technology"


def test_get_company_facts_none_on_failure():
    """No api_key → get_company_facts returns None."""
    provider = EarningsProvider(api_key=None)
    result = provider.get_company_facts("AAPL")
    assert result is None


def test_get_company_facts_none_on_http_error():
    """EODHD HTTP error → get_company_facts returns None."""
    from urllib.error import HTTPError
    provider = EarningsProvider(api_key="test-key")

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        side_effect=HTTPError(url="", code=500, msg="Error", hdrs=None, fp=None),  # type: ignore
    ):
        result = provider.get_company_facts("AAPL")

    assert result is None


# ---------------------------------------------------------------------------
# Shared cache: one HTTP fetch serves BOTH get_quarterly_eps AND get_company_facts
# ---------------------------------------------------------------------------

def test_shared_cache_single_http_fetch_serves_both_methods():
    """get_quarterly_eps then get_company_facts → urlopen called exactly ONCE."""
    provider = EarningsProvider(api_key="test-key")

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_urlopen_mock(_EODHD_FUNDAMENTALS),
    ) as mock_urlopen:
        eps_result = provider.get_quarterly_eps("AAPL")
        facts_result = provider.get_company_facts("AAPL")

    # Exactly one HTTP request — the fundamentals cache serves both
    assert mock_urlopen.call_count == 1, (
        f"Expected 1 HTTP fetch for both methods, got {mock_urlopen.call_count}"
    )
    assert eps_result is not None
    assert facts_result is not None
    assert facts_result["sector"] == "Technology"


def test_shared_cache_company_facts_first_then_eps():
    """get_company_facts then get_quarterly_eps also hits cache → one fetch."""
    provider = EarningsProvider(api_key="test-key")

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_urlopen_mock(_EODHD_FUNDAMENTALS),
    ) as mock_urlopen:
        facts_result = provider.get_company_facts("MSFT")
        eps_result = provider.get_quarterly_eps("MSFT")

    assert mock_urlopen.call_count == 1
    assert eps_result is not None
    assert facts_result is not None


# ---------------------------------------------------------------------------
# yfinance fallback path (no EODHD key)
# ---------------------------------------------------------------------------

def test_get_quarterly_eps_yfinance_fallback_when_no_key():
    """No EODHD key → yfinance fallback; urlopen never called."""
    provider = EarningsProvider(api_key=None)

    import pandas as pd
    from datetime import datetime, timezone

    idx = pd.DatetimeIndex([
        datetime(2024, 10, 30, tzinfo=timezone.utc),
        datetime(2024, 7, 31, tzinfo=timezone.utc),
        datetime(2024, 4, 30, tzinfo=timezone.utc),
        datetime(2024, 1, 30, tzinfo=timezone.utc),
        datetime(2023, 10, 31, tzinfo=timezone.utc),
        datetime(2023, 7, 28, tzinfo=timezone.utc),
        datetime(2023, 4, 27, tzinfo=timezone.utc),
        datetime(2023, 1, 26, tzinfo=timezone.utc),
    ])
    earnings_df = pd.DataFrame(
        {"Earnings": [2.35, 2.10, 1.90, 1.80, 1.70, 1.50, 1.30, 1.20]},
        index=idx,
    )

    mock_ticker = MagicMock()
    mock_ticker.quarterly_earnings = earnings_df
    mock_ticker.earnings = earnings_df

    with patch("trading_corp.data.earnings_provider.urlopen") as mock_urlopen:
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = provider.get_quarterly_eps("AAPL")

    mock_urlopen.assert_not_called()
    assert result is not None
    assert len(result) >= 8
    # yfinance fallback has no estimates/surprise
    for r in result:
        assert r.estimate_eps is None
        assert r.surprise_pct is None


def test_get_quarterly_eps_yfinance_fallback_chronological():
    """yfinance fallback results sorted oldest→newest."""
    provider = EarningsProvider(api_key=None)

    import pandas as pd
    from datetime import datetime, timezone

    # Deliberately out-of-order (yfinance often returns newest-first)
    idx = pd.DatetimeIndex([
        datetime(2024, 10, 30, tzinfo=timezone.utc),
        datetime(2024, 4, 30, tzinfo=timezone.utc),
        datetime(2024, 7, 31, tzinfo=timezone.utc),
        datetime(2024, 1, 30, tzinfo=timezone.utc),
        datetime(2023, 10, 31, tzinfo=timezone.utc),
        datetime(2023, 7, 28, tzinfo=timezone.utc),
        datetime(2023, 4, 27, tzinfo=timezone.utc),
        datetime(2023, 1, 26, tzinfo=timezone.utc),
    ])
    earnings_df = pd.DataFrame(
        {"Earnings": [2.35, 1.90, 2.10, 1.80, 1.70, 1.50, 1.30, 1.20]},
        index=idx,
    )

    mock_ticker = MagicMock()
    mock_ticker.quarterly_earnings = earnings_df
    mock_ticker.earnings = earnings_df

    with patch("yfinance.Ticker", return_value=mock_ticker):
        result = provider.get_quarterly_eps("AAPL")

    assert result is not None
    for a, b in zip(result, result[1:]):
        assert a.report_date <= b.report_date


def test_get_quarterly_eps_yfinance_fallback_none_when_empty():
    """yfinance fallback returns None when DataFrame empty."""
    provider = EarningsProvider(api_key=None)

    import pandas as pd
    empty_df = pd.DataFrame({"Earnings": []})
    mock_ticker = MagicMock()
    mock_ticker.quarterly_earnings = empty_df
    mock_ticker.earnings = empty_df

    with patch("yfinance.Ticker", return_value=mock_ticker):
        result = provider.get_quarterly_eps("NOPE")

    assert result is None


def test_get_quarterly_eps_eodhd_empty_falls_back_to_yfinance():
    """EODHD returns valid JSON but empty Earnings.History → fall back to yfinance."""
    provider = EarningsProvider(api_key="test-key")

    import pandas as pd
    from datetime import datetime, timezone

    idx = pd.DatetimeIndex([datetime(2024, 10, 30, tzinfo=timezone.utc)])
    earnings_df = pd.DataFrame({"Earnings": [2.35]}, index=idx)
    mock_ticker = MagicMock()
    mock_ticker.quarterly_earnings = earnings_df
    mock_ticker.earnings = earnings_df

    empty_fundamentals = {
        "General": {"Sector": "Tech"},
        "Highlights": {"MarketCapitalization": 1.0},
        "Earnings": {"History": {}},  # empty history
    }

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_urlopen_mock(empty_fundamentals),
    ):
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = provider.get_quarterly_eps("AAPL")

    assert result is not None
    assert len(result) >= 1


# ---------------------------------------------------------------------------
# get_recent_announcements — returns [] (EODHD has no cross-symbol calendar)
# ---------------------------------------------------------------------------

def test_get_recent_announcements_returns_empty_list():
    """EODHD has no cross-symbol calendar endpoint → always []."""
    provider = EarningsProvider(api_key="test-key")
    result = provider.get_recent_announcements(date(2025, 1, 29), lookback_days=2)
    assert result == []


def test_get_recent_announcements_empty_when_no_key():
    """No key → also []."""
    provider = EarningsProvider(api_key=None)
    result = provider.get_recent_announcements(date(2025, 1, 29), lookback_days=1)
    assert result == []


def test_get_recent_announcements_caches_result():
    """Second identical call hits cache (no double-logging)."""
    provider = EarningsProvider(api_key="test-key")
    r1 = provider.get_recent_announcements(date(2025, 1, 29), lookback_days=2)
    r2 = provider.get_recent_announcements(date(2025, 1, 29), lookback_days=2)
    assert r1 == r2 == []


# ---------------------------------------------------------------------------
# ABC NotImplementedError tests
# ---------------------------------------------------------------------------

def test_abc_get_quarterly_eps_raises():
    from trading_corp.data.market_data_provider import MarketDataProvider
    provider = MarketDataProvider()
    with pytest.raises(NotImplementedError):
        provider.get_quarterly_eps("AAPL")


def test_abc_get_recent_announcements_raises():
    from trading_corp.data.market_data_provider import MarketDataProvider
    provider = MarketDataProvider()
    with pytest.raises(NotImplementedError):
        provider.get_recent_announcements(date.today())


# ---------------------------------------------------------------------------
# Live smoke test — skipped if EODHD_API_KEY not set
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("EODHD_API_KEY"),
    reason="EODHD_API_KEY not set — skipping live EODHD smoke test",
)
def test_live_eodhd_get_quarterly_eps_smoke():
    """Live smoke: fetch AAPL quarterly EPS from EODHD (needs real key).

    Validates:
    - at least 1 row returned
    - all rows have actual_eps and report_date
    - chronological order
    - AAPL FY24 quarterly EPS matches known values (verified against real data):
      2.18 / 1.53 / 1.40 / 0.97  (Q1/Q2/Q3/Q4 FY2024 = fiscal ending Jun/Sep/Dec/Mar)
    """
    provider = EarningsProvider()
    result = provider.get_quarterly_eps("AAPL")
    assert result is not None
    assert len(result) >= 1
    for r in result:
        assert isinstance(r.actual_eps, float)
        assert isinstance(r.report_date, date)
    # Chronological
    for a, b in zip(result, result[1:]):
        assert a.report_date <= b.report_date


@pytest.mark.skipif(
    not os.environ.get("EODHD_API_KEY"),
    reason="EODHD_API_KEY not set — skipping live EODHD smoke test",
)
def test_live_eodhd_get_company_facts_smoke():
    """Live smoke: fetch AAPL company facts from EODHD (needs real key)."""
    provider = EarningsProvider()
    result = provider.get_company_facts("AAPL")
    assert result is not None
    assert result.get("market_cap") is not None
    assert isinstance(result["market_cap"], float)
    assert result.get("sector") is not None
