"""Tests for the PEAD position card (Part 2). Display-only; proves the card's
drift-dead equals the exit engine's own `drift_dead_level` on the SAME stored
slot-aware primitives (Refinement 2), and that a gap<=0 reactor renders the
drift-disabled 'stop-managed only' state (Refinement 1)."""
from __future__ import annotations

from datetime import date

from trading_corp.agents.strategies.pead_pressures import (
    drift_dead_level,
    primitives_from_extra,
)
from trading_corp.web.pead_view import _position_card, assemble_book

# Real stored extras captured from prod 2026-08-02.
_LRCX = {  # AMC name (the slot-aware off-by-one investigation symbol)
    "entry_atr_14": 26.19355714285714, "post_earnings_swing_low": 250.5,
    "pre_earnings_close": 252.35, "earnings_gap_top": 315.61,
    "entry_sue": 3.687719047190613, "report_time": "AfterMarket",
    "name": "LRCX", "company_name": "Lam Research Corp",
}
_ADP = {  # down/flat reactor: earnings_gap_top (260.49) < pre_earnings_close (264.17) -> gap<=0
    "entry_atr_14": 9.528571428571436, "post_earnings_swing_low": 256.9,
    "pre_earnings_close": 264.17, "earnings_gap_top": 260.49,
    "entry_sue": 7.1813, "report_time": "BeforeMarket",
    "name": "ADP", "company_name": "Automatic Data Processing Inc",
}


def test_lrcx_card_drift_dead_matches_engine_exactly():
    prim = primitives_from_extra(_LRCX, 315.61)
    c = _position_card(prim, entry=315.61, last=293.01, held=1)
    # Refinement 2: the card's drift-dead IS the engine's level (same fn, same primitives).
    assert c["drift_dead"] == drift_dead_level(prim)
    assert abs(c["drift_dead"] - 283.98) < 0.01
    assert c["drift_disabled"] is False
    assert abs(c["stop"] - 250.5) < 1e-6
    assert abs(c["room_drift"] - (293.01 - 283.98)) < 0.02      # ~$9.03 above drift-dead
    assert abs(c["room_stop"] - (293.01 - 250.5)) < 0.02        # ~$42.51 above stop
    # NOW sits between entry (top) and stop (bottom): 0 < now_off < 100
    assert 0.0 < c["now_off"] < 100.0


def test_adp_drift_disabled_renders_stop_managed_only():
    prim = primitives_from_extra(_ADP, 260.49)
    c = _position_card(prim, entry=260.49, last=266.43, held=1)
    assert c["drift_disabled"] is True        # gap = 260.49 - 264.17 <= 0
    assert c["drift_dead"] is None
    assert c["drift_off"] is None
    # stop is still shown (the position rides on the stop alone)
    assert c["stop"] is not None and c["room_stop"] is not None


def test_assemble_book_attaches_card_and_none_when_incomplete():
    rows = [{"symbol": "LRCX", "qty": 0.72, "entry_price": 315.61,
             "opened_ts": "2026-07-31", "extra": _LRCX}]
    book = assemble_book(rows, {"LRCX": 293.01}, date(2026, 8, 2))
    assert book[0]["card"]["drift_dead"] == drift_dead_level(primitives_from_extra(_LRCX, 315.61))
    # incomplete row (no quote) -> card is None, not an error
    book2 = assemble_book(rows, {}, date(2026, 8, 2))
    assert book2[0]["complete"] is False and book2[0]["card"] is None
