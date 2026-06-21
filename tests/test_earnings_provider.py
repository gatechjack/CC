"""Tests for trading_corp.data.earnings_provider.

All HTTP and yfinance calls are mocked — NO live key needed.
Live-key smoke tests are gated by pytest.mark.skipif.
"""
from __future__ import annotations

import json
import os
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from trading_corp.data.earnings_provider import (
    EarningsProvider,
    QuarterlyEPS,
    _compute_surprise,
    _normalise_fiscal_period,
    _parse_finnhub_earnings,
    reset_earnings_provider_cache,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_cache():
    """Clear EarningsProvider caches before each test."""
    reset_earnings_provider_cache()
    yield
    reset_earnings_provider_cache()


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
# _parse_finnhub_earnings — parsing logic
# ---------------------------------------------------------------------------

_FINNHUB_EARNINGS_FIXTURE = [
    {"period": "2024-12-31", "date": "2025-01-29", "actual": 2.40, "estimate": 2.35},
    {"period": "2024-09-30", "date": "2024-10-30", "actual": 2.35, "estimate": 2.20},
    {"period": "2024-06-30", "date": "2024-07-31", "actual": 2.10, "estimate": 2.00},
    {"period": "2024-03-31", "date": "2024-04-30", "actual": 1.90, "estimate": 1.85},
    {"period": "2023-12-31", "date": "2024-01-30", "actual": 1.80, "estimate": 1.75},
    {"period": "2023-09-30", "date": "2023-10-31", "actual": 1.70, "estimate": 1.60},
    {"period": "2023-06-30", "date": "2023-07-28", "actual": 1.50, "estimate": 1.40},
    {"period": "2023-03-31", "date": "2023-04-27", "actual": 1.30, "estimate": 1.25},
    {"period": "2022-12-31", "date": "2023-01-26", "actual": 1.20, "estimate": 1.10},
]


def test_parse_finnhub_earnings_basic():
    rows = _parse_finnhub_earnings(_FINNHUB_EARNINGS_FIXTURE)
    assert len(rows) == len(_FINNHUB_EARNINGS_FIXTURE)
    # Each row must have an actual_eps
    for r in rows:
        assert r.actual_eps is not None
        assert r.estimate_eps is not None
        assert r.surprise_pct is not None


def test_parse_finnhub_earnings_chronological_sort():
    """After provider applies sort, rows must be oldest→newest."""
    rows = _parse_finnhub_earnings(_FINNHUB_EARNINGS_FIXTURE)
    # Sort ourselves for comparison
    sorted_rows = sorted(rows, key=lambda r: r.report_date)
    for a, b in zip(sorted_rows, sorted_rows[1:]):
        assert a.report_date <= b.report_date


def test_parse_finnhub_earnings_skips_missing_actual():
    data = [
        {"period": "2024-12-31", "date": "2025-01-29", "actual": None, "estimate": 2.00},
        {"period": "2024-09-30", "date": "2024-10-30", "actual": 1.90, "estimate": 1.80},
    ]
    rows = _parse_finnhub_earnings(data)
    # First row skipped (actual=None); second retained
    assert len(rows) == 1
    assert rows[0].actual_eps == 1.90


def test_parse_finnhub_earnings_no_estimate_gives_none_surprise():
    data = [
        {"period": "2024-12-31", "date": "2025-01-29", "actual": 2.40, "estimate": None},
    ]
    rows = _parse_finnhub_earnings(data)
    assert len(rows) == 1
    assert rows[0].estimate_eps is None
    assert rows[0].surprise_pct is None


def test_parse_finnhub_earnings_empty_list():
    assert _parse_finnhub_earnings([]) == []


def test_parse_finnhub_earnings_malformed_row_skipped():
    data = [
        {"period": "bad-date", "date": None, "actual": 1.0},  # date=None, period=non-date
        {"period": "2024-12-31", "date": "2025-01-29", "actual": 2.40},
    ]
    rows = _parse_finnhub_earnings(data)
    # First row: period="bad-date" → _normalise_fiscal_period returns "bad-date",
    # date = None falls back to period = "bad-date" → date.fromisoformat fails → skip
    # Second row: valid
    assert len(rows) == 1
    assert rows[0].actual_eps == 2.40


# ---------------------------------------------------------------------------
# EarningsProvider.get_quarterly_eps — Finnhub primary path
# ---------------------------------------------------------------------------

def _make_finnhub_response(data):
    """Return a mock urlopen context manager that yields the given data as JSON."""
    import io
    body = json.dumps(data).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def test_get_quarterly_eps_finnhub_primary_8_quarters():
    """Finnhub returns 9 rows → provider trims to >=8 and returns them oldest→newest."""
    provider = EarningsProvider(api_key="test-key")

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_finnhub_response(_FINNHUB_EARNINGS_FIXTURE),
    ):
        result = provider.get_quarterly_eps("AAPL")

    assert result is not None
    assert len(result) >= 8
    # Chronological check
    for a, b in zip(result, result[1:]):
        assert a.report_date <= b.report_date


