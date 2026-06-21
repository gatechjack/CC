"""Tests for the PEAD dashboard data builder (`web/pead_view.py`).

Covers the contract the plan calls out: synthetic extra_json -> fuse bars
compute + sort by governing pressure; missing primitives/quote -> graceful
pressure-empty placeholder; and build_pead_view degrades cleanly when the
division isn't wired yet (pressure-empty-first).
"""
from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from trading_corp.persistence.db import init_db
from trading_corp.web.pead_view import (
    assemble_book,
    build_pead_view,
    business_days,
)

_TODAY = date(2026, 6, 22)   # a Monday
_FULL_EXTRA = {
    "entry_atr_14": 4.0, "post_earnings_swing_low": 90.0,
    "pre_earnings_close": 100.0, "earnings_gap_top": 110.0,
    "entry_sue": 2.5, "next_earnings_date": "2026-12-31", "name": "Test Co",
}


def _row(sym, extra, entry=100.0, qty=1.0):
    return {"order_id": sym, "symbol": sym, "qty": qty, "entry_price": entry,
            "opened_ts": _TODAY.isoformat(), "extra": extra}


def test_business_days_weekday_count():
    assert business_days(date(2026, 6, 22), date(2026, 6, 29)) == 5   # Mon→Mon
    assert business_days(date(2026, 6, 22), date(2026, 6, 22)) == 0


def test_assemble_book_computes_pressures_and_sorts_by_governing():
    rows = [_row("AAA", _FULL_EXTRA), _row("BBB", _FULL_EXTRA), _row("CCC", {})]
    # AAA last 107 -> drift (110-107)/10/0.5 = 0.6; BBB last 109 -> drift 0.2; CCC no primitives.
    quotes = {"AAA": 107.0, "BBB": 109.0, "CCC": 105.0}
    book = assemble_book(rows, quotes, _TODAY)
    assert [b["symbol"] for b in book] == ["AAA", "BBB", "CCC"]   # 0.6 > 0.2 > placeholder
    a = book[0]
    assert a["complete"] is True
    assert a["governing"] == "drift"
    assert a["fuse_pct"] == pytest.approx(0.6)
    assert a["fuse_color"] == "amber"
    assert a["name"] == "Test Co"
    assert a["pnl_usd"] == pytest.approx(7.0)         # (107-100)*1
    assert book[2]["complete"] is False               # CCC = pressure-empty placeholder
    assert book[2]["pressures"] is None


def test_assemble_book_placeholder_when_quote_missing():
    book = assemble_book([_row("AAA", _FULL_EXTRA)], {}, _TODAY)   # no quote
    assert book[0]["complete"] is False
    assert book[0]["fuse_pct"] is None


def test_build_pead_view_graceful_when_division_unwired(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    init_db(url)
    deps = SimpleNamespace(db_url=url, data_exec=None)
    view = asyncio.run(build_pead_view(deps, today=_TODAY))
    assert view["mode"] == {"paper": True, "label": "PAPER", "wired": False}
    assert view["book"] == []
    assert view["funnel"]["scanned"] == 0
    assert view["rejections"]["reconciles"] is True   # 0 - 0 == 0
    assert view["health"]["eodhd"]["status"] == "unknown"
    assert view["attribution"]["empty"] is True
    assert view["edge"]["empty"] and view["equity"]["empty"]
    assert view["account"]["equity"] is None
