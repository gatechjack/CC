"""Dependency-free mirror of the categories that HAVE a live matcher + ctx-builder (i.e. CAN trade).

WHY A HAND-MAINTAINED CONSTANT instead of importing the registries: the matcher registry
(``execution.MATCHER_ADAPTERS``) pulls the Kalshi broker and the ctx-builder registry
(``live_driver.CATEGORY_CTX_BUILDERS``) pulls pykalshi -- NEITHER can be imported into pm_web, a
deliberately credential-free process that holds no broker. So the sub-division tile filter needs a
plain literal set here (no imports), and a DRIFT-GUARD TEST -- ``tests/prediction_markets/
test_tradable_categories.py`` (the ``test_drift_guard_*`` case), run in the FULL env / box-scratch where the registries import --
asserts this set stays EXACTLY equal to ``set(MATCHER_ADAPTERS) == set(CATEGORY_CTX_BUILDERS)``.
Adding a category => add its matcher + builder AND one line here, or the drift-guard fails loudly.

USE: hide SUB-DIVISION tiles (/live + account pages) for a category with NO matcher -- it can never
trade under ANY configuration (e.g. the kalshi_jack/soccer + kalshi_jack/tennis mis-attach orphans).

★ SCOPE (load-bearing): this gates SUB-DIVISION tiles ONLY. It must NEVER gate FARM categories.
``golf`` (no matcher, but a REAL farm league with prospects + a watchlist) and the coarse ``tennis``
farm bucket stay on the Farm side, which is gated by ``search.CATEGORY_ALLOWLIST`` -- NOT by this set.
Verified 2026-09-15 against the box registries: 23 categories, MATCHER == BUILDER exactly.
"""

TRADABLE_CATEGORIES: frozenset[str] = frozenset({
    # singletons (own matcher + builder)
    "mlb", "ufc", "atp", "wta", "cs2", "fed", "boxing", "f1",
    # structural (shared structural matcher/builder) -- moneyline only
    "nfl", "nba", "nhl", "wnba", "cfb",
    # soccer per-league (SOC.LEAGUES) -- 3-way win/draw->TIE
    "epl", "ucl", "uel", "lal", "fl1", "mls", "sea", "bun", "bra", "mex",
})


def is_tradable_category(category: str | None) -> bool:
    """True iff ``category`` has a live matcher + builder (can trade). Matcherless categories
    (bare 'soccer', bare 'tennis', 'golf', cbb/fifwc/nascar/unknown) return False -> their
    SUB-DIVISION tile is hidden (they can never trade). Case-insensitive; None -> False. Normalized
    .strip().lower() to match the DB category (category.derive_category_from_slug) and the farm-side gate."""
    return (category or "").strip().lower() in TRADABLE_CATEGORIES
