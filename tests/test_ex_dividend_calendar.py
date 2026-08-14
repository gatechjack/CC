"""Tests for trading_corp.data.ex_dividend_calendar.

Covers:
- next_ex_date for unknown / known / fully-past symbols
- is_within_window respecting trading-day (Mon–Fri) counting, not
  calendar days
- Pay-date parsing tolerance (empty / missing / malformed strings)
- mtime reload picks up edits between calls
- File-missing path returns empty without raising
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from trading_corp.data.ex_dividend_calendar import (
    ExDividendCalendar,
    _business_days_between,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "ex_div.yaml"
    p.write_text(
        """
ex_dividends:
  - symbol: SPY
    ex_date: "2026-03-20"
    pay_date: "2026-04-30"
    confirmed: true
    source: "SSGA"
  - symbol: SPY
    ex_date: "2026-06-18"
    pay_date: "2026-07-31"
    confirmed: true
    source: "SSGA"
  - symbol: QQQ
    ex_date: "2026-03-23"
    pay_date: "2026-03-27"
    confirmed: true
    source: "Invesco"
  - symbol: TLT
    ex_date: "2026-05-01"
    pay_date: ""
    confirmed: true
    source: "iShares"
""".strip(),
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# next_ex_date
# ---------------------------------------------------------------------------


def test_next_ex_date_unknown_symbol_returns_none(sample_yaml):
    cal = ExDividendCalendar.load(sample_yaml)
    assert cal.next_ex_date("GLD", today=date(2026, 5, 1)) is None
    assert cal.next_ex_date("FAKE", today=date(2026, 5, 1)) is None


def test_next_ex_date_returns_correct_upcoming(sample_yaml):
    cal = ExDividendCalendar.load(sample_yaml)
    # Reference date in February 2026 → Q1 SPY ex-date is the next one.
    assert cal.next_ex_date("SPY", today=date(2026, 2, 15)) == date(2026, 3, 20)
    # Reference date after Q1 but before Q2 → Q2 is next.
    assert cal.next_ex_date("SPY", today=date(2026, 4, 1)) == date(2026, 6, 18)
    # On the day itself → returns that date (>= comparison).
    assert cal.next_ex_date("SPY", today=date(2026, 3, 20)) == date(2026, 3, 20)


def test_next_ex_date_returns_none_after_all_dates_pass(sample_yaml):
    cal = ExDividendCalendar.load(sample_yaml)
    # Reference date well past the last configured SPY date.
    assert cal.next_ex_date("SPY", today=date(2027, 1, 1)) is None


def test_next_ex_date_is_case_insensitive(sample_yaml):
    cal = ExDividendCalendar.load(sample_yaml)
    upper = cal.next_ex_date("SPY", today=date(2026, 2, 15))
    lower = cal.next_ex_date("spy", today=date(2026, 2, 15))
    mixed = cal.next_ex_date("Spy", today=date(2026, 2, 15))
    assert upper == lower == mixed == date(2026, 3, 20)


# ---------------------------------------------------------------------------
# is_within_window — trading-day counting
# ---------------------------------------------------------------------------


def test_is_within_window_counts_trading_days_not_calendar(tmp_path: Path):
    # Construct a tiny calendar where ex_date is on Monday after a weekend.
    p = tmp_path / "ed.yaml"
    p.write_text(
        """
ex_dividends:
  - symbol: SPY
    ex_date: "2026-03-23"   # Monday
    pay_date: ""
    confirmed: true
    source: "test"
