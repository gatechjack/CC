"""Tests for the company-name backfill transform (scripts/pead_name_backfill.py).

Proves the write is SURGICAL — only extra_json['company_name'] is added; every other
key/value (entry, stop, the locked primitives, notional, name/ticker, ...) is preserved
exactly; idempotent; empty name is a no-op. Plus: the book maps the backfilled
company_name onto the rendered row.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

# scripts/ is not a package; load the module by path.
_SPEC = importlib.util.spec_from_file_location(
    "pead_name_backfill",
    Path(__file__).resolve().parents[1] / "scripts" / "pead_name_backfill.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
add_company_name = _MOD.add_company_name

# A real OPEN-position extra (LRCX) captured from prod 2026-08-02.
_LRCX_EXTRA = {
    "entry_atr_14": 26.19355714285714, "post_earnings_swing_low": 250.5,
    "pre_earnings_close": 252.35, "earnings_gap_top": 315.61, "next_earnings_date": None,
    "entry_sue": 3.687719047190613, "report_time": "AfterMarket", "name": "LRCX",
    "entry_reference_price": 315.61, "stop_price": 250.5, "source_signal": "srw_sue",
    "notional_usd": 227.82097, "executed_notional": 227.81,
}
_TRADING_KEYS = ("entry_reference_price", "stop_price", "post_earnings_swing_low",
                 "earnings_gap_top", "pre_earnings_close", "entry_atr_14",
                 "notional_usd", "executed_notional", "name")


def test_backfill_adds_only_company_name():
    new, changed = add_company_name(_LRCX_EXTRA, "Lam Research Corp.")
    assert changed is True
    assert new["company_name"] == "Lam Research Corp."
    assert set(new) - set(_LRCX_EXTRA) == {"company_name"}   # ONLY company_name added
    for k, v in _LRCX_EXTRA.items():                          # every original value intact
        assert new[k] == v, k


def test_backfill_touches_no_trading_field():
    new, _ = add_company_name(_LRCX_EXTRA, "Lam Research Corp.")
    for k in _TRADING_KEYS:
        assert new[k] == _LRCX_EXTRA[k], k


def test_backfill_idempotent():
    once, _ = add_company_name(_LRCX_EXTRA, "Lam Research Corp.")
    twice, changed = add_company_name(once, "Lam Research Corp.")
    assert changed is False and twice == once


def test_backfill_skips_empty_name():
    new, changed = add_company_name(_LRCX_EXTRA, "")
    assert changed is False and "company_name" not in new


def test_assemble_book_renders_backfilled_company_name():
    from trading_corp.web.pead_view import assemble_book
    extra, _ = add_company_name(_LRCX_EXTRA, "Lam Research Corp.")
    rows = [{"symbol": "LRCX", "qty": 0.7218, "entry_price": 315.61,
             "opened_ts": "2026-07-31", "extra": extra}]
    book = assemble_book(rows, {"LRCX": 293.01}, date(2026, 8, 2))
    assert book[0]["company_name"] == "Lam Research Corp."
