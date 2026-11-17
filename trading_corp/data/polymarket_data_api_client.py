"""Polymarket Data API client for the copy-trading division (Data API **v2**).

Async wrapper over the public, unauthenticated REST endpoints at
`data-api.polymarket.com/v2`. Used by `polymarket_copy_trader` for whale
discovery (leaderboard) and per-wallet enrichment (activity history,
current positions).

**v1 -> v2 migration (2026-11-17).** Polymarket retired Data API v1 on
2026-11-02 (HTTP 410 `"Data API v1 has been retired"`), which silenced the
whale `/positions` feed for two weeks. This client now speaks v2
(live-verified 2026-11-17 from the prod box, all endpoints HTTP 200,
no auth required):

  - `/v2/leaderboard?category=<C>&limit=N`      — discovery (was `/v1/leaderboard`)
  - `/v2/activity?user=<proxyWallet>&limit=N`   — trade history (was `/activity`)
  - `/v2/positions?user=<proxyWallet>&limit=N`  — current positions (was `/positions`)
  - `/v2/positions?...&status=CLOSED`           — resolved positions (was `/closed-positions`,
                                                   MERGED into /v2/positions in v2)
  - `gamma-api.polymarket.com/markets`          — resolutions (separate host, UNAFFECTED by
                                                   the v1 retirement; unchanged)

**The migration contract: every consumer sees exactly what it saw from v1.**
The v2 response differences are translated HERE, at the client boundary:

  1. ENVELOPE — v2 wraps every list in `{"data": [...], "pagination": {...}}`
     (v1 returned a bare array). `_unwrap_v2` unwraps it and FAILS LOUD on a
     malformed shape. The v1 code's `if not isinstance(rows, list): return []`
     tolerance is GONE for data-api endpoints: pointed at an unexpected payload
     it used to return a silent empty — which reads as "this whale holds
     nothing" and, via the /positions snapshot diff, as a whale EXIT. A failed
     or malformed fetch now always RAISES (`PolymarketDataAPIError`), which
     every caller already handles (per-whale isolation / chunk skip).
  2. PAGINATION — v2 is CURSOR-ONLY. Sending `offset=` (even `offset=0`)
     returns HTTP 400 `"'offset' is not a query param on this API"`
     (live-verified 2026-11-17). The public `offset=` signatures are KEPT and
     translated internally: a per-client cursor cache maps (endpoint-scope,
     offset) -> cursor so the sequential paging loops every caller uses cost
     one request per page, exactly as under v1; a non-sequential offset is
     served by a bounded forward walk. An offset at/past the end of the data
     returns `[]` — the same terminal signal v1 gave the paging loops.
     Terminal detection trusts ONLY the absence of `pagination.next_cursor`,
     never row counts (a silently-capped `limit` must not read as
     end-of-data).
  3. FIELD NAMES — v2 is snake_case and renames several fields. The
     dataclass constructors remap so every dataclass field keeps its v1 name,
     meaning and units (`size` <- `current_size`, `asset` <- `token_id`,
     `initial_value` <- `entry_cost_usdc`, `pnl` <- `unrealized_pnl`,
     `total_bought` <- `total_size` [SHARES notional, per ingest §13A(g)],
     `cur_price` <- `current_price`, `timestamp` <- `last_event_at`, ...).
     `PositionRow.extra` additionally carries the four v1-era raw keys that
     `prediction_markets.paper` reads by name (`outcomeIndex`, `curPrice`,
     `redeemable`, `endDate`) — see `PositionRow.from_api`.
  4. TIMESTAMPS — v2 serves epoch SECONDS for `timestamp` / `last_event_at`
     (live-verified: 1794890329 etc.), the SAME unit v1 served and every
     consumer expects (`execution.detect_exit_signals` 300s window;
     `ingest.cp_to_record` -> `resolved_ts` -> the 30d recency gate). No
     conversion is performed; tests pin the unit in both directions.
  5. FAILURE IS LOUD — any 410 / non-retryable 4xx / exhausted 5xx / 429 /
     timeout / malformed envelope logs at ERROR (rate-limited to one line per
     endpoint per `_ERROR_LOG_INTERVAL_SEC`, with a suppressed-count) and
     raises. 429 now backs off with jitter (`_RATE_LIMIT_RETRY_DELAYS_SEC`)
     before giving up instead of raising on the first hit.

All endpoints are free and require no auth (v2 docs + live-verified).
Rate limits (v2, per IP, sliding 10s): /v2/positions + /v2/activity
200 req/10s; general 800 req/10s — the live loop's per-account stagger keeps
us under the per-endpoint cap; do not remove it.

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
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)


_DATA_API_BASE = "https://data-api.polymarket.com"
_DATA_API_V2 = f"{_DATA_API_BASE}/v2"
_GAMMA_API_BASE = "https://gamma-api.polymarket.com"
_HTTP_TIMEOUT_SEC = 30.0

# Exponential-backoff schedule for Cloudflare 403 retries. Each delay is the
# sleep BEFORE the next attempt; 5 entries → up to 6 total attempts. Tests
# monkeypatch this to shorten the wall-clock cost.
# Sized for the failure observed 2026-05-17 16:00 UTC on prod's shared-IP
# gamma-api sweep — Cloudflare blocks typically clear inside 60-300s.
_CLOUDFLARE_RETRY_DELAYS_SEC: tuple[float, ...] = (30.0, 60.0, 120.0, 240.0, 300.0)

# Short-backoff schedule for transient upstream 5xx. Each delay is the sleep
# BEFORE the next attempt; 2 entries → up to 3 total attempts. Sized for the
# gamma-api flakiness observed 2026-05-27 04:00 UTC on /markets?condition_ids
# where individual chunks intermittently 500 while neighbours succeed — most
# clears on the first retry inside a second.
_SERVER_ERROR_RETRY_DELAYS_SEC: tuple[float, ...] = (0.5, 1.5)

# Backoff schedule for HTTP 429 (v2 publishes per-endpoint sliding-10s rate
# limits and serves Retry-After on heavy queries). Each delay is the BASE
# sleep before the next attempt; actual sleep adds jitter (uniform 0..50% of
# the base) so N wallets hitting the limit together do not re-collide. v1
# behavior raised on the FIRST 429 — that stays the post-budget outcome, the
# ladder only absorbs transient limit brushes. No hot retry loop: every retry
# sleeps. Tests monkeypatch this to shorten wall-clock cost.
_RATE_LIMIT_RETRY_DELAYS_SEC: tuple[float, ...] = (5.0, 15.0, 30.0)

# Hard-failure ERROR logging is rate-limited PER ENDPOINT to one line per
# this interval (plus a suppressed-similar count), so a dead endpoint under
# the ~7s live-loop fan-out (hundreds of calls/min) cannot flood the journal
# the way the 2026-11 v1-retirement WARNING spam did (~19 lines/sec).
_ERROR_LOG_INTERVAL_SEC = 60.0
_err_log_state: dict[str, list] = {}  # endpoint-key -> [last_emit_monotonic, suppressed_count]

# Infinite-loop backstop for the internal cursor walk that serves the
# offset-contract translation (see module docstring, item 2). 200 pages at
# the smallest caller page size (50) covers 10k rows — far past every
# caller's own depth cap (loss-grounding 5000, ingest cap 8000 at limit 50 =
# 160 pages). Hitting it raises LOUD rather than walking forever.
_CURSOR_WALK_MAX_PAGES = 200

# Categories that empirically return leaderboard rows. Polymarket's taxonomy
# has more, but most are empty — these 5 are where the data actually lives.
POLYMARKET_LEADERBOARD_CATEGORIES: tuple[str, ...] = (
    "Politics", "Sports", "Crypto", "Tech", "Mentions",
)


class PolymarketDataAPIError(Exception):
    """Base error for Polymarket data-api calls."""


class PolymarketRateLimitError(PolymarketDataAPIError):
    """HTTP 429 past the backoff budget, or a Cloudflare 403 that survived
    the in-client retry budget.

    `_get_json` retries Cloudflare-marked 403 responses on its own with
    exponential backoff (`_CLOUDFLARE_RETRY_DELAYS_SEC`) and 429s with a
    jittered ladder (`_RATE_LIMIT_RETRY_DELAYS_SEC`); only after the budget
    is exhausted does it raise this. Callers (`fetch_market_resolutions`
    in particular) catch it and continue with whatever data accumulated, so
    a single rate-limited chunk doesn't abort a long sweep.
    """


class PolymarketIncompletePositionsError(PolymarketDataAPIError):
    """`fetch_positions` could not confirm it retrieved a wallet's FULL open book.

    `/v2/positions` serves one page per request; the full book is retrieved by
    cursor-paging (`fetch_positions_book`). If paging hits the `max_pages`
    guard while the API still offers a `next_cursor` (i.e. we never saw the
    terminal page), completeness cannot be asserted and we must NOT silently
    return a partial book (that is exactly how the 100-cap under-representation
    of high-volume whales went unnoticed). `fetch_positions` raises this
    instead; callers that cannot tolerate a partial read (the poller) treat it
    as "leave this whale un-polled" (Ruling G).
    """


def _is_cloudflare_block(resp: httpx.Response) -> bool:
    """Detect Cloudflare WAF / rate-limit interstitials on a 4xx response.

    Cloudflare's block pages send a `cf-ray` header and a `Server: cloudflare`
    header; the body is HTML containing "Cloudflare" or "Attention Required".
    Either signal is enough — `cf-ray` alone is conclusive.
    """
    headers = resp.headers
    if "cf-ray" in headers or "cf-mitigated" in headers:
        return True
    if headers.get("server", "").lower() == "cloudflare":
        return True
    body = (resp.text or "")[:2000].lower()
    return "cloudflare" in body or "attention required" in body


def _endpoint_key(url: str) -> str:
    """Short per-endpoint key for the rate-limited hard-failure logger
    (e.g. 'data-api/v2/positions', 'gamma-api/markets')."""
    try:
        host, _, path = url.partition("//")[2].partition("/")
        return f"{host.split('.')[0]}/{path}"
    except Exception:  # noqa: BLE001 — a log key must never raise
        return url[:60]


def _log_hard_failure(endpoint: str, message: str) -> None:
    """ERROR-log a hard API failure, rate-limited per endpoint.

    One ERROR line per endpoint per `_ERROR_LOG_INTERVAL_SEC`; calls inside
    the window increment a suppressed-count that is reported on the next
    emitted line. The v1 retirement sat invisible for two weeks because the
    410 surfaced only as per-whale WARNING spam — this is the LOUD, greppable
    (`HARD-FAIL`) signal that replaces it, without flooding the journal.
    """
    now = time.monotonic()
    st = _err_log_state.setdefault(endpoint, [0.0, 0])
    if now - st[0] >= _ERROR_LOG_INTERVAL_SEC:
        suppressed = st[1]
        st[0] = now
        st[1] = 0
        if suppressed:
            log.error(
                "polymarket-data-api HARD-FAIL %s: %s (+%d similar suppressed in last %.0fs)",
                endpoint, message, suppressed, _ERROR_LOG_INTERVAL_SEC,
            )
        else:
            log.error("polymarket-data-api HARD-FAIL %s: %s", endpoint, message)
    else:
        st[1] += 1


def _unwrap_v2(payload: Any, label: str, *, endpoint: str) -> tuple[list, dict]:
    """Unwrap the v2 `{"data": [...], "pagination": {...}}` envelope.

    Returns `(rows, pagination)`. A bare list passes through (defensive;
    `pagination={}` → no cursor → terminal). ANYTHING else raises LOUD:
    under v1 the fetchers' `isinstance(rows, list)` tolerance turned an
    unexpected payload into a silent `[]`, which downstream reads as "this
    whale holds nothing" — and a vanished book diffs as a whale EXIT. A
    malformed envelope must never look like an empty book.
    """
    if isinstance(payload, list):
        return payload, {}
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            pagination = payload.get("pagination")
            return data, (pagination if isinstance(pagination, dict) else {})
    preview = repr(payload)[:200]
    _log_hard_failure(endpoint, f"{label}: malformed v2 envelope — {preview}")
    raise PolymarketDataAPIError(f"{label}: malformed v2 envelope — {preview}")


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
        # v2 key remap (v1 -> v2): proxyWallet -> user_id, userName -> user_name,
        # xUsername -> x_username, verifiedBadge -> verified, vol -> volume,
        # profileImage -> profile_image. Dataclass fields keep v1 names/units.
        try:
            rank = int(row.get("rank") or 0)
        except (TypeError, ValueError):
            rank = 0
        return cls(
            rank=rank,
            proxy_wallet=str(row.get("user_id") or "").lower(),
            user_name=str(row.get("user_name") or ""),
            x_username=str(row.get("x_username") or ""),
            verified_badge=bool(row.get("verified")),
            vol=float(row.get("volume") or 0.0),
            pnl=float(row.get("pnl") or 0.0),
            profile_image=str(row.get("profile_image") or ""),
        )


@dataclass(frozen=True)
class ActivityRow:
    """One row from `/v2/activity?user=<wallet>` — a TRADE or other event.

    `side` is BUY/SELL on the outcome at index `outcome_index`. To recover
    the YES/NO axis, look up the market by `condition_id` (or trust the
    `outcome` human label, e.g. "Spurs" / "Yes" / "No").
    """
    proxy_wallet: str
    timestamp: int  # unix seconds (v2 serves seconds, same as v1 — live-verified)
    condition_id: str
    type: str  # "TRADE" | possibly other types we'd ignore
    size: float  # contract count
    usdc_size: float  # USDC value of the fill
    transaction_hash: str
    price: float  # entry price in [0.0, 1.0]
    asset: str  # ERC1155 token ID (long; both sides have distinct IDs) — v2 `token_id`
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

        # v2 key remap (v1 -> v2): proxyWallet -> proxy_wallet, conditionId ->
        # condition_id, usdcSize -> usdc_size, transactionHash ->
        # transaction_hash, asset -> token_id, outcomeIndex -> outcome_index,
        # eventSlug -> event_slug; timestamp/type/size/price/side/title/slug/
        # outcome/name unchanged. Dataclass fields keep v1 names/units.
        consumed = {
            "proxy_wallet", "timestamp", "condition_id", "type", "size",
            "usdc_size", "transaction_hash", "price", "token_id", "side",
            "outcome_index", "title", "slug", "event_slug", "outcome", "name",
        }
        return cls(
            proxy_wallet=str(row.get("proxy_wallet") or "").lower(),
            timestamp=_i("timestamp"),
            condition_id=str(row.get("condition_id") or ""),
            type=str(row.get("type") or "").upper(),
            size=_f("size"),
            usdc_size=_f("usdc_size"),
            transaction_hash=str(row.get("transaction_hash") or ""),
            price=_f("price"),
            asset=str(row.get("token_id") or ""),
            side=str(row.get("side") or "").upper(),
            outcome_index=_i("outcome_index"),
            title=str(row.get("title") or ""),
            slug=str(row.get("slug") or ""),
            event_slug=str(row.get("event_slug") or ""),
            outcome=str(row.get("outcome") or ""),
            name=str(row.get("name") or ""),
            extra={k: v for k, v in row.items() if k not in consumed},
        )


@dataclass(frozen=True)
class PositionRow:
    """One row from `/v2/positions?user=<wallet>` — a current open position."""
    proxy_wallet: str
    condition_id: str
    asset: str  # ERC1155 token ID (encodes side) — v2 `token_id`
    size: float  # contracts currently held — v2 `current_size`
    avg_price: float  # average entry price [0, 1]
    initial_value: float  # USDC paid in (fee-exclusive) — v2 `entry_cost_usdc`
    current_value: float  # mark-to-market USDC
    pnl: float  # unrealized P&L (current_value - initial_value) — v2 `unrealized_pnl`
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

        # v2 key remap (v1 -> v2): proxyWallet -> proxy_wallet, conditionId ->
        # condition_id, asset -> token_id, size -> current_size, avgPrice ->
        # avg_price, initialValue -> entry_cost_usdc, currentValue ->
        # current_value, cashPnl -> unrealized_pnl, eventSlug -> event_slug.
        # Dataclass fields keep v1 names/units (verified against stored v1
        # rows: initial_value = size*avg_price, pnl = current_value -
        # initial_value — both identities hold on live v2 payloads).
        consumed = {
            "proxy_wallet", "condition_id", "token_id", "current_size",
            "avg_price", "entry_cost_usdc", "current_value", "unrealized_pnl",
            "title", "outcome", "slug", "event_slug",
        }
        # ★ THE EXTRA-KEY CONTRACT: prediction_markets.paper reads FOUR raw v1
        # keys out of `extra` BY NAME — pos_outcome_index -> "outcomeIndex",
        # is_genuinely_open -> "curPrice" + "redeemable", pos_end_date ->
        # "endDate" (paper.py:95-113). Under v2's snake_case raw keys those
        # reads would MISS and default: outcome_index -> 0 (the WRONG LEG for
        # every index-1 position) and curPrice -> 0.0 (every position reads
        # as settled -> ZERO entry signals, silently). So the v1-era keys are
        # SYNTHESIZED here from the v2 fields; raw v2 leftovers ride along
        # (debug value) but never overwrite the synthesized four.
        extra: dict[str, Any] = {
            "outcomeIndex": row.get("outcome_index"),
            "curPrice": row.get("current_price"),
            "redeemable": row.get("redeemable"),
            "endDate": row.get("end_date"),
        }
        for k, v in row.items():
            if k not in consumed and k not in extra:
                extra[k] = v
        return cls(
            proxy_wallet=str(row.get("proxy_wallet") or "").lower(),
            condition_id=str(row.get("condition_id") or ""),
            asset=str(row.get("token_id") or ""),
            size=_f("current_size"),
            avg_price=_f("avg_price"),
            initial_value=_f("entry_cost_usdc"),
            current_value=_f("current_value"),
            pnl=_f("unrealized_pnl"),
            title=str(row.get("title") or ""),
            outcome=str(row.get("outcome") or ""),
            slug=str(row.get("slug") or ""),
            event_slug=str(row.get("event_slug") or ""),
            extra=extra,
        )


@dataclass(frozen=True)
class ClosedPositionRow:
    """One row from `/v2/positions?status=CLOSED` — a resolved position.

    (v1 served these from the separate `/closed-positions` endpoint; v2
    merged it into `/v2/positions` behind the `status` filter.)

    Each row represents one resolved binary-market position. `cur_price` is
    the market's final settlement price; values ≥ 0.9 indicate a win (the
    holder's side resolved YES). `realized_pnl` is the USDC profit/loss after
    resolution.
    """
    proxy_wallet: str
    asset: str               # ERC1155 token ID — v2 `token_id`
    condition_id: str
    avg_price: float         # average entry price [0, 1]
    total_bought: float      # SHARES bought (notional payout@$1), NOT USDC — v2 `total_size`.
    #                          ingest §13A(g) established v1 `totalBought` carried shares;
    #                          cost_basis = total_bought * avg_price downstream. Verified
    #                          against stored v1 rows (box check 2026-11-17).
    realized_pnl: float      # USDC profit/loss after resolution
    cur_price: float         # final settlement price (≥0.9 → holder's side won) — v2 `current_price`
    title: str               # human-readable market question
    slug: str
    icon: str
    event_slug: str
    outcome: str             # human label ("Yes", "No", "Spurs", etc.)
    outcome_index: int
    opposite_outcome: str
    opposite_asset: str      # v2 `opposite_token_id`
    end_date: str            # ISO string or empty
    timestamp: int           # unix seconds — v2 `last_event_at` (the position's final
    #                          event: last sell/redeem). v1 called this the resolution
    #                          ts; for a closed position the two sit within the same
    #                          settlement window. Feeds ingest resolved_ts -> the 30d
    #                          recency gate (units pinned by tests).

    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "ClosedPositionRow":
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

        # v2 key remap (v1 -> v2): proxyWallet -> proxy_wallet, asset ->
        # token_id, conditionId -> condition_id, avgPrice -> avg_price,
        # totalBought -> total_size, realizedPnl -> realized_pnl, curPrice ->
        # current_price, eventSlug -> event_slug, outcomeIndex ->
        # outcome_index, oppositeOutcome -> opposite_outcome, oppositeAsset ->
        # opposite_token_id, endDate -> end_date, timestamp -> last_event_at.
        consumed = {
            "proxy_wallet", "token_id", "condition_id", "avg_price",
            "total_size", "realized_pnl", "current_price", "title", "slug",
            "icon", "event_slug", "outcome", "outcome_index",
            "opposite_outcome", "opposite_token_id", "end_date",
            "last_event_at",
        }
        return cls(
            proxy_wallet=str(row.get("proxy_wallet") or "").lower(),
            asset=str(row.get("token_id") or ""),
            condition_id=str(row.get("condition_id") or ""),
            avg_price=_f("avg_price"),
            total_bought=_f("total_size"),
            realized_pnl=_f("realized_pnl"),
            cur_price=_f("current_price"),
            title=str(row.get("title") or ""),
            slug=str(row.get("slug") or ""),
            icon=str(row.get("icon") or ""),
            event_slug=str(row.get("event_slug") or ""),
            outcome=str(row.get("outcome") or ""),
            outcome_index=_i("outcome_index"),
            opposite_outcome=str(row.get("opposite_outcome") or ""),
            opposite_asset=str(row.get("opposite_token_id") or ""),
            end_date=str(row.get("end_date") or ""),
            timestamp=_i("last_event_at"),
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


@dataclass(frozen=True)
class PositionBook:
    """The result of a FULL-BOOK `/v2/positions` fetch (`fetch_positions_book`).

    `complete` is the load-bearing field: True iff paging reached the terminal
    page (the API stopped offering a `next_cursor`), i.e. we can assert we saw
    the whole open book. False iff the `max_pages` guard was hit while a
    `next_cursor` was still offered — the book may be larger than what `rows`
    holds, so a caller must NOT treat `rows` as the whole book. `pages` =
    requests made; `n` = len(rows) after de-duplication by
    (condition_id, token_id).
    """
    rows: list[PositionRow]
    complete: bool
    pages: int
    n: int


class PolymarketDataAPIClient:
    """Async client for Polymarket's public Data API v2.

    Used as an async context manager so the underlying httpx.AsyncClient
    is cleaned up properly. Concurrency is gated by an internal semaphore
    so a runaway loop can't exhaust the per-IP rate limit.
    """

    def __init__(self, *, max_concurrent: int = 5) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)
        self._client: httpx.AsyncClient | None = None
        # Cursor cache for the v1 offset-contract translation (module
        # docstring item 2): (scope..., offset) -> the cursor that starts the
        # page AT that offset. Populated as pages are fetched, so sequential
        # paging (every caller's shape) is one request per page. Bounded by
        # `_prune_cursors` so the never-die live loop cannot grow it forever.
        self._page_cursors: dict[tuple, str] = {}

    async def __aenter__(self) -> "PolymarketDataAPIClient":
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(_HTTP_TIMEOUT_SEC))
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _prune_cursors(self) -> None:
        """Keep the cursor cache bounded (the live loop's client is
        long-lived). 4096 entries ≈ 80+ full offset-sweeps; clearing simply
        costs the next non-zero-offset call a forward walk."""
        if len(self._page_cursors) > 4096:
            self._page_cursors.clear()

    async def _paged_rows(
        self, url: str, *, base_params: dict[str, Any], scope: tuple,
        limit: int, offset: int, label: str,
    ) -> list:
        """Serve the v1 `(limit, offset)` page contract from a cursor-only v2
        endpoint. Returns the rows at [offset, offset+limit) — `[]` when the
        data ends at/before `offset` (the v1 terminal signal the paging loops
        break on).

        Sequential callers hit the cursor cache (one request per page). A
        cold/non-sequential offset is served by walking forward from the
        nearest cached boundary (or 0), caching boundaries on the way.
        Terminal = the API offers NO `next_cursor`. `_CURSOR_WALK_MAX_PAGES`
        bounds the walk and raises LOUD if exceeded.
        """
        endpoint = _endpoint_key(url)
        # Seek: the nearest cached boundary at or below the requested offset.
        pos = 0
        cursor: str | None = None
        if offset > 0:
            best = -1
            for key, cur in self._page_cursors.items():
                if key[:-1] == scope and isinstance(key[-1], int) and best < key[-1] <= offset:
                    best, cursor = key[-1], cur
            if best >= 0:
                pos = best
        out: list = []
        pages = 0
        while True:
            if pages >= _CURSOR_WALK_MAX_PAGES:
                msg = (f"{label}: cursor walk exceeded {_CURSOR_WALK_MAX_PAGES} pages "
                       f"(offset={offset}, reached pos={pos}) — refusing to walk forever")
                _log_hard_failure(endpoint, msg)
                raise PolymarketDataAPIError(msg)
            params = dict(base_params)
            params["limit"] = int(limit)
            if cursor:
                params["cursor"] = cursor
            payload = await self._get_json(url, params=params, label=f"{label} @pos={pos}")
            pages += 1
            rows, pagination = _unwrap_v2(payload, label, endpoint=endpoint)
            next_cursor = pagination.get("next_cursor") if isinstance(pagination, dict) else None
            page_end = pos + len(rows)
            if next_cursor:
                self._page_cursors[scope + (page_end,)] = str(next_cursor)
                self._prune_cursors()
            if page_end > offset:
                start_in_page = max(0, offset - pos)
                out.extend(rows[start_in_page:])
            if len(out) >= limit:
                return out[:limit]
            if not next_cursor:          # terminal page — the API offered no continuation
                return out[:limit]       # may be [] (offset at/past the end) — v1-identical
            pos, cursor = page_end, str(next_cursor)

    async def fetch_leaderboard(
        self,
        *,
        category: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str | None = None,
    ) -> list[LeaderboardEntry]:
        """Pull a slice of the leaderboard (`/v2/leaderboard`). `category=None`
        returns the global leaderboard. Working category values are in
        `POLYMARKET_LEADERBOARD_CATEGORIES`. v2 is cursor-only; the `offset`
        contract is translated internally (see `_paged_rows`)."""
        base: dict[str, Any] = {}
        if category:
            base["category"] = category
        if sort_by:
            base["sort_by"] = sort_by   # v2 renamed v1's `sortBy`
        rows = await self._paged_rows(
            f"{_DATA_API_V2}/leaderboard",
            base_params=base,
            scope=("leaderboard", category or "", sort_by or "", int(limit)),
            limit=int(limit), offset=int(offset),
            label=f"leaderboard[cat={category or 'all'}, limit={limit}, offset={offset}]",
        )
        return [LeaderboardEntry.from_api(r) for r in rows if isinstance(r, dict)]

    async def fetch_activity(
        self,
        wallet: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ActivityRow]:
        """Pull trade history for one wallet (`/v2/activity`). Returns
        most-recent first. For Wilson-LCB scoring we typically pull the
        most-recent ~50-100 trades over a time-weighted window. v2 is
        cursor-only; the `offset` contract is translated internally."""
        rows = await self._paged_rows(
            f"{_DATA_API_V2}/activity",
            base_params={"user": wallet},
            scope=("activity", wallet.lower(), int(limit)),
            limit=int(limit), offset=int(offset),
            label=f"activity[{wallet[:10]}…, limit={limit}, offset={offset}]",
        )
        return [ActivityRow.from_api(r) for r in rows if isinstance(r, dict)]

    async def fetch_positions_book(
        self, wallet: str, *, page_size: int = 500, max_pages: int = 40,
    ) -> PositionBook:
        """Retrieve a wallet's FULL open book from `/v2/positions` by cursor-paging.

        Pages with `limit=page_size`, following `pagination.next_cursor` until the API stops
        offering one — THAT is the terminal signal (never row counts: a silently-capped `limit`
        must not read as end-of-book). De-dupes by (condition_id, token_id) so a book that
        shifts between pages cannot double-count. `max_pages` is an infinite-loop backstop:
        hitting it while a `next_cursor` is still offered yields complete=False (we could NOT
        confirm we saw the end). A malformed envelope RAISES (it must never read as an empty
        book — an empty book diffs as a whale exit).
        """
        endpoint = _endpoint_key(f"{_DATA_API_V2}/positions")
        seen: set[tuple[str, str]] = set()
        rows: list[PositionRow] = []
        pages = 0
        complete = False
        cursor: str | None = None
        while pages < max_pages:
            params: dict[str, Any] = {"user": wallet, "limit": int(page_size)}
            if cursor:
                params["cursor"] = cursor
            label = f"positions[{wallet[:10]}…, limit={page_size}, page={pages}]"
            payload = await self._get_json(
                f"{_DATA_API_V2}/positions", params=params, label=label,
            )
            pages += 1
            page_rows, pagination = _unwrap_v2(payload, label, endpoint=endpoint)
            for r in page_rows:
                if not isinstance(r, dict):
                    continue
                key = (str(r.get("condition_id") or ""), str(r.get("token_id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(PositionRow.from_api(r))
            next_cursor = pagination.get("next_cursor") if isinstance(pagination, dict) else None
            if not next_cursor:                     # terminal — the API offered no continuation
                complete = True
                break
            cursor = str(next_cursor)
        return PositionBook(rows=rows, complete=complete, pages=pages, n=len(rows))

    async def fetch_positions(
        self, wallet: str, *, page_size: int = 500, max_pages: int = 40,
    ) -> list[PositionRow]:
        """A wallet's FULL open book (cursor-paged internally; see `fetch_positions_book`).

        Backward-compatible list contract -- the COMPLETE book. Raises
        `PolymarketIncompletePositionsError` if completeness cannot be confirmed: never
        returns a silent partial (a partial read is exactly what hid the high-volume-whale
        under-count). Callers that want to branch on completeness without an exception (the
        poller) call `fetch_positions_book` and read `.complete`.
        """
        book = await self.fetch_positions_book(wallet, page_size=page_size, max_pages=max_pages)
        if not book.complete:
            raise PolymarketIncompletePositionsError(
                f"positions[{wallet[:10]}…]: full book NOT confirmed after {book.pages} pages "
                f"({book.n} rows, page_size={page_size}, max_pages={max_pages}) -- refusing partial"
            )
        return book.rows

    async def fetch_closed_positions(
        self,
        wallet: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ClosedPositionRow]:
        """Pull resolved positions for one wallet (`/v2/positions?status=CLOSED`).

        v2 MERGED v1's separate `/closed-positions` endpoint into
        `/v2/positions` behind the `status` filter — without `status=CLOSED`
        the default is OPEN-only, so the filter here is load-bearing. Each
        row is one resolved binary-market position. v2 is cursor-only; the
        `offset` paging contract callers use is translated internally.

        Win detection: `cur_price >= 0.9` means the holder's side resolved as
        the winner — mirrors `_decode_resolution`'s threshold.
        """
        rows = await self._paged_rows(
            f"{_DATA_API_V2}/positions",
            base_params={"user": wallet, "status": "CLOSED"},
            scope=("closed-positions", wallet.lower(), int(limit)),
            limit=int(limit), offset=int(offset),
            label=f"closed-positions[{wallet[:10]}…, limit={limit}, offset={offset}]",
        )
        return [ClosedPositionRow.from_api(r) for r in rows if isinstance(r, dict)]

    async def fetch_market_resolutions(
        self, condition_ids: list[str], *, chunk_size: int = 50,
    ) -> dict[str, dict]:
        """Batch-lookup market resolutions for a list of condition_ids.

        Hits `gamma-api.polymarket.com/markets?condition_ids=A&condition_ids=B&…`
        with the repeated-param batch form (verified live; comma-separated is
        silently ignored). Chunks at `chunk_size` to keep URL length bounded.
        (gamma-api is a SEPARATE host from the data-api and was NOT part of
        the v1 retirement — this path is unchanged.)

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
        rate_limited_chunks = 0
        upstream_error_chunks = 0
        for i in range(0, len(unique), chunk_size):
            chunk = unique[i:i + chunk_size]
            # Gamma-api default is `closed=false` (active markets only) and
            # the `condition_ids` filter intersects with that — so we need
            # TWO queries per chunk: one for open, one for closed. Merge.
            base_params = [("condition_ids", c) for c in chunk]
            base_params.append(("limit", str(chunk_size)))
            chunk_rate_limited = False
            chunk_upstream_error = False
            for variant in ("open", "closed"):
                params = list(base_params)
                if variant == "closed":
                    params.append(("closed", "true"))
                try:
                    rows = await self._get_json(
                        f"{_GAMMA_API_BASE}/markets",
                        params=params,
                        label=f"resolutions[{len(chunk)} ids, chunk {i // chunk_size}, {variant}]",
                    )
                except PolymarketRateLimitError as e:
                    # Cloudflare 403 (or 429) survived in-client retry. Skip
                    # this chunk-variant and continue; missing condition_ids
                    # fall through to the not_found sentinel below so the
                    # caller still gets a complete dict.
                    chunk_rate_limited = True
                    log.warning(
                        "polymarket-data-api fetch_market_resolutions chunk %d "
                        "(%s) rate-limited; partial coverage: %s",
                        i // chunk_size, variant, e,
                    )
                    continue
                except PolymarketDataAPIError as e:
                    # Upstream 5xx (or other non-rate-limit gamma-api error)
                    # survived the in-client retry budget. Same fallback as
                    # rate-limited path: skip chunk-variant, fall through to
                    # not_found sentinels so the caller still gets a complete
                    # dict and one bad chunk doesn't kill the whole sweep.
                    # PolymarketRateLimitError subclasses this — the order of
                    # except blocks above keeps it on its dedicated path.
                    chunk_upstream_error = True
                    log.warning(
                        "polymarket-data-api fetch_market_resolutions chunk %d "
                        "(%s) upstream error; partial coverage: %s",
                        i // chunk_size, variant, e,
                    )
                    continue
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
            if chunk_rate_limited:
                rate_limited_chunks += 1
            if chunk_upstream_error:
                upstream_error_chunks += 1
        # Fill in "not_found" for any missing condition_ids the caller asked for
        # so downstream code can iterate without KeyError.
        for c in unique:
            if c not in out:
                out[c] = {"status": "not_found", "winning_outcome_index": None,
                          "yes_won": None, "outcomes": [], "outcome_prices": [],
                          "closed": False, "title": ""}
        if rate_limited_chunks or upstream_error_chunks:
            log.warning(
                "polymarket-data-api fetch_market_resolutions: "
                "%d/%d chunks rate-limited, %d/%d chunks upstream-errored; "
                "%d/%d condition_ids resolved",
                rate_limited_chunks, (len(unique) + chunk_size - 1) // chunk_size,
                upstream_error_chunks, (len(unique) + chunk_size - 1) // chunk_size,
                sum(1 for c in unique if out[c]["status"] != "not_found"),
                len(unique),
            )
        return out

    async def _get_json(
        self, url: str, *, params: Any, label: str,
    ) -> Any:
        if self._client is None:
            raise RuntimeError(
                "PolymarketDataAPIClient must be used as an async context "
                "manager: `async with PolymarketDataAPIClient() as client:`"
            )
        endpoint = _endpoint_key(url)
        attempt = 0
        server_error_attempts = 0
        rate_limit_attempts = 0
        while True:
            t0 = time.monotonic()
            async with self._sem:
                try:
                    resp = await self._client.get(url, params=params)
                except httpx.TimeoutException as e:
                    _log_hard_failure(endpoint, f"{label}: HTTP timeout")
                    raise PolymarketDataAPIError(f"{label}: HTTP timeout") from e
                except httpx.HTTPError as e:
                    _log_hard_failure(
                        endpoint, f"{label}: HTTP error {type(e).__name__}: {e}"
                    )
                    raise PolymarketDataAPIError(
                        f"{label}: HTTP error {type(e).__name__}: {e}"
                    ) from e
            dur_ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code == 429:
                # Backoff with jitter (v2 publishes sliding-10s per-endpoint
                # limits); only an exhausted budget raises. Never a hot loop —
                # every retry sleeps.
                if rate_limit_attempts >= len(_RATE_LIMIT_RETRY_DELAYS_SEC):
                    _log_hard_failure(
                        endpoint,
                        f"{label}: HTTP 429 after {rate_limit_attempts + 1} attempts",
                    )
                    raise PolymarketRateLimitError(
                        f"{label}: HTTP 429 after {rate_limit_attempts + 1} attempts"
                    )
                base = _RATE_LIMIT_RETRY_DELAYS_SEC[rate_limit_attempts]
                delay = base + random.uniform(0.0, base * 0.5)
                log.warning(
                    "polymarket-data-api %s: HTTP 429 on attempt %d (%dms); "
                    "backing off %.1fs (jittered)",
                    label, rate_limit_attempts + 1, dur_ms, delay,
                )
                rate_limit_attempts += 1
                await asyncio.sleep(delay)
                continue
            if resp.status_code == 403 and _is_cloudflare_block(resp):
                if attempt >= len(_CLOUDFLARE_RETRY_DELAYS_SEC):
                    _log_hard_failure(
                        endpoint,
                        f"{label}: HTTP 403 Cloudflare block survived "
                        f"{attempt + 1} attempts",
                    )
                    raise PolymarketRateLimitError(
                        f"{label}: HTTP 403 Cloudflare block survived "
                        f"{attempt + 1} attempts; giving up"
                    )
                delay = _CLOUDFLARE_RETRY_DELAYS_SEC[attempt]
                log.warning(
                    "polymarket-data-api %s: HTTP 403 Cloudflare block on "
                    "attempt %d (%dms); backing off %.0fs",
                    label, attempt + 1, dur_ms, delay,
                )
                attempt += 1
                await asyncio.sleep(delay)
                continue
            if 500 <= resp.status_code < 600:
                if server_error_attempts >= len(_SERVER_ERROR_RETRY_DELAYS_SEC):
                    body_preview = resp.text[:300] if resp.text else "(empty)"
                    _log_hard_failure(
                        endpoint,
                        f"{label}: HTTP {resp.status_code} after "
                        f"{server_error_attempts + 1} attempts",
                    )
                    raise PolymarketDataAPIError(
                        f"{label}: HTTP {resp.status_code} after "
                        f"{server_error_attempts + 1} attempts — {body_preview}"
                    )
                delay = _SERVER_ERROR_RETRY_DELAYS_SEC[server_error_attempts]
                log.warning(
                    "polymarket-data-api %s: HTTP %d on attempt %d (%dms); "
                    "backing off %.1fs",
                    label, resp.status_code, server_error_attempts + 1,
                    dur_ms, delay,
                )
                server_error_attempts += 1
                await asyncio.sleep(delay)
                continue
            if resp.status_code >= 400:
                # Non-retryable 4xx. 410 = endpoint retired/gone (the class of
                # failure that silenced the division for two weeks in 2026-11)
                # — named explicitly so it is greppable.
                body_preview = resp.text[:300] if resp.text else "(empty)"
                tag = " (endpoint retired/gone)" if resp.status_code == 410 else ""
                _log_hard_failure(
                    endpoint, f"{label}: HTTP {resp.status_code}{tag} — {body_preview}"
                )
                raise PolymarketDataAPIError(
                    f"{label}: HTTP {resp.status_code}{tag} — {body_preview}"
                )
            try:
                payload = resp.json()
            except json.JSONDecodeError as e:
                _log_hard_failure(
                    endpoint,
                    f"{label}: non-JSON response (HTTP {resp.status_code})",
                )
                raise PolymarketDataAPIError(
                    f"{label}: non-JSON response (HTTP {resp.status_code}): "
                    f"{resp.text[:200]}"
                ) from e
            n_rows = len(payload) if isinstance(payload, list) else 1
            log.info("polymarket-data-api %s: %d rows in %dms", label, n_rows, dur_ms)
            return payload
