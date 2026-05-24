"""the-odds-api.com client (free tier compatible).

Free tier: 500 requests/month, no auth-header — API key passed as URL
param. Each /v4/sports/{sport_key}/odds request returns ALL upcoming
games for the sport with ALL bookmakers' lines — one request gets us
~30+ games. Aggressive caching essential to stay under quota during
the 7-day scout window.

Two interfaces are exposed:
  - `get_games(sport_key)` → list[GameOdds]: vig-removed median implied
    probability per game across books, h2h-only. Used by
    `kalshi_sports_scout` (production, since 2026-05-14).
  - `get_lines(sport_key, markets, books)` → list[GameLine]: raw
    per-book per-side prices for h2h/spreads/totals. Used by the
    Kalshi Sports Arbitrage observer (Phase 0). Caller is responsible
    for any vig-removal or median aggregation.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4"
_DEFAULT_TIMEOUT = 15.0
_CACHE_TTL_SEC = 30 * 60  # 30 min — quota-friendly; bookmaker lines move slowly pre-game


@dataclass(frozen=True)
class GameOdds:
    sport_key: str
    home_team: str
    away_team: str
    commenced_at: str        # ISO; the game's start time
    implied_home: float      # vig-removed median across books, in [0, 1]
    implied_away: float
    implied_tie: float | None  # for 3-way soccer markets; None if 2-way
    n_books: int
    median_vig_pct: float    # observable book vig (1 - sum(raw)); informational


# ── Phase 0 (arbitrage) DTOs — per-book, raw prices ───────────────────────

@dataclass(frozen=True)
class BookPrice:
    """One book's offering for one side of one market.

    `line` is None for h2h. For spreads it's the signed point spread
    (positive for the underdog side, negative for the favorite). For
    totals it's the (positive) total. `implied_raw` is vig-INCLUDED;
    caller is responsible for vig-removal across both sides.
    """
    book_key: str          # e.g. "draftkings", "fanduel", "betmgm", "pinnacle"
    side: str              # h2h: home/away; spreads: home/away; totals: over/under
    line: float | None     # None for h2h; signed point for spreads; total for totals
    american: int
    implied_raw: float     # 1/decimal_odds, in [0, 1]


@dataclass(frozen=True)
class GameLine:
    """All books' offerings for one (sport, game, market_type) combination.

    For spreads/totals the SAME (sport, game) may yield MULTIPLE GameLine
    rows if books offer different line values — each distinct line is a
    separate GameLine. Hypothesis A matching requires line-exact compare;
    median-across-lines would silently corrupt the arb math.
    """
    sport_key: str
    home_team: str
    away_team: str
    commenced_at: str
    market: str            # "h2h" | "spreads" | "totals"
    line: float | None     # the line value this row represents (None for h2h)
    books: tuple[BookPrice, ...]


class OddsAPIClient:
    """Async odds-api client with per-sport caching."""

    def __init__(self, api_key: str | None) -> None:
        self._api_key = api_key
        # cache[sport_key] = (cached_at_epoch, list[GameOdds])
        self._cache: dict[str, tuple[float, list[GameOdds]]] = {}
        # Phase 0 cache keyed by (sport, markets_tuple, books_tuple_or_None).
        self._lines_cache: dict[tuple[str, tuple[str, ...], tuple[str, ...] | None], tuple[float, list[GameLine]]] = {}
        self._http: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._quota_remaining: int | None = None
        self._quota_used: int | None = None

    @property
    def has_credentials(self) -> bool:
        return bool(self._api_key)

    async def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def get_games(self, sport_key: str) -> list[GameOdds]:
        """Returns upcoming games for `sport_key`. Cached per sport (30 min)."""
        if not self.has_credentials:
            return []
        async with self._lock:
            cached = self._cache.get(sport_key)
            if cached and (time.time() - cached[0]) < _CACHE_TTL_SEC:
                return cached[1]
            games = await self._fetch_sport(sport_key)
            self._cache[sport_key] = (time.time(), games)
            return games

    async def _fetch_sport(self, sport_key: str) -> list[GameOdds]:
        http = await self._ensure_http()
        url = f"{BASE_URL}/sports/{sport_key}/odds/"
        params = {
            "apiKey": self._api_key,
            "regions": "us",         # US sportsbooks
            "markets": "h2h",        # head-to-head moneyline only for v1
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        try:
            r = await http.get(url, params=params)
            # the-odds-api returns quota headers
            try:
                self._quota_remaining = int(
                    r.headers.get("x-requests-remaining") or 0
                )
                self._quota_used = int(r.headers.get("x-requests-used") or 0)
            except (TypeError, ValueError):
                pass
            r.raise_for_status()
            data = r.json() or []
        except Exception as e:
            log.warning("odds_api: get_games(%s) failed: %s", sport_key, e)
            return []
        return [g for g in (self._parse_game(sport_key, raw) for raw in data) if g]

    def _parse_game(self, sport_key: str, raw: dict[str, Any]) -> GameOdds | None:
        home = raw.get("home_team") or ""
        away = raw.get("away_team") or ""
        if not home or not away:
            return None
        commenced = raw.get("commence_time") or ""
        # Collect raw probs across all books, then take medians, then vig-remove.
        home_probs: list[float] = []
        away_probs: list[float] = []
        tie_probs: list[float] = []
        vigs: list[float] = []
        for book in raw.get("bookmakers") or []:
            for market in book.get("markets") or []:
                if market.get("key") != "h2h":
                    continue
                outcomes = market.get("outcomes") or []
                # Build name → American-odds map
                odds_by_name: dict[str, int] = {}
                for o in outcomes:
                    name = o.get("name") or ""
                    price = o.get("price")
                    try:
                        odds_by_name[name] = int(price)
                    except (TypeError, ValueError):
                        continue
                h_p = _american_to_prob(odds_by_name.get(home))
                a_p = _american_to_prob(odds_by_name.get(away))
                # 3-way soccer
                t_p = _american_to_prob(odds_by_name.get("Draw"))
                if h_p is None or a_p is None:
                    continue
                total = h_p + a_p + (t_p or 0)
                if total <= 0:
                    continue
                vigs.append(total - 1.0)
                # Vig-removed: divide by total
                home_probs.append(h_p / total)
                away_probs.append(a_p / total)
                if t_p is not None:
                    tie_probs.append(t_p / total)
        if not home_probs or not away_probs:
            return None
        return GameOdds(
            sport_key=sport_key,
            home_team=home,
            away_team=away,
            commenced_at=commenced,
            implied_home=statistics.median(home_probs),
            implied_away=statistics.median(away_probs),
            implied_tie=statistics.median(tie_probs) if tie_probs else None,
            n_books=len(home_probs),
            median_vig_pct=float(statistics.median(vigs) * 100.0) if vigs else 0.0,
        )

    @property
    def quota_remaining(self) -> int | None:
        return self._quota_remaining

    @property
    def quota_used(self) -> int | None:
        return self._quota_used

    # ── Phase 0 (arbitrage) per-book interface ────────────────────────────

    async def get_lines(
        self,
        sport_key: str,
        *,
        markets: tuple[str, ...] = ("h2h", "spreads", "totals"),
        books: tuple[str, ...] | None = None,
    ) -> list[GameLine]:
        """Returns raw per-book per-side prices, one GameLine per
        (game, market_type, distinct_line) combination.

        `markets` controls quota cost — the-odds-api charges 1 credit
        per requested market per region (us). Default 3 markets ⇒ 3
        credits per call. Cached per (sport, markets, books) for
        `_CACHE_TTL_SEC`.

        `books` filters response to a subset of bookmaker keys (e.g.
        ("pinnacle",) for the Pinnacle probe; None = all US books). The
        filter is applied client-side by the API; quota charge is
        unaffected.
        """
        if not self.has_credentials:
            return []
        cache_key = (sport_key, tuple(markets), tuple(books) if books else None)
        async with self._lock:
            cached = self._lines_cache.get(cache_key)
            if cached and (time.time() - cached[0]) < _CACHE_TTL_SEC:
                return cached[1]
            lines = await self._fetch_lines(sport_key, markets, books)
            self._lines_cache[cache_key] = (time.time(), lines)
            return lines

    async def _fetch_lines(
        self,
        sport_key: str,
        markets: tuple[str, ...],
        books: tuple[str, ...] | None,
    ) -> list[GameLine]:
        http = await self._ensure_http()
        url = f"{BASE_URL}/sports/{sport_key}/odds/"
        params: dict[str, Any] = {
            "apiKey": self._api_key,
            "regions": "us",
            "markets": ",".join(markets),
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        if books:
            params["bookmakers"] = ",".join(books)
        try:
            r = await http.get(url, params=params)
            try:
                self._quota_remaining = int(r.headers.get("x-requests-remaining") or 0)
                self._quota_used = int(r.headers.get("x-requests-used") or 0)
            except (TypeError, ValueError):
                pass
            r.raise_for_status()
            data = r.json() or []
        except Exception as e:
            log.warning("odds_api: get_lines(%s, %s) failed: %s", sport_key, markets, e)
            return []

        out: list[GameLine] = []
        for raw in data:
            out.extend(self._parse_lines(sport_key, raw))
        return out

    def _parse_lines(self, sport_key: str, raw: dict[str, Any]) -> list[GameLine]:
        home = raw.get("home_team") or ""
        away = raw.get("away_team") or ""
        if not home or not away:
            return []
        commenced = raw.get("commence_time") or ""

        # Bucket: (market_key, line_value) → list[BookPrice]
        buckets: dict[tuple[str, float | None], list[BookPrice]] = {}
        for book in raw.get("bookmakers") or []:
            book_key = book.get("key") or ""
            if not book_key:
                continue
            for market in book.get("markets") or []:
                m_key = market.get("key") or ""
                if m_key not in ("h2h", "spreads", "totals"):
                    continue
                outcomes = market.get("outcomes") or []
                for o in outcomes:
                    name = o.get("name") or ""
                    raw_price = o.get("price")
                    try:
                        american = int(raw_price)
                    except (TypeError, ValueError):
                        continue
                    implied = _american_to_prob(american)
                    if implied is None:
                        continue
                    line: float | None
                    side: str
                    if m_key == "h2h":
                        line = None
                        if name == home:
                            side = "home"
                        elif name == away:
                            side = "away"
                        elif name == "Draw":
                            side = "draw"
                        else:
                            continue
                    elif m_key == "spreads":
                        point = o.get("point")
                        try:
                            line = float(point)
                        except (TypeError, ValueError):
                            continue
                        if name == home:
                            side = "home"
                        elif name == away:
                            side = "away"
                        else:
                            continue
                    else:  # totals
                        point = o.get("point")
                        try:
                            line = float(point)
                        except (TypeError, ValueError):
                            continue
                        n_lower = name.lower()
                        if n_lower == "over":
                            side = "over"
                        elif n_lower == "under":
                            side = "under"
                        else:
                            continue
                    # Spread sign normalization: store the line as seen
                    # for the side, so a "home -5.5" yields side=home,
                    # line=-5.5; "away +5.5" yields side=away, line=+5.5.
                    # Bucketing key uses |line| so home/away both land
                    # in the same GameLine row for one spread market.
                    bucket_line: float | None
                    if m_key == "spreads" and line is not None:
                        bucket_line = abs(line)
                    else:
                        bucket_line = line
                    key = (m_key, bucket_line)
                    buckets.setdefault(key, []).append(
                        BookPrice(
                            book_key=book_key,
                            side=side,
                            line=line,
                            american=american,
                            implied_raw=implied,
                        )
                    )

        out: list[GameLine] = []
        for (m_key, line_value), prices in buckets.items():
            out.append(
                GameLine(
                    sport_key=sport_key,
                    home_team=home,
                    away_team=away,
                    commenced_at=commenced,
                    market=m_key,
                    line=line_value,
                    books=tuple(prices),
                )
            )
        return out


def _american_to_prob(price: int | None) -> float | None:
    """American odds → raw implied probability (NOT vig-removed)."""
    if price is None:
        return None
    try:
        p = int(price)
    except (TypeError, ValueError):
        return None
    if p == 0:
        return None
    if p > 0:
        return 100.0 / (p + 100.0)
    return -p / (-p + 100.0)
