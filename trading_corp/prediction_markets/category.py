"""Two-tier category derivation for Prediction Markets (P1).

Tier 1 (`derive_category_from_slug`): eventSlug/slug prefix -> category. Covers all
four live P1 categories (MLB/UFC/NBA/Fed), which are clean-prefix (P1_PLAN §13 dec 2).

Tier 2 (`derive_categories_batch`): gamma **events**-tag-join for the tail tier-1 left
'unknown'. Tags live on `/events` (list of {id,label,slug}), NOT on `/markets` (2026-08-22
probe: `/markets` tags are null). Keyed on eventSlug; `fetch_events` is injectable so tests
run offline against recorded fixtures (tests/prediction_markets/fixtures/gamma_events/).

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


GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# Specific league/topic tag slugs -> canonical category. Broad tags ('sports', 'games',
# 'basketball', 'baseball', 'economy', 'politics') are intentionally NOT mapped, so the
# specific tag always wins. Extensible. Live P1 categories + scout-confirmed tag ids:
# ufc=279, fed-rates=100196, nba=745, mlb=100381 (2026-08-22 /events probe).
TAG_SLUG_TO_CATEGORY: dict[str, str] = {
    "fed-rates": "fed",
    "mlb": "mlb",
    "nba": "nba",
    "ufc": "ufc",
    "nfl": "nfl",
    "nhl": "nhl",
    "soccer": "soccer",
    "golf": "golf",
    "tennis": "tennis",
    "cs2": "cs2",
    "csgo": "cs2",
}


def category_from_event_tags(tags) -> str | None:
    """Map an event's `tags` (list of {slug,...}) to a category. First tag whose slug is a
    known league/topic wins; broad tags are skipped. None if no tag maps."""
    if not isinstance(tags, list):
        return None
    for t in tags:
        if isinstance(t, dict):
            slug = (t.get("slug") or "").lower()
            if slug in TAG_SLUG_TO_CATEGORY:
                return TAG_SLUG_TO_CATEGORY[slug]
    return None


async def _default_fetch_events(slug: str, *, closed: bool = True, timeout: float = 30.0):
    """Real gamma /events?slug=<slug>[&closed=true] fetch (the &closed=true quirk, matching
    the scout's /events usage). Lazy-imports httpx so category.py imports without it (tier-2
    tests inject fetch_events and never reach here). This is a NEW helper, not a client call:
    PolymarketDataAPIClient exposes NO public /events method, its _get_json / AsyncClient are
    private, and the client must not be edited (Jack ruling 2026-08-22). Returns the raw
    /events response (list of event dicts)."""
    import httpx
    params = {"slug": slug}
    if closed:
        params["closed"] = "true"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as c:
        resp = await c.get("%s/events" % GAMMA_API_BASE, params=params)
        resp.raise_for_status()
        data = resp.json()
    return data if isinstance(data, list) else []


async def derive_categories_batch(event_slugs: Iterable[str], *, fetch_events=None) -> dict[str, tuple[str, str]]:
    """Tier-2 gamma **events**-tag-join for the tail tier-1 left 'unknown'.

    Keyed on EVENT SLUG -- tags live on the event, not the market (2026-08-22 probe:
    /markets tags are null; /events tags = list of {id,label,slug}). For each unique
    event_slug: fetch the event, map its tags -> category via `category_from_event_tags`,
    else 'unknown'. Per-slug try/except: one slug failing yields ('unknown','unknown')
    (repairable via `repair-categories`), never aborts the batch. Empty input short-circuits
    with no network. `fetch_events(slug) -> list[event]` is INJECTABLE (tests pass a
    fixture-backed fake; default hits gamma /events). Returns {event_slug: (category, source)}.
    """
    fetch = fetch_events or _default_fetch_events
    slugs = list(dict.fromkeys(s for s in (event_slugs or []) if s))
    out: dict[str, tuple[str, str]] = {}
    for s in slugs:
        try:
            events = await fetch(s)
            ev = events[0] if (isinstance(events, list) and events) else {}
            cat = category_from_event_tags(ev.get("tags")) if isinstance(ev, dict) else None
        except Exception:
            cat = None
        out[s] = (cat, SOURCE_GAMMA) if cat else (CATEGORY_UNKNOWN, SOURCE_UNKNOWN)
    return out
