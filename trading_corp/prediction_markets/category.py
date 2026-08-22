"""Two-tier category derivation for Prediction Markets (P1).

Tier 1 (`derive_category_from_slug`): eventSlug/slug prefix -> category. Covers all
four live P1 categories (MLB/UFC/NBA/Fed), which are clean-prefix (P1_PLAN §13 dec 2).

Tier 2 (`derive_categories_batch`): gamma tag-join for the tail tier-1 left 'unknown'.
See the function docstring — this is a FLAGGED DEFERRED STUB in the first build slice.

Spec: reports/prediction_markets/P1_PLAN.md §5.
"""
from __future__ import annotations

from typing import Iterable

# Prefix -> canonical category. Multi-token keys (e.g. 'fed-decision') are matched
# longest-first so they win over their shorter siblings ('fed'). Extensible.
SLUG_PREFIX_MAP: dict[str, str] = {
    "mlb": "mlb",
    "nba": "nba",
    "nfl": "nfl",
    "nhl": "nhl",
    "ufc": "ufc",
    "cs2": "cs2",
    "atp": "atp",
    "wta": "wta",
    "cbb": "cbb",
    "fifwc": "fifwc",
    "epl": "epl",
    "ucl": "ucl",
    "wnba": "wnba",
    "nascar": "nascar",
    # Fed rate markets appear under several event-slug shapes; all -> 'fed'.
    "fed-decision": "fed",
    "fed-interest-rates": "fed",
    "fed-rate": "fed",
    "fed": "fed",
}

# Longest prefixes first so 'fed-decision' beats 'fed', etc.
_PREFIXES_BY_LEN: list[str] = sorted(SLUG_PREFIX_MAP, key=len, reverse=True)

CATEGORY_UNKNOWN = "unknown"
SOURCE_SLUG = "slug_prefix"
SOURCE_GAMMA = "gamma_tags"
SOURCE_UNKNOWN = "unknown"


def derive_category_from_slug(event_slug: str | None, slug: str | None = None) -> tuple[str, str]:
    """Tier-1: derive (category, category_source) from the eventSlug prefix
    (falling back to the market slug). Case-insensitive; longest matching prefix wins.
    Unknown -> (CATEGORY_UNKNOWN, SOURCE_UNKNOWN) — never raises.
    """
    for candidate in (event_slug, slug):
        s = (candidate or "").strip().lower()
        if not s:
            continue
        for pref in _PREFIXES_BY_LEN:
            if s == pref or s.startswith(pref + "-"):
                return SLUG_PREFIX_MAP[pref], SOURCE_SLUG
    return CATEGORY_UNKNOWN, SOURCE_UNKNOWN


async def derive_categories_batch(
    condition_ids: Iterable[str], *, client=None, chunk_size: int = 50,
) -> dict[str, tuple[str, str]]:
    """Tier-2: gamma tag-join to reclassify rows tier-1 left 'unknown'.

    ** FLAGGED DEFERRED STUB (first build slice — awaiting Jack's ruling). **
    Why it is not implemented here yet:
      - `/closed-positions` rows carry NO tags (verified in the realizedPnl probe:
        `EXTRA={}`), so the category must come from gamma.
      - the reusable `fetch_market_resolutions` decodes RESOLUTION (status / outcomes /
        winner / title), NOT market tags — so it cannot supply the tag-join as-is, and
        editing that client is out of scope (ZERO existing-file edits).
      - the gamma `/markets?condition_ids=...&closed=true` TAGS field schema is
        unconfirmed (no gamma probe in this slice), so implementing extraction now would
        be guessing the response shape.
    The four live categories are clean slug-prefix (tier-1 covers them); this tier repairs
    only the tail, so deferring it does not block P1's tracked categories. Once the gamma
    approach is ruled and the tags schema confirmed, this fills in by mirroring
    `fetch_market_resolutions`' batched `&closed=true` + per-chunk-error-tolerance pattern
    and reading the market tags -> (category, SOURCE_GAMMA). Empty input short-circuits
    with no network call.

    Returns {condition_id: (category, category_source)}.
    """
    ids = list(dict.fromkeys(c for c in (condition_ids or []) if c))
    if not ids:
        return {}
    # DEFERRED: leave as unknown (repairable via `repair-categories` later) rather than
    # guess the gamma tags schema. No network call is made.
    return {cid: (CATEGORY_UNKNOWN, SOURCE_UNKNOWN) for cid in ids}
