"""Apify client for the Kalshi leaderboard + profile scrapers.

Data source for the K3 Kalshi Copy Trading division. Wraps the two
saswave Apify actors via Apify's sync REST endpoint:

  - saswave/kalshi-leaderboard-scraper — leaderboard rows (whale discovery)
  - saswave/kalshi-profile-scraper     — per-whale profile / positions / trades

Auth: APIFY_API_TOKEN — Authorization: Bearer header.

Gotchas discovered 2026-05-12 during K3 smoke tests:
  - `max_results` on the profile actor is silently ignored — open_positions
    returns a 20-row-per-name floor, trades returns a 50-row floor. Plan
    cost accordingly: every position call is ~$0.03 (BRONZE) regardless
    of what we ask for.
  - Trade/position visibility is per-user opt-in. Top leaderboard names
    (e.g. Domer at #2 world all-time P&L) may expose only profile-level
    data. Selection has to filter for whales who actually expose positions.
  - Profile actor's `feature` field is one-at-a-time — separate call for
    `open_positions` vs `closed_positions` vs `trades` vs `profile`.
  - Leaderboard actor returns ~100 rows regardless of any limit hint.

Pricing (per result):
                  FREE     BRONZE   GOLD
  Leaderboard:    $0.0015  $0.001   $0.0005
  Profile:        $0.002   $0.0015  $0.0008

At BRONZE / 5-min cadence / 12 whales: ~$80-150/mo total platform spend.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

log = logging.getLogger(__name__)


_APIFY_BASE_URL = "https://api.apify.com/v2"
_LEADERBOARD_ACTOR = "saswave~kalshi-leaderboard-scraper"
_PROFILE_ACTOR = "saswave~kalshi-profile-scraper"
_DEFAULT_RUN_TIMEOUT_SEC = 120
_HTTP_CLIENT_TIMEOUT_SEC = 150  # > actor timeout so we surface the actor's own timeout error

LeaderboardName = Literal["volume", "projected_pnl", "num_markets_traded"]
LeaderboardTime = Literal["daily", "weekly", "monthly", "yearly", "all_time"]
LeaderboardCategory = Literal[
    "", "Politics", "Sports", "Entertainment", "Crypto",
    "Climate+and+Weather", "Economics", "Mentions", "Companies",
    "Financials", "Science+and+Technology", "Elections",
]
ProfileFeature = Literal[
    "profile", "open_positions", "closed_positions", "trades",
    "top_categories", "posts", "comments", "likes",
]


class ApifyClientError(Exception):
    """Base error for Apify actor calls."""


class ApifyAuthError(ApifyClientError):
    """401/403 — token bad, missing, or insufficient permissions."""


class ApifyOverCapError(ApifyClientError):
    """Plan usage cap exceeded — upgrade tier or wait for monthly reset."""


class ApifyTimeoutError(ApifyClientError):
    """Actor run exceeded the configured timeout."""


@dataclass(frozen=True)
class LeaderboardEntry:
    nickname: str
    rank: int
    value: float
    profile_image_path: str
    social_id: str
    is_anonymous: bool

    @classmethod
    def from_apify(cls, row: dict[str, Any]) -> "LeaderboardEntry":
        return cls(
            nickname=str(row.get("nickname") or ""),
            rank=int(row.get("rank") or 0),
            value=float(row.get("value") or 0.0),
            profile_image_path=str(row.get("profile_image_path") or ""),
            social_id=str(row.get("social_id") or ""),
            is_anonymous=bool(row.get("is_anonymous")),
        )


@dataclass
class WhaleProfile:
    """Profile-level snapshot. `extra` holds the raw payload for fields we
    don't surface yet (badges array etc.)."""
    nickname: str
    social_id: str
    pnl_units: int  # raw integer from Kalshi — unit-unclear; use for relative ranking only, not dollar math
    num_markets_traded: int
    follower_count: int
    profile_view_count: int
    top_categories: tuple[str, ...]
    joined_at: str
    posts_count: int
    volume: int | None
    open_interest: int | None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_apify(cls, row: dict[str, Any]) -> "WhaleProfile":
        cats = row.get("top_categories") or ()
        if isinstance(cats, list):
            cats = tuple(str(c) for c in cats)
        else:
            cats = ()
        return cls(
            nickname=str(row.get("nickname") or ""),
            social_id=str(row.get("social_id") or ""),
            pnl_units=int(row.get("pnl") or 0),
            num_markets_traded=int(row.get("num_markets_traded") or 0),
            follower_count=int(row.get("follower_count") or 0),
            profile_view_count=int(row.get("profile_view_count") or 0),
            top_categories=cats,
            joined_at=str(row.get("joined_at") or ""),
            posts_count=int(row.get("posts_count") or 0),
            volume=int(row["volume"]) if row.get("volume") is not None else None,
            open_interest=int(row["open_interest"]) if row.get("open_interest") is not None else None,
            extra={k: v for k, v in row.items() if k not in {
                "nickname", "social_id", "pnl", "num_markets_traded",
                "follower_count", "profile_view_count", "top_categories",
                "joined_at", "posts_count", "volume", "open_interest",
            }},
        )


