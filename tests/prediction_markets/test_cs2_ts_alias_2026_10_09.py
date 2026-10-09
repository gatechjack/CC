"""CS2 ticker-code alias ts->spirit (2026-10-09). Kalshi's KXCS2GAME -TS side = Team Spirit, whose Polymarket
outcome is the SHORT name "Spirit"; the full-name initialism TS is not an ordered subsequence of "Spirit", so the
INDEPENDENT ticker-code audit soft-flagged a CORRECT, venue-verified FILLED position (kalshi_jack id 5038; Kalshi
title "Spirit wins", yes_sub_title "Spirit"; whale 0xd27cc742 held "Spirit"; cond 0x363c..5e8f).

This adds ONE venue-verified alias entry in the established shape (lg->luminosity, tl->liquid), which PRESERVES the
audit's independence from the matcher (the matcher binds on yes_sub_title; the ticker code is independent evidence).
★ The alias MUST NOT clear a genuine inversion -- it clears TS<->Spirit ONLY, never TS<->any-other-team.
"""
from trading_corp.prediction_markets.live_driver import _audit_leg_independent as AUD
from trading_corp.prediction_markets import leg_audit as LA

TS_TICKER = "KXCS2GAME-26OCT091200TSFAL-TS"     # the Spirit side of "Team Falcons vs Spirit"
FAL_TICKER = "KXCS2GAME-26OCT091200TSFAL-FAL"   # the Falcons side of the SAME market


# ── the alias fires: venue-confirmed TS = Spirit -> ok:code_alias, and reads CLEAN (not surfaced) ────────────────
def test_ts_alias_clears_spirit():
    v = AUD("cs2", "Spirit", TS_TICKER, "yes")
    assert v == "ok:code_alias"
    assert LA.classify_leg_audit(v) == LA.STATE_CLEAN


# ── ★ the one direction that is not survivable: the alias must NEVER pass a genuine inversion ────────────────────
def test_falcons_side_against_spirit_outcome_still_flags():
    # we hold the FALCONS side (-FAL) while the whale's outcome is Spirit -> a REAL inversion -> MUST stay flagged
    v = AUD("cs2", "Spirit", FAL_TICKER, "yes")
    assert v.startswith("code_review")
    assert LA.classify_leg_audit(v) == LA.STATE_SOFT   # surfaced, not cleared


def test_ts_side_against_falcons_outcome_still_flags():
    # inverse: we hold the SPIRIT side (-TS) while the whale holds Falcons -> inversion. The alias maps
    # ts->spirit ONLY (get('ts')=='spirit' != 'falcons'), so a wrong team is NEVER cleared.
    assert AUD("cs2", "Falcons", TS_TICKER, "yes").startswith("code_review")


# ── regression: the two existing aliases still clear; a non-aliased non-subsequence still soft-flags ─────────────
def test_existing_aliases_unaffected():
    assert AUD("cs2", "Luminosity", "KXCS2GAME-26SEP170000LGXXX-LG", "yes") == "ok:code_alias"
    assert AUD("cs2", "Liquid", "KXCS2GAME-26SEP180000TLXXX-TL", "yes") == "ok:code_alias"


def test_unaliased_nonsubsequence_still_soft_flags():
    # a code neither a subsequence nor aliased stays code_review -- the independent soft net is intact
    assert AUD("cs2", "Vitality", "KXCS2GAME-26OCT090000VITXXX-ZZ", "yes").startswith("code_review")
