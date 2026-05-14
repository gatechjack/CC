"""Polymarket Data API client for the copy-trading division.

Async wrapper over the public, unauthenticated REST endpoints at
`data-api.polymarket.com`. Used by `polymarket_copy_trader` for whale
discovery (leaderboard) and per-wallet enrichment (activity history,
current positions).

Endpoints (live-verified 2026-05-11):
  - `/v1/leaderboard?category=<C>&limit=N&offset=N` — discovery
  - `/activity?user=<proxyWallet>&limit=N&offset=N`   — trade history
  - `/positions?user=<proxyWallet>`                    — current positions

All endpoints are free and require no auth. Polymarket's documented
`/leaderboards` (plural) does NOT exist — singular `/v1/leaderboard` is
the real path. `sortBy` accepts `pnl`/`vol`/etc but the default order is
already useful for our purpose. `timeframe` is silently ignored — only
all-time data is returned.

**Category filter is the load-bearing feature** for our top-N-per-category
selection design. Working category values: `Politics`, `Sports`, `Crypto`,
`Tech`, `Mentions`. Most subcategories (NBA, NFL, Bitcoin, etc.) return
empty. Polymarket's `gamma-api.polymarket.com/categories` taxonomy has 9
top-level categories but only ~5 produce leaderboard data.

Compared to Kalshi's Apify wrapper (`kalshi_apify_client.py`):
  - No cost — pure free public API, no recurring fees.
  - No `max_results` floor — `limit` is honored.
  - Per-trade `side` (BUY/SELL) and `outcome_index` + `outcome` are
    explicit; no size-match side detection needed.
  - Per-trade `price` and `usdc_size` enable real ROI math (not
    the per-contract proxy K3 was stuck with on Apify).
  - All wallets public — no opt-in visibility gradient.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)


_DATA_API_BASE = "https://data-api.polymarket.com"
_GAMMA_API_BASE = "https://gamma-api.polymarket.com"
_HTTP_TIMEOUT_SEC = 30.0

# Categories that empirically return leaderboard rows. Polymarket's taxonomy
# has more, but most are empty — these 5 are where the data actually lives.
POLYMARKET_LEADERBOARD_CATEGORIES: tuple[str, ...] = (
    "Politics", "Sports", "Crypto", "Tech", "Mentions",
)


class PolymarketDataAPIError(Exception):
    """Base error for Polymarket data-api calls."""


class PolymarketRateLimitError(PolymarketDataAPIError):
    """HTTP 429 — back off and retry."""


@dataclass(frozen=True)
class LeaderboardEntry:
    rank: int
    proxy_wallet: str  # the Safe/proxy address; this is what /activity + /positions accept
    user_name: str
    x_username: str
    verified_badge: bool
    vol: float  # USDC traded
    pnl: float  # USDC realized
    profile_image: str

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "LeaderboardEntry":
        try:
            rank = int(row.get("rank") or 0)
        except (TypeError, ValueError):
            rank = 0
        return cls(
            rank=rank,
            proxy_wallet=str(row.get("proxyWallet") or "").lower(),
            user_name=str(row.get("userName") or ""),
            x_username=str(row.get("xUsername") or ""),
            verified_badge=bool(row.get("verifiedBadge")),
            vol=float(row.get("vol") or 0.0),
            pnl=float(row.get("pnl") or 0.0),
            profile_image=str(row.get("profileImage") or ""),
        )


@dataclass(frozen=True)
class ActivityRow:
    """One row from `/activity?user=<wallet>` — a TRADE or other event.

    `side` is BUY/SELL on the outcome at index `outcome_index`. To recover
    the YES/NO axis, look up the market by `condition_id` (or trust the
    `outcome` human label, e.g. "Spurs" / "Yes" / "No").
    """
    proxy_wallet: str
    timestamp: int  # unix seconds
    condition_id: str
    type: str  # "TRADE" | possibly other types we'd ignore
    size: float  # contract count
    usdc_size: float  # USDC value of the fill
    transaction_hash: str
    price: float  # entry price in [0.0, 1.0]
    asset: str  # ERC1155 token ID (long; both sides have distinct IDs)
    side: str  # "BUY" | "SELL"
    outcome_index: int
    title: str
    slug: str
    event_slug: str
    outcome: str  # human label of the leg ("Spurs", "Yes", etc.)
    name: str  # whale's display name
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "ActivityRow":
        # Coerce loosely-typed numerics — Polymarket returns numbers as JSON
        # numbers but a few fields occasionally come back as strings.
        def _f(k: str) -> float:
            v = row.get(k)
            try:
                return float(v) if v is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        def _i(k: str) -> int:
            v = row.get(k)
            try:
                return int(v) if v is not None else 0
            except (TypeError, ValueError):
                return 0

        consumed = {
            "proxyWallet", "timestamp", "conditionId", "type", "size",
            "usdcSize", "transactionHash", "price", "asset", "side",
            "outcomeIndex", "title", "slug", "eventSlug", "outcome", "name",
        }
        return cls(
            proxy_wallet=str(row.get("proxyWallet") or "").lower(),
            timestamp=_i("timestamp"),
            condition_id=str(row.get("conditionId") or ""),
            type=str(row.get("type") or "").upper(),
            size=_f("size"),
            usdc_size=_f("usdcSize"),
            transaction_hash=str(row.get("transactionHash") or ""),
            price=_f("price"),
            asset=str(row.get("asset") or ""),
            side=str(row.get("side") or "").upper(),
            outcome_index=_i("outcomeIndex"),
            title=str(row.get("title") or ""),
            slug=str(row.get("slug") or ""),
            event_slug=str(row.get("eventSlug") or ""),
            outcome=str(row.get("outcome") or ""),
            name=str(row.get("name") or ""),
            extra={k: v for k, v in row.items() if k not in consumed},
        )


@dataclass(frozen=True)
class PositionRow:
    """One row from `/positions?user=<wallet>` — a current open position."""
    proxy_wallet: str
    condition_id: str
    asset: str  # ERC1155 token ID (encodes side)
    size: float  # contracts
    avg_price: float  # average entry price [0, 1]
    initial_value: float  # USDC paid in
    current_value: float  # mark-to-market USDC
    pnl: float
    title: str
    outcome: str
    slug: str
    event_slug: str
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "PositionRow":
        def _f(k: str) -> float:
            v = row.get(k)
            try:
                return float(v) if v is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        consumed = {
            "proxyWallet", "conditionId", "asset", "size", "avgPrice",
            "initialValue", "currentValue", "cashPnl", "title", "outcome",
            "slug", "eventSlug",
        }
        return cls(
            proxy_wallet=str(row.get("proxyWallet") or "").lower(),
            condition_id=str(row.get("conditionId") or ""),
            asset=str(row.get("asset") or ""),
            size=_f("size"),
            avg_price=_f("avgPrice"),
            initial_value=_f("initialValue"),
            current_value=_f("currentValue"),
            pnl=_f("cashPnl"),
            title=str(row.get("title") or ""),
            outcome=str(row.get("outcome") or ""),
            slug=str(row.get("slug") or ""),
            event_slug=str(row.get("eventSlug") or ""),
            extra={k: v for k, v in row.items() if k not in consumed},
        )


def _decode_resolution(market: dict) -> dict:
    """Map a gamma-api market row to a uniform resolution record.

    `outcomePrices` is a JSON-encoded string of two stringified floats. On
    resolution, the winner's price ≈ 1.0 and the loser's ≈ 0.0. VOID markets
    (CFTC dispute, never settled) have both ≈ 0.0 and `closed=True`.
    """
    closed = bool(market.get("closed"))
    raw_prices = market.get("outcomePrices")
    raw_outcomes = market.get("outcomes")
    try:
        prices = (
            json.loads(raw_prices) if isinstance(raw_prices, str)
            else (raw_prices or [])
        )
        prices = [float(p) for p in prices]
    except (TypeError, ValueError, json.JSONDecodeError):
        prices = []
    try:
        outcomes = (
            json.loads(raw_outcomes) if isinstance(raw_outcomes, str)
            else (raw_outcomes or [])
        )
        outcomes = [str(o) for o in outcomes]
    except (TypeError, ValueError, json.JSONDecodeError):
        outcomes = []

    win_idx: int | None = None
    status = "pending"
    if closed:
        # Find the price ≥ 0.9 — that's the winner. If none, the market is void.
        winners = [i for i, p in enumerate(prices) if p >= 0.9]
        if winners:
            win_idx = winners[0]
            status = "resolved"
        else:
            status = "void"
    yes_won = (win_idx == 0) if (win_idx is not None and len(outcomes) >= 2) else None

    return {
        "status": status,
        "winning_outcome_index": win_idx,
        "yes_won": yes_won,
        "outcomes": outcomes,
        "outcome_prices": prices,
        "closed": closed,
        "title": str(market.get("question") or market.get("title") or ""),
    }


class PolymarketDataAPIClient:
    """Async client for Polymarket's public Data API.

    Used as an async context manager so the underlying httpx.AsyncClient
    is cleaned up properly. Concurrency is gated by an internal semaphore
    so a runaway loop can't exhaust the per-IP rate limit.
    """

    def __init__(self, *, max_concurrent: int = 5) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "PolymarketDataAPIClient":
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(_HTTP_TIMEOUT_SEC))
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_leaderboard(
        self,
        *,
        category: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str | None = None,
    ) -> list[LeaderboardEntry]:
        """Pull a slice of the leaderboard. `category=None` returns the global
        leaderboard. Working category values are in `POLYMARKET_LEADERBOARD_
        CATEGORIES`. Most subcategories return empty."""
        params: dict[str, Any] = {"limit": int(limit), "offset": int(offset)}
        if category:
            params["category"] = category
        if sort_by:
            params["sortBy"] = sort_by
        rows = await self._get_json(
            f"{_DATA_API_BASE}/v1/leaderboard",
            params=params,
            label=f"leaderboard[cat={category or 'all'}, limit={limit}, offset={offset}]",
        )
        if not isinstance(rows, list):
            return []
        return [LeaderboardEntry.from_api(r) for r in rows if isinstance(r, dict)]

    async def fetch_activity(
        self,
        wallet: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ActivityRow]:
        """Pull trade history for one wallet. Returns most-recent first.
        For Wilson-LCB scoring we typically pull the most-recent ~50-100
        trades over a time-weighted window."""
        rows = await self._get_json(
            f"{_DATA_API_BASE}/activity",
            params={"user": wallet, "limit": int(limit), "offset": int(offset)},
            label=f"activity[{wallet[:10]}…, limit={limit}, offset={offset}]",
        )
        if not isinstance(rows, list):
            return []
        return [ActivityRow.from_api(r) for r in rows if isinstance(r, dict)]

    async def fetch_positions(self, wallet: str) -> list[PositionRow]:
        """Current open positions for one wallet."""
        rows = await self._get_json(
            f"{_DATA_API_BASE}/positions",
            params={"user": wallet},
            label=f"positions[{wallet[:10]}…]",
        )
        if not isinstance(rows, list):
            return []
        return [PositionRow.from_api(r) for r in rows if isinstance(r, dict)]

    async def fetch_market_resolutions(
        self, condition_ids: list[str], *, chunk_size: int = 50,
    ) -> dict[str, dict]:
        """Batch-lookup market resolutions for a list of condition_ids.

        Hits `gamma-api.polymarket.com/markets?condition_ids=A&condition_ids=B&…`
        with the repeated-param batch form (verified live; comma-separated is
        silently ignored). Chunks at `chunk_size` to keep URL length bounded.

        Returns a dict keyed by condition_id with shape::

            {
              "status": "resolved" | "void" | "pending" | "not_found",
              "winning_outcome_index": int | None,
              "yes_won": bool | None,             # convenience for binary markets
              "outcomes": list[str],
              "outcome_prices": list[float],
              "closed": bool,
              "title": str,
            }

        Resolution decoding: `closed=True` + `outcomePrices` contains a value
        >= 0.9 → resolved, winner = index of that value. `closed=True` with
        all-near-zero prices → void (market never settled). `closed=False` →
        pending.
        """
        out: dict[str, dict] = {}
        if not condition_ids:
            return out

        unique = list(dict.fromkeys(condition_ids))  # dedupe preserving order
        for i in range(0, len(unique), chunk_size):
            chunk = unique[i:i + chunk_size]
            # Gamma-api default is `closed=false` (active markets only) and
            # the `condition_ids` filter intersects with that — so we need
            # TWO queries per chunk: one for open, one for closed. Merge.
            base_params = [("condition_ids", c) for c in chunk]
            base_params.append(("limit", str(chunk_size)))
            for variant in ("open", "closed"):
                params = list(base_params)
                if variant == "closed":
                    params.append(("closed", "true"))
                rows = await self._get_json(
                    f"{_GAMMA_API_BASE}/markets",
                    params=params,
                    label=f"resolutions[{len(chunk)} ids, chunk {i // chunk_size}, {variant}]",
                )
                if not isinstance(rows, list):
                    continue
                for m in rows:
                    if not isinstance(m, dict):
                        continue
                    cid = m.get("conditionId") or ""
                    if not cid:
                        continue
                    # Closed variant wins if both present (the canonical
                    # resolution record sits in `closed=true` rows once a
                    # market settles).
                    if variant == "closed" or cid not in out:
                        out[cid] = _decode_resolution(m)
        # Fill in "not_found" for any missing condition_ids the caller asked for
        # so downstream code can iterate without KeyError.
        for c in unique:
            if c not in out:
                out[c] = {"status": "not_found", "winning_outcome_index": None,
                          "yes_won": None, "outcomes": [], "outcome_prices": [],
                          "closed": False, "title": ""}
        return out

    async def _get_json(
        self, url: str, *, params: dict[str, Any], label: str,
    ) -> Any:
        if self._client is None:
            raise RuntimeError(
                "PolymarketDataAPIClient must be used as an async context "
                "manager: `async with PolymarketDataAPIClient() as client:`"
            )
        t0 = time.monotonic()
        async with self._sem:
            try:
                resp = await self._client.get(url, params=params)
            except httpx.TimeoutException as e:
                raise PolymarketDataAPIError(f"{label}: HTTP timeout") from e
            except httpx.HTTPError as e:
                raise PolymarketDataAPIError(
                    f"{label}: HTTP error {type(e).__name__}: {e}"
                ) from e
        dur_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code == 429:
            raise PolymarketRateLimitError(f"{label}: HTTP 429")
        if resp.status_code >= 400:
            body_preview = resp.text[:300] if resp.text else "(empty)"
            raise PolymarketDataAPIError(
                f"{label}: HTTP {resp.status_code} — {body_preview}"
            )
        try:
            payload = resp.json()
        except json.JSONDecodeError as e:
            raise PolymarketDataAPIError(
                f"{label}: non-JSON response (HTTP {resp.status_code}): "
                f"{resp.text[:200]}"
            ) from e
        n_rows = len(payload) if isinstance(payload, list) else 1
        log.info("polymarket-data-api %s: %d rows in %dms", label, n_rows, dur_ms)
        return payload
