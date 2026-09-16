"""Item 3 (2026-09-15): the matcher-presence SUB-DIVISION tile filter + its dependency-free constant.

- is_tradable_category: matcherless categories (bare soccer/tennis, golf, cbb/fifwc/nascar/unknown) are NOT
  tradable -> their sub-division tile is hidden; the 24 real categories ARE (23 + itf, 2026-09-16).
- ★ GOLF SCOPE GUARD (Jack, load-bearing): golf + tennis are REAL farm leagues with NO matcher. They MUST stay
  on the FARM side (search.CATEGORY_ALLOWLIST) while being hidden from SUB-DIVISION tiles (TRADABLE_CATEGORIES).
  Two DIFFERENT gates. If the filter were ever pointed at farm categories, golf's farm page would vanish -> these
  tests fail. Catch it here, not after deploy.
- DRIFT-GUARD: TRADABLE_CATEGORIES must equal the real matcher+builder registries. Runs in the FULL env
  (box-scratch) where execution/live_driver import (they pull the box-only data/ matchers + pykalshi); SKIPPED
  locally where those are absent -- so the check is not silently green when it cannot run.
"""
import pytest

from trading_corp.prediction_markets import tradable_categories as TC
from trading_corp.prediction_markets import search

# the registries need the box env (data/ matchers force-added on the box only + pykalshi). Import-guard so the
# drift-guard SKIPS (not passes) where it cannot run; on box-scratch pytest it imports and the assertion RUNS.
try:
    from trading_corp.prediction_markets import execution as _E, live_driver as _LD
    _REG_OK = True
except Exception:  # noqa: BLE001 -- ImportError locally (no data/ matchers); the drift-guard is a box-scratch check
    _E = _LD = None
    _REG_OK = False


_MATCHERLESS = ("soccer", "tennis", "golf", "cbb", "fifwc", "nascar", "unknown", "", None)
_TRADABLE = ("mlb", "ufc", "atp", "wta", "cs2", "fed", "boxing", "f1", "itf",
             "nfl", "nba", "nhl", "wnba", "cfb",
             "epl", "ucl", "uel", "lal", "fl1", "mls", "sea", "bun", "bra", "mex")


def test_matcherless_categories_are_not_tradable():
    for cat in _MATCHERLESS:
        assert TC.is_tradable_category(cat) is False, cat


def test_known_tradable_categories_are_tradable():
    for cat in _TRADABLE:
        assert TC.is_tradable_category(cat) is True, cat
    assert TC.is_tradable_category("MLB") is True                 # case-insensitive
    assert TC.TRADABLE_CATEGORIES == set(_TRADABLE)               # the constant is exactly the 23 (drift-guard on box)


def test_golf_scope_guard_farm_visible_but_tile_hidden():
    # golf: a REAL farm league (prospects+watchlist), NO matcher -> stays on the Farm side, hidden from tiles.
    assert "golf" in search.CATEGORY_ALLOWLIST                    # farm page/prospects/watchlist SURVIVE
    assert not TC.is_tradable_category("golf")                    # sub-division tile HIDDEN
    # tennis: NOT retired (item-4 STOP -- real ITF volume) -> stays on Farm; matcherless -> tile hidden.
    assert "tennis" in search.CATEGORY_ALLOWLIST
    assert not TC.is_tradable_category("tennis")
    # soccer: retired from the allowlist (per-league only) AND matcherless.
    assert "soccer" not in search.CATEGORY_ALLOWLIST
    assert not TC.is_tradable_category("soccer")


def test_tradable_set_and_farm_allowlist_are_distinct_gates():
    # the two gates are DIFFERENT objects: the allowlist carries matcherless farm leagues (golf, tennis) that the
    # tradable set does not. If a refactor made the tile filter reuse the allowlist, golf's tile would wrongly show;
    # if it made the farm page reuse the tradable set, golf's farm page would wrongly vanish. Lock them apart.
    assert TC.TRADABLE_CATEGORIES != search.CATEGORY_ALLOWLIST
    assert {"golf", "tennis"} <= search.CATEGORY_ALLOWLIST
    assert not ({"golf", "tennis"} & TC.TRADABLE_CATEGORIES)


@pytest.mark.skipif(not _REG_OK, reason="matcher/builder registries need the box env (data/ matchers + pykalshi)")
def test_drift_guard_tradable_equals_matcher_and_builder_registries():
    # ★ the constant is a hand-maintained mirror of the registries pm_web cannot import -> assert it has not drifted.
    assert TC.TRADABLE_CATEGORIES == set(_E.MATCHER_ADAPTERS), (
        TC.TRADABLE_CATEGORIES ^ set(_E.MATCHER_ADAPTERS))
    assert TC.TRADABLE_CATEGORIES == set(_LD.CATEGORY_CTX_BUILDERS), (
        TC.TRADABLE_CATEGORIES ^ set(_LD.CATEGORY_CTX_BUILDERS))