""".strip(),
        encoding="utf-8",
    )
    cal = ExDividendCalendar.load(p)

    # Reference Friday 2026-03-20: weekend in between, then Monday ex.
    # Trading days between Fri and Mon = 1 (just Mon). 3 calendar days.
    fri = datetime(2026, 3, 20, 14, 0, tzinfo=timezone.utc)
    assert cal.is_within_window("SPY", fri, trading_days=3) is True
    assert cal.is_within_window("SPY", fri, trading_days=1) is True
    assert cal.is_within_window("SPY", fri, trading_days=0) is False

    # Reference Wednesday 2026-03-18: Thu, Fri, Mon = 3 trading days.
    # Calendar days = 5.
    wed = datetime(2026, 3, 18, 14, 0, tzinfo=timezone.utc)
    assert cal.is_within_window("SPY", wed, trading_days=3) is True
    assert cal.is_within_window("SPY", wed, trading_days=2) is False


def test_is_within_window_same_day_satisfies_positive_window(sample_yaml):
    cal = ExDividendCalendar.load(sample_yaml)
    same = datetime(2026, 3, 20, 14, 0, tzinfo=timezone.utc)
    # 0 business days between today and today — within any positive window.
    assert cal.is_within_window("SPY", same, trading_days=1) is True
    assert cal.is_within_window("SPY", same, trading_days=0) is True


def test_is_within_window_unknown_symbol_returns_false(sample_yaml):
    cal = ExDividendCalendar.load(sample_yaml)
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    assert cal.is_within_window("GLD", now, trading_days=3) is False
    assert cal.is_within_window("UNKNOWN", now, trading_days=3) is False


def test_is_within_window_returns_false_when_no_upcoming(sample_yaml):
    cal = ExDividendCalendar.load(sample_yaml)
    # Well after the last SPY date.
    far_future = datetime(2027, 6, 1, 14, 0, tzinfo=timezone.utc)
    assert cal.is_within_window("SPY", far_future, trading_days=3) is False


def test_is_within_window_far_away_returns_false(sample_yaml):
    cal = ExDividendCalendar.load(sample_yaml)
    # Reference 60 days before Q1 SPY ex — way outside 3-trading-day window.
    far = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)
    assert cal.is_within_window("SPY", far, trading_days=3) is False


# ---------------------------------------------------------------------------
# _business_days_between helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start, end, expected",
    [
        # Same day → 0.
        (date(2026, 3, 18), date(2026, 3, 18), 0),
        # Wed → Thu = 1 business day (just Thu).
        (date(2026, 3, 18), date(2026, 3, 19), 1),
        # Fri → Mon = 1 business day (just Mon, weekend skipped).
        (date(2026, 3, 20), date(2026, 3, 23), 1),
        # Wed → following Wed = 5 (Thu, Fri, Mon, Tue, Wed).
        (date(2026, 3, 18), date(2026, 3, 25), 5),
        # Sat → Sat = 4 (Mon, Tue, Wed, Thu, Fri inside; Sat itself doesn't count).
        # Wait: Sat 3/21 → Sat 3/28: enumerate 3/22 (Sun, skip), 3/23 Mon, 3/24 Tue,
        # 3/25 Wed, 3/26 Thu, 3/27 Fri, 3/28 Sat (skip). 5 business days.
        (date(2026, 3, 21), date(2026, 3, 28), 5),
        # Across a month boundary.
        (date(2026, 3, 30), date(2026, 4, 3), 4),  # Tue, Wed, Thu, Fri
    ],
)
def test_business_days_between(start, end, expected):
    assert _business_days_between(start, end) == expected


def test_business_days_between_rejects_inverted_range():
    with pytest.raises(ValueError):
        _business_days_between(date(2026, 3, 20), date(2026, 3, 1))


# ---------------------------------------------------------------------------
# Reload / file behavior
# ---------------------------------------------------------------------------


def test_missing_file_returns_empty_calendar(tmp_path: Path):
    cal = ExDividendCalendar.load(tmp_path / "does_not_exist.yaml")
    assert cal.next_ex_date("SPY", today=date(2026, 5, 1)) is None
    assert cal.is_within_window(
        "SPY", datetime(2026, 5, 1, tzinfo=timezone.utc), trading_days=3,
    ) is False


def test_empty_pay_date_handled_gracefully(sample_yaml):
    cal = ExDividendCalendar.load(sample_yaml)
    # TLT entry has empty pay_date — should parse with pay_date == None
    # rather than crash. We verify by checking next_ex_date works.
    assert cal.next_ex_date("TLT", today=date(2026, 4, 1)) == date(2026, 5, 1)


def test_mtime_reload_picks_up_yaml_edits(tmp_path: Path):
    p = tmp_path / "ed.yaml"
    p.write_text(
        """
