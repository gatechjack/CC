"""BitUnix Futures broker — Phase 4 live order path (Stage-1 broker-write).

`snapshot()` + `quote()` + `get_funding_rate()` read the live API (Phase 1).
This module adds the Stage-1 **write** surface: `place_order` (market entry +
reduce-only exit), `cancel_order`, fill observation (poll order-detail +
trade-history), and the kill-switch primitives (`cancel_all_orders`,
`flash_close_position`, `close_all_position`, `flatten`). REST bodies/endpoints
are reimplemented from the official BitUnix futures spec; the place/cancel/
flatten *flow* is adapted from Lumiwealth/lumibot's `BitUnixClient` (MIT, with
attribution) per runbooks/2026-05-29_bitunix_live_reuse_audit.md.

In PAPER mode (default) `trading_corp.main` wraps this broker in
`PaperExecutionBroker`, so snapshots return real BitUnix data while orders
simulate via `PaperBroker`. `place_order` is therefore only ever reached in
LIVE mode — but note `connect()` still runs on this real broker in paper mode
(for reads), so this module performs NO account-state writes at connect; the
one-way position mode is set+verified lazily on the first live entry instead.

Two BitUnix-specific invariants this module is built around:

  * **Sign-what-you-send.** The POST body is serialized to compact JSON
    exactly once; that exact string is both signed and sent (raw content,
    never httpx `json=`). Re-serialization adds whitespace and breaks the
    signature (error 10007) — the #1 BitUnix integration failure.
  * **clientId idempotency.** clientId = ``tc-<order.id>`` is deterministic,
    so a retry that returns 30042 (CLIENT_ID_DUPLICATE) provably means the
    order already landed — safe to treat as success.

Position mode is ONE_WAY, account-wide (operator decision 2026-05-29; see the
live-readiness audit Decision log). Exits use ``reduceOnly: true`` — no
positionId/tradeSide bookkeeping.

VERIFY-ON-LIVE (flagged, not yet exercised against a real order): the official
docs mark ``tradeSide`` as hedge-mode-only and ``effect`` (TIF) as required
for LIMIT orders only, yet the operator-confirmed one-way open payload sends
both. If the first live entry is rejected with a param error, drop
``tradeSide``/``effect`` from the open body (see `_build_order_body`).

Auth scheme (per https://www.bitunix.com/api-docs/futures/common/sign.html):

    digest_hex = SHA256(nonce + timestamp_ms + api_key + sortedQuery + body)
    sign_hex   = SHA256(digest_hex + api_secret)

Headers required on every private request:
    api-key:   the API key
    sign:      sign_hex from above (lowercase hex)
    nonce:     UUID4 hex (no hyphens)
    timestamp: current time in ms

Notes on signing:
    - Query params are sorted ascending by key, then concatenated as
      "k1v1k2v2..." with no separators.
    - Body is the raw JSON string with all whitespace stripped (or "" for GETs).
    - No passphrase. The local .env's `BITUNIX_FUTURES_PASSPHRASE` is unused —
      kept blank in the .env section 5c comment as a placeholder.

Endpoints used:
    GET  /api/v1/futures/account?marginCoin=<coin>        (private; per coin)
    GET  /api/v1/futures/position/get_pending_positions    (private)
    GET  /api/v1/futures/market/tickers?symbol=...         (public, no auth)

Margin coins:
    BitUnix Futures supports multiple margin coins (USDT, USDC, BTC, ETH...).
    Account balance must be queried per coin. Phase 1 sums across stablecoins
    (USDT + USDC) and treats them as 1:1 USD. Crypto-margined balances
    (BTC/ETH) require quote conversion and are deferred.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import logging
import random
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import json

import httpx

from trading_corp.brokers.base import AccountSnapshot, Broker
from trading_corp.brokers.bitunix_exceptions import (
    BitunixMakerEntryUnfilled,
    BitunixPositionModeMismatch,
    BitunixStaleSnapshot,
    BitunixStuckOrderCancelFailed,
    BitunixStuckOrderCancelled,
    BitunixUntrackedTpslOrder,
)
from trading_corp.brokers.bitunix_symbols import to_wire_format
from trading_corp.persistence import db
from trading_corp.persistence.models import FillEvent, OpenPosition, Position, ProposedOrder
from trading_corp.utils.time import iso, now_utc

if TYPE_CHECKING:
    from trading_corp.agents.logger import LoggerAgent

# Re-exported here so existing callers can still write
# `from trading_corp.brokers.bitunix import BitunixPositionModeMismatch`.
# The canonical class objects live in `bitunix_exceptions.py` (see that
# module's docstring for the cross-branch class-identity rationale).
__all__ = [
    "BitunixPositionModeMismatch",
    "BitunixStaleSnapshot",
    "BitunixStuckOrderCancelled",
    "BitunixStuckOrderCancelFailed",
]

log = logging.getLogger(__name__)

_BASE_URL = "https://fapi.bitunix.com"
_DEFAULT_TIMEOUT_S = 15.0

# Margin coins to query for total futures equity. Stablecoins only —
# treated as 1:1 USD without conversion. BTC/ETH-margined balances exist
# on BitUnix but need quote conversion to USD; defer until Phase 2+.
_STABLE_MARGIN_COINS = ("USDT", "USDC")

# ── Phase 4 write-path constants ────────────────────────────────────────────
_ONE_WAY = "ONE_WAY"
_DEFAULT_MARGIN_COIN = "USDT"
_CLIENT_ID_PREFIX = "tc-"

# Order statuses that mean "no longer working" — stop polling for a fill.
# BitUnix status enum: INIT, NEW, PART_FILLED, CANCELED, FILLED.
_TERMINAL_STATUSES = {"FILLED", "CANCELED"}

# Fill-observation poll defaults (overridable per-instance for tests).
_FILL_MAX_POLLS = 8
_FILL_POLL_INTERVAL_S = 0.4

# ── BitUnix position `side` → signed qty (P1 reconciler fix, 2026-06-14) ──────
# One-way-mode positions mirror the OPEN order side. Orders are placed with
# side="BUY"/"SELL" (`_build_order_body`), and the FIRST live fill (2026-06-14)
# returned a SELL-opened short whose position `side` was NOT "SHORT": under the
# old `if side == "SHORT"` check the short read back as POSITIVE qty, so the
# position-state reconciler saw it as a "buy" it could not match → false
# `position_state_divergence_detected` every ~60s → `_halt_new_orders` latched →
# new live entries blocked. (reports/2026-06-14_bitunix_first_fill_closeout.md.)
#
# Grounded from captured data: orders use BUY/SELL; the live short read back
# positive ⇒ the label is NOT "SHORT" (the strong inference is "SELL"). We negate
# qty for SELL/SHORT and keep BUY/LONG positive, case-insensitively, covering
# both the order-side convention and the legacy LONG/SHORT assumption. An
# UNRECOGNIZED non-empty label is logged LOUDLY and left positive (fail-loud,
# never silently mis-signed) so a third convention surfaces instead of silently
# re-creating a reconciler divergence.
# NB: the exact short label ("SELL") is a strong inference, not a captured
# string — confirm against the live BitUnix position payload at deploy.
_SHORT_POSITION_SIDE_LABELS = frozenset({"SELL", "SHORT"})
_LONG_POSITION_SIDE_LABELS = frozenset({"BUY", "LONG"})


def _signed_position_qty(side: str | None, qty: float) -> float:
    """Sign a BitUnix position qty by its `side` label: SHORT/SELL → negative,
    LONG/BUY → positive. `abs()` makes it idempotent regardless of the raw sign.
    An unrecognized non-empty label warns and is treated as LONG (positive) —
    fail-loud, never silently mis-signed."""
    label = (side or "").strip().upper()
    if label in _SHORT_POSITION_SIDE_LABELS:
        return -abs(qty)
    if label in _LONG_POSITION_SIDE_LABELS:
        return abs(qty)
    log.warning(
        "BitUnix position with unrecognized side label %r (qty=%s); treating as "
        "LONG (positive). Confirm the BitUnix position-side enum and extend "
        "_SHORT_POSITION_SIDE_LABELS/_LONG_POSITION_SIDE_LABELS if needed.",
        side, qty,
    )
    return abs(qty)

# Default snapshot-staleness threshold (seconds). Overridable via
# `bitunix_futures.snapshot_staleness_threshold_seconds` in
# config/strategies.yaml. 60s = 2× the strategy's per-bar cadence on the
# 1-min bar; a single missed snapshot is fine, two-in-a-row is the halt
# signal.
_DEFAULT_SNAPSHOT_STALENESS_S = 60.0

# BitUnix business-error taxonomy. The envelope `code` is non-zero on a
# business error even when HTTP is 200. Codes are facts from the official
# error table (runbooks/2026-05-29_bitunix_live_reuse_audit.md §8) —
# reimplemented here as a lookup.
_ERROR_CODES: dict[int, tuple[str, str]] = {
    10004: ("IP_NOT_WHITELISTED", "API key IP whitelist rejection — whitelist the prod VM IP"),
    10005: ("RATE_LIMIT", "request rate limit exceeded — back off"),
    10006: ("RATE_LIMIT", "request rate limit exceeded — back off"),
    10007: ("SIGN_ERROR", "signature error — body re-serialized after signing, or clock drift"),
    20003: ("INSUFFICIENT_BALANCE", "insufficient balance for the order"),
    20006: ("LEVERAGE_LOCKED", "cannot change leverage/mode while orders/positions are open"),
    30001: ("WOULD_LIQUIDATE", "order would immediately liquidate"),
    30016: ("QTY_BELOW_MIN", "quantity below the symbol minimum"),
    30017: ("QTY_BELOW_MIN", "quantity below the symbol minimum"),
    30018: ("REDUCE_ONLY_VIOLATION", "reduce-only rule violation"),
    30019: ("REDUCE_ONLY_VIOLATION", "reduce-only rule violation"),
    30024: ("SL_BEYOND_LIQ", "stop-loss set beyond liquidation price"),
    30025: ("SL_BEYOND_LIQ", "stop-loss set beyond liquidation price"),
    30038: ("TPSL_EXCEEDS_POSITION", "TP/SL amount exceeds position size"),
    30042: ("CLIENT_ID_DUPLICATE", "clientId already used — order already accepted; safe to treat as success"),
}

# Rate-limit codes the retry layer (in `_request`) backs off on.
_RETRYABLE_CODES = {10005, 10006}
# Codes meaning "your write already landed" — safe to treat as success because
# clientId is a deterministic idempotency key.
_IDEMPOTENT_OK_CODES = {30042}

# ── Account-snapshot poll-cache TTL (10006 rate-limit mitigation) ───────────
# Default seconds a COMPLETE account snapshot is reused before refetching. The
# bitunix observer reads snapshot() per TradingView alert (tier-sizing AND the
# drawdown-breaker equity); clustered alerts across concurrent webhook handlers
# hammered /account (2 signed calls each) → ~12 calls in 2s → BitUnix 10006
# "request too frequently". 3 s collapses the burst while staying far below any
# tolerable staleness for a 15%-account-drawdown breaker. Per-broker
# overridable via the constructor.
_SNAPSHOT_CACHE_TTL_S = 3.0

# ── Phase 4 REST retry layer (gate (a) 2026-05-30) ──────────────────────────
# Resilience-against-transient-failure for the signed `_request` chokepoint.
# Read-side calls (snapshot/quote/get_funding_rate) deliberately bypass
# `_request` and therefore do not retry — for those paths the stale-snapshot
# halt (sub-item 2) is the safety primitive, not retry. See `_request`
# docstring for the design rationale.
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_S = 0.25
_RETRY_CAP_DELAY_S = 4.0
# Total backoff budget across all retries. With 4 total attempts × 15 s
# per-attempt timeout + ≤10 s of sleep across the 3 backoffs, worst-case
# `_request` wallclock is ≤70 s.
_RETRY_WALLCLOCK_CAP_S = 10.0
# HTTP statuses we treat as transient (after `raise_for_status()`).
_RETRY_HTTP_STATUSES = {408, 429, 502, 503, 504}

# ── B2 maker (POST_ONLY) entry execution defaults ───────────────────────────
# A maker entry rests as a POST_ONLY limit (guaranteed-maker — BitUnix rejects
# it if it would cross, so it can never accidentally take). These are FALLBACK
# defaults only: the observer stamps the real values from config onto
# `order.extra` when the operator flips the maker flag ON (DEFAULT OFF). Absent
# `extra["maker_entry"]`, place_order keeps its current taker/market path
# unchanged (behavior-preserving).
_MAKER_REST_TIMEOUT_S = 2.0      # short rest, to not worsen the PA-redeem late-entry drag
_MAKER_OFFSET_PCT = 0.0          # passive offset from the signal/reference price (0 = at ref)
_MAKER_FALLBACK_MODE = "cross_to_taker"   # 'cross_to_taker' | 'abandon'


def _is_retryable_httpx_exc(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRY_HTTP_STATUSES
    return False


def _retry_backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter. `attempt` is 1-indexed.

    attempt=1 → ~0.25-0.50 s, attempt=2 → ~0.50-1.00 s, attempt=3 → ~1.00-2.00 s.
    Capped at `_RETRY_CAP_DELAY_S` per attempt.
    """
    base = min(_RETRY_CAP_DELAY_S, _RETRY_BASE_DELAY_S * (2 ** (attempt - 1)))
    # Full-jitter pattern (AWS architecture blog): random in [base/2, base].
    # Tests pin `random.uniform` to make this deterministic.
    return random.uniform(base / 2.0, base)


