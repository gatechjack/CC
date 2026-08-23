"""Tests for trading_corp.prediction_markets.category — pure, no network.

Spec: reports/prediction_markets/P1_PLAN.md §11.
"""
import json
from pathlib import Path

from trading_corp.prediction_markets import category as cat

_FIX = Path(__file__).parent / "fixtures" / "gamma_events"


def _load_events(name):
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


# eventSlug -> recorded /events fixture file
_SLUG_FIXTURE = {
    "mlb-mia-nym-2026-05-29": "mlb.json",
    "ufc-kin-ter1-2026-07-11": "ufc.json",
    "2026-nba-champion": "nba_champion_futures.json",
    "fed-interest-rates-may-2025": "fed.json",
    "soccer-lec-rsl-atlante-2026-08-08": "soccer_lec.json",
}


def _fake_fetch(mapping):
    async def _f(slug, **kw):
        if slug not in mapping:
            raise RuntimeError("no fixture for %s" % slug)
        return _load_events(mapping[slug])
    return _f


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


async def test_tier2_empty_short_circuits_no_fetch():
    # empty input returns {} WITHOUT invoking fetch (asyncio_mode=auto runs async tests)
    calls = {"n": 0}
    async def _f(slug, **kw):
        calls["n"] += 1
        return []
    assert await cat.derive_categories_batch([], fetch_events=_f) == {}
    assert calls["n"] == 0


def test_category_from_event_tags_specific_over_broad():
    # broad tags (sports/games/baseball/basketball) skipped; the specific league tag wins
    assert cat.category_from_event_tags(_load_events("mlb.json")[0]["tags"]) == "mlb"
    assert cat.category_from_event_tags(_load_events("ufc.json")[0]["tags"]) == "ufc"
    assert cat.category_from_event_tags(_load_events("nba_champion_futures.json")[0]["tags"]) == "nba"
    # fed: 'fed-rates' wins, NOT the forceHide 'politics' tag on the same event
    assert cat.category_from_event_tags(_load_events("fed.json")[0]["tags"]) == "fed"
    assert cat.category_from_event_tags([{"slug": "sports"}, {"slug": "games"}]) is None
    assert cat.category_from_event_tags(None) is None


async def test_tier2_maps_live_categories_from_fixtures():
    res = await cat.derive_categories_batch(list(_SLUG_FIXTURE), fetch_events=_fake_fetch(_SLUG_FIXTURE))
    assert res["mlb-mia-nym-2026-05-29"] == ("mlb", cat.SOURCE_GAMMA)
    assert res["ufc-kin-ter1-2026-07-11"] == ("ufc", cat.SOURCE_GAMMA)
    assert res["2026-nba-champion"] == ("nba", cat.SOURCE_GAMMA)   # futures still -> nba (category only)
    assert res["fed-interest-rates-may-2025"] == ("fed", cat.SOURCE_GAMMA)
    assert res["soccer-lec-rsl-atlante-2026-08-08"] == ("soccer", cat.SOURCE_GAMMA)


async def test_tier2_no_matching_tag_is_unknown():
    async def _f(slug, **kw):
        return [{"slug": slug, "tags": [{"slug": "sports"}, {"slug": "games"}]}]
    res = await cat.derive_categories_batch(["weird-event"], fetch_events=_f)
    assert res["weird-event"] == (cat.CATEGORY_UNKNOWN, cat.SOURCE_UNKNOWN)


async def test_tier2_per_slug_error_tolerance():
    # one slug raises -> ('unknown','unknown'); the other resolves; batch not aborted
    async def _f(slug, **kw):
        if slug == "boom":
            raise RuntimeError("gamma 500")
        return _load_events("mlb.json")
    res = await cat.derive_categories_batch(["boom", "mlb-mia-nym-2026-05-29"], fetch_events=_f)
    assert res["boom"] == (cat.CATEGORY_UNKNOWN, cat.SOURCE_UNKNOWN)
    assert res["mlb-mia-nym-2026-05-29"] == ("mlb", cat.SOURCE_GAMMA)


async def test_tier2_dedups_event_slugs():
    calls = []
    async def _f(slug, **kw):
        calls.append(slug)
        return _load_events("fed.json")
    res = await cat.derive_categories_batch(
        ["fed-interest-rates-may-2025", "fed-interest-rates-may-2025"], fetch_events=_f)
    assert calls == ["fed-interest-rates-may-2025"]   # deduped -> single fetch
    assert res["fed-interest-rates-may-2025"] == ("fed", cat.SOURCE_GAMMA)