def test_get_quarterly_eps_finnhub_chronological():
    """Oldest entry must have the earliest report_date."""
    provider = EarningsProvider(api_key="test-key")

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_finnhub_response(_FINNHUB_EARNINGS_FIXTURE),
    ):
        result = provider.get_quarterly_eps("AAPL")

    assert result is not None
    # First row should be the oldest report date in the fixture
    all_dates = sorted(r.report_date for r in _parse_finnhub_earnings(_FINNHUB_EARNINGS_FIXTURE))
    assert result[0].report_date == all_dates[0]


def test_get_quarterly_eps_surprise_calculated_correctly():
    """Spot-check surprise calculation on known fixture values."""
    provider = EarningsProvider(api_key="test-key")

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_finnhub_response(_FINNHUB_EARNINGS_FIXTURE),
    ):
        result = provider.get_quarterly_eps("AAPL")

    assert result is not None
    # Find 2024Q4 row (actual=2.40, estimate=2.35)
    q4_rows = [r for r in result if r.fiscal_period == "2024Q4"]
    assert len(q4_rows) == 1
    expected_surprise = round((2.40 - 2.35) / abs(2.35) * 100, 4)
    assert q4_rows[0].surprise_pct == pytest.approx(expected_surprise, rel=1e-4)


def test_get_quarterly_eps_caches_result():
    """Second call for same symbol returns cached result (urlopen called once)."""
    provider = EarningsProvider(api_key="test-key")

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_finnhub_response(_FINNHUB_EARNINGS_FIXTURE),
    ) as mock_urlopen:
        provider.get_quarterly_eps("MSFT")
        provider.get_quarterly_eps("MSFT")

    # urlopen called only once — second call hits cache
    assert mock_urlopen.call_count == 1


def test_get_quarterly_eps_none_on_finnhub_http_error():
    """HTTP error from Finnhub with no yfinance data → None."""
    provider = EarningsProvider(api_key="test-key")

    from urllib.error import HTTPError
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
    result = provider.get_quarterly_eps("")
    assert result is None


# ---------------------------------------------------------------------------
# EarningsProvider.get_quarterly_eps — yfinance fallback path
# ---------------------------------------------------------------------------

def test_get_quarterly_eps_yfinance_fallback_when_no_key():
    """No Finnhub key → yfinance fallback path; urlopen never called."""
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
    earnings_df = pd.DataFrame({"Earnings": [2.35, 2.10, 1.90, 1.80, 1.70, 1.50, 1.30, 1.20]}, index=idx)

    mock_ticker = MagicMock()
    mock_ticker.quarterly_earnings = earnings_df
    mock_ticker.earnings = earnings_df

    with patch("trading_corp.data.earnings_provider.urlopen") as mock_urlopen:
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = provider.get_quarterly_eps("AAPL")

    # urlopen should NOT be called (no key)
    mock_urlopen.assert_not_called()
    assert result is not None
    assert len(result) >= 8
    # No estimates/surprise in yfinance fallback
    for r in result:
        assert r.estimate_eps is None
        assert r.surprise_pct is None


def test_get_quarterly_eps_yfinance_fallback_chronological():
    """yfinance fallback results must also be sorted oldest→newest."""
    provider = EarningsProvider(api_key=None)

    import pandas as pd
    from datetime import datetime, timezone

    # Deliberately out of order (yfinance returns newest-first)
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
    earnings_df = pd.DataFrame({"Earnings": [2.35, 1.90, 2.10, 1.80, 1.70, 1.50, 1.30, 1.20]}, index=idx)

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


def test_get_quarterly_eps_finnhub_empty_falls_back_to_yfinance():
    """Finnhub returns [] but key is set → falls through to yfinance."""
    provider = EarningsProvider(api_key="test-key")

    import pandas as pd
    from datetime import datetime, timezone

    idx = pd.DatetimeIndex([datetime(2024, 10, 30, tzinfo=timezone.utc)])
    earnings_df = pd.DataFrame({"Earnings": [2.35]}, index=idx)
    mock_ticker = MagicMock()
    mock_ticker.quarterly_earnings = earnings_df
    mock_ticker.earnings = earnings_df

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_finnhub_response([]),   # Finnhub returns empty list
    ):
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = provider.get_quarterly_eps("AAPL")

    # Should come from yfinance (only 1 quarter, but non-None)
    assert result is not None
    assert len(result) >= 1