def classify_error(code: int | None) -> tuple[str, str]:
    """Return (name, human-meaning) for a BitUnix error code."""
    try:
        return _ERROR_CODES[int(code)]
    except (TypeError, ValueError, KeyError):
        return ("UNKNOWN", "unrecognized BitUnix error code")


class BitunixAPIError(RuntimeError):
    """A non-zero `code` in a BitUnix REST envelope (a business error)."""

    def __init__(self, code, msg=None, *, path: str | None = None) -> None:
        self.code = code
        self.msg = msg
        self.path = path
        self.error_name, self.meaning = classify_error(code)
        self.retryable = code in _RETRYABLE_CODES
        super().__init__(
            f"BitUnix API error {code} ({self.error_name}) "
            f"on {path}: {msg!r} — {self.meaning}"
        )


def _amount_str(qty: float) -> str:
    """Format a base-coin amount / price as BitUnix expects: a plain decimal
    string with no scientific notation and no trailing zeros."""
    s = f"{abs(float(qty)):.8f}".rstrip("0").rstrip(".")
    return s or "0"


def _sign(
    api_key: str,
    api_secret: str,
    query: dict[str, str] | None = None,
    body: str = "",
) -> dict[str, str]:
    """Build the auth headers for a single request.

    Returns a dict with `api-key`, `sign`, `nonce`, `timestamp`. Caller
    merges into the request headers.
    """
    nonce = uuid.uuid4().hex
    timestamp = str(int(time.time() * 1000))

    if query:
        sorted_q = "".join(f"{k}{v}" for k, v in sorted(query.items()))
    else:
        sorted_q = ""

    digest_input = nonce + timestamp + api_key + sorted_q + body
    digest_hex = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    sign_hex = hashlib.sha256((digest_hex + api_secret).encode("utf-8")).hexdigest()

    return {
        "api-key": api_key,
        "sign": sign_hex,
        "nonce": nonce,
        "timestamp": timestamp,
    }


def _iso_from_ms(ms: int | str | None) -> str:
    if not ms:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_float(v) -> float:
    """Coerce a string-or-number field to float, defaulting to 0.0."""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _extract_tpsl_order_id(data) -> str:
    """Extract the venue order id from a `/tpsl/...` place response, tolerating
    BOTH shapes the venue has been observed to use.

    The BitUnix docs show `data` as a single dict `{"orderId": "..."}`, but the
    LIVE `/tpsl/place_order` endpoint returned a LIST `[{"orderId": "..."}]`
    (report c8a426d / trade cb6b4d4a — the old `(data or {}).get("orderId")`
    crashed with "'list' object has no attribute 'get'", failing all 3 TP legs).
    Since the documented shape and the live shape disagree — and an exchange that
    contradicts its own docs can change again — this parses defensively rather
    than assume one form: dict, list-of-dicts, and bare scalar / list-of-scalars
    id forms are all accepted. Returns "" when no id is present.
    """
    if not data:
        return ""
    if isinstance(data, dict):
        oid = data.get("orderId") or data.get("id")
        return str(oid) if oid else ""
    if isinstance(data, list):
        for el in data:
            if isinstance(el, dict):
                oid = el.get("orderId") or el.get("id")
                if oid:
                    return str(oid)
            elif el:  # bare scalar id in a list
                return str(el)
        return ""
    # bare scalar id (str / int)
    return str(data)


