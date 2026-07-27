"""PMCCPair.structure_type — classify by WHAT COVERS THE SHORT, not by the long
leg's remaining DTE.

Regression for the "COVERED CALL" tile mislabel: a real LEAP that has aged below
180 DTE (e.g. a 2027-01-15 LEAP at ~172 DTE) is STILL a PMCC — the long call
covers the short at any remaining DTE. A covered call requires equity SHARES
(>= 100 per short contract) as the cover. Display/classifier layer only.
"""
from trading_corp.web.data import OptionLeg, PMCCPair, _group_pmcc_pairs


def _call(dte, qty, strike=100.0, expiry="2027-01-15"):
    """A minimal call leg (qty signed: >0 long, <0 short)."""
    return OptionLeg(
        underlying="X", option_type="call", expiry=expiry, strike=strike,
        dte=dte, qty=qty, avg_per_share=1.0, mark_per_share=1.0, delta=0.5,
        underlying_price=105.0,
    )


def _pair(leap=None, short=None, shares=None, extras=None):
    return PMCCPair(
        underlying="X", underlying_price=105.0, leap=leap, short_call=short,
        extras=extras or [], underlying_shares=shares,
    )


# ── the core fix: aged LEAP is still a PMCC, never covered_call ──────────────

def test_aged_172dte_leap_is_pmcc_not_covered_call():
    # A genuine LEAP aged to ~172 DTE (2027-01-15) + a short. The OLD code
    # returned 'covered_call' (172 < 180); it must now be 'pmcc'.
    leap = _call(dte=172, qty=1)
    short = _call(dte=4, qty=-1, strike=120.0)
    assert _pair(leap=leap, short=short).structure_type == "pmcc"


def test_long_dated_leap_is_pmcc():
    # A far-dated LEAP (~1639 DTE, 2028-01-21) + short — unchanged.
    leap = _call(dte=1639, qty=1, expiry="2028-01-21")
    short = _call(dte=4, qty=-1, strike=120.0)
    assert _pair(leap=leap, short=short).structure_type == "pmcc"


def test_pmcc_regardless_of_leap_age_boundary():
    # No 180 discriminator anywhere: dte just under / at / over 180 all -> pmcc.
    for dte in (1, 90, 179, 180, 181, 5000):
        assert _pair(leap=_call(dte=dte, qty=1), short=_call(dte=3, qty=-1)
                     ).structure_type == "pmcc"


# ── covered_call now means SHARES-backed (>= 100 per short contract) ─────────

def test_shares_covered_short_is_covered_call():
    # No long call; 164.69 shares (>= 100) cover one short contract.
    short = _call(dte=30, qty=-1)
    assert _pair(short=short, shares=164.69).structure_type == "covered_call"


def test_shares_must_meet_100_per_short_contract():
    # 164.69 shares cover ONE short but not TWO (needs 200) -> naked short.
    two_short = _call(dte=30, qty=-2)
    assert _pair(short=two_short, shares=164.69).structure_type == "short_only"
    # ...bump shares to 250 -> fully covers 2 contracts -> covered_call.
    assert _pair(short=two_short, shares=250.0).structure_type == "covered_call"


def test_short_with_no_cover_is_short_only():
    short = _call(dte=30, qty=-1)
    assert _pair(short=short, shares=None).structure_type == "short_only"   # unknown shares
    assert _pair(short=short, shares=0.0).structure_type == "short_only"    # zero shares
    assert _pair(short=short, shares=50.0).structure_type == "short_only"   # < 100


def test_long_call_cover_wins_over_shares():
    # Both a LEAP AND shares present -> PMCC (the long call is the cover).
    leap = _call(dte=172, qty=1)
    short = _call(dte=4, qty=-1)
    assert _pair(leap=leap, short=short, shares=1000.0).structure_type == "pmcc"


# ── uncovered_leap / naked_call: no flip on LEAP age (the same DTE artifact) ─

def test_long_only_is_uncovered_leap_at_any_age():
    # Old code: dte>=180 -> uncovered_leap, else -> naked_call. Now both ages
    # classify as uncovered_leap (a long call with no short, regardless of age).
    assert _pair(leap=_call(dte=172, qty=1)).structure_type == "uncovered_leap"
    assert _pair(leap=_call(dte=1639, qty=1)).structure_type == "uncovered_leap"
    assert _pair(leap=_call(dte=10, qty=1)).structure_type == "uncovered_leap"
    # 'naked_call' is retired — never returned.
    assert _pair(leap=_call(dte=10, qty=1)).structure_type != "naked_call"


def test_no_calls_is_other():
    assert _pair().structure_type == "other"


# ── _group_pmcc_pairs threads underlying_shares end-to-end ───────────────────

def _leg(sym, dte, qty, otype="call"):
    return OptionLeg(underlying=sym, option_type=otype, expiry="2027-01-15",
                     strike=100.0, dte=dte, qty=qty, avg_per_share=1.0,
                     mark_per_share=1.0, delta=0.5, underlying_price=105.0)


def test_group_threads_shares_and_labels_covered_call():
    # STRC: a short call + 164.69 shares, no long call -> covered_call.
    legs = [_leg("STRC", dte=30, qty=-1)]
    pairs, _ = _group_pmcc_pairs(legs, {"STRC": 105.0}, {"STRC": 164.69})
    p = next(p for p in pairs if p.underlying == "STRC")
    assert p.underlying_shares == 164.69
    assert p.structure_type == "covered_call"


def test_group_aged_leap_pair_labels_pmcc():
    # TSLA: aged LEAP (172 DTE) + short, no shares -> pmcc (not covered_call).
    legs = [_leg("TSLA", dte=172, qty=1), _leg("TSLA", dte=4, qty=-1)]
    pairs, _ = _group_pmcc_pairs(legs, {"TSLA": 300.0}, {})
    p = next(p for p in pairs if p.underlying == "TSLA")
    assert p.underlying_shares is None
    assert p.structure_type == "pmcc"


def test_group_shares_default_none_when_omitted():
    # Backward-compat: shares arg optional; omitted -> underlying_shares None.
    legs = [_leg("TSLA", dte=172, qty=1), _leg("TSLA", dte=4, qty=-1)]
    pairs, _ = _group_pmcc_pairs(legs, {"TSLA": 300.0})
    assert next(p for p in pairs if p.underlying == "TSLA").underlying_shares is None
