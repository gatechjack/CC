"""Rung-3 follow-up (2026-09-07, Jack ruled per-league, retire coarse 'soccer'): the 8 built leagues
classify per-league via the slug prefix; 'soccer' is retired from BOTH the tag map and the allowlist
(and therefore the farm tile set). epl/ucl unchanged. The deferred tail falls to 'unknown', not 'soccer'."""
import pytest
from trading_corp.prediction_markets import category as C, search, farm

BUILT = ("epl", "ucl", "lal", "fl1", "uel", "mls", "sea", "bun", "bra", "mex")


@pytest.mark.parametrize("lg", BUILT)
def test_built_leagues_classify_per_league(lg):
    cat, src = C.derive_category_from_slug("%s-ars-che-2026-09-05" % lg, "%s-ars-che-2026-09-05-ars" % lg)
    assert cat == lg and src == C.SOURCE_SLUG, (lg, cat, src)


def test_soccer_retired_from_tag_map():
    # a 'soccer' gamma tag no longer maps to any category (retired) -> None
    assert C.category_from_event_tags([{"slug": "soccer"}]) is None
    # a per-league tag still maps where one exists (epl was never a tag; but the retire must not add one)
    assert "soccer" not in C.TAG_SLUG_TO_CATEGORY


def test_deferred_tail_falls_to_unknown_not_soccer():
    # a tail league (no slug prefix mapped, no per-league tag) that previously rode the 'soccer' tag
    # is now 'unknown' -- correct: uncopyable until its matcher is built, never silently coarse
    cat, src = C.derive_category_from_slug("nor-bra-sar-2026-05-29", "nor-bra-sar-2026-05-29-bra")
    assert cat == C.CATEGORY_UNKNOWN, (cat, src)
    # and the 'soccer' tag no longer rescues it
    assert C.category_from_event_tags([{"slug": "soccer"}]) is None


def test_allowlist_has_ten_leagues_not_soccer():
    for lg in BUILT:
        assert lg in search.CATEGORY_ALLOWLIST, lg
    assert "soccer" not in search.CATEGORY_ALLOWLIST


def test_farm_tiles_derive_from_allowlist():
    cats = set(farm.league_categories())
    for lg in BUILT:
        assert lg in cats and farm.is_league_category(lg), lg
    assert "soccer" not in cats
    assert farm.is_league_category("soccer") is False


def test_epl_ucl_unchanged_regression():
    # the two already-per-league leagues are untouched by the retire
    assert C.derive_category_from_slug("epl-x-y-2026-09-05")[0] == "epl"
    assert C.derive_category_from_slug("ucl-x-y-2026-09-05")[0] == "ucl"
    assert "epl" in search.CATEGORY_ALLOWLIST and "ucl" in search.CATEGORY_ALLOWLIST


def test_prefix_precedence_not_broken():
    # longest-prefix-first still holds (fed-decision beats fed); a soccer prefix doesn't shadow others
    assert C.derive_category_from_slug("fed-decision-2026")[0] == "fed"
    assert C.derive_category_from_slug("mlb-nyy-bos-2026-09-05")[0] == "mlb"
