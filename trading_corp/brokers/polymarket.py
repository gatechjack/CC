"""Polymarket broker — Phase 1 read-only + Phase 2a market discovery.

Subclasses `ReadOnlyBroker`: there is no `place_order` method on this class.
A code path that tries to place orders against a Polymarket adapter is a
static type error, not a runtime exception. Live order placement is Phase 3
work and will land as a separate `PolymarketLiveBroker(Broker)` (or similar)
when the Backtester verdict + Board memo greenlight it.

Architecture (Phase 1):

    USDC balance       <-- Polygon RPC eth_call (USDC.balanceOf) -- direct from tc-prod-vm
    Positions          <-- GET data-api.polymarket.com/positions?user=<funder>
    Last trade price   <-- GET clob.polymarket.com last-prices for a token_id

No EU egress proxy. The 2026-05-09 smoke test (runbooks/eu_proxy_smoke_test.md)
verified Polymarket's read APIs serve tc-prod-vm's US-east IP without
geo-block. If Phase 3 trade placement triggers write-path geo-checks, the
proxy scope can be revived from the existing runbook.

Wallet pattern: Externally Owned Account (EOA), `signature_type=EOA` in
py-clob-client terms. The funder address IS the signer EOA — no Polymarket
proxy/SAFE in this configuration. Single address holds USDC + signs orders
(Phase 3+). Smaller mental footprint than the proxy pattern; gas trade-off
is rounding error at $1-notional shakedown sizing.

Stub mode: if any of (funder_address, polygon_rpc_url) is missing, the
broker initializes as a STUB returning $0 / no positions. This matches
the BitUnix bring-up pattern — the dashboard tile renders "online · $0"
rather than "not_wired", and the adapter goes live the moment the KV
secrets land. The private_key constructor arg is accepted but unused in
Phase 1 (signing only matters at Phase 3); accepting it now keeps the
constructor signature stable across phases.

Field-mapping caveat: data-api.polymarket.com/positions returned an empty
array `[]` against a dummy address in the smoke test, so we don't have a
verified non-empty response shape yet. The position-mapping code below
uses `.get()` with sensible fallbacks and documents each guess. First
non-empty response from a funded wallet should be eyeballed against the
mapping and corrected if needed.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta

import httpx

from trading_corp.brokers.base import AccountSnapshot, ReadOnlyBroker
from trading_corp.persistence.models import Position

log = logging.getLogger(__name__)


# USDC.e (bridged) is the Polymarket CLOB collateral token — confirmed
# 2026-05-29 via on-chain getCollateral() on both exchange contracts
# (std 0x4bFb…982E and negRisk 0xC5d5…f80a both return 0x2791…84174).
# An earlier comment here claimed Polymarket migrated to native USDC
# (0x3c499c…); that is WRONG for the CLOB — the exchanges still settle in
# USDC.e. The snapshot must read the collateral token so equity reflects
# *tradeable* balance, not an untradeable native-USDC holding. Both tokens
# use 6 decimals. See reports/2026-05-29_polymarket_live_prep_groupB_spike.md.
_USDC_CONTRACT = "0x2791Bca1f2de4661eD88A30C99A7a9449Aa84174"
_USDC_DECIMALS = 6

# Public read APIs. All three confirmed reachable from US Azure VMs in the
# 2026-05-09 smoke test — no EU proxy needed for reads.
_GAMMA_API = "https://gamma-api.polymarket.com"
_CLOB_API = "https://clob.polymarket.com"
_DATA_API = "https://data-api.polymarket.com"

# Function selector for ERC-20 `balanceOf(address)` — first 4 bytes of
# keccak256("balanceOf(address)"). Hardcoded to avoid pulling in eth-utils
# for one constant.
_BALANCE_OF_SELECTOR = "0x70a08231"

_DEFAULT_TIMEOUT_S = 15.0

# Phase 2a defensive HTTP posture. Polymarket's CLOB documents ~10-30
# req/sec for public reads; gamma-api is similar. We keep our outbound
# concurrency well below the published limit so the scanner doesn't
# saturate on a single 30s tick. 429s are rare in practice but the
# backoff is the right behavior if they ever fire.
_HTTP_CONCURRENCY_LIMIT = 6
_HTTP_BACKOFF_BASE_S = 1.0
_HTTP_BACKOFF_MAX_S = 30.0
_HTTP_MAX_RETRIES = 4

# ── Category mapping (Phase 2a Step 5) ───────────────────────────────
# Polymarket's gamma-api `/markets` response doesn't carry a top-level
# `category` field; classification info is split across:
#   - market.events[0].seriesSlug  (e.g. "mlb", "atp", "eurovision-2026")
#   - market.sportsMarketType      (e.g. "moneyline" — only on sports)
#   - market.slug                  (slug-prefix patterns)
#
# We classify each market into TWO levels:
#   - top category: sports / politics / geopolitics / finance / crypto /
#                   entertainment / celebrity / health / other
#   - series sub-tag: the seriesSlug or event slug (specific to the market)
#
# Empirically tested 2026-05-10 — keyword sets below classify 100% of
# 68 currently-passing markets. The `other` bucket is the catch-all if
# a new theme emerges and isn't covered yet; expand the keyword sets
# rather than letting drift accumulate. See tests/test_polymarket_arbitrage.py.

_SPORTS_SERIES = frozenset({
    "mlb", "nba", "nfl", "nhl", "ncaaf", "ncaab",
    "atp", "wta", "tennis",
    "epl", "premier-league", "champions-league", "la-liga", "serie-a",
    "bundesliga", "ligue-1", "mls", "soccer", "world-cup", "uefa",
    "ufc", "boxing", "f1", "nascar", "indy-car", "moto-gp",
    "pga", "golf", "olympics",
})
_POLITICS_KEYWORDS = (
    "election", "primary", "senate", "congress", "house-",
    "trump", "biden", "harris", "vance", "obama",
    "cabinet", "supreme-court", "speaker", "presidential", "vice-president",
)
_FINANCE_KEYWORDS = (
    "fed-", "fomc", "rate-cut", "rate-hike", "cpi-", "gdp-",
    "jobs-report", "unemployment", "ppi-", "jolts",
    "sp-500-", "spx-", "nasdaq-", "s-and-p",
)
_CRYPTO_KEYWORDS = (
    "btc", "bitcoin", "eth", "ethereum", "sol", "solana", "doge",
    "crypto", "stablecoin", "etf-", "fartcoin", "nft",
)
_GEOPOLITICS_KEYWORDS = (
    "iran", "ukraine", "russia", "china", "taiwan", "israel",
    "gaza", "houthi", "north-korea", "putin", "zelensky",
    "ceasefire", "peace-deal", "treaty", "war-",
    "nato", "hormuz", "blockade", "invasion",
)
_ENTERTAINMENT_KEYWORDS = (
    "eurovision", "oscars", "grammys", "emmys",
    "movie-", "album-", "song-",
    "rihanna", "gta-vi", "release", "series-finale",
)
_CELEBRITY_KEYWORDS = (
    "elon", "musk", "kanye", "kardashian", "taylor-swift", "tweets",
)
_HEALTH_KEYWORDS = (
    "hantavirus", "disease", "virus", "pandemic", "outbreak",
    "epidemic", "vaccine", "measles", "ebola", "monkeypox", "covid",
)


def _classify_market(market: dict) -> tuple[str, str]:
    """Return (top_category, series_subtag) for a gamma-api market dict.

    See module-level constants for the keyword sets. `top_category` is
    one of the documented buckets; `series_subtag` is `events[0].seriesSlug`
    when present, else the event slug, else the first segment of the
    market slug, else empty string.
    """
    events = market.get("events") or []
    series = ""
    if events and isinstance(events[0], dict):
        evt = events[0]
        series = (evt.get("seriesSlug") or evt.get("slug") or "").lower()
    sport_type = market.get("sportsMarketType")
    slug = (market.get("slug") or "").lower()

    # Sports first — strongest signal (series match OR sportsMarketType set).
    if series in _SPORTS_SERIES or sport_type:
        return "sports", series

    # Geopolitics before politics — overlap potential ("Trump announces blockade").
    if any(kw in slug for kw in _GEOPOLITICS_KEYWORDS):
        return "geopolitics", series or slug.split("-")[0]
    if any(kw in slug for kw in _POLITICS_KEYWORDS):
        return "politics", series or slug.split("-")[0]
    if any(kw in slug for kw in _FINANCE_KEYWORDS):
        return "finance", series or slug.split("-")[0]
    if any(kw in slug for kw in _CRYPTO_KEYWORDS):
        return "crypto", series or slug.split("-")[0]
    if any(kw in slug for kw in _CELEBRITY_KEYWORDS):
        return "celebrity", series or slug.split("-")[0]
    if any(kw in slug for kw in _HEALTH_KEYWORDS):
        return "health", series or slug.split("-")[0]
    if any(kw in slug for kw in _ENTERTAINMENT_KEYWORDS):
        return "entertainment", series or slug.split("-")[0]
    # Catch-all: surface SOMETHING in the series sub-tag even when the
    # category is unknown — slug's first segment is a useful breadcrumb.
    return "other", series or (slug.split("-")[0] if slug else "")


def _erc20_balanceof_calldata(address: str) -> str:
    """Build the eth_call `data` field for `USDC.balanceOf(address)`.

    Encoding: 4-byte selector + 32-byte address (left-padded with zeros).
    """
    addr_clean = address.lower().removeprefix("0x")
    if len(addr_clean) != 40:
        raise ValueError(f"expected 20-byte address, got {len(addr_clean)//2} bytes: {address!r}")
    return _BALANCE_OF_SELECTOR + ("0" * 24) + addr_clean


def _hex_uint_to_int(hex_str: str) -> int:
    """Parse `0x...` hex into int. Empty / `0x` returns 0."""
    if not hex_str or hex_str in ("0x", "0x0"):
        return 0
    return int(hex_str, 16)


def _to_float(v) -> float:
    """Coerce string-or-number to float, defaulting to 0.0 on garbage.

    Polymarket API responses sometimes return numeric fields as strings
    (volume24hr=\"13730.55\") and sometimes as numbers; this normalizes.
    """
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class PolymarketBroker(ReadOnlyBroker):
    """Read-only Polymarket adapter (Phase 1).

    Constructed with the funder address + Polygon RPC URL + signer key.
    The signer key is stored but unused in Phase 1; Phase 3 will pass it
    to the signing path when live order placement lands.
    """

    paper = False  # reads real on-chain + Polymarket data; paper-wrap upstream
    name = "polymarket"

    def __init__(
        self,
        private_key: str | None = None,
        funder_address: str | None = None,
        polygon_rpc_url: str | None = None,
    ) -> None:
        self._private_key = private_key  # Phase 3 signing path; Phase 1 unused
        self._funder = funder_address
        self._rpc_url = polygon_rpc_url
        # Stub mode if either of the read-essential fields is missing.
        # private_key absence does NOT trigger stub — Phase 1 doesn't need it.
        self._stub = not (funder_address and polygon_rpc_url)
        self._client: httpx.AsyncClient | None = None
        self._connected = False
        # Phase 2a — outbound concurrency cap. Bound the number of
        # in-flight HTTP requests so a Phase 2 scanner cycle that
        # touches K=10 markets in parallel doesn't burst above
        # Polymarket's published rate limits.
        self._http_sem = asyncio.Semaphore(_HTTP_CONCURRENCY_LIMIT)

    async def connect(self) -> None:
        if self._stub:
            self._connected = True
            log.info("PolymarketBroker connected as STUB (missing funder or RPC URL)")
            return

        # One async client for all HTTP — Polymarket REST + Polygon RPC POST.
        # Per-request `base_url` not used since we hit three different hosts;
        # full URLs at call-site instead.
        self._client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S)
        try:
            snap = await self.snapshot()
            log.info(
                "PolymarketBroker connected (funder=%s, equity=$%.2f, %d positions)",
                self._funder, snap.equity, len(snap.positions),
            )
        except Exception as e:
            # Same posture as Bitunix — surface but don't raise. Hydration
            # will retry; a transient network blip at boot shouldn't crash
            # the whole process.
            log.warning("PolymarketBroker connect-time snapshot failed: %s", e)
        self._connected = True

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    async def snapshot(self) -> AccountSnapshot:
        if self._stub or not self._client or not self._funder or not self._rpc_url:
            return AccountSnapshot(
                account="polymarket-stub",
                equity=0.0, buying_power=0.0, cash=0.0,
                positions=[],
            )

        # USDC balance via Polygon RPC eth_call. We don't need MATIC balance
        # for the snapshot — gas reserves are operational concern, not
        # tradeable equity. Could surface MATIC in `extra` later if useful.
        cash_usdc = await self._fetch_usdc_balance()

        # Open positions from Polymarket's data API.
        positions = await self._fetch_positions()

        # Equity = USDC cash + market value of open YES/NO positions.
        # Position market value comes from data-api's `currentValue` field
        # (or whatever the actual field name turns out to be — see caveat
        # in module docstring); fallback to qty*avg_price if unavailable.
        position_value = sum(
            float(p.extra.get("current_value") or (p.qty * p.avg_price))
            for p in positions
        )
        equity = cash_usdc + position_value

        return AccountSnapshot(
            account=f"polymarket-{self._funder[:10]}",  # short identifier for logs
            equity=equity,
            buying_power=cash_usdc,  # only cash can fund new positions
            cash=cash_usdc,
            positions=positions,
        )

    async def get_market_resolution(
        self, *, condition_id: str | None = None, slug: str | None = None,
    ) -> dict:
        """Look up resolution status for a market. Used by the
        Backtester to score paper trades against actual outcomes.

        Returns dict with shape:
            {
              "status": "resolved" | "pending" | "void" | "not_found",
              "yes_won": bool | None,         # only meaningful when resolved
              "outcome_prices": [str, str],   # raw from gamma-api
              "closed": bool,
              "end_date": str,                # ISO
            }

        Resolution decoding (from gamma-api conventions verified
        2026-05-10): for a resolved market `outcomePrices` becomes
        `["1","0"]` (YES won) or `["0","1"]` (NO won), and
        `umaResolutionStatus == "resolved"`. Markets still in flight
        keep fractional prices and `umaResolutionStatus` empty/missing.

        Lookup precedence: condition_id (most stable) > slug (URL-safe).
        """
        if self._stub or not self._client:
            return {"status": "not_found", "yes_won": None,
                    "outcome_prices": [], "closed": False, "end_date": ""}

        # Build the lookup key. condition_ids (plural) is the gamma-api
        # parameter name; condition_id is just the field name on our
        # ProposedOrder.extra. slug as fallback.
        base: dict[str, str] = {"limit": "1"}
        if condition_id:
            base["condition_ids"] = condition_id
        elif slug:
            base["slug"] = slug
        else:
            return {"status": "not_found", "yes_won": None,
                    "outcome_prices": [], "closed": False, "end_date": ""}

        # Two-pass lookup: gamma-api filters out closed markets by default,
        # so a single `?slug=X` query won't find a RESOLVED market. We
        # check open markets first (cheaper if it's still in flight),
        # then closed if not found.
        m: dict | None = None
        for closed_flag in ("false", "true"):
            params = {**base, "closed": closed_flag}
            try:
                data = await self._http_get_json(f"{_GAMMA_API}/markets", params=params)
            except Exception as e:
                log.warning("PolymarketBroker.get_market_resolution failed: %s", e)
                continue
            if isinstance(data, list) and data:
                m = data[0]
                break

        if m is None:
            return {"status": "not_found", "yes_won": None,
                    "outcome_prices": [], "closed": False, "end_date": ""}
        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            try:
                import json as _json
                prices = _json.loads(prices)
            except (json.JSONDecodeError, ValueError):
                prices = []
        if not isinstance(prices, list):
            prices = []

        uma_status = (m.get("umaResolutionStatus") or "").lower()
        closed = bool(m.get("closed", False))
        end_date = str(m.get("endDate") or m.get("end_date") or "")

        # Resolution semantics (multi-leg-aware as of 2026-05-14):
        #   exactly-one-1 in prices AND closed AND uma=resolved → resolved
        #     (binary YES/NO and N-leg both fit; caller uses outcome_index
        #      to look up which entry of outcome_prices is "1")
        #   fractional / no-clear-winner → void
        #   closed=false / uma!=resolved → pending
        # `yes_won` is preserved for binary-only callers (None for multi-leg).
        status = "pending"
        yes_won: bool | None = None
        if uma_status == "resolved" and closed and len(prices) >= 2:
            try:
                prices_f = [float(x) for x in prices]
            except (TypeError, ValueError):
                prices_f = []
            n_winners = sum(1 for p in prices_f if p == 1.0)
            n_losers = sum(1 for p in prices_f if p == 0.0)
            if prices_f and n_winners == 1 and n_winners + n_losers == len(prices_f):
                status = "resolved"
                # Binary backwards-compat: only set yes_won for 2-outcome
                # markets. Multi-leg callers must use outcome_index.
                if len(prices_f) == 2:
                    yes_won = prices_f[0] == 1.0
            else:
                # Fractional / partial resolution — treat as void.
                status = "void"

        return {
            "status": status,
            "yes_won": yes_won,
            "outcome_prices": [str(x) for x in prices],
            "closed": closed,
            "end_date": end_date,
        }

    async def quote(self, symbol: str) -> float:
        """Return the last trade price for a Polymarket outcome.

        `symbol` is in `{market_slug}:{outcome}` form, e.g.
        `trump-2024-elected:yes`. Phase 1's only caller is
        `data_exec.dry_run` (which uses `quote()` to synthesize fill
        prices); the strategy code's `quote` calls land in Phase 2.

        Returns 0.0 on any error or stub mode — the caller is expected
        to treat 0.0 as "unknown" and degrade gracefully.
        """
        if self._stub or not self._client:
            return 0.0
        try:
            slug, _, outcome = symbol.partition(":")
            if not slug:
                return 0.0
            outcome = (outcome or "yes").lower()

            # Step 1: get the market's clob token IDs from gamma-api.
            r = await self._client.get(f"{_GAMMA_API}/markets", params={"slug": slug})
            r.raise_for_status()
            markets = r.json() or []
            if not markets:
                return 0.0
            market = markets[0]
            # `clobTokenIds` is a JSON-encoded string in the response;
            # `outcomes` is the parallel "Yes"/"No" labels (or similar).
            # Defensive parse — first non-empty response from a real market
            # should verify these field names.
            import json
            token_ids = market.get("clobTokenIds")
            if isinstance(token_ids, str):
                try:
                    token_ids = json.loads(token_ids)
                except (TypeError, ValueError):
                    return 0.0
            outcomes = market.get("outcomes")
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except (TypeError, ValueError):
                    outcomes = []
            if not (isinstance(token_ids, list) and isinstance(outcomes, list)):
                return 0.0

            # Match outcome label case-insensitively.
            token_id = None
            for tid, label in zip(token_ids, outcomes):
                if str(label).lower() == outcome:
                    token_id = tid
                    break
            if token_id is None:
                return 0.0

            # Step 2: last-trade price for that token from CLOB.
            r2 = await self._client.get(
                f"{_CLOB_API}/last-trade-price",
                params={"token_id": token_id},
            )
            r2.raise_for_status()
            data = r2.json() or {}
            return float(data.get("price") or 0.0)
        except Exception as e:
            log.debug("PolymarketBroker.quote(%r) failed: %s", symbol, e)
            return 0.0

    # ── Phase 2a — market discovery for the arbitrage scanner ─────────

    async def list_markets(
        self,
        *,
        min_volume_24h_usd: float = 0.0,
        max_spread_cents: float = 100.0,
        min_hours_to_resolution: float = 0.0,
        max_days_to_resolution: float | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Return open / accepting-orders Polymarket markets matching the
        deterministic pre-filter. Used by the polymarket_arbitrage scanner
        to narrow the universe before any LLM probability calls.

        Filters applied (all in Python on top of gamma-api results;
        gamma-api's own query-param filtering is inconsistently documented,
        so we do it client-side for predictability):

          - active=true, closed=false, archived=false, accepting_orders=true
          - liquidity (24h volume proxy) >= min_volume_24h_usd
          - bid-ask spread <= max_spread_cents
          - hours-to-resolution within [min_hours, max_days × 24]
          - implied probability bounds enforced by the strategy AFTER
            this call (needs token-id-level last-trade lookup)

        Returns a list of market dicts with the gamma-api fields plus
        derived helpers (`hours_to_resolution`, `category` if extractable).
        Returns [] in stub mode or on any HTTP failure.
        """
        if self._stub or not self._client:
            return []

        # Server-side query — empirically tuned 2026-05-10 against live
        # gamma-api. The default page sort returns long-tail markets first
        # (200 results, 0 within a 7d horizon). With `order=volume24hr` +
        # `ascending=false` and an `end_date_min/max` server-side window,
        # we get ~68 markets per page that already pass the Phase 2 caps
        # (volume >= $50K, horizon 1-7d). The client-side filter below
        # then enforces the rest (spread, accepting_orders, etc.) and
        # remains the source-of-truth for cap semantics.
        now = datetime.now(timezone.utc)
        params = {
            "closed": "false",
            "active": "true",
            "archived": "false",
            "order": "volume24hr",
            "ascending": "false",
            "limit": str(int(limit)),
        }
        if min_hours_to_resolution > 0:
            params["end_date_min"] = (
                now + timedelta(hours=float(min_hours_to_resolution))
            ).isoformat().replace("+00:00", "Z")
        if max_days_to_resolution is not None:
            params["end_date_max"] = (
                now + timedelta(days=float(max_days_to_resolution))
            ).isoformat().replace("+00:00", "Z")
        try:
            data = await self._http_get_json(f"{_GAMMA_API}/markets", params=params)
        except Exception as e:
            log.warning("PolymarketBroker.list_markets: gamma fetch failed: %s", e)
            return []

        if not isinstance(data, list):
            return []

        now = datetime.now(timezone.utc)
        max_horizon = (
            now + timedelta(days=float(max_days_to_resolution))
            if max_days_to_resolution is not None else None
        )
        min_horizon = now + timedelta(hours=float(min_hours_to_resolution))

        out: list[dict] = []
        for m in data:
            if not isinstance(m, dict):
                continue
            # Gamma-api doesn't always expose `accepting_orders`; if absent,
            # treat as accepting. Strategy can re-check via CLOB if needed.
            if m.get("acceptingOrders") is False or m.get("accepting_orders") is False:
                continue
            # Volume / liquidity. Gamma-api's `liquidity` and `volume24hr`
            # are both surfaced — prefer 24hr where available.
            vol = _to_float(m.get("volume24hr") or m.get("volume_24hr") or m.get("liquidity"))
            if vol < float(min_volume_24h_usd):
                continue
            # Bid-ask spread. Gamma-api returns `spread` as a fraction
            # (0.01 = 1 cent on a $0-1 market). Some markets omit it
            # (use the CLOB orderbook for true spread). We keep markets
            # with missing spread and let the strategy verify via CLOB.
            spread = m.get("spread")
            if spread is not None:
                # Normalize: if returned as 0-1 fraction, convert to cents.
                spread_cents = _to_float(spread) * 100.0
                if spread_cents > float(max_spread_cents):
                    continue
            # Time-to-resolution. End date may be `endDate` (gamma).
            end_iso = m.get("endDate") or m.get("end_date")
            try:
                end_dt = datetime.fromisoformat(str(end_iso).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                # No usable end date — exclude rather than include unbounded markets.
                continue
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if end_dt < min_horizon:
                continue
            if max_horizon is not None and end_dt > max_horizon:
                continue
            hours_to_res = (end_dt - now).total_seconds() / 3600.0
            # Two-layer category classification (Phase 2a Step 5 tuning).
            # `category` = top bucket (sports / politics / crypto / ...) for
            # dashboard activity-rail filtering; `series` = sub-tag (mlb,
            # atp, eurovision-2026) for finer breakdown. See
            # _classify_market for keyword sets + 100%-current-coverage
            # empirical test.
            top_cat, series_tag = _classify_market(m)
            out.append({
                **m,
                "hours_to_resolution": hours_to_res,
                "category": top_cat,
                "series": series_tag,
                "_volume_24h_used": vol,
            })

        return out

    async def _http_get_json(
        self,
        url: str,
        params: dict[str, str] | None = None,
    ) -> dict | list:
        """GET with concurrency-cap + 429 backoff + jitter. Returns parsed JSON.

        Reused by list_markets and quote(); could replace the existing inline
        client.get calls in those paths for consistency, but those work today
        and we don't refactor without reason.
        """
        if not self._client:
            raise RuntimeError("client not connected")
        attempt = 0
        async with self._http_sem:
            while True:
                attempt += 1
                try:
                    r = await self._client.get(url, params=params)
                    if r.status_code == 429 and attempt <= _HTTP_MAX_RETRIES:
                        # Exponential backoff with jitter. Honor Retry-After
                        # if the server returns it; otherwise compute.
                        retry_after = r.headers.get("Retry-After")
                        if retry_after:
                            try:
                                delay = float(retry_after)
                            except ValueError:
                                delay = _HTTP_BACKOFF_BASE_S * (2 ** (attempt - 1))
                        else:
                            delay = _HTTP_BACKOFF_BASE_S * (2 ** (attempt - 1))
                        delay = min(delay, _HTTP_BACKOFF_MAX_S)
                        # +/- 25% jitter to avoid thundering-herd retries
                        delay *= (0.75 + random.random() * 0.5)
                        log.info(
                            "PolymarketBroker: 429 from %s; backoff %.1fs (attempt %d/%d)",
                            url, delay, attempt, _HTTP_MAX_RETRIES,
                        )
                        await asyncio.sleep(delay)
                        continue
                    r.raise_for_status()
                    return r.json()
                except (httpx.ConnectError, httpx.ReadTimeout) as e:
                    if attempt <= _HTTP_MAX_RETRIES:
                        delay = _HTTP_BACKOFF_BASE_S * (2 ** (attempt - 1))
                        delay = min(delay, _HTTP_BACKOFF_MAX_S)
                        delay *= (0.75 + random.random() * 0.5)
                        log.info(
                            "PolymarketBroker: %s on %s; backoff %.1fs (attempt %d/%d)",
                            type(e).__name__, url, delay, attempt, _HTTP_MAX_RETRIES,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise

    # ── helpers ────────────────────────────────────────────────────────

    async def _fetch_usdc_balance(self) -> float:
        """Polygon RPC eth_call for USDC.balanceOf(funder). Returns dollars."""
        if not self._client or not self._rpc_url or not self._funder:
            return 0.0
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [
                    {
                        "to": _USDC_CONTRACT,
                        "data": _erc20_balanceof_calldata(self._funder),
                    },
                    "latest",
                ],
                "id": 1,
            }
            r = await self._client.post(
                self._rpc_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            body = r.json() or {}
            if "error" in body:
                log.warning("Polygon RPC error fetching USDC: %s", body["error"])
                return 0.0
            raw = body.get("result", "0x0")
            return _hex_uint_to_int(raw) / (10 ** _USDC_DECIMALS)
        except Exception as e:
            log.warning("PolymarketBroker._fetch_usdc_balance failed: %s", e)
            return 0.0

    async def _fetch_positions(self) -> list[Position]:
        """Pull open positions from Polymarket's data API.

        Field mapping is best-effort against an unverified non-empty
        response shape. Once a funded wallet returns real data, eyeball
        the response and tighten the field names. Defensive .get() calls
        ensure unknown shape doesn't crash the snapshot path — at worst,
        positions render with degraded data.
        """
        if not self._client or not self._funder:
            return []
        try:
            r = await self._client.get(
                f"{_DATA_API}/positions",
                params={"user": self._funder},
            )
            r.raise_for_status()
            rows = r.json() or []
        except Exception as e:
            log.warning("PolymarketBroker._fetch_positions failed: %s", e)
            return []

        positions: list[Position] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            qty = float(row.get("size") or row.get("qty") or 0.0)
            if qty == 0:
                continue
            avg_price = float(row.get("avgPrice") or row.get("avg_price") or 0.0)
            outcome_label = str(
                row.get("outcome") or row.get("outcomeLabel") or "yes"
            ).lower()
            slug = (
                row.get("slug")
                or row.get("marketSlug")
                or row.get("eventSlug")
                or "unknown"
            )
            symbol = f"{slug}:{outcome_label}"
            opened_ts = (
                row.get("createdAt")
                or row.get("created_at")
                or datetime.now(timezone.utc).isoformat(timespec="seconds")
            )
            positions.append(Position(
                account=f"polymarket-{(self._funder or '')[:10]}",
                symbol=symbol,
                qty=qty,
                avg_price=avg_price,
                opened_ts=str(opened_ts),
                extra={
                    "condition_id": row.get("conditionId") or row.get("condition_id"),
                    "market_id": row.get("marketId") or row.get("market_id"),
                    "outcome_index": row.get("outcomeIndex") or row.get("outcome_index"),
                    "title": row.get("title") or row.get("question"),
                    "current_value": row.get("currentValue") or row.get("current_value"),
                    "current_price": row.get("currentPrice") or row.get("current_price"),
                    "realized_pnl": row.get("realizedPnl") or row.get("realized_pnl"),
                    "unrealized_pnl": row.get("unrealizedPnl") or row.get("unrealized_pnl"),
                },
            ))
        return positions
