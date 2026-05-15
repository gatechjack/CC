"""the-odds-api.com client (free tier compatible).

Free tier: 500 requests/month, no auth-header — API key passed as URL
param. Each /v4/sports/{sport_key}/odds request returns ALL upcoming
games for the sport with ALL bookmakers' lines — one request gets us
~30+ games. Aggressive caching essential to stay under quota during
the 7-day scout window.

Returns vig-removed implied probabilities (median across books for
consensus). Two-way moneyline only (h2h market); spread/total/props
deferred to a future build.
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


class OddsAPIClient:
    """Async odds-api client with per-sport caching."""

    def __init__(self, api_key: str | None) -> None:
        self._api_key = api_key
        # cache[sport_key] = (cached_at_epoch, list[GameOdds])
        self._cache: dict[str, tuple[float, list[GameOdds]]] = {}
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
