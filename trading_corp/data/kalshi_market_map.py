"""Kalshi market discovery + classification (Phase K2.0).

Fetches all OPEN Kalshi markets via pykalshi, filters out non-tradeable
collection tickers (the `KXMVE*` family that returns $1/$1 as a sentinel),
groups markets by event, and classifies each event by structural type:

  BINARY        single-market event with YES/NO outcomes (sum to 1 by construction)
  MULTI_OUTCOME `mutually_exclusive=True` event with N>1 markets summing to 1
                (e.g., presidential primary winners, Pope candidates)
  TEMPORAL      multi-market event whose markets are dated thresholds
                ("X by D1", "X by D2", ...) — must satisfy P(D1) ≤ P(D2)
                for D2 > D1
  BUCKET        multi-market event whose markets partition outcome space
                ("Q1" / "Q2" / "Q3" / "Q4" / "none") — should sum to ≤1
                (mutually_exclusive=True with subtitle date pattern)
  COLLECTION    KXMVE* aggregate — not directly tradeable, filtered out
  OTHER         doesn't fit any of the above — leave for manual inspection

Phase K2.0 ships audit-only: `discover()` returns the classified map;
`audit_scan_summary()` produces a count breakdown per type. Phase K2.1
(tail-price detector) and the future temporal/bucket arb detector
(K2.2) consume this map.

Helpers `is_tradeable_market` and `get_market_prices` lifted (MIT) from
ryanfrigo/kalshi-ai-trading-bot:src/utils/market_prices.py. They handle
the API-v2 dollar-float vs legacy-cents-int field-naming drift and the
collection-ticker $1/$1 sentinel.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)


# ── Helpers lifted from ryanfrigo/kalshi-ai-trading-bot (MIT) ────────

# Threshold above which both sides signal a collection/aggregate ticker.
# Per upstream comment: KXMVECROSSCATEGORY-*, KXMVESPORTSMULTIGAMEEXTENDED-*
# return yes_ask == no_ask == $1.00 as a sentinel; ordering against them
# 400s with `invalid_price`.
_COLLECTION_TICKER_THRESHOLD = 0.99


def get_market_prices(market_info: dict[str, Any]) -> tuple[float, float, float, float]:
    """Extract (yes_bid, yes_ask, no_bid, no_ask) as floats in [0,1].

    Supports both Kalshi API v2 (dollar floats: `yes_bid_dollars` etc.)
    and legacy (cent ints: `yes_bid` etc.). Missing/None values become 0.
    """
    if "yes_bid_dollars" in market_info:
        yb = float(market_info.get("yes_bid_dollars") or 0)
        ya = float(market_info.get("yes_ask_dollars") or 0)
        nb = float(market_info.get("no_bid_dollars") or 0)
        na = float(market_info.get("no_ask_dollars") or 0)
    else:
        yb = (market_info.get("yes_bid") or 0) / 100
        ya = (market_info.get("yes_ask") or 0) / 100
        nb = (market_info.get("no_bid") or 0) / 100
        na = (market_info.get("no_ask") or 0) / 100
    return yb, ya, nb, na


def is_tradeable_market(market_info: dict[str, Any]) -> bool:
    """False if the market looks like a non-tradeable collection ticker.

    A market is non-tradeable when BOTH ask prices are at/above the
    collection-ticker threshold (≥0.99). This is the empirically-derived
    guard for KXMVE* collection tickers.
    """
    _, yes_ask, _, no_ask = get_market_prices(market_info)
    if yes_ask >= _COLLECTION_TICKER_THRESHOLD and no_ask >= _COLLECTION_TICKER_THRESHOLD:
        return False
    return True


# ── Classification ──────────────────────────────────────────────────


class EventType(str, Enum):
    BINARY = "binary"
    MULTI_OUTCOME = "multi_outcome"
    TEMPORAL = "temporal"
    BUCKET = "bucket"
    COLLECTION = "collection"
    OTHER = "other"


# Subtitle patterns suggesting temporal/date-based markets.
# Examples seen on Kalshi:
#   "Before May 31"            -> temporal upper-bound
#   "On or before 2026-09-15"  -> temporal upper-bound
#   "Q1 2026" / "Q2 2026"      -> bucket (quarterly)
#   "January" / "February"     -> bucket (monthly)
#   "May 12, 2026"             -> bucket (single-date)
_DATE_PATTERNS = [
    re.compile(r"\b(before|by|on or before|prior to|no later than)\b", re.IGNORECASE),
    re.compile(r"\bQ[1-4]\s*\d{4}\b", re.IGNORECASE),
    re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b", re.IGNORECASE),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
]
_THRESHOLD_PATTERNS = [
    re.compile(r"\b(before|by|on or before|prior to|no later than)\b", re.IGNORECASE),
]
_BUCKET_PATTERNS = [
    re.compile(r"\bQ[1-4]\s*\d{4}\b", re.IGNORECASE),
    re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b", re.IGNORECASE),
]


def _looks_temporal(subtitles: list[str]) -> bool:
    """Heuristic: subtitles all say 'before/by <date>' (threshold form).

    Temporal series have constraint P(D1) ≤ P(D2) for D2 > D1.
    """
    if len(subtitles) < 2:
        return False
    threshold_hits = sum(
        1 for s in subtitles if any(p.search(s) for p in _THRESHOLD_PATTERNS)
    )
    return threshold_hits >= max(2, len(subtitles) - 1)  # nearly all subtitles match


def _looks_bucket(subtitles: list[str]) -> bool:
    """Heuristic: subtitles are partition labels (Q1/Q2/Q3/Q4 or month names)."""
    if len(subtitles) < 2:
        return False
    bucket_hits = sum(
        1 for s in subtitles if any(p.search(s) for p in _BUCKET_PATTERNS)
    )
    return bucket_hits >= max(2, len(subtitles) - 1)


def _is_collection_ticker(event_ticker: str) -> bool:
    """KXMVE* prefix indicates a non-tradeable collection / aggregate."""
    return event_ticker.upper().startswith("KXMVE")


# ── Records ─────────────────────────────────────────────────────────


@dataclass
class MarketRecord:
    """Normalized Kalshi market — flat dict-ish view for downstream code."""
    ticker: str
    event_ticker: str
    title: str
    subtitle: str
    yes_bid: float          # dollars in [0,1]
    yes_ask: float
    no_bid: float
    no_ask: float
    volume: int
    liquidity: int
    open_interest: int
    expected_expiration_time: str | None  # ISO-8601 or None

    @property
    def yes_mid(self) -> float:
        if self.yes_bid > 0 and self.yes_ask > 0:
            return (self.yes_bid + self.yes_ask) / 2
        return self.yes_bid or self.yes_ask

    @property
    def no_mid(self) -> float:
        if self.no_bid > 0 and self.no_ask > 0:
            return (self.no_bid + self.no_ask) / 2
        return self.no_bid or self.no_ask

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "event_ticker": self.event_ticker,
            "title": self.title,
            "subtitle": self.subtitle,
            "yes_bid": self.yes_bid,
            "yes_ask": self.yes_ask,
            "no_bid": self.no_bid,
            "no_ask": self.no_ask,
            "yes_mid": self.yes_mid,
            "no_mid": self.no_mid,
            "volume": self.volume,
            "liquidity": self.liquidity,
            "open_interest": self.open_interest,
            "expected_expiration_time": self.expected_expiration_time,
        }


@dataclass
class EventRecord:
    """One Kalshi event with its tradeable markets, plus classification."""
    event_ticker: str
    series_ticker: str
    title: str
    sub_title: str
    category: str
    mutually_exclusive: bool
    markets: list[MarketRecord] = field(default_factory=list)
    event_type: EventType = EventType.OTHER

    @property
    def n_markets(self) -> int:
        return len(self.markets)


# ── Discovery ───────────────────────────────────────────────────────


def _market_to_dict(m: Any) -> dict[str, Any]:
    """Pull all the fields we care about off a pykalshi AsyncMarket."""
    out = {}
    for attr in (
        "ticker", "event_ticker", "title", "subtitle", "yes_sub_title", "no_sub_title",
        "yes_bid", "yes_ask", "no_bid", "no_ask",
        "yes_bid_dollars", "yes_ask_dollars", "no_bid_dollars", "no_ask_dollars",
        "volume", "volume_24h", "liquidity", "open_interest",
        "expected_expiration_time", "close_time", "expiration_time",
        "status",
    ):
        if hasattr(m, attr):
            v = getattr(m, attr)
            if v is not None:
                out[attr] = v
    return out


def _build_market_record(d: dict[str, Any]) -> MarketRecord:
    yb, ya, nb, na = get_market_prices(d)
    return MarketRecord(
        ticker=str(d.get("ticker", "")),
        event_ticker=str(d.get("event_ticker", "")),
        title=str(d.get("title", "") or ""),
        subtitle=str(d.get("subtitle") or d.get("yes_sub_title") or ""),
        yes_bid=yb, yes_ask=ya, no_bid=nb, no_ask=na,
        volume=int(d.get("volume") or 0),
        liquidity=int(d.get("liquidity") or 0),
        open_interest=int(d.get("open_interest") or 0),
        expected_expiration_time=(
            str(d["expected_expiration_time"])
            if d.get("expected_expiration_time") else None
        ),
    )


def classify_event(
    event_ticker: str,
    mutually_exclusive: bool,
    markets: list[MarketRecord],
) -> EventType:
    """Classify an event by its market structure.

    Order of checks:
      1. KXMVE* prefix -> COLLECTION (non-tradeable container)
      2. zero markets -> OTHER (event without exposed markets)
      3. one market -> BINARY (single YES/NO contract)
      4. multi-market + temporal subtitle pattern -> TEMPORAL
      5. multi-market + bucket subtitle pattern -> BUCKET
      6. multi-market + mutually_exclusive -> MULTI_OUTCOME
      7. fallthrough -> OTHER
    """
    if _is_collection_ticker(event_ticker):
        return EventType.COLLECTION
    if not markets:
        return EventType.OTHER
    if len(markets) == 1:
        return EventType.BINARY
    subs = [m.subtitle for m in markets if m.subtitle]
    if _looks_temporal(subs):
        return EventType.TEMPORAL
    if _looks_bucket(subs):
        return EventType.BUCKET
    if mutually_exclusive:
        return EventType.MULTI_OUTCOME
    return EventType.OTHER


@dataclass
class DiscoveryResult:
    """Output of one discovery pass."""
    events: list[EventRecord]
    n_markets_total: int
    n_markets_filtered_collection: int  # tradeable-guard rejections
    n_events_total: int
    by_type: dict[str, int]            # EventType.value -> count

    def audit_summary(self) -> dict[str, Any]:
        return {
            "n_events_total": self.n_events_total,
            "n_markets_total": self.n_markets_total,
            "n_markets_filtered_collection": self.n_markets_filtered_collection,
            "events_by_type": self.by_type,
        }


# ── Curated category seed list for targeted discovery ───────────────
#
# Discovered empirically 2026-05-10: Kalshi's get_markets endpoint without
# filters returns ~all KXMVE* sports parlay containers in the first many
# pages — `discover_open_markets()` is essentially useless as the primary
# discovery path because pagination terminates inside the noise. The right
# pattern is category-targeted discovery via `get_all_series(category=...)`
# then `get_markets(series_ticker=...)` per series.
#
# Seed categories prioritize where intra-Kalshi arb actually exists per
# the K2 fee research (memory trading_corp_kalshi.md):
#   - Politics + Elections: long-shot candidate markets at tail prices
#   - Economics: Fed decisions, CPI, GDP -> bucketed temporal markets
#   - Crypto: BTC/ETH/SOL price markets -> binary, sometimes tail
#   - Climate: weather + temperature markets -> bucketed
DEFAULT_DISCOVERY_CATEGORIES = (
    "Politics",
    "Elections",
    "Economics",
    "Financials",
    "Crypto",
    "Climate and Weather",
)


async def discover_by_categories(
    client,
    *,
    categories: tuple[str, ...] = DEFAULT_DISCOVERY_CATEGORIES,
    max_series_per_category: int = 50,
    max_markets_per_series: int = 50,
    inter_call_delay_sec: float = 0.15,
) -> DiscoveryResult:
    """Discovery via category -> series -> markets traversal.

    Far more efficient than scanning all OPEN markets (which is dominated
    by KXMVE* sports parlay noise — see module-level comment). Cost is
    bounded: O(categories) + O(min(series_per_cat, max_series_per_category) ×
    n_categories) get_markets calls + O(events) get_event calls.

    Two cost guards (added 2026-05-10 after a runaway scan hit Kalshi's
    rate limit on 4482 series across 6 categories — pykalshi's
    `get_all_series` ignores the `limit` kwarg and returns everything for
    the category):
      1. We TRUNCATE our consumption of the get_all_series result to
         `max_series_per_category` BEFORE iterating get_markets — pykalshi's
         own limit param is unreliable as a true cap.
      2. We sleep `inter_call_delay_sec` between get_markets calls to stay
         under Kalshi's per-IP rate limit (empirically ~5-10 req/s; with
         150ms between calls we're at ~6.7 req/s sustained).
    """
    import asyncio
    from pykalshi import MarketStatus

    # Step 1: enumerate series in the target categories.
    # NOTE: pykalshi's get_all_series silently fetches all pages for the
    # category despite limit + fetch_all=False. We must cap consumption
    # ourselves; never trust the param to bound output. See comment above.
    all_series_tickers: list[str] = []
    for cat in categories:
        try:
            series = await client.get_all_series(
                category=cat, limit=max_series_per_category,
            )
        except Exception as e:
            log.warning("kalshi_market_map: get_all_series(%s) failed: %s", cat, e)
            continue
        cat_count = 0
        for s_obj in series:
            if cat_count >= max_series_per_category:
                break
            t = getattr(s_obj, "ticker", None)
            if t:
                all_series_tickers.append(t)
                cat_count += 1

    log.info(
        "kalshi_market_map: enumerated %d series (capped at %d/category × %d categories)",
        len(all_series_tickers), max_series_per_category, len(categories),
    )

    # Step 2: pull OPEN markets for each series, with explicit per-call
    # delay to stay under Kalshi's rate limit. pykalshi's internal retry
    # handles 429s but we want to AVOID them in the first place.
    all_market_dicts: list[dict[str, Any]] = []
    n_filtered = 0
    for st in all_series_tickers:
        try:
            ms = await client.get_markets(
                series_ticker=st,
                status=MarketStatus.OPEN,
                limit=max_markets_per_series,
            )
        except Exception as e:
            log.debug("kalshi_market_map: get_markets(%s) failed: %s", st, e)
            await asyncio.sleep(inter_call_delay_sec)
            continue
        for m in ms:
            d = _market_to_dict(m)
            if not is_tradeable_market(d):
                n_filtered += 1
                continue
            all_market_dicts.append(d)
        await asyncio.sleep(inter_call_delay_sec)

    log.info(
        "kalshi_market_map: collected %d tradeable markets (%d filtered as collection)",
        len(all_market_dicts), n_filtered,
    )

    # Step 3: group + classify (shared with discover_open_markets).
    return await _build_discovery_result(
        client, all_market_dicts, n_filtered,
        inter_call_delay_sec=inter_call_delay_sec,
    )


async def discover_open_markets(
    client,
    *,
    page_size: int = 200,
    max_pages: int = 50,
) -> DiscoveryResult:
    """Bulk OPEN-markets scan — DEPRECATED for primary discovery.

    Kalshi's get_markets endpoint without filters returns the KXMVE*
    sports parlay containers in the first many pages and pagination
    typically terminates inside that noise (empirically observed
    2026-05-10). Use `discover_by_categories` instead.

    Kept for completeness / audit comparison. Same return shape.
    """
    from pykalshi import MarketStatus

    # Step 1: page OPEN markets, dropping non-tradeable ones eagerly.
    all_market_dicts: list[dict[str, Any]] = []
    cursor: str | None = None
    pages = 0
    n_filtered = 0
    while pages < max_pages:
        ms = await client.get_markets(
            status=MarketStatus.OPEN,
            limit=page_size,
            cursor=cursor,
        )
        page_markets = list(ms)
        if not page_markets:
            break
        for m in page_markets:
            d = _market_to_dict(m)
            if not is_tradeable_market(d):
                n_filtered += 1
                continue
            all_market_dicts.append(d)
        cursor = getattr(ms, "cursor", None) or getattr(ms, "next_cursor", None)
        pages += 1
        if not cursor:
            break

    log.info(
        "kalshi_market_map: paged %d markets across %d pages; %d filtered as collection",
        len(all_market_dicts) + n_filtered, pages, n_filtered,
    )

    return await _build_discovery_result(client, all_market_dicts, n_filtered)


async def _build_discovery_result(
    client,
    market_dicts: list[dict[str, Any]],
    n_filtered: int,
    *,
    inter_call_delay_sec: float = 0.15,
) -> DiscoveryResult:
    """Group market dicts by event, fetch event metadata, classify.

    Shared by both discovery paths. Per-event get_event() call budget
    is O(n_unique_events). Failures degrade to OTHER classification.
    Per-call sleep matches the discovery rate limit guard.
    """
    import asyncio
    by_event: dict[str, list[MarketRecord]] = {}
    for d in market_dicts:
        rec = _build_market_record(d)
        if not rec.event_ticker:
            continue
        by_event.setdefault(rec.event_ticker, []).append(rec)

    events: list[EventRecord] = []
    by_type: dict[str, int] = {t.value: 0 for t in EventType}

    for event_ticker, mkts in by_event.items():
        try:
            evt = await client.get_event(event_ticker)
        except Exception as e:
            log.debug("kalshi_market_map: get_event(%s) failed: %s", event_ticker, e)
            evt = None
        await asyncio.sleep(inter_call_delay_sec)

        mut_excl = bool(getattr(evt, "mutually_exclusive", False)) if evt else False
        et = classify_event(event_ticker, mut_excl, mkts)
        rec = EventRecord(
            event_ticker=event_ticker,
            series_ticker=str(getattr(evt, "series_ticker", "") or ""),
            title=str(getattr(evt, "title", "") or ""),
            sub_title=str(getattr(evt, "sub_title", "") or ""),
            category=str(getattr(evt, "category", "") or ""),
            mutually_exclusive=mut_excl,
            markets=mkts,
            event_type=et,
        )
        events.append(rec)
        by_type[et.value] += 1

    return DiscoveryResult(
        events=events,
        n_markets_total=len(market_dicts),
        n_markets_filtered_collection=n_filtered,
        n_events_total=len(events),
        by_type=by_type,
    )


__all__ = [
    "EventType", "MarketRecord", "EventRecord", "DiscoveryResult",
    "is_tradeable_market", "get_market_prices",
    "classify_event",
    "discover_by_categories", "discover_open_markets",
    "DEFAULT_DISCOVERY_CATEGORIES",
]
