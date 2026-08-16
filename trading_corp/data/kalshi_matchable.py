"""Kalshi-matchable category gate for whale selection (Phase 1, CP5).

A whale is Kalshi-matchable iff its DOMINANT Polymarket category maps to a Kalshi
contract type we can trade. At launch that is MLB single-game moneyline only, so
`MATCHABLE_CATEGORIES = {"mlb"}` (expandable to nba/nhl/nfl in-season). Used by
`refresh_polymarket_whales.py` to keep esports/mixed whales off `selected_whales`.

The taxonomy is the same one the discovery probe used (title-keyword classify);
dominant = the whale's most-frequent bucket over its activity/closed titles.
"""
from __future__ import annotations

# Launch-matchable set. Expand as seasons/contract-types come online.
MATCHABLE_CATEGORIES: frozenset[str] = frozenset({"mlb"})

_EXCLUDE = [
    ("weather", ("weather", "temperature", "highest temp", "degrees f", "rainfall",
                 "snowfall", "hurricane")),
    ("crypto_price", ("bitcoin", "btc ", "ethereum", " eth ", "solana", " sol ",
                      "price of", "hit $", "all-time high", "dogecoin", "xrp")),
    ("geopolitics_war", ("war", "ukraine", "russia", "israel", "gaza", "hamas",
                         "iran", "north korea", "nuclear", "ceasefire", "invade",
                         "military", "troops", "nato", "missile", "annex")),
    ("viral_gossip", ("tweet", "how many times", " elon", "musk", "kanye", "tiktok",
                      " viral", "post on x", "instagram")),
    ("parlay", ("parlay", "multi-leg", "same game parlay")),
]
_ESPORTS = ("league of legends", "lol:", "lol ", " lck", " lpl", "dota",
            "counter-strike", "cs2", "csgo", "valorant", "esports", " worlds ",
            "the international", "rocket league")
_MATCH_MARK = (" map ", "- map", "map 1", "map 2", "map 3", "game 1", "game 2", "game 3")
_PRIMARY_RULES = [
    ("nfl", ("nfl", "super bowl")),
    ("nba", ("nba", "nba finals")),
    ("mlb", ("mlb", "world series")),
    ("nhl", ("nhl", "stanley cup")),
    ("soccer", ("premier league", "epl", "la liga", "laliga", "champions league",
                "uefa", "serie a", "bundesliga", " mls", "world cup", "ligue 1",
                "copa", "europa league", "matchday")),
    ("awards_culture", ("oscar", "grammy", "emmy", "billboard", "spotify",
                        "golden globe", "album of the year", "box office",
                        "rotten tomatoes")),
    ("cpi_fed", ("cpi", "inflation", "consumer price", "core pce", "fed ", "fomc",
                 "federal reserve", "rate cut", "rate hike", "interest rate",
                 "powell", "basis points")),
    ("politics", ("election", "senate", "governor", "president", "presidential",
                  "primary", "caucus", "control of", "majority", "house seat",
                  "parliament", "prime minister", "referendum", "nomination",
                  "mayor", "reelect")),
]
_OTHER_SPORTS = ("ufc", "heavyweight", "tennis", "wimbledon", "us open", "golf",
                 "pga", "formula 1", " f1 ", "nascar", "ncaa", "wnba", "boxing")


def classify(title: str, event_slug: str = "") -> str:
    """One title/slug -> a taxonomy bucket. MLB via the slug 'mlb-' prefix or the
    'mlb'/'world series' keywords."""
    hay = f" {(title or '').lower()} | {(event_slug or '').lower()} "
    if (event_slug or "").lower().startswith("mlb-") or (title or "").lower().startswith("mlb-"):
        return "mlb"
    for b, kws in _EXCLUDE:
        if any(k in hay for k in kws):
            return b
    if any(k in hay for k in _ESPORTS):
        return "esports_match" if any(k in hay for k in _MATCH_MARK) else "esports_series"
    for b, kws in _PRIMARY_RULES:
        if any(k in hay for k in kws):
            return b
    if any(k in hay for k in _OTHER_SPORTS):
        return "other_sports"
    return "other_unknown"


def classify_dominant(rows) -> str | None:
    """Dominant bucket over a whale's rows (each needs `.title`, optional `.event_slug`
    / `.slug`). None if empty."""
    counts: dict[str, int] = {}
    for r in rows:
        ev = getattr(r, "event_slug", None) or getattr(r, "slug", "") or ""
        b = classify(getattr(r, "title", "") or "", ev)
        counts[b] = counts.get(b, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def is_kalshi_matchable(rows) -> bool:
    """True iff the whale's dominant category is in MATCHABLE_CATEGORIES."""
    return classify_dominant(rows) in MATCHABLE_CATEGORIES
