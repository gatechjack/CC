"""Unit tests for the PEAD derived, self-balancing sizer + the live max_concurrent
dial (trading_corp.agents.strategies.pead_sizing).

Covers the Part A properties the operator relies on: every slot funds when cash is
sufficient, the last slot is never stranded, a safety buffer is always reserved,
sub-$1 slots skip cleanly (no partial-fund), and the dial override round-trips
through agent_state. Also a static guard that the sizer never leaked into the exit
path (max_concurrent is ENTRY-only).
"""
from __future__ import annotations

import inspect

from trading_corp.agents.strategies import pead_pressures, pead_sizing
from trading_corp.agents.strategies.pead_sizing import derive_wave_sizes
from trading_corp.agents.strategies.pead_strategy import PEADStrategy
from trading_corp.persistence.db import init_db, set_agent_state


def test_full_wave_funds_every_slot_and_last_is_not_stranded():
    sizes = derive_wave_sizes(213.01, 10, safety_factor=0.95)
    assert len(sizes) == 10                       # all 10 slots funded at ~$213 settled
    assert all(s >= 1.0 for s in sizes)           # each above RH's $1 fractional floor
    assert sum(sizes) <= 213.01 + 1e-9            # never overspends settled cash
    assert sizes[-1] > 0                          # last slot fundable BY CONSTRUCTION
    assert sizes[0] < sizes[-1]                   # sizes rise as the reserved sliver redistributes


def test_recompute_leaves_a_safety_buffer():
    sizes = derive_wave_sizes(1000.0, 10, safety_factor=0.95)
    assert len(sizes) == 10
    leftover = 1000.0 - sum(sizes)
    assert leftover > 0                           # safety_factor<1 always leaves a buffer
    assert leftover < 1000.0 * 0.10               # ~5% sliver, bounded


def test_sub_dollar_slot_skips_cleanly_no_partial():
    # $5 across 20 slots -> per_name ~ $0.24 < $1 -> nothing funds (clean skip, no partial)
    assert derive_wave_sizes(5.0, 20, safety_factor=0.95) == []


def test_zero_none_and_no_slots_fund_nothing():
    assert derive_wave_sizes(0.0, 10) == []
    assert derive_wave_sizes(None, 10) == []
    assert derive_wave_sizes(100.0, 0) == []


def test_dial_raised_to_20_with_more_cash_funds_20():
    # +$2,500 hypothetical on the ~$213 settled, dial at 20
    sizes = derive_wave_sizes(2713.01, 20, safety_factor=0.95)
    assert len(sizes) == 20
    assert sizes[0] > 100.0                        # ~$128 each — legibly fundable
    assert sum(sizes) <= 2713.01 + 1e-9


def test_safety_factor_scales_every_slot():
    a = derive_wave_sizes(1000.0, 5, safety_factor=0.95)
    b = derive_wave_sizes(1000.0, 5, safety_factor=1.00)
    assert a[0] < b[0]                             # lower safety -> smaller first slot


def test_override_roundtrip_and_effective(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    init_db(url)
    # unset -> effective falls back to yaml, override inactive
    n0, active0 = pead_sizing.effective_max_concurrent(url)
    assert active0 is False and n0 >= 1
    assert pead_sizing.read_max_concurrent_override(url) is None
    # set -> override wins
    set_agent_state(pead_sizing.DIVISION, pead_sizing.OVERRIDE_KEY,
                    {"max_concurrent": 25}, db_url=url)
    assert pead_sizing.read_max_concurrent_override(url) == 25
    assert pead_sizing.effective_max_concurrent(url) == (25, True)
    # non-integer stored value -> ignored, not crash
    set_agent_state(pead_sizing.DIVISION, pead_sizing.OVERRIDE_KEY,
                    {"max_concurrent": "oops"}, db_url=url)
    assert pead_sizing.read_max_concurrent_override(url) is None
    # non-positive -> ignored
    set_agent_state(pead_sizing.DIVISION, pead_sizing.OVERRIDE_KEY,
                    {"max_concurrent": 0}, db_url=url)
    assert pead_sizing.read_max_concurrent_override(url) is None


def test_max_concurrent_never_leaked_into_the_exit_path():
    # max_concurrent is an ENTRY-only dial; the exit engine must never read it.
    assert "max_concurrent" not in inspect.getsource(pead_pressures)
    assert "max_concurrent" not in inspect.getsource(PEADStrategy.manage)
    # ...and the derived/floored sizer's symbols are entry-only too.
    for token in ("cash_remaining", "size_min_usd", "derive_wave_sizes", "settled_cash"):
        assert token not in inspect.getsource(PEADStrategy.manage), token


def test_scan_consumes_the_shared_sizer_single_source_of_truth():
    # The scan must SIZE via pead_sizing.derive_wave_sizes (same fn the dashboard
    # readout uses) so the two can never diverge.
    src = inspect.getsource(PEADStrategy.scan)
    assert "derive_wave_sizes" in src
    assert "size_min_usd" in src


# ── $50 per-name floor (fund fewer, not smaller) ─────────────────────────────
def test_floor_funds_fewer_at_or_above_the_floor():
    # $213 settled, 10 slots, $50 floor -> ~4 names each >= $50 (NOT 10 at ~$20)
    sizes = derive_wave_sizes(213.01, 10, safety_factor=0.95, size_min_usd=50.0)
    assert len(sizes) == 4
    assert all(s >= 50.0 for s in sizes)
    assert sum(sizes) <= 213.01 + 1e-9


def test_floor_not_binding_when_cash_is_ample():
    # $2,713 settled, 20 slots, $50 floor -> all 20 fund (~$129); floor not binding
    sizes = derive_wave_sizes(2713.01, 20, safety_factor=0.95, size_min_usd=50.0)
    assert len(sizes) == 20
    assert all(s >= 50.0 for s in sizes)


def test_floor_dial_ceiling_does_not_inflate_the_count():
    # dial 30 but $213 settled + $50 floor -> ~4, NOT 30 at ~$7
    sizes = derive_wave_sizes(213.01, 30, safety_factor=0.95, size_min_usd=50.0)
    assert len(sizes) == 4
    assert all(s >= 50.0 for s in sizes)


def test_floor_funds_zero_cleanly_when_cash_below_one_name():
    # $30 can't fund even one $50 name -> zero, clean
    assert derive_wave_sizes(30.0, 10, safety_factor=0.95, size_min_usd=50.0) == []


def test_floor_never_opens_sub_floor_even_with_safety_haircut():
    # $200/floor $50: a naive floor(200/50)=4 would make the first name $47.50 (< $50);
    # the floor-guaranteeing count is 3, and every name is >= $50.
    sizes = derive_wave_sizes(200.0, 10, safety_factor=0.95, size_min_usd=50.0)
    assert len(sizes) == 3
    assert all(s >= 50.0 for s in sizes)


def test_default_call_has_no_floor_backcompat():
    # No size_min_usd -> pre-floor behaviour preserved (10 names at ~$20).
    assert len(derive_wave_sizes(213.01, 10)) == 10