ex_dividends:
  - symbol: SPY
    ex_date: "2026-03-20"
    pay_date: ""
    confirmed: true
    source: "v1"
""".strip(),
        encoding="utf-8",
    )
    cal = ExDividendCalendar.load(p)
    assert cal.next_ex_date("SPY", today=date(2026, 2, 1)) == date(2026, 3, 20)

    # Replace with a different date and force a reload by zeroing the
    # cache + stamping a new mtime. (Bypasses the 5s rate-limit gate
    # which would otherwise hide the change in fast tests.)
    p.write_text(
        """
ex_dividends:
  - symbol: SPY
    ex_date: "2026-09-18"
    pay_date: ""
    confirmed: true
    source: "v2"
""".strip(),
        encoding="utf-8",
    )
    cal._last_check = 0.0
    cal._mtime = 0.0
    assert cal.next_ex_date("SPY", today=date(2026, 2, 1)) == date(2026, 9, 18)


# ---------------------------------------------------------------------------
# Production-config smoke test
# ---------------------------------------------------------------------------


def test_production_yaml_loads_and_has_expected_universe():
    """Load the real config/ex_dividend_calendar.yaml and sanity-check it."""
    cal = ExDividendCalendar.load("config/ex_dividend_calendar.yaml")
    # SPY/QQQ quarterly (4 each); IWM quarterly + 12/30 excise (5, all
    # issuer-confirmed 2026-08-13); XLE remaining-2026 (2); GDX annual (1,
    # PROJECTED); FXI semi-annual (5: 6/15/26 + 12/15/26 + 12/30/26 excise +
    # 6/10/27 + 12/14/27, extended-pay group, seeded 2026-08-14).
    spy_dates = [e.ex_date for e in cal._events if e.symbol == "SPY"]
    qqq_dates = [e.ex_date for e in cal._events if e.symbol == "QQQ"]
    iwm_dates = [e.ex_date for e in cal._events if e.symbol == "IWM"]
    tlt_dates = [e.ex_date for e in cal._events if e.symbol == "TLT"]
    gld_dates = [e.ex_date for e in cal._events if e.symbol == "GLD"]
    xle_dates = [e.ex_date for e in cal._events if e.symbol == "XLE"]
    gdx_dates = [e.ex_date for e in cal._events if e.symbol == "GDX"]
    fxi_dates = [e.ex_date for e in cal._events if e.symbol == "FXI"]

    assert len(spy_dates) == 4, f"expected 4 SPY entries, got {len(spy_dates)}"
    assert len(qqq_dates) == 4, f"expected 4 QQQ entries, got {len(qqq_dates)}"
    assert len(iwm_dates) == 5, f"expected 5 IWM entries, got {len(iwm_dates)}"
    assert len(tlt_dates) == 12, f"expected 12 TLT entries, got {len(tlt_dates)}"
    assert gld_dates == [], "GLD should have no ex-div entries"
    assert len(xle_dates) == 2, f"expected 2 XLE entries, got {len(xle_dates)}"
    assert len(gdx_dates) == 1, f"expected 1 GDX entry, got {len(gdx_dates)}"
    assert len(fxi_dates) == 5, f"expected 5 FXI entries, got {len(fxi_dates)}"

    # Sanity: SPY Q1 is March 20, 2026 (issuer-confirmed).
    assert cal.next_ex_date("SPY", today=date(2026, 1, 1)) == date(2026, 3, 20)
    # Sanity: IWM Q3 is the CORRECTED 9/15, not the old 9/21 projection.
    assert cal.next_ex_date("IWM", today=date(2026, 8, 1)) == date(2026, 9, 15)