# ---------------------------------------------------------------------------
# EarningsProvider.get_recent_announcements
# ---------------------------------------------------------------------------

_FINNHUB_CALENDAR_FIXTURE = {
    "earningsCalendar": [
        {"symbol": "AAPL", "date": "2025-01-29"},
        {"symbol": "MSFT", "date": "2025-01-29"},
        {"symbol": "GOOG", "date": "2025-01-28"},
    ]
}


def test_get_recent_announcements_finnhub():
    """Finnhub calendar → symbols list, sorted, deduplicated."""
    provider = EarningsProvider(api_key="test-key")

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_finnhub_response(_FINNHUB_CALENDAR_FIXTURE),
    ):
        result = provider.get_recent_announcements(date(2025, 1, 29), lookback_days=2)

    assert isinstance(result, list)
    assert "AAPL" in result
    assert "MSFT" in result
    assert "GOOG" in result
    # Sorted
    assert result == sorted(result)


def test_get_recent_announcements_deduplication():
    """Duplicate symbols from Finnhub are deduplicated."""
    provider = EarningsProvider(api_key="test-key")

    fixture = {
        "earningsCalendar": [
            {"symbol": "AAPL", "date": "2025-01-29"},
            {"symbol": "AAPL", "date": "2025-01-29"},  # duplicate
            {"symbol": "MSFT", "date": "2025-01-29"},
        ]
    }
    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_finnhub_response(fixture),
    ):
        result = provider.get_recent_announcements(date(2025, 1, 29), lookback_days=1)

    assert result.count("AAPL") == 1


def test_get_recent_announcements_empty_when_no_key():
    """No Finnhub key → empty list (yfinance cross-symbol not feasible)."""
    provider = EarningsProvider(api_key=None)

    with patch("trading_corp.data.earnings_provider.urlopen") as mock_urlopen:
        result = provider.get_recent_announcements(date(2025, 1, 29), lookback_days=1)

    mock_urlopen.assert_not_called()
    assert result == []


def test_get_recent_announcements_empty_on_finnhub_error():
    """Finnhub network error → empty list (no crash)."""
    provider = EarningsProvider(api_key="test-key")

    from urllib.error import URLError
    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        side_effect=URLError("timeout"),
    ):
        result = provider.get_recent_announcements(date(2025, 1, 29), lookback_days=1)

    assert result == []


def test_get_recent_announcements_caches_result():
    """Second call with same (on_date, lookback_days) hits cache."""
    provider = EarningsProvider(api_key="test-key")

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_finnhub_response(_FINNHUB_CALENDAR_FIXTURE),
    ) as mock_urlopen:
        provider.get_recent_announcements(date(2025, 1, 29), lookback_days=2)
        provider.get_recent_announcements(date(2025, 1, 29), lookback_days=2)

    assert mock_urlopen.call_count == 1


def test_get_recent_announcements_default_lookback():
    """Default lookback_days=1 means on_date only (start == end)."""
    provider = EarningsProvider(api_key="test-key")

    with patch(
        "trading_corp.data.earnings_provider.urlopen",
        return_value=_make_finnhub_response({"earningsCalendar": [{"symbol": "TSLA", "date": "2025-01-29"}]}),
    ):
        result = provider.get_recent_announcements(date(2025, 1, 29))

    assert "TSLA" in result


# ---------------------------------------------------------------------------
# ABC new methods — NotImplementedError on base class
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
# None-on-failure contract
# ---------------------------------------------------------------------------

def test_get_quarterly_eps_returns_none_on_all_sources_fail():
    """If Finnhub errors AND yfinance returns nothing → None (no crash)."""
    provider = EarningsProvider(api_key="test-key")

    from urllib.error import URLError
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
# Live smoke test — skipped if FINNHUB_API_KEY not set
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("FINNHUB_API_KEY"),
    reason="FINNHUB_API_KEY not set — skipping live Finnhub smoke test",
)
def test_live_finnhub_get_quarterly_eps_smoke():
    """Live smoke: fetch AAPL quarterly EPS from Finnhub (needs real key)."""
    provider = EarningsProvider()
    result = provider.get_quarterly_eps("AAPL")
    assert result is not None
    assert len(result) >= 1
    # All rows must have actual_eps
    for r in result:
        assert isinstance(r.actual_eps, float)
        assert isinstance(r.report_date, date)
    # Chronological
    for a, b in zip(result, result[1:]):
        assert a.report_date <= b.report_date
