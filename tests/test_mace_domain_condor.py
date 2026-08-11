"""Regression for CondorSpec.closing_legs() (fix 2026-08-10).

The Phase-1 draft inverted the CALL side of the flatten (short_call "sell" /
long_call "buy" — which re-OPENS the call spread instead of closing it). Surfaced
from Phase-3 rh_broker as the first consumer; a live exit/PT would have DOUBLED
the call spread rather than flattened it. These tests pin the invariant: closing
reverses EVERY opening side and carries effect="close"."""
from __future__ import annotations

from datetime import date

from trading_corp.mace.domain import CondorSpec

SPEC = CondorSpec("SPY", date(2026, 9, 18), 585.0, 582.0, 615.0, 618.0, 3.0)
_REV = {"buy": "sell", "sell": "buy"}


def test_closing_reverses_every_opening_side():
    opening = {(l.opt_type, l.strike): l.side for l in SPEC.opening_legs()}
    closing = {(l.opt_type, l.strike): l.side for l in SPEC.closing_legs()}
    for key, oside in opening.items():
        assert closing[key] == _REV[oside], f"{key} not reversed on close"


def test_closing_call_side_flattens_correctly():
    c = {(l.opt_type, l.strike): l.side for l in SPEC.closing_legs()}
    assert c[("call", 615.0)] == "buy"    # buy back the short call
    assert c[("call", 618.0)] == "sell"   # sell the long call
    assert c[("put", 585.0)] == "buy"     # buy back the short put
    assert c[("put", 582.0)] == "sell"    # sell the long put


def test_effects_open_vs_close():
    assert all(l.effect == "open" for l in SPEC.opening_legs())
    assert all(l.effect == "close" for l in SPEC.closing_legs())
