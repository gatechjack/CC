"""Tests for trading_corp.prediction_markets.category — pure, no network.

Spec: reports/prediction_markets/P1_PLAN.md §11.
"""
from trading_corp.prediction_markets import category as cat


def test_prefix_basic():
    assert cat.derive_category_from_slug("mlb-nyy-bos-2026-04-01") == ("mlb", cat.SOURCE_SLUG)
    assert cat.derive_category_from_slug("ufc-jones-aspinall-2026-05-01") == ("ufc", cat.SOURCE_SLUG)
    assert cat.derive_category_from_slug("nba-lal-bos-2026-01-01")[0] == "nba"
    assert cat.derive_category_from_slug("nhl-bos-mtl-2026-01-01")[0] == "nhl"


def test_fed_variants_longest_prefix():
    # all fed event-slug shapes -> 'fed'; longest prefix wins over 'fed'
    assert cat.derive_category_from_slug("fed-decision-in-september")[0] == "fed"
    assert cat.derive_category_from_slug("fed-interest-rates-january-2025")[0] == "fed"
    assert cat.derive_category_from_slug("fed-rate-cut-by-629")[0] == "fed"


def test_case_insensitive():
    assert cat.derive_category_from_slug("MLB-NYY-BOS-2026-04-01")[0] == "mlb"
    assert cat.derive_category_from_slug("UFC-Jones-2026-05-01")[0] == "ufc"


def test_unknown_not_error():
    assert cat.derive_category_from_slug("presidential-election-winner-2024") == (
        cat.CATEGORY_UNKNOWN, cat.SOURCE_UNKNOWN)
    assert cat.derive_category_from_slug("") == (cat.CATEGORY_UNKNOWN, cat.SOURCE_UNKNOWN)
    assert cat.derive_category_from_slug(None) == (cat.CATEGORY_UNKNOWN, cat.SOURCE_UNKNOWN)


def test_fallback_to_slug_when_event_slug_empty():
    assert cat.derive_category_from_slug("", "mlb-nyy-bos-2026-04-01")[0] == "mlb"
    assert cat.derive_category_from_slug(None, "ufc-a-b-2026-05-01")[0] == "ufc"


def test_no_false_prefix_match():
    # 'fedex-...' must NOT match the 'fed' prefix (prefix requires '-' boundary or exact)
    assert cat.derive_category_from_slug("fedex-shipping-2026")[0] == cat.CATEGORY_UNKNOWN
    # 'mlbpa-...' must NOT match 'mlb'
    assert cat.derive_category_from_slug("mlbpa-vote-2026")[0] == cat.CATEGORY_UNKNOWN


async def test_empty_gamma_batch_short_circuits():
    # asyncio_mode=auto (pyproject) runs async tests directly
    assert await cat.derive_categories_batch([]) == {}


async def test_gamma_stub_returns_unknown_deferred():
    # DEFERRED stub: returns unknown (repairable) without a network call
    res = await cat.derive_categories_batch(["0xabc", "0xdef", "0xabc"])
    assert res == {
        "0xabc": (cat.CATEGORY_UNKNOWN, cat.SOURCE_UNKNOWN),
        "0xdef": (cat.CATEGORY_UNKNOWN, cat.SOURCE_UNKNOWN),
    }