class BitunixBroker(Broker):
    """Read-only BitUnix Futures broker (Phase 1).

    Constructed with API key + secret. Without credentials the broker
    initializes as a STUB — `snapshot()` returns zeros so the dashboard
    renders "online · $0" rather than "not_wired". This matches the
    Coinbase pattern and lets the prod tile light up immediately even
    before KV migration of the BitUnix secrets.
    """

    paper = False  # this broker reads real data; paper-wrapping happens upstream

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        logger: "LoggerAgent | None" = None,
        safety_notifier=None,
        snapshot_cache_ttl_s: float = _SNAPSHOT_CACHE_TTL_S,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self.name = "bitunix_futures"
        # Stub mode if either credential is missing
        self._stub = not bool(api_key and api_secret)
        self._client: httpx.AsyncClient | None = None
        self._connected = False
        # ── Phase 4 write-path state ────────────────────────────────────
        self._margin_coin = _DEFAULT_MARGIN_COIN
        # Position mode (ONE_WAY, account-wide). None until set+verified on
        # the first live entry — NOT at connect (see module docstring).
        self._position_mode: str | None = None
        # Per-symbol leverage cache to dodge redundant change_leverage calls
        # (and error 20006 when a position is already open).
        self._leverage_cache: dict[str, int] = {}
        # Fail-closed kill-switch latch. Once set, place_order refuses.
        self._halt_new_orders = False
        self._halt_reason: str | None = None
        # Fill-observation poll knobs (overridable for tests).
        self._fill_max_polls = _FILL_MAX_POLLS
        self._fill_poll_interval_s = _FILL_POLL_INTERVAL_S
        # Optional LoggerAgent for `rest_request_retried` audit rows. When
        # None, the retry layer logs via `logging` only (no audit row). Wired
        # from main.py at broker construction; tests inject a fake.
        self.logger: "LoggerAgent | None" = logger
        # ── Snapshot-health primitive (gate (a) sub-item 2) ─────────────
        # `time.monotonic()` of the last successful `snapshot()` return.
        # None until the first successful snapshot — `is_healthy()` fails
        # closed (returns False) in that state, which is the intended
        # behavior for a broker that has never produced a fresh snapshot.
        self._last_successful_snapshot_ts: float | None = None
        # mtime-cache for the snapshot-staleness threshold YAML read.
        # `(mtime, value)`; None means "not yet read or cache invalidated".
        self._staleness_threshold_cache: tuple[float, float] | None = None
        # Optional safety_notifier for `stuck_order_cancelled` /
        # `stuck_order_cancel_failed` telegram pushes (gate (a) sub-item 3).
        # Duck-typed contract matches `DataExecAgent.safety_notifier`:
        #   async def push(text: str, *, audit_path: str = "other",
        #                  audit_context: dict | None = None) -> bool
        # Wired from main.py to the same TelegramChannel singleton.
        self.safety_notifier = safety_notifier
        # ── Account-snapshot poll cache + single-flight (10006 mitigation) ──
        # TTL (seconds) a COMPLETE snapshot is reused for. Constructor-
        # overridable (tests pin it; ops can tune). 0 disables caching. Kept
        # well below the drawdown breaker's equity-freshness tolerance so the
        # breaker never acts on dangerously stale equity.
        self._snapshot_cache_ttl_s = snapshot_cache_ttl_s
        # (monotonic_ts, AccountSnapshot) of the last COMPLETE fetch; None when
        # cold or invalidated by a state mutation.
        self._snapshot_cache: tuple[float, AccountSnapshot] | None = None
        # Single-flight handle: concurrent callers await this one in-flight
        # fetch rather than each firing a request.
        self._snapshot_inflight: asyncio.Task[AccountSnapshot] | None = None
        self._snapshot_lock = asyncio.Lock()

    async def connect(self) -> None:
        if self._stub:
            self._connected = True
            log.info("BitunixBroker connected as STUB (no credentials)")
            return

        self._client = httpx.AsyncClient(base_url=_BASE_URL, timeout=_DEFAULT_TIMEOUT_S)
        # Smoke-check: surface auth errors at startup, but don't raise — let
        # hydration catch failures so a bad key doesn't crash the whole boot.
        try:
            snap = await self.snapshot()
            log.info(
                "BitunixBroker connected (account=%s, equity=$%.2f, %d positions)",
                snap.account, snap.equity, len(snap.positions),
            )
        except Exception as e:
            log.warning("BitunixBroker connect-time snapshot failed: %s", e)

        self._connected = True

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    async def snapshot(self, *, force_refresh: bool = False) -> AccountSnapshot:
        """Account snapshot with a short TTL cache + single-flight.

        10006 mitigation: the bitunix observer calls `snapshot()` on every
        TradingView alert (account_equity for tier-sizing AND the drawdown
        breaker), and alerts cluster at candle closes across concurrent
        webhook-handler tasks. Each `snapshot()` fires two signed `/account`
        calls (USDT+USDC), so a cluster produced ~12 calls in 2 s → BitUnix
        10006 "request too frequently".

          * **Single-flight** — concurrent callers share ONE in-flight fetch.
          * **TTL cache** — a *complete* fetch is reused for
            `_snapshot_cache_ttl_s` (default `_SNAPSHOT_CACHE_TTL_S`), set well
            below the drawdown breaker's equity-freshness tolerance.
          * **Never caches a partial fetch** — a 10006 on one stablecoin
            under-counts equity and an errored position read hides positions;
            a partial would poison the breaker for the whole TTL window.
          * State mutations call `_invalidate_snapshot_cache()`, so post-trade
            and post-flatten reads are always fresh (the breaker's flatten
            verification in `data_exec.flatten_division` relies on this).
          * `force_refresh=True` bypasses the cache (still single-flighted).

        The existing read-side 10006 handling (log + skip coin) and the
        `_request` write-path backoff are unchanged — they remain the fallback
        for any residual rate-limiting the cache does not absorb.
        """
        # Stub / unconfigured: preserve the original direct zero-snapshot path
        # (no API, no caching, no lock).
        if self._stub or not self._client or not self._api_key or not self._api_secret:
            snap, _complete = await self._fetch_snapshot()
            return snap

        ttl = self._snapshot_cache_ttl_s

        # 1) Fast path — a fresh cached snapshot needs no lock.
        if not force_refresh and ttl > 0:
            cached = self._snapshot_cache
            if cached is not None and (time.monotonic() - cached[0]) < ttl:
                return cached[1]

        # 2) Single-flight. The lock guards only the cache re-check + in-flight
        #    handoff; it is NOT held across the network fetch, so concurrent
        #    callers all join the same in-flight task instead of each firing a
        #    request (this is what collapses the 12-in-2s burst to one call).
        async with self._snapshot_lock:
            if not force_refresh and ttl > 0:
                cached = self._snapshot_cache
                if cached is not None and (time.monotonic() - cached[0]) < ttl:
                    return cached[1]
            inflight = self._snapshot_inflight
            if inflight is None or inflight.done():
                inflight = asyncio.create_task(self._fetch_and_maybe_cache())
                self._snapshot_inflight = inflight

        try:
            return await inflight
        finally:
            # Release the shared handle once resolved so the next call (after
            # the TTL lapses) starts a fresh fetch. `is inflight` guards against
            # clobbering a newer task another caller may have started.
            if self._snapshot_inflight is inflight and inflight.done():
                self._snapshot_inflight = None

    def _invalidate_snapshot_cache(self) -> None:
        """Drop the cached snapshot so the next `snapshot()` refetches.

        Called after every state mutation (place_order/cancel/flatten/close)
        so post-trade equity and the post-flatten position verification read
        fresh broker truth, never a pre-mutation cached snapshot.
        """
        self._snapshot_cache = None

    async def _fetch_and_maybe_cache(self) -> AccountSnapshot:
        """Run one real fetch; cache it only if it was complete."""
        snap, complete = await self._fetch_snapshot()
        if complete and self._snapshot_cache_ttl_s > 0:
            self._snapshot_cache = (time.monotonic(), snap)
        return snap

    async def _fetch_snapshot(self) -> tuple[AccountSnapshot, bool]:
        """Raw account+positions fetch (no caching).

        Returns `(snapshot, complete)`. `complete` is False when any stablecoin
        balance read or the position read returned a non-zero BitUnix code
        (e.g. a 10006) — i.e. the snapshot is partial and must NOT be cached.
        """
        if self._stub or not self._client or not self._api_key or not self._api_secret:
            return (
                AccountSnapshot(
                    account="bitunix-stub",
                    equity=0.0,
                    buying_power=0.0,
                    cash=0.0,
                    positions=[],
                ),
                True,
            )

        # ── account balance (sum across stablecoin margin coins) ──────────
        # BitUnix Futures supports per-coin sub-accounts (USDT, USDC, BTC...).
        # `/account` returns balance for one coin at a time; we sum stablecoins
        # to get total futures equity in USD-equivalent terms.
        #
        # Per-coin response shape (verified 2026-05-10 against live API):
        #   available              — free margin, ready to use (the actual cash)
        #   frozen                 — locked in pending orders (separate bucket)
        #   margin                 — locked in open positions (separate bucket)
        #   crossUnrealizedPNL     — floating PnL on cross-margin positions
        #   isolationUnrealizedPNL — floating PnL on isolated-margin positions
        #   transfer               — attribution metadata: amount currently in
        #                            `available` that arrived via wallet transfer.
        #                            ALREADY INCLUDED IN `available` — must NOT
        #                            be added separately.
        #   bonus                  — attribution metadata: amount currently in
        #                            `available` that came from promo credit.
        #                            ALREADY INCLUDED IN `available` — must NOT
        #                            be added separately.
        #
        # Empirical evidence (2026-05-10, no open positions):
        #   USDT: available=25.27,  transfer=0,        bonus=25.27   → bonus dup
        #   USDC: available=3356.7, transfer=3356.7,   bonus=0       → transfer dup
        # Including transfer + bonus produced a 2× equity reading
        # ($6,763.94 vs real $3,381.97). The 2026-05-03 reconciliation
        # ("transfer is additive") was incorrect — retracted in memory
        # `trading_corp_bitunix_vision.md`.
        # Two completeness flags (reconciled: b0ae39d 10006 + e947ab4 breaker-abstain):
        #  * equity_complete — True unless a STABLECOIN balance read errors (which
        #    under-reports equity). Surfaced on AccountSnapshot so the drawdown
        #    breaker ABSTAINS on a partial read instead of false-flattening.
        #  * complete — the BROADER flag (stablecoin AND position reads OK) that
        #    gates the snapshot CACHE (never cache a partial). A position-read error
        #    sets complete=False but leaves equity_complete True (positions don't
        #    enter the equity sum).
        equity_complete = True
        complete = True
        total_equity = 0.0
        total_cash = 0.0
        for margin_coin in _STABLE_MARGIN_COINS:
            query = {"marginCoin": margin_coin}
            headers = _sign(self._api_key, self._api_secret, query=query)
            r = await self._client.get(
                "/api/v1/futures/account",
                params=query,
                headers=headers,
            )
            r.raise_for_status()
            ad = r.json()
            if ad.get("code") != 0:
                log.warning(
                    "BitUnix account error for %s: code=%s msg=%r",
                    margin_coin, ad.get("code"), ad.get("msg"),
                )
                # This coin is dropped from the equity sum -> equity UNDER-reported.
                # equity_complete=False → the breaker abstains; complete=False →
                # never cache the partial.
                equity_complete = False
                complete = False
                continue
            d = ad.get("data") or {}
            if not d:
                continue
            coin_equity = (
                _to_float(d.get("available")) +
                _to_float(d.get("frozen")) +
                _to_float(d.get("margin")) +
                _to_float(d.get("crossUnrealizedPNL")) +
                _to_float(d.get("isolationUnrealizedPNL"))
            )
            total_equity += coin_equity
            total_cash += _to_float(d.get("available"))

        equity = total_equity
        cash = total_cash

        # ── open positions ─────────────────────────────────────────────────
        pos_query: dict[str, str] = {}
        h2 = _sign(self._api_key, self._api_secret, query=pos_query)
        r2 = await self._client.get(
            "/api/v1/futures/position/get_pending_positions",
            params=pos_query,
            headers=h2,
        )
        r2.raise_for_status()
        pos_data = r2.json()
        positions: list[Position] = []
        if pos_data.get("code") == 0:
            for p in (pos_data.get("data") or []):
                qty = _to_float(p.get("qty"))
                if qty == 0:
                    continue
                # Sign qty by side (P1 fix): SHORT/SELL → negative qty so the
                # dashboard's downstream sum/PnL math is consistent. See
                # `_signed_position_qty` for label handling + grounding.
                side = (p.get("side") or "").upper()
                qty = _signed_position_qty(side, qty)
                positions.append(Position(
                    account="bitunix-futures",
                    symbol=p.get("symbol") or "?",
                    qty=qty,
                    avg_price=_to_float(p.get("avgOpenPrice")),
                    opened_ts=_iso_from_ms(p.get("ctime")),
                    extra={
                        "leverage": p.get("leverage"),
                        "marginMode": p.get("marginMode"),
                        "unrealizedPNL": p.get("unrealizedPNL"),
                        "liqPrice": p.get("liqPrice"),
                        "side": side,
                    },
                ))
        else:
            log.warning(
                "BitUnix get_pending_positions error: code=%s msg=%r",
                pos_data.get("code"), pos_data.get("msg"),
            )
            # Errored position read → positions list is empty but NOT proven
            # flat. Mark incomplete so it is never cached (a cached empty list
            # could false-pass the post-flatten verification).
            complete = False

        # Mark snapshot as fresh BEFORE returning. Any earlier raise (e.g.
        # r.raise_for_status on a 503) skips this line — staleness then
        # grows until `_assert_snapshot_fresh()` halts the order path.
        # See gate (a) sub-item 2 for the rationale.
        self._last_successful_snapshot_ts = time.monotonic()
        return (
            AccountSnapshot(
                account="bitunix-futures",
                equity=equity,
                buying_power=cash,
                cash=cash,
                positions=positions,
                equity_complete=equity_complete,
            ),
            complete,
        )

    # ── Snapshot-health primitives (gate (a) sub-item 2, 2026-05-30) ────
    def _staleness_threshold_s(self) -> float:
        """Mtime-cached read of `bitunix_futures.snapshot_staleness_threshold_seconds`
        from `config/strategies.yaml`. Falls back to `_DEFAULT_SNAPSHOT_STALENESS_S`
        on read error so a malformed YAML never disables the halt.
        """
        try:
            from pathlib import Path as _Path
            strat_path = (
                _Path(__file__).resolve().parent.parent.parent
                / "config" / "strategies.yaml"
            )
            mtime = strat_path.stat().st_mtime
        except Exception:
            return _DEFAULT_SNAPSHOT_STALENESS_S
        if (
            self._staleness_threshold_cache is not None
            and self._staleness_threshold_cache[0] == mtime
        ):
            return self._staleness_threshold_cache[1]
        try:
            import yaml as _yaml
            with strat_path.open(encoding="utf-8") as f:
                raw = _yaml.safe_load(f) or {}
            bx_block = raw.get("bitunix_futures") or {}
            value = float(
                bx_block.get("snapshot_staleness_threshold_seconds",
                             _DEFAULT_SNAPSHOT_STALENESS_S)
            )
            if value <= 0:
                value = _DEFAULT_SNAPSHOT_STALENESS_S
        except Exception as e:
            log.warning(
                "bitunix _staleness_threshold_s YAML read failed: %s "
                "— defaulting to %.1fs",
                e, _DEFAULT_SNAPSHOT_STALENESS_S,
            )
            value = _DEFAULT_SNAPSHOT_STALENESS_S
        self._staleness_threshold_cache = (mtime, value)
        return value

    def is_healthy(self) -> bool:
        """True iff the broker has produced a successful `snapshot()` recently
        enough (within `_staleness_threshold_s()`).

        Fail-closed semantics:
          * No successful snapshot yet (`_last_successful_snapshot_ts is None`)
            → False. A freshly-constructed broker is "stale" until its first
            successful snapshot.
          * `now - last_successful_snapshot > threshold` → False.

        Pure read; no side effects. The halt latch is set only by
        `_assert_snapshot_fresh()` when an order is about to be placed.
        """
        if self._last_successful_snapshot_ts is None:
            return False
        age_s = time.monotonic() - self._last_successful_snapshot_ts
        return age_s <= self._staleness_threshold_s()

    async def _assert_snapshot_fresh(self) -> None:
        """Fail-closed pre-trade gate: refuse to place unless the most recent
        snapshot is younger than the configured threshold.

        Symmetric with `_assert_position_mode_one_way`: the broker latches
        `_halt_new_orders=True` BEFORE raising, so the halt is sticky beyond
        the immediate call. The latch is operator-cleared via `resume()` —
        is_healthy() will turn True again on a fresh snapshot, but the latch
        does NOT auto-clear (intentional, per the operator-gates-everything
        discipline).

        Async for symmetry with the other `_assert_*` guards; no actual
        I/O is performed here (state is already cached on the broker
        instance — `_last_successful_snapshot_ts` is set by `snapshot()`).
        """
        if self.is_healthy():
            return
        age_s = (
            float("inf")
            if self._last_successful_snapshot_ts is None
            else time.monotonic() - self._last_successful_snapshot_ts
        )
        threshold_s = self._staleness_threshold_s()
        self._halt_new_orders = True
        self._halt_reason = f"snapshot_stale:{age_s:.1f}s"
        raise BitunixStaleSnapshot(age_s=age_s, threshold_s=threshold_s)

    async def quote(self, symbol: str) -> float:
        """Return last price for `symbol`. Public endpoint — no auth needed."""
        if self._stub or not self._client:
            return 0.0
        r = await self._client.get(
            "/api/v1/futures/market/tickers",
            params={"symbols": symbol},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(
                f"BitUnix ticker error for {symbol}: code={data.get('code')} "
                f"msg={data.get('msg')!r}"
            )
        rows = data.get("data") or []
        if not rows:
            return 0.0
        for t in rows:
            if t.get("symbol") == symbol:
                return _to_float(
                    t.get("last")
                    or t.get("lastPrice")
                    or t.get("close")
                )
        return 0.0

    async def get_funding_rate(self, symbol: str) -> float | None:
        """Return current funding rate for `symbol` already in percent per 8h
        (e.g. 0.0066 means 0.0066% per 8h — API returns percent directly, not
        a raw decimal fraction). Public endpoint, no auth.

        Works regardless of broker stub/connected state — funding is a
        public endpoint, so we construct a transient httpx client here
        rather than depending on `self._client` (which only exists when
        the broker has API credentials and `connect()` has run). This
        lets the HTF context provider call this method even in test /
        unauthenticated environments.

        Returns None on error so callers can treat "unknown funding"
        distinctly from "zero funding". The HTF gate's funding-extreme
        check uses None to skip the override — it does NOT default to
        the safe direction here because the regime gate's other hard-
        zero checks still apply.

        BitUnix returns funding-rate fields under varying key names
        across endpoints; we accept the canonical `fundingRate` plus
        `funding_rate` as a fallback.
        """
        try:
            async with httpx.AsyncClient(
                base_url=_BASE_URL, timeout=_DEFAULT_TIMEOUT_S,
            ) as client:
                r = await client.get(
                    "/api/v1/futures/market/funding_rate",
                    params={"symbol": symbol},
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            log.warning("BitUnix funding_rate fetch failed for %s: %s", symbol, e)
            return None
        if data.get("code") != 0:
            log.warning(
                "BitUnix funding_rate error for %s: code=%s msg=%r",
                symbol, data.get("code"), data.get("msg"),
            )
            return None
        d = data.get("data") or {}
        # Endpoint returns either a single dict or a list of one dict
        # depending on BitUnix's response shape; handle both defensively.
        if isinstance(d, list):
            d = d[0] if d else {}
        raw = d.get("fundingRate") if isinstance(d, dict) else None
        if raw is None and isinstance(d, dict):
            raw = d.get("funding_rate")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    # ── Phase 4: signed REST core ───────────────────────────────────────
    async def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict | None = None,
    ):
        """Signed request against the BitUnix futures REST API.

        Implements **sign-what-you-send**: the POST body is serialized to
        compact JSON exactly once; that exact string is BOTH signed and sent
        as raw content (never httpx `json=`, which would re-serialize with
        whitespace and break the signature → error 10007). Returns the
        envelope `data`; raises `BitunixAPIError` on a non-zero `code`.

        ── Retry layer (gate (a) 2026-05-30) ───────────────────────────
        Transient failures retry with exponential backoff + jitter:
          * `httpx.TimeoutException` (network or server-side timeout)
          * `httpx.HTTPStatusError` with status in `_RETRY_HTTP_STATUSES`
          * `BitunixAPIError` with code in `_RETRYABLE_CODES` (rate-limit
            only — narrow set; expand on evidence)

        Up to `_RETRY_MAX_ATTEMPTS` (3) retries → 4 total attempts; ≤10 s
        of total backoff sleep. Worst-case `_request` wallclock ≤70 s.

        **POST retries are gated on `clientId` presence in the body.** Without
        a deterministic idempotency key we cannot prove a retried request
        that succeeds didn't *also* land the first time. POSTs without
        `clientId` (e.g. `change_position_mode`, `change_leverage`,
        `cancel_orders` without one) raise immediately on transient failure.

        **Sign-stability under retry**: the JSON body string is computed once
        and re-used across attempts (body bytes never change). `nonce` and
        `timestamp` ARE re-computed per attempt — duplicate nonces are
        server-rejected, and the signature stays valid because the signing
        inputs incorporate the fresh nonce/timestamp.

        Read-path note: `snapshot()`, `quote()`, and `get_funding_rate()`
        deliberately bypass `_request` (they construct httpx GETs directly
        for per-call signing efficiency). Retry therefore does NOT cover the
        read path — for snapshot specifically, the staleness-signal halt
        (sub-item 2 of gate (a)) is the resilience primitive.
        """
        if self._stub or not self._client or not self._api_key or not self._api_secret:
            raise RuntimeError(
                "BitunixBroker._request requires credentials + an open client"
            )
        body_str = ""
        content = None
        headers_base: dict[str, str] = {}
        if body is not None:
            body_str = json.dumps(body, separators=(",", ":"))
            content = body_str
            headers_base["Content-Type"] = "application/json"

        # Retry-eligibility decision. GETs are read-only; POSTs need a
        # deterministic idempotency key to be safely retriable.
        if method == "GET":
            retry_eligible = True
        else:
            retry_eligible = bool(body and "clientId" in body)

        attempt = 0
        wallclock_used_s = 0.0
        last_error_summary: str | None = None
        while True:
            attempt += 1
            # Fresh signing per attempt — same body bytes, new nonce/timestamp.
            headers = dict(headers_base)
            headers.update(
                _sign(self._api_key, self._api_secret, query=query, body=body_str)
            )
            try:
                if method == "GET":
                    r = await self._client.get(
                        path, params=query or None, headers=headers,
                    )
                else:
                    r = await self._client.post(
                        path, content=content, headers=headers,
                    )
                r.raise_for_status()
                data = r.json()
                if data.get("code") != 0:
                    raise BitunixAPIError(data.get("code"), data.get("msg"), path=path)
            except BitunixAPIError as exc:
                transient = exc.code in _RETRYABLE_CODES
                err_summary = (
                    f"BitunixAPIError code={exc.code} name={exc.error_name}"
                )
                caught = exc
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                transient = _is_retryable_httpx_exc(exc)
                if isinstance(exc, httpx.HTTPStatusError):
                    err_summary = f"HTTPStatusError {exc.response.status_code}"
                else:
                    err_summary = f"{type(exc).__name__}"
                caught = exc
            else:
                # Success path. Audit retry summary iff we retried.
                if attempt > 1:
                    self._audit_request_retried(
                        path=path,
                        method=method,
                        attempts=attempt,
                        last_error_summary=last_error_summary,
                        wallclock_used_s=wallclock_used_s,
                    )
                return data.get("data")

            # Reached only on caught exception. Decide whether to retry.
            if (
                not transient
                or not retry_eligible
                or attempt > _RETRY_MAX_ATTEMPTS
            ):
                raise caught
            delay = _retry_backoff_delay(attempt)
            if wallclock_used_s + delay > _RETRY_WALLCLOCK_CAP_S:
                # Out of backoff budget — fail fast rather than violate the cap.
                raise caught
            last_error_summary = err_summary
            log.warning(
                "BitUnix _request transient failure on %s %s (attempt %d): %s "
                "— sleeping %.3fs before retry",
                method, path, attempt, err_summary, delay,
            )
            await asyncio.sleep(delay)
            wallclock_used_s += delay

    def _audit_request_retried(
        self,
        *,
        path: str,
        method: str,
        attempts: int,
        last_error_summary: str | None,
        wallclock_used_s: float,
    ) -> None:
        """One audit row per `_request` invocation that needed any retries.
        Once-per-summary (not per-attempt) to avoid audit-row flooding on a
        sustained 5xx storm. No-op when no logger is wired."""
        if self.logger is None:
            return
        try:
            self.logger.log_event(
                actor="bitunix_broker",
                kind="rest_request_retried",
                payload={
                    "path": path,
                    "method": method,
                    "attempts": attempts,
                    "last_error": last_error_summary,
                    "wallclock_used_s": round(wallclock_used_s, 3),
                    "ts": iso(now_utc()),
                },
            )
        except Exception as e:  # never block the success path on audit
            log.warning("rest_request_retried audit failed: %s", e)

    # ── Phase 4: order placement ────────────────────────────────────────
    async def place_order(
        self, order: ProposedOrder, *, fill_timeout_s: float | None = None,
    ) -> FillEvent:
        # Wrapper (b0ae39d 10006): invalidate the snapshot poll-cache after any
        # entry/exit attempt (success OR raise) so the next snapshot() reads
        # post-trade equity/positions fresh — never a pre-trade cached value the
        # sizing/breaker would act on. `fill_timeout_s` threads through for the B2
        # maker rest window (the maker/taker clones re-enter via this wrapper, so
        # each placement also invalidates the cache).
        try:
            return await self._place_order_impl(order, fill_timeout_s=fill_timeout_s)
        finally:
            self._invalidate_snapshot_cache()

    async def _place_order_impl(
        self, order: ProposedOrder, *, fill_timeout_s: float | None = None,
    ) -> FillEvent:
        """Place a live BitUnix futures order (one-way mode).

        Opening vs reducing is taken from ``order.extra["reduce_only"]`` (the
        order-path layer sets it for exits; absent ⇒ entry). Entries send
        tradeSide=OPEN; exits set reduceOnly=true. Fails closed on the halt
        latch and on a position-mode mismatch. After placement, observes the
        real fill (poll order-detail → VWAP from trade-history) and returns a
        `FillEvent`.

        Only reached in LIVE mode (PAPER intercepts at `PaperExecutionBroker`),
        which is why the position-mode and leverage WRITES live here, not in
        `connect()` (which runs on this real broker in paper mode too).
        """
        if self._stub or not self._client:
            raise NotImplementedError(
                "BitunixBroker.place_order: broker is in STUB mode (no "
                "credentials). In PAPER mode orders route to PaperBroker via "
                "PaperExecutionBroker — if you see this, the wrapping was bypassed."
            )
        # Determine reduce_only FIRST so the halt latch can exempt exits. #5-B:
        # a halt blocks NEW ENTRIES only — a reduce_only EXIT must always be
        # allowed to close an existing position ("exits are never halted",
        # Phase 1a §9c). The B1 catastrophic stop is a separate slPrice
        # attachment, not a reduce_only order, so it is unaffected.
        extra = order.extra or {}
        reduce_only = bool(extra.get("reduce_only", False))
        if self._halt_new_orders and not reduce_only:
            raise RuntimeError(
                f"BitunixBroker halted, refusing new orders: {self._halt_reason}"
            )
        # B2 maker-entry dispatch: ENTRIES only (never exits/reduce-only, and
        # never the B1 catastrophic stop, which is a separate slPrice attachment
        # that stays MARK_PRICE+MARKET taker). Absent extra["maker_entry"] this is
        # skipped → the current taker/market path runs unchanged.
        if not reduce_only and extra.get("maker_entry"):
            return await self._place_maker_entry(order)
        wire = to_wire_format(order.symbol)

        # Fail-closed position-mode guard (sets+verifies ONE_WAY on a flat entry).
        await self._assert_position_mode_one_way(allow_set=not reduce_only)

        # Leverage is per-symbol and must be set before opening (error 20006 if
        # changed with an open position). Entries only; exits inherit.
        if not reduce_only:
            await self._ensure_leverage(wire, extra.get("leverage"))

        body = self._build_order_body(order, wire, reduce_only)
        client_id = body["clientId"]
        try:
            data = await self._request(
                "POST", "/api/v1/futures/trade/place_order", body=body,
            )
        except BitunixAPIError as e:
            if e.code in _IDEMPOTENT_OK_CODES:
                # Deterministic clientId ⇒ a duplicate means this exact order
                # already landed. Treat as success and observe the fill.
                log.warning(
                    "BitUnix place_order clientId=%s duplicate (30042) — "
                    "treating as already-placed", client_id,
                )
                data = {"clientId": client_id}
            else:
                raise
        venue_order_id = (data or {}).get("orderId")
        client_id = (data or {}).get("clientId") or client_id
        log.info(
            "BitUnix place_order accepted: venue_order_id=%s clientId=%s "
            "%s %s qty=%s reduce_only=%s [order_id=%s]",
            venue_order_id, client_id, body["side"], wire, body["qty"],
            reduce_only, order.id,
        )

        status, filled_qty, avg_price, fee, entry_role = await self._observe_fill(
            order_id=venue_order_id, client_id=client_id,
            fill_timeout_s=fill_timeout_s,
        )

        # Encode non-terminal status in the venue suffix (mirrors coinbase):
        #   bitunix_futures              → fully filled
        #   bitunix_futures:part_filled  → partially filled
        #   bitunix_futures:new / :init  → accepted, not yet filled
        venue = "bitunix_futures"
        if status and status != "FILLED":
            venue = f"bitunix_futures:{status.lower()}"
        # Layer 1 fee plumbing (Session B): fee summed by
        # _fill_price_from_history → _observe_fill returns it → passed
        # through to FillEvent.fee for downstream consumers (Path C
        # entry_fee_usd stamp, _record_exit_outcome exit_fee_usd stamp).
        return FillEvent(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            qty=filled_qty if filled_qty > 0 else float(order.qty),
            price=avg_price,
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            venue=venue,
            fee=fee,
            role=entry_role,
        )

    async def _place_maker_entry(self, order: ProposedOrder) -> FillEvent:
        """B2: place the ENTRY as a POST_ONLY maker limit; on non-fill within the
        rest timeout (or a post-only would-cross rejection) fall back to a taker
        market entry so the signal is NEVER silently dropped.

        Maker params come from `order.extra` (stamped by the observer from the
        FeeConfig.maker_entry_* fields): `maker_rest_timeout_s`,
        `maker_offset_pct`, `maker_fallback_mode` ('cross_to_taker' default |
        'abandon'). The maker and taker-fallback attempts use DISTINCT clientIds
        (-mk / -tk) so the cancelled maker can't 30042-collide the fallback. #1's
        signed-fetch auto-book reads the real per-fill fee, so a maker fill books
        at the maker rate automatically.

        VERIFY-ON-LIVE: the exact BitUnix rejection code for a POST_ONLY-would-
        cross is not confirmed read-only — so ANY business rejection
        (BitunixAPIError) of the maker order, and any clean stuck-cancel
        (non-fill), routes to the taker fallback. A cancel-FAILED on the maker
        (BitunixStuckOrderCancelFailed) does NOT fall back: the resting maker may
        still fill, so crossing would risk a DOUBLE position → it raises loud for
        operator intervention.
        """
        extra = order.extra or {}
        timeout_s = float(extra.get("maker_rest_timeout_s", _MAKER_REST_TIMEOUT_S))
        mode = str(extra.get("maker_fallback_mode", _MAKER_FALLBACK_MODE))

        maker = self._maker_clone(order)
        if maker is None:
            log.info("B2 maker entry: no usable reference price for order_id=%s "
                     "— placing taker market entry", order.id)
            return await self.place_order(self._taker_clone(order))

        try:
            # Fills (full or partial) → place_order returns the FillEvent.
            return await self.place_order(maker, fill_timeout_s=timeout_s)
        except BitunixStuckOrderCancelled:
            log.info("B2 maker entry unfilled within %.1fs (cancelled) — "
                     "crossing to taker [order_id=%s]", timeout_s, order.id)
        except BitunixStuckOrderCancelFailed:
            raise  # resting maker may still fill → crossing = double-fill risk
        except BitunixAPIError as e:
            log.info("B2 maker entry rejected (code=%s) — crossing to taker "
                     "[order_id=%s]: %s", e.code, order.id, e.msg)

        if mode == "abandon":
            raise BitunixMakerEntryUnfilled(order_id=order.id)
        # 'cross_to_taker' (default; any unrecognized mode also crosses — never
        # silently drops the signal).
        return await self.place_order(self._taker_clone(order))

    def _maker_clone(self, order: ProposedOrder) -> "ProposedOrder | None":
        """A POST_ONLY LIMIT copy of an entry order, priced passively at a
        configurable offset from the signal/reference price (buy BELOW ref, sell
        ABOVE ref → rests as maker; POST_ONLY rejects if it would still cross).
        Returns None when no usable reference price exists (caller → taker)."""
        extra = order.extra or {}
        ref = extra.get("entry_reference_price")
        if ref is None:
            ref = order.limit_price
        try:
            ref = float(ref) if ref is not None else 0.0
        except (TypeError, ValueError):
            ref = 0.0
        if ref <= 0:
            return None
        try:
            offset = float(extra.get("maker_offset_pct", _MAKER_OFFSET_PCT))
        except (TypeError, ValueError):
            offset = _MAKER_OFFSET_PCT
        side = (order.side or "").lower()
        limit = ref * (1.0 - offset) if side == "buy" else ref * (1.0 + offset)
        new_extra = {k: v for k, v in extra.items() if k != "maker_entry"}
        new_extra["tif"] = "POST_ONLY"
        new_extra["client_id_suffix"] = "-mk"
        return dataclasses.replace(
            order, order_type="limit", limit_price=limit, extra=new_extra,
        )

    def _taker_clone(self, order: ProposedOrder) -> ProposedOrder:
        """A plain taker MARKET copy of an entry order (the maker fallback = the
        current behavior), with a distinct clientId suffix so it can't 30042-
        collide with the cancelled maker attempt."""
        extra = order.extra or {}
        new_extra = {k: v for k, v in extra.items()
                     if k not in ("maker_entry", "tif")}
        new_extra["client_id_suffix"] = "-tk"
        return dataclasses.replace(
            order, order_type="market", limit_price=None, extra=new_extra,
        )

    def _client_id(self, order: ProposedOrder) -> str:
        """Deterministic idempotency key: the same ProposedOrder always maps to
        the same clientId, so a retry that 30042-duplicates is provably
        'already placed', not a double-send.

        `extra["client_id_suffix"]` (B2): the maker attempt ("-mk") and its
        taker fallback ("-tk") MUST carry distinct clientIds — otherwise the
        cancelled maker's clientId would 30042-collide on the taker fallback and
        the broker would wrongly treat the fallback as already-placed. The suffix
        keeps determinism (same order+suffix → same clientId)."""
        suffix = str((order.extra or {}).get("client_id_suffix", ""))
        return f"{_CLIENT_ID_PREFIX}{order.id}{suffix}"

    def _build_order_body(
        self, order: ProposedOrder, wire: str, reduce_only: bool,
    ) -> dict:
        """Build the place_order body for one-way mode.

        Entry (open): tradeSide=OPEN, reduceOnly=false, effect (TIF), + an
        attached server-side stop (B1) when the order carries a positive
        ``extra["stop_price"]``.
        Exit (reduce): reduceOnly=true only — no tradeSide/positionId/stop.

        B1 — atomic catastrophic stop: the protective stop is attached to the
        SAME open call (BitUnix place_order accepts slPrice/slStopType/
        slOrderType), so the position and its server-side stop are born in one
        request — no naked window between entry fill and stop placement.
          * slStopType=MARK_PRICE — fires ahead of liquidation on the same
            reference the venue liquidates against; wick-resistant vs LAST_PRICE.
          * slOrderType=MARKET — guaranteed exit on trigger (not a limit that
            could sit unfilled through the move).
          * The attached SL is a position-closing trigger (reduce-by-nature);
            BitUnix exposes no separate reduce flag for it, and the open order
            itself stays reduceOnly=False.
        Scope: places the stop at its original structural level only — NO
        ratchet / trail-to-BE (that is `modify_position_tp_sl_order`, a separate
        later build). Absent or non-positive stop_price ⇒ no SL attached
        (behavior unchanged). Exits (reduce_only) never carry an SL.

        VERIFY-ON-LIVE: official docs mark `tradeSide` hedge-mode-only and
        `effect` LIMIT-only; we send both on opens per the operator-confirmed
        payload. If the first live entry param-errors, drop them (module docstring).
        The slPrice/slStopType/slOrderType attachment is likewise unverified
        against a real fill — see Phase C live validation.
        """
        otype = (order.order_type or "market").upper()
        body: dict = {
            "symbol": wire,
            "side": order.side.upper(),
            "orderType": otype,
            "qty": _amount_str(order.qty),
        }
        if otype == "LIMIT":
            if not order.limit_price:
                raise ValueError("BitUnix LIMIT order requires limit_price")
            body["price"] = _amount_str(order.limit_price)
        if reduce_only:
            body["reduceOnly"] = True
        else:
            body["tradeSide"] = "OPEN"
            body["reduceOnly"] = False
            # B1: attach the structural stop server-side, atomically with entry.
            sl_raw = (order.extra or {}).get("stop_price")
            if sl_raw is not None:
                try:
                    sl_px = float(sl_raw)
                except (TypeError, ValueError):
                    sl_px = 0.0
                if sl_px > 0:
                    body["slPrice"] = _amount_str(sl_px)
                    body["slStopType"] = "MARK_PRICE"
                    body["slOrderType"] = "MARKET"
        if otype == "LIMIT" or not reduce_only:
            body["effect"] = str((order.extra or {}).get("tif", "GTC")).upper()
        body["clientId"] = self._client_id(order)
        return body

    # ── Phase 3 (N+2): public position-list accessor for the reconciler ─
    async def get_pending_positions(self) -> list[Position]:
        """Return broker-truth list of open futures positions.

        Thin public wrapper over `/api/v1/futures/position/get_pending_positions`
        for the N+2 position-state reconciler. Uses `_request` (signed +
        gate (a) retry-aware) and parses the response into the same
        `Position` dataclass shape `snapshot()` returns — SHORT positions
        render with negative qty for downstream PnL math consistency.

        Stub-mode and missing-creds return `[]` (no exception) so dormant
        paper-mode callers don't crash. Transient errors are logged +
        treated as "no positions known" — the reconciler's "verdict =
        missing" branch handles this.

        Distinct from `snapshot()` which fetches account equity + cash
        in the same call; this method is positions-only and avoids the
        N×marginCoin balance fetches when the caller only needs the
        position list.
        """
        if (
            self._stub
            or not self._client
            or not self._api_key
            or not self._api_secret
        ):
            return []
        try:
            data = await self._request(
                "GET",
                "/api/v1/futures/position/get_pending_positions",
                query={},
            )
        except Exception as e:
            log.warning("BitUnix get_pending_positions failed: %s", e)
            return []
        positions: list[Position] = []
        for p in (data or []):
            qty = _to_float(p.get("qty"))
            if qty == 0:
                continue
            # Sign qty by side (P1 fix) — same helper as snapshot(); a
            # SELL-opened short must render negative so the reconciler's
            # `_broker_side` reads "sell" and matches the bot's tracked row.
            side = (p.get("side") or "").upper()
            qty = _signed_position_qty(side, qty)
            positions.append(Position(
                account="bitunix-futures",
                symbol=p.get("symbol") or "?",
                qty=qty,
                avg_price=_to_float(p.get("avgOpenPrice")),
                opened_ts=_iso_from_ms(p.get("ctime")),
                extra={
                    "leverage": p.get("leverage"),
                    "marginMode": p.get("marginMode"),
                    "unrealizedPNL": p.get("unrealizedPNL"),
                    "liqPrice": p.get("liqPrice"),
                    "side": side,
                    "positionId": p.get("positionId"),
                },
            ))
        return positions

    # ── Phase 4: position-mode guard (one-way, account-wide) ────────────
    async def _position_mode_from_positions(self) -> str | None:
        """Read the account position mode off any open position.

        BitUnix exposes `positionMode` only on the position object
        (`get_pending_positions`); there is no standalone getter. Returns the
        mode (upper-cased) when a position exists, else None (flat)."""
        data = await self._request(
            "GET", "/api/v1/futures/position/get_pending_positions", query={},
        )
        for p in (data or []):
            pm = p.get("positionMode")
            if pm:
                return str(pm).upper()
        return None

    async def _set_position_mode_one_way(self) -> str:
        """Set + verify ONE_WAY (account-wide); returns the resulting mode.
        Only ever called from the live order path (see place_order docstring)."""
        data = await self._request(
            "POST", "/api/v1/futures/account/change_position_mode",
            body={"positionMode": _ONE_WAY},
        )
        mode = data.get("positionMode") if isinstance(data, dict) else None
        # code==0 already means success; trust the returned mode if present,
        # else the value we just set.
        self._position_mode = str(mode).upper() if mode else _ONE_WAY
        return self._position_mode

    async def _assert_position_mode_one_way(self, *, allow_set: bool) -> None:
        """Fail-closed guard: refuse to place unless the account is ONE_WAY.

        Reads positionMode off an open position when one exists; on a flat
        opening order (`allow_set`) sets+verifies ONE_WAY. On mismatch: latch
        the halt flag and raise `BitunixPositionModeMismatch`."""
        live_mode = await self._position_mode_from_positions()
        if live_mode is not None:
            mode = live_mode
        elif allow_set:
            mode = await self._set_position_mode_one_way()
        else:
            mode = self._position_mode
        if mode is not None and mode != _ONE_WAY:
            self._halt_new_orders = True
            self._halt_reason = f"position_mode_mismatch:{mode}"
            raise BitunixPositionModeMismatch(current=mode)

    async def _ensure_leverage(self, wire: str, leverage) -> None:
        """Set per-symbol leverage before opening (cached to dodge 20006)."""
        if leverage is None:
            return
        try:
            lev = int(leverage)
        except (TypeError, ValueError):
            return
        if lev <= 0 or self._leverage_cache.get(wire) == lev:
            return
        try:
            await self._request(
                "POST", "/api/v1/futures/account/change_leverage",
                body={"symbol": wire, "marginCoin": self._margin_coin, "leverage": lev},
            )
            self._leverage_cache[wire] = lev
        except BitunixAPIError as e:
            if e.code == 20006:
                # A position/order is already open; leverage can't change now
                # and the existing setting stands. Don't block the order.
                log.warning(
                    "BitUnix change_leverage %s=%dx blocked (20006); "
                    "using existing leverage", wire, lev,
                )
            else:
                raise

    # ── Phase 4: fill observation ───────────────────────────────────────
    async def get_order_detail(self, *, order_id=None, client_id=None) -> dict:
        """Fetch a single order's detail by venue order id or clientId."""
        if not order_id and not client_id:
            raise ValueError("get_order_detail requires order_id or client_id")
        query: dict[str, str] = {}
        if order_id:
            query["orderId"] = str(order_id)
        else:
            query["clientId"] = str(client_id)
        data = await self._request(
            "GET", "/api/v1/futures/trade/get_order_detail", query=query,
        )
        if isinstance(data, list):
            return data[0] if data else {}
        return data or {}

    async def get_history_trades(self, *, order_id=None, symbol=None) -> list[dict]:
        """Fetch fills (tradeList) for an order/symbol. Carries the real
        per-fill price + fee (order detail has neither avgPrice nor fill price)."""
        query: dict[str, str] = {}
        if order_id:
            query["orderId"] = str(order_id)
        if symbol:
            query["symbol"] = to_wire_format(symbol)
        data = await self._request(
            "GET", "/api/v1/futures/trade/get_history_trades", query=query,
        )
        if isinstance(data, dict):
            return data.get("tradeList") or []
        if isinstance(data, list):
            return data
        return []

    async def _observe_fill(self, *, order_id, client_id, fill_timeout_s=None):
        """Poll order detail to a terminal/partial state, then derive the VWAP
        fill price + total fee + filled qty from trade history (neither order
        detail nor pending orders carry a fill price — only the fills do).

        Returns (status, filled_qty, avg_price, fee). `filled_qty` reflects
        partial fills via `tradeQty` (BitUnix status PART_FILLED).

        ── Stuck-order timeout → cancel (gate (a) sub-item 3, 2026-05-30) ──
        If polling exhausts WITHOUT a terminal status (still NEW / INIT /
        PART_FILLED at the last poll), cancel the order and emit safety
        side-effects:
          * cancel succeeded AND status was PART_FILLED → continue past the
            cancel block; the remaining quantity has been cancelled at the
            venue, but the partial fill that already landed is real money,
            so we return the partial-fill tuple normally and `place_order`
            constructs a `bitunix_futures:part_filled` FillEvent. The audit
            (`stuck_order_cancelled`) + telegram fire from this method.
          * cancel succeeded AND status was unfilled (NEW / INIT / None) →
            raise `BitunixStuckOrderCancelled`. Caller's path treats the
            order as not-placed.
          * cancel FAILED → raise `BitunixStuckOrderCancelFailed` regardless
            of whether status was partial or unfilled; operator intervention
            may be required because the broker cannot prove the order isn't
            still resting at the venue.

        Threshold note: the existing `_fill_max_polls × _fill_poll_interval_s`
        (3.2s at the defaults 8 × 0.4s) IS the threshold. Market orders on
        BTC-PERP typically fill in <1s; 3.2s of inactivity is a strong
        signal of stuck. If we observe false-positive stuck cancels on slow
        fills, this is the knob to raise (config addition deferred until
        we have evidence the default is too tight).
        """
        # B2: a maker entry passes a short `fill_timeout_s` so the resting
        # POST_ONLY limit's rest window = the maker timeout (not the default
        # fill-stuck budget). None → default budget (current taker behavior).
        max_polls = self._fill_max_polls
        if fill_timeout_s is not None and self._fill_poll_interval_s > 0:
            _n = float(fill_timeout_s) / self._fill_poll_interval_s
            max_polls = max(1, int(_n) + (0 if _n == int(_n) else 1))
        status: str | None = None
        filled_qty = 0.0
        resolved_id = order_id
        for i in range(max_polls):
            detail = await self.get_order_detail(
                order_id=resolved_id,
                client_id=None if resolved_id else client_id,
            )
            if detail:
                resolved_id = detail.get("orderId") or resolved_id
                status = (detail.get("status") or "").upper() or None
                filled_qty = _to_float(detail.get("tradeQty"))
            if status in _TERMINAL_STATUSES:
                break
            if i < max_polls - 1:
                await asyncio.sleep(self._fill_poll_interval_s)

        # Stuck-order check (sub-item 3). Polling exhausted without a
        # terminal status → cancel + audit + telegram. May raise.
        if status not in _TERMINAL_STATUSES:
            await self._handle_stuck_order(
                order_id=resolved_id, status=status,
            )

        avg_price, fee, hist_qty, role = await self._fill_price_from_history(resolved_id)
        if filled_qty <= 0 and hist_qty > 0:
            filled_qty = hist_qty
        return status, filled_qty, avg_price, fee, role

    async def _handle_stuck_order(self, *, order_id, status) -> None:
        """Cancel a stuck order; emit audit + telegram; raise unless the
        order was partially filled (in which case the caller's path returns
        the partial fill normally — see `_observe_fill` docstring for the
        decision matrix).

        gate (a) sub-item 3 of REST resilience (2026-05-30).
        """
        cancel_ok = False
        cancel_error: Exception | None = None
        try:
            cancel_ok = await self.cancel_order(order_id) if order_id else False
        except Exception as e:
            cancel_error = e
            cancel_ok = False

        poll_budget_s = (
            self._fill_max_polls * self._fill_poll_interval_s
        )
        common_payload = {
            "order_id": str(order_id) if order_id else None,
            "status_at_exhaustion": status,
            "poll_budget_s": round(poll_budget_s, 3),
            "max_polls": self._fill_max_polls,
            "poll_interval_s": self._fill_poll_interval_s,
            "cancel_attempted": True,
            "cancel_ok": cancel_ok,
            "cancel_error": str(cancel_error) if cancel_error else None,
            "ts": iso(now_utc()),
        }
        is_partial = status == "PART_FILLED"
        if cancel_ok:
            audit_kind = "stuck_order_cancelled"
            telegram_text = (
                f"⚠️ BitUnix STUCK ORDER cancelled — order_id={order_id} "
                f"status={status} (poll budget {poll_budget_s:.1f}s exhausted). "
                f"{'Partial fill kept.' if is_partial else 'No fills landed.'}"
            )
        else:
            audit_kind = "stuck_order_cancel_failed"
            telegram_text = (
                f"🚨 BitUnix STUCK ORDER + CANCEL FAILED — order_id={order_id} "
                f"status={status} (poll budget {poll_budget_s:.1f}s exhausted). "
                f"cancel_error={cancel_error or '(returned False)'}. "
                f"Operator: verify order is not still resting at venue."
            )

        # Audit row (best-effort — never block the safety path on audit failure).
        if self.logger is not None:
            try:
                self.logger.log_event(
                    actor="bitunix_broker",
                    kind=audit_kind,
                    payload=common_payload,
                )
            except Exception as e:
                log.warning(
                    "BitUnix _handle_stuck_order audit (%s) failed: %s",
                    audit_kind, e,
                )

        # Telegram (best-effort — never block the safety path on push failure).
        if self.safety_notifier is not None:
            try:
                await self.safety_notifier.push(
                    telegram_text,
                    audit_path="safety_alert",
                    audit_context={
                        "kind": audit_kind,
                        "order_id": str(order_id) if order_id else None,
                        "status_at_exhaustion": status,
                    },
                )
            except Exception as e:
                log.warning(
                    "BitUnix _handle_stuck_order telegram failed: %s", e,
                )

        # Raise unless cancel succeeded AND we have a real partial fill.
        if not cancel_ok:
            raise BitunixStuckOrderCancelFailed(order_id=order_id, status=status)
        if not is_partial:
            raise BitunixStuckOrderCancelled(order_id=order_id, status=status)
        # PART_FILLED + cancel succeeded → fall through; caller returns
        # the partial fill tuple normally.

    async def _fill_price_from_history(self, order_id):
        """VWAP fill price + summed fee + filled qty + dominant maker/taker role
        from trade history. role: 'maker'|'taker'|'mixed'|'' (no roleType)."""
        if not order_id:
            return 0.0, 0.0, 0.0, ""
        trades = await self.get_history_trades(order_id=order_id)
        notional = qty = fee = 0.0
        maker_qty = taker_qty = 0.0
        for t in trades:
            q = _to_float(t.get("qty"))
            p = _to_float(t.get("price"))
            notional += q * p
            qty += q
            fee += _to_float(t.get("fee"))
            role = str(t.get("roleType") or "").upper()
            if role == "MAKER":
                maker_qty += q
            elif role == "TAKER":
                taker_qty += q
        avg = (notional / qty) if qty > 0 else 0.0
        if maker_qty <= 0 and taker_qty <= 0:
            role_tag = ""
        elif taker_qty <= 0:
            role_tag = "maker"
        elif maker_qty <= 0:
            role_tag = "taker"
        else:
            role_tag = "mixed"
        return avg, fee, qty, role_tag

    async def get_recent_close_fills(
        self, *, symbol: str, exit_side: str, since_ms: float | None = None,
    ) -> list[dict]:
        """Signed fetch of the REAL fills that CLOSED a position — for accurate
        auto-booking of a server-side (B1) stop close (#1, replaces the optimistic
        known-level estimate).

        The B1 stop is venue-managed (attached to the entry via slPrice), so its
        close fills carry a DIFFERENT orderId than the entry. We therefore query
        by SYMBOL and keep only the `exit_side` fills (the side that CLOSES the
        position = opposite of entry), optionally restricted to fills at/after
        `since_ms` (the entry time) to exclude a prior position's close. Each kept
        fill carries the REAL per-fill price/qty/fee — including the real maker-vs-
        taker fee (B2 makes that per-fill variable, so we READ it, never assume a
        rate). Aggregation (VWAP / summed fee) is the caller's job.

        Returns a list of ``{"price","qty","fee"}`` for the close fills, or ``[]``
        when none can be confidently identified — the caller then falls back to
        the known-level estimate (a close is NEVER left unbooked).

        VERIFY-ON-LIVE: the side/timestamp keys read here are inferred from the
        BitUnix trade-history shape and are NOT yet verified against a real
        close-fill response (agent SSH is read-only — no live call). If the live
        shape differs, this returns ``[]`` and the estimate fallback fires (safe);
        the real-fetch path needs a one-shot live validation at deploy.
        """
        want = (exit_side or "").upper()
        try:
            raw = await self.get_history_trades(symbol=symbol)
        except Exception as e:
            log.warning(
                "get_recent_close_fills(%s): trade-history fetch failed: %s",
                symbol, e,
            )
            return []
        out: list[dict] = []
        for t in (raw or []):
            side = str(t.get("side") or t.get("tradeSide") or "").upper()
            # Require a readable exit-side fill — if the venue omits side we
            # CANNOT distinguish entry from close, so we skip (→ estimate fallback)
            # rather than risk booking the entry fill as the close.
            if not side or side != want:
                continue
            if since_ms:
                tms = _to_float(t.get("ctime") or t.get("time") or t.get("ts"))
                if tms and tms < float(since_ms):
                    continue
            out.append({
                "price": _to_float(t.get("price")),
                "qty": _to_float(t.get("qty")),
                "fee": _to_float(t.get("fee")),
                # roleType is "MAKER" | "TAKER" (BitUnix trade-history doc); the
                # order id lets the reconciler classify tp-vs-stop. Both absent →
                # "" so the role mix / exit-kind logic degrades gracefully.
                "role": str(t.get("roleType") or "").upper(),
                "order_id": str(t.get("orderId") or ""),
            })
        return out

    # ── Phase 4: cancel + kill-switch primitives ────────────────────────
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel one resting order by venue order id.

        `cancel_orders` requires `symbol`; we resolve it from the order detail
        (the ABC gives us only the id). Returns True iff the id is in the
        response successList. Never raises — cancel is often a cleanup path."""
        if self._stub or not self._client:
            return False
        try:
            detail = await self.get_order_detail(order_id=order_id)
            body: dict = {"orderList": [{"orderId": str(order_id)}]}
            symbol = detail.get("symbol")
            if symbol:
                body["symbol"] = symbol
            data = await self._request(
                "POST", "/api/v1/futures/trade/cancel_orders", body=body,
            )
        except Exception as e:
            log.warning("BitUnix cancel_order(%s) failed: %s", order_id, e)
            return False
        # A cancel changes resting-order state → drop the snapshot cache.
        self._invalidate_snapshot_cache()
        success = (data or {}).get("successList") or []
        return any(str(s.get("orderId")) == str(order_id) for s in success)

    async def cancel_all_orders(self, symbol: str | None = None) -> dict:
        """Cancel all resting orders (account-wide, or one symbol).
        Kill-switch primitive."""
        body: dict = {}
        if symbol:
            body["symbol"] = to_wire_format(symbol)
        result = await self._request(
            "POST", "/api/v1/futures/trade/cancel_all_orders", body=body,
        ) or {}
        self._invalidate_snapshot_cache()
        return result

    async def flash_close_position(self, position_id: str) -> dict:
        """Market-flatten a single position by id. Kill-switch primitive."""
        result = await self._request(
            "POST", "/api/v1/futures/trade/flash_close_position",
            body={"positionId": str(position_id)},
        ) or {}
        self._invalidate_snapshot_cache()
        return result

    async def close_all_position(self, symbol: str | None = None) -> dict:
        """Market-flatten all positions (account-wide, or one symbol).
        Kill-switch primitive."""
        body: dict = {}
        if symbol:
            body["symbol"] = to_wire_format(symbol)
        result = await self._request(
            "POST", "/api/v1/futures/trade/close_all_position", body=body,
        ) or {}
        self._invalidate_snapshot_cache()
        return result

    async def flatten(self, symbol: str | None = None) -> dict:
        """Kill switch: latch halt-new-orders, cancel all resting orders, then
        market-flatten all positions. Best-effort — runs every step and
        collects errors rather than aborting on the first failure."""
        self._halt_new_orders = True
        self._halt_reason = "flatten() kill-switch invoked"
        results: dict = {"halted": True}
        try:
            results["cancel_all_orders"] = await self.cancel_all_orders(symbol)
        except Exception as e:
            results["cancel_all_orders_error"] = str(e)
        try:
            results["close_all_position"] = await self.close_all_position(symbol)
        except Exception as e:
            results["close_all_position_error"] = str(e)
        # Safety guarantee: always drop the snapshot cache before returning so
        # the post-flatten verification in data_exec.flatten_division re-reads
        # fresh broker truth (positions=0), even if a close step raised above.
        self._invalidate_snapshot_cache()
        return results

    def resume(self) -> None:
        """Clear the halt latch (operator action after resolving the cause)."""
        self._halt_new_orders = False
        self._halt_reason = None

    def list_open_positions(self, db_url: str) -> list[OpenPosition]:
        """Reconciler-facing enumeration of unresolved positions.

        Paper mode (today): queries `paper_trade_record WHERE division =
        'bitunix_futures' AND result IS NULL`, hydrates from `extra_json`.
        Phase 4 swap: replace the SQL with `/api/v1/futures/position`
        — same `OpenPosition` return shape, different source.

        `filled_legs` and `current_sl` are read from `extra_json` when
        present (paper-mode multi-leg replay writes them back as legs
        fill and the lifecycle advances). Defaults to `[]` and the
        original `stop_price` column for trades the replay hasn't yet
        touched. Legacy single-leg trades (no v2 tp_plan) are filtered
        out — reconciler only manages v2 lifecycle.
        """
        with db.connect(db_url) as conn:
            rows = conn.execute(
                "SELECT order_id, symbol, side, qty, entry_reference_price, "
                "stop_price, ts, extra_json "
                "FROM paper_trade_record "
                "WHERE division = ? AND result IS NULL",
                ("bitunix_futures",),
            ).fetchall()

        out: list[OpenPosition] = []
        for r in rows:
            try:
                extra = json.loads(r["extra_json"]) if r["extra_json"] else {}
            except (TypeError, ValueError):
                extra = {}
            tp_plan = extra.get("tp_plan") or []
            if not tp_plan or extra.get("tp_plan_version") != "v2":
                # Skip legacy single-leg trades — reconciler only manages
                # v2 lifecycle. Pre-PR-4 trades retain their original SL.
                continue
            # filled_legs: paper-mode replay writes these back into
            # extra_json as legs fill; live-mode Phase 4 will hydrate
            # from broker truth instead.
            filled_legs_raw = extra.get("filled_legs") or []
            try:
                filled_legs = [str(x) for x in filled_legs_raw]
            except (TypeError, ValueError):
                filled_legs = []
            # current_sl: replay updates this as lifecycle advances.
            # Fall back to the structural stop column for trades that
            # haven't had a tp1 fill yet.
            sl_from_extra = extra.get("current_sl")
            if sl_from_extra is not None:
                try:
                    current_sl = float(sl_from_extra)
                except (TypeError, ValueError):
                    current_sl = _to_float(r["stop_price"])
            else:
                current_sl = _to_float(r["stop_price"])
            out.append(OpenPosition(
                order_id=r["order_id"],
                symbol=r["symbol"],
                side=r["side"],
                qty=_to_float(r["qty"]),
                entry_price=_to_float(r["entry_reference_price"]),
                current_sl=current_sl,
                tp_plan=tp_plan,
                filled_legs=filled_legs,
                opened_ts=r["ts"] or "",
            ))
        return out

    async def place_resting_reduce_only_limit(self, order: ProposedOrder) -> str:
        """Place a REDUCE-ONLY LIMIT that RESTS on the book (a bracket TP leg) and
        return its venue orderId WITHOUT observing the fill.

        Unlike `place_order`, this does NOT poll for a fill — a TP rests until the
        market reaches it (or native OCO cancels it on the final close). reduce_only
        is FORCED True, so it is exit-exempt from the halt latch (#5-B). The caller
        stores the returned orderId so the bot can identify its own resting orders
        (OCO verify + #4 orphan ID).

        VERIFY-ON-LIVE (Phase-C validation): that a reduce-only LIMIT rests as a
        maker at the TP price, that 3 legs + the attached SL coexist (no 30038
        TPSL_EXCEEDS_POSITION), and that native OCO / SL-auto-reduce behave as in
        the manual UI — the open question this build's validation answers.
        """
        if self._stub or not self._client:
            raise NotImplementedError(
                "BitunixBroker.place_resting_reduce_only_limit: STUB mode (no creds)"
            )
        extra = dict(order.extra or {})
        extra["reduce_only"] = True
        order = dataclasses.replace(order, order_type="limit", extra=extra)
        wire = to_wire_format(order.symbol)
        # reduce-only on an existing position: verify ONE_WAY (never set/leverage).
        await self._assert_position_mode_one_way(allow_set=False)
        body = self._build_order_body(order, wire, reduce_only=True)
        client_id = body["clientId"]
        try:
            data = await self._request(
                "POST", "/api/v1/futures/trade/place_order", body=body,
            )
        except BitunixAPIError as e:
            if e.code in _IDEMPOTENT_OK_CODES:
                data = {"clientId": client_id}
            else:
                raise
        venue_order_id = (data or {}).get("orderId")
        log.info(
            "BitUnix resting reduce-only LIMIT placed: venue_order_id=%s "
            "clientId=%s %s %s qty=%s price=%s [order_id=%s]",
            venue_order_id, client_id, body["side"], wire, body["qty"],
            body.get("price"), order.id,
        )
        return str(venue_order_id) if venue_order_id else ""

    async def place_tpsl_order(
        self,
        *,
        symbol: str,
        position_id: str,
        tp_price: float,
        tp_qty: float,
        tp_stop_type: str = "MARK_PRICE",
        tp_order_type: str = "LIMIT",
    ) -> str:
        """Place ONE partial-qty TP leg via the native `/tpsl/place_order` endpoint.

        Builds a position-tied TP/SL order with a partial `tpQty` — call N times
        to build the TP ladder (0.25/0.50/0.25 splits). The `tpOrderPrice` is set
        equal to `tp_price` to preserve the maker-LIMIT-at-price behaviour (LIMIT
        TP fills at `tpOrderPrice` when the mark hits `tpPrice`).

        Unlike `place_resting_reduce_only_limit` (standalone reduce-only LIMIT via
        `/futures/trade/place_order`), this uses the native venue TP/SL order family
        which gives: native OCO with the attached B1 SL, auto-reducing SL qty as
        legs fill, and clean fill-tracking via `get_pending_tpsl_orders`.

        Returns the venue tp-order id string. Empty string ONLY on the idempotent
        duplicate path (the leg is already resting from a prior attempt; logged).
        Raises `BitunixAPIError` on non-idempotent errors. Raises
        `BitunixUntrackedTpslOrder` when the POST was accepted (code 0) but no
        orderId could be parsed — the leg likely rested but is now untracked, so
        the caller MUST flag it (never silently treat it as not-placed).

        Response shape (CONFIRMED live, report c8a426d): the docs show a dict
        `{"orderId": ...}` but the live `/tpsl/place_order` returned a LIST
        `[{"orderId": ...}]`. `_extract_tpsl_order_id` parses both defensively.
        """
        if self._stub or not self._client:
            raise NotImplementedError(
                "BitunixBroker.place_tpsl_order: STUB mode (no creds)"
            )
        try:
            wire = to_wire_format(symbol)
        except Exception:
            wire = symbol
        body: dict = {
            "symbol": wire,
            "positionId": str(position_id),
            "tpPrice": _amount_str(tp_price),
            "tpQty": _amount_str(tp_qty),
            "tpStopType": tp_stop_type,
            "tpOrderType": tp_order_type,
            "tpOrderPrice": _amount_str(tp_price),
        }
        idempotent_dup = False
        try:
            data = await self._request(
                "POST", "/api/v1/futures/tpsl/place_order", body=body,
            )
        except BitunixAPIError as e:
            if e.code in _IDEMPOTENT_OK_CODES:
                # Duplicate of an already-resting leg (prior attempt). Known to be
                # on the venue — not a surprise order; no untracked risk.
                data = {}
                idempotent_dup = True
            else:
                raise
        # Defensive parse: docs show a dict {"orderId": ...} but the LIVE endpoint
        # returned a LIST [{"orderId": ...}] (report c8a426d). Tolerate both.
        venue_order_id = _extract_tpsl_order_id(data)
        if venue_order_id:
            log.info(
                "BitUnix tpsl/place_order: venue_order_id=%s positionId=%s "
                "%s tpPrice=%s tpQty=%s",
                venue_order_id, position_id, wire,
                body["tpPrice"], body["tpQty"],
            )
            return venue_order_id
        if idempotent_dup:
            log.warning(
                "BitUnix tpsl/place_order: idempotent duplicate accepted (leg "
                "already resting from a prior attempt) positionId=%s %s tpPrice=%s "
                "tpQty=%s — no orderId returned",
                position_id, wire, body["tpPrice"], body["tpQty"],
            )
            return ""
        # POST was ACCEPTED (code 0) but no orderId could be extracted from an
        # unexpected response shape. The leg has very likely RESTED on the venue
        # but we could not capture its id. Do NOT return "" as if nothing was
        # placed — the reconciler is position-level and will not catch a stray TP
        # order. Log loudly + RAISE so the caller flags it for reconciliation.
        log.error(
            "BitUnix tpsl/place_order: POST accepted but NO orderId in response "
            "%r — TP leg may be RESTING UNTRACKED (positionId=%s %s tpPrice=%s "
            "tpQty=%s); flagging for reconciliation",
            data, position_id, wire, body["tpPrice"], body["tpQty"],
        )
        raise BitunixUntrackedTpslOrder(
            position_id=str(position_id), symbol=wire,
            tp_price=body["tpPrice"], tp_qty=body["tpQty"], raw_response=data,
        )

    async def place_position_tpsl(
        self,
        *,
        symbol: str,
        position_id: str,
        sl_price: float,
        sl_stop_type: str = "MARK_PRICE",
        sl_order_type: str = "MARKET",
    ) -> str:
        """Place the auto-reducing whole-position STOP-LOSS via the native
        `/tpsl/position/place_order` endpoint.

        This is the ONE position-level SL (NO qty) — it "closes based on the
        position quantity AT THAT TIME", so it auto-shrinks as the partial TP
        legs (`place_tpsl_order`) fill. It mirrors the BitUnix UI's *Position
        TP/SL* tab (one SL, no size box) — confirmed by the operator's UI
        network capture. It is the SL the trail (`modify_position_sl` ->
        `/tpsl/position/modify_order`) moves price-only to breakeven / TP1.

        HARD RULE: NO `slQty` — this is position-level and auto-reducing; a qty
        would defeat the auto-reduce. The SL stays a guaranteed-fill MARKET stop
        (`slOrderType=MARKET`, `slStopType=MARK_PRICE`), matching B1's behaviour.

        Coexists with the B1 entry-attached `slPrice` MARKET stop (the always-on
        catastrophic backstop — UNCHANGED). This managed Position SL is the
        trail-able one; B1 is the immutable price-only backstop. Fail-soft at the
        call site: if this placement fails, the TP legs + the B1 entry stop still
        protect the position.

        Returns the venue sl-order id string (empty string on success with no
        id). Raises `BitunixAPIError` on non-idempotent errors; idempotent
        duplicate codes (_IDEMPOTENT_OK_CODES) are silently accepted (same
        positionId+price already resting). STUB mode raises NotImplementedError.

        VERIFY-ON-LIVE: the exact response shape (`orderId` field) and the
        coexistence with the B1 entry stop (no `30038`) are grounded against the
        docs + the UI capture; confirm on the first real multi-leg placement.
        """
        if self._stub or not self._client:
            raise NotImplementedError(
                "BitunixBroker.place_position_tpsl: STUB mode (no creds)"
            )
        try:
            wire = to_wire_format(symbol)
        except Exception:
            wire = symbol
        body: dict = {
            "symbol": wire,
            "positionId": str(position_id),
            "slPrice": _amount_str(sl_price),
            "slStopType": sl_stop_type,
            "slOrderType": sl_order_type,
        }
        try:
            data = await self._request(
                "POST", "/api/v1/futures/tpsl/position/place_order", body=body,
            )
        except BitunixAPIError as e:
            if e.code in _IDEMPOTENT_OK_CODES:
                data = {}
            else:
                raise
        venue_order_id = (data or {}).get("orderId")
        log.info(
            "BitUnix tpsl/position/place_order: venue_order_id=%s positionId=%s "
            "%s slPrice=%s (auto-reducing, no qty)",
            venue_order_id, position_id, wire, body["slPrice"],
        )
        return str(venue_order_id) if venue_order_id else ""

    async def get_pending_orders(self, symbol: str | None = None) -> list[dict]:
        """Return the venue's currently-RESTING (unfilled) orders — for the OCO
        light-verify (no stale SL/TP lingers after a terminal close). Read-only;
        returns [] (never raises) on stub/creds/transient error so callers can
        treat it as 'unknown, skip the verify this tick'.

        VERIFY-ON-LIVE: the exact endpoint/response shape is grounded against the
        BitUnix futures docs but unconfirmed read-only (Phase-C validation).
        """
        if self._stub or not self._client or not self._api_key:
            return []
        query: dict = {}
        if symbol:
            try:
                query["symbol"] = to_wire_format(symbol)
            except Exception:
                query["symbol"] = symbol
        try:
            data = await self._request(
                "GET", "/api/v1/futures/trade/get_pending_orders", query=query,
            )
        except Exception as e:
            log.warning("BitUnix get_pending_orders failed: %s", e)
            return []
        if isinstance(data, dict):
            data = data.get("orderList") or data.get("list") or []
        return list(data or [])

    async def modify_position_sl(
        self,
        symbol: str,
        new_sl_price: float,
        *,
        position_id: str | None = None,
        sl_stop_type: str = "MARK_PRICE",
        sl_order_type: str = "MARKET",
    ) -> bool:
        """Move the attached position STOP-LOSS to `new_sl_price` IN PLACE (no
        cancel-replace naked window). PRICE-ONLY — the venue auto-reduces SL qty
        as TPs fill, so the bot never sets SL qty (hard rule). The SL stays a
        guaranteed-fill MARKET stop (slOrderType=MARKET).

        `position_id` is MANDATORY: if absent or empty, this method returns
        False immediately without calling the venue (a positionId-less SL-move
        would 404 or silently target the wrong position). The caller (reconciler)
        must thread positionId from broker Position.extra.

        Fail-soft: returns True on success, False on any failure or absent
        positionId (NEVER raises) — the SL-move is failure-tolerant (a missed
        move leaves the SL at its prior, still-protective price; the TP already
        filled on-exchange).

        Path fix: uses the correct `/api/v1/futures/tpsl/position/modify_order`
        (the previously used `/tpsl/modify_position_tp_sl_order` returned 404).
        """
        if self._stub or not self._client or not self._api_key:
            return False
        if not position_id:
            log.warning(
                "BitUnix modify_position_sl: positionId absent for %s — "
                "skipping (fail-soft; SL stays at prior price)", symbol,
            )
            return False
        try:
            wire = to_wire_format(symbol)
        except Exception:
            wire = symbol
        body: dict = {
            "symbol": wire,
            "positionId": str(position_id),
            "slPrice": _amount_str(new_sl_price),
            "slStopType": sl_stop_type,
            "slOrderType": sl_order_type,
        }
        try:
            await self._request(
                "POST",
                "/api/v1/futures/tpsl/position/modify_order",
                body=body,
            )
            log.info("BitUnix SL moved (price-only) %s -> %s", wire, body["slPrice"])
            return True
        except Exception as e:
            log.warning(
                "BitUnix modify_position_sl failed (%s -> %s): %s — SL stays at "
                "prior price (failure-tolerant)", wire, body["slPrice"], e,
            )
            return False

    async def modify_position_tp_sl_order(
        self,
        order_id: str,
        new_sl: float,
        new_tp: float | None = None,
    ) -> None:
        """Adjust SL (and optionally TP) on an open position.

        Phase 1 stub — real wiring lands in Phase 4 alongside
        `place_order`. The PR 5 reconciler does NOT call this method;
        it only logs intent via the `position_sl_update` audit row. The
        stub exists so Phase 4 has a stable surface to implement.
        """
        raise NotImplementedError(
            "BitunixBroker.modify_position_tp_sl_order: Phase 1 is "
            "read-only. SL lifecycle decisions are emitted as "
            "`position_sl_update` audit rows by the reconciler; real "
            "BitUnix `/api/v1/futures/tpsl/modify_position_tp_sl_order` "
            "calls land in Phase 4."
        )