@dataclass(frozen=True)
class WhalePosition:
    """Open or closed position. `name` is the queried whale's nickname."""
    market_id: str
    market_ticker: str
    name: str
    is_open: bool
    pnl: float
    contracts: int

    @classmethod
    def from_apify(cls, row: dict[str, Any]) -> "WhalePosition":
        return cls(
            market_id=str(row.get("market_id") or ""),
            market_ticker=str(row.get("market_ticker") or ""),
            name=str(row.get("name") or ""),
            is_open=bool(row.get("open_position")),
            pnl=float(row.get("pnl") or 0.0),
            contracts=int(row.get("contracts") or 0),
        )


@dataclass(frozen=True)
class WhaleTrade:
    """Per-trade record. The whale's side is `maker_action` if they were the
    maker (maker_nickname == name), else `taker_action`."""
    trade_id: str
    market_id: str
    ticker: str
    price: int  # cents (0-99)
    price_dollars: float
    count: int
    taker_side: str
    maker_action: str
    taker_action: str
    maker_nickname: str
    taker_nickname: str
    create_date: str  # ISO timestamp
    name: str  # queried whale

    @classmethod
    def from_apify(cls, row: dict[str, Any]) -> "WhaleTrade":
        return cls(
            trade_id=str(row.get("trade_id") or ""),
            market_id=str(row.get("market_id") or ""),
            ticker=str(row.get("ticker") or ""),
            price=int(row.get("price") or 0),
            price_dollars=float(row.get("price_dollars") or 0.0),
            count=int(row.get("count") or 0),
            taker_side=str(row.get("taker_side") or ""),
            maker_action=str(row.get("maker_action") or ""),
            taker_action=str(row.get("taker_action") or ""),
            maker_nickname=str(row.get("maker_nickname") or ""),
            taker_nickname=str(row.get("taker_nickname") or ""),
            create_date=str(row.get("create_date") or ""),
            name=str(row.get("name") or ""),
        )

    @property
    def whale_action(self) -> str:
        """The queried whale's action — `buy` or `sell`, on whichever side
        of the trade they were on."""
        if self.maker_nickname == self.name:
            return self.maker_action
        return self.taker_action

    @property
    def whale_is_maker(self) -> bool:
        return self.maker_nickname == self.name


