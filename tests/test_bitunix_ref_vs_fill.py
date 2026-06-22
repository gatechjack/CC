"""ref-vs-fill (2026-06-22) — close-side PnL must book from the ACTUAL entry
fill price (broker-observed VWAP, captured at fill-time on the observer path and
stored in extra['actual_entry_fill_price']), NOT the alert/reference price
(entry_reference_price). A systematic per-trade error affecting EVERY live trade,
incl. single/clean ones (unlike D1, which only hit stacked closes).

The fix is two orthogonal pieces:
  * CAPTURE (observer): stamp extra['actual_entry_fill_price'] = fill.price at the
    live-entry registration (mirrors entry_fee_usd / entry_role — covered there).
  * CONSUME (reconciler): `_resolve_entry_price(extra, ref)` prefers the stored
    actual fill, else falls back to entry_reference_price.

Tests here cover the CONSUME side (where the PnL number is produced):
  A. fill != reference (125b6f9e geometry: ref 63465.3, fill 63413.6) → PnL books
     from the FILL and differs from the old reference-based number by ~52pt*qty.
  B. backward-compat: a record with NO actual-fill field still books (fallback to
     entry_reference_price), no crash — old/paper rows unaffected.
  C. composition with D1: a stacked/over-fetch close books
     pnl = (actual_entry_fill - vwap) * min(qty, q_close) — entry term from the
     fill, qty term from D1's cap, orthogonal.
  D. `_resolve_entry_price` unit: prefer fill>0, fall back on absent/None/0/bad.

Mocked + fundless — the signed fetch is always mocked; NO live API call.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_corp.agents.divisions.bitunix_position_reconciler import (
    _autobook_missing_close_real,
    _resolve_entry_price,
)
from trading_corp.persistence import db

_NOW = "2026-06-22T03:30:00+00:00"


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'rvf.db'}"
    db.init_db(url)
    return url


def _seed_short(db_url, order_id, *, qty, ref_entry, actual_fill, stop,
                symbol="BTC/USDT.P", entry_fee=0.0, mdr=0.212) -> None:
    """Live, unbooked short. `actual_fill=None` → omit the field (old/paper row)."""
    extra = {
        "execution_mode": "live", "broker_order_id": order_id,
        "filled_legs": [], "stop_price": stop,
        "max_dollar_risk": mdr, "entry_reference_price": ref_entry,
        "entry_fee_usd": entry_fee,
    }
    if actual_fill is not None:
        extra["actual_entry_fill_price"] = actual_fill
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record (order_id, ts, strategy, division, "
            "symbol, side, qty, entry_reference_price, stop_price, tp_price, "
            "max_hold_seconds, result, extra_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, "2026-06-22T03:00:00+00:00", "bitunix_futures",
             "bitunix_futures", symbol, "sell", qty, ref_entry, stop, None,
             86400, None, json.dumps(extra)),
        )


class _FillBroker:
    def __init__(self, fills):
        self._fills = list(fills)

    async def get_recent_close_fills(self, *, symbol, exit_side, since_ms=None):
        return list(self._fills)


def _row(db_url, order_id):
    with db.connect(db_url) as conn:
        r = conn.execute(
            "SELECT actual_pnl_dollars, extra_json FROM paper_trade_record "
            "WHERE order_id=?", (order_id,)).fetchone()
    extra = json.loads(r["extra_json"]) if r and r["extra_json"] else {}
    return r, extra


# 125b6f9e geometry — the field case.
_REF = 63465.3      # alert / reference (the OLD, wrong basis)
_FILL = 63413.6     # the ACTUAL entry fill — 51.7pt below ref
_DIFF = _REF - _FILL  # 51.7


# ─── A. fill != reference → PnL books from the FILL ──────────────────────


@pytest.mark.asyncio
async def test_pnl_books_from_actual_fill_not_reference(db_url):
    qty, vwap = 0.0014, 63300.0          # short, closes below entry → a win
    _seed_short(db_url, "125b6f9e", qty=qty, ref_entry=_REF, actual_fill=_FILL,
                stop=63600.0)
    broker = _FillBroker([{"price": vwap, "qty": qty, "fee": 0.003}])
    assert await _autobook_missing_close_real(broker, db_url, "125b6f9e", _NOW) \
        == "booked"
    r, _ = _row(db_url, "125b6f9e")

    pnl_from_fill = (_FILL - vwap) * qty   # short: (entry - vwap) * qty
    pnl_from_ref = (_REF - vwap) * qty     # the OLD, over-stated number

    assert r["actual_pnl_dollars"] == pytest.approx(pnl_from_fill)
    assert r["actual_pnl_dollars"] != pytest.approx(pnl_from_ref)
    # the systematic error removed == ~52pt * qty (ref over-credited this short).
    assert pnl_from_ref - r["actual_pnl_dollars"] == pytest.approx(_DIFF * qty)
    assert _DIFF == pytest.approx(51.7, abs=1e-6)


# ─── B. backward-compat: no actual-fill field → fallback, no crash ───────


@pytest.mark.asyncio
async def test_old_record_without_fill_field_falls_back_to_reference(db_url):
    qty, vwap = 0.0014, 63300.0
    # actual_fill=None → field omitted (a pre-fix / paper row).
    _seed_short(db_url, "oldrec", qty=qty, ref_entry=_REF, actual_fill=None,
                stop=63600.0)
    broker = _FillBroker([{"price": vwap, "qty": qty, "fee": 0.003}])
    assert await _autobook_missing_close_real(broker, db_url, "oldrec", _NOW) \
        == "booked"
    r, extra = _row(db_url, "oldrec")
    # books via fallback to entry_reference_price (old behavior, no crash).
    assert r["actual_pnl_dollars"] == pytest.approx((_REF - vwap) * qty)
    assert "actual_entry_fill_price" not in extra


# ─── C. composition with D1: (actual_fill - vwap) * min(qty, q_close) ────


@pytest.mark.asyncio
async def test_composes_with_d1_min_qty_overfetch(db_url):
    # Over-fetch: record qty 0.0005 < netted close 0.0009. D1 caps the qty at
    # 0.0005; ref-vs-fill sets the entry to the actual fill. Combined formula.
    rec_qty, q_close, vwap = 0.0005, 0.0009, 63300.0
    _seed_short(db_url, "compose", qty=rec_qty, ref_entry=_REF, actual_fill=_FILL,
                stop=63600.0)
    broker = _FillBroker([{"price": vwap, "qty": q_close, "fee": 0.006}])
    assert await _autobook_missing_close_real(broker, db_url, "compose", _NOW) \
        == "booked"
    r, _ = _row(db_url, "compose")

    # entry term = ACTUAL fill; qty term = min(rec_qty, q_close) = rec_qty.
    expected = (_FILL - vwap) * min(rec_qty, q_close)
    assert r["actual_pnl_dollars"] == pytest.approx(expected)
    # NOT the reference entry, and NOT the full netted close qty.
    assert r["actual_pnl_dollars"] != pytest.approx((_REF - vwap) * rec_qty)
    assert r["actual_pnl_dollars"] != pytest.approx((_FILL - vwap) * q_close)


# ─── D. _resolve_entry_price unit ────────────────────────────────────────


def test_resolve_entry_price_prefers_actual_fill():
    assert _resolve_entry_price({"actual_entry_fill_price": 63413.6}, 63465.3) \
        == pytest.approx(63413.6)


def test_resolve_entry_price_falls_back():
    # absent → ref
    assert _resolve_entry_price({}, 63465.3) == 63465.3
    # explicit None → ref
    assert _resolve_entry_price({"actual_entry_fill_price": None}, 63465.3) == 63465.3
    # non-positive (0 / negative) → ref (guards a bad/unknown capture)
    assert _resolve_entry_price({"actual_entry_fill_price": 0.0}, 63465.3) == 63465.3
    assert _resolve_entry_price({"actual_entry_fill_price": -5}, 63465.3) == 63465.3
    # non-numeric → ref (never raises)
    assert _resolve_entry_price({"actual_entry_fill_price": "x"}, 63465.3) == 63465.3
    # ref itself may be None (caller handles downstream) — no crash
    assert _resolve_entry_price({}, None) is None