class KalshiApifyClient:
    """Async client over Apify's sync actor endpoints.

    Single-token, single-account. Concurrent calls are gated by an internal
    semaphore so a runaway loop can't exhaust the per-account rate limit.

    Token is loaded by the caller (typically from `Secrets.apify_api_token`
    in `secrets.py`). Stub mode (no token) returns empty results — matches
    the broker stub-mode convention.
    """

    def __init__(
        self,
        token: str | None,
        *,
        max_concurrent: int = 5,
        run_timeout_sec: int = _DEFAULT_RUN_TIMEOUT_SEC,
    ) -> None:
        self._token = token
        self._stub = not token
        self._sem = asyncio.Semaphore(max_concurrent)
        self._run_timeout_sec = run_timeout_sec
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "KalshiApifyClient":
        if not self._stub:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(_HTTP_CLIENT_TIMEOUT_SEC),
                headers={"Authorization": f"Bearer {self._token}"},
            )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_leaderboard(
        self,
        *,
        name: LeaderboardName = "projected_pnl",
        time: LeaderboardTime = "all_time",
        category: LeaderboardCategory = "",
    ) -> list[LeaderboardEntry]:
        """Pull one leaderboard slice. Returns ~100 rows."""
        if self._stub:
            return []
        rows = await self._run_actor_sync(
            actor=_LEADERBOARD_ACTOR,
            input_={"name": name, "time": time, "category": category},
            label=f"leaderboard[{name}/{time}/{category or 'all'}]",
        )
        return [LeaderboardEntry.from_apify(r) for r in rows if isinstance(r, dict)]

    async def fetch_profiles(self, names: list[str]) -> list[WhaleProfile]:
        """Pull profile-level metadata for one or more whales (1 row/name)."""
        if self._stub or not names:
            return []
        rows = await self._run_actor_sync(
            actor=_PROFILE_ACTOR,
            input_={"feature": "profile", "names": names, "max_results": 1},
            label=f"profile[{len(names)} names]",
        )
        return [WhaleProfile.from_apify(r) for r in rows if isinstance(r, dict)]

    async def fetch_open_positions(self, names: list[str]) -> list[WhalePosition]:
        """Pull open positions for one or more whales. ~20 rows/name floor."""
        if self._stub or not names:
            return []
        rows = await self._run_actor_sync(
            actor=_PROFILE_ACTOR,
            input_={"feature": "open_positions", "names": names, "max_results": 20},
            label=f"open_positions[{len(names)} names]",
        )
        return [WhalePosition.from_apify(r) for r in rows if isinstance(r, dict)]

    async def fetch_closed_positions(self, names: list[str]) -> list[WhalePosition]:
        """Pull closed (resolved) positions for one or more whales. ~20/name floor.
        Used during selection-refresh to compute Wilson LCB + ROI."""
        if self._stub or not names:
            return []
        rows = await self._run_actor_sync(
            actor=_PROFILE_ACTOR,
            input_={"feature": "closed_positions", "names": names, "max_results": 20},
            label=f"closed_positions[{len(names)} names]",
        )
        return [WhalePosition.from_apify(r) for r in rows if isinstance(r, dict)]

    async def fetch_trades(self, names: list[str]) -> list[WhaleTrade]:
        """Pull recent trades. ~50 rows/name floor; the actor ignores max_results."""
        if self._stub or not names:
            return []
        rows = await self._run_actor_sync(
            actor=_PROFILE_ACTOR,
            input_={"feature": "trades", "names": names, "max_results": 50},
            label=f"trades[{len(names)} names]",
        )
        return [WhaleTrade.from_apify(r) for r in rows if isinstance(r, dict)]

    async def _run_actor_sync(
        self,
        *,
        actor: str,
        input_: dict[str, Any],
        label: str,
    ) -> list[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError(
                "KalshiApifyClient must be used as an async context manager: "
                "`async with KalshiApifyClient(token) as client:`"
            )
        url = (
            f"{_APIFY_BASE_URL}/acts/{actor}/run-sync-get-dataset-items"
            f"?timeout={self._run_timeout_sec}"
        )
        t0 = time.monotonic()
        async with self._sem:
            try:
                resp = await self._client.post(url, json=input_)
            except httpx.TimeoutException as e:
                raise ApifyTimeoutError(f"{label}: HTTP timeout after {_HTTP_CLIENT_TIMEOUT_SEC}s") from e
            except httpx.HTTPError as e:
                raise ApifyClientError(f"{label}: HTTP error {type(e).__name__}: {e}") from e

        dur_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code in (401, 403):
            raise ApifyAuthError(f"{label}: HTTP {resp.status_code} — bad/missing APIFY_API_TOKEN")
        if resp.status_code == 402:
            raise ApifyOverCapError(f"{label}: HTTP 402 — plan usage cap exceeded")
        if resp.status_code == 408:
            raise ApifyTimeoutError(f"{label}: HTTP 408 — actor run timed out at {self._run_timeout_sec}s")
        if resp.status_code >= 400:
            body_preview = resp.text[:300] if resp.text else "(empty)"
            raise ApifyClientError(
                f"{label}: HTTP {resp.status_code} — {body_preview}"
            )

        try:
            payload = resp.json()
        except json.JSONDecodeError as e:
            raise ApifyClientError(
                f"{label}: non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}"
            ) from e

        if not isinstance(payload, list):
            raise ApifyClientError(
                f"{label}: expected list response, got {type(payload).__name__}: "
                f"{json.dumps(payload)[:300]}"
            )

        log.info(
            "apify %s: %d rows in %dms", label, len(payload), dur_ms,
        )
        return payload
