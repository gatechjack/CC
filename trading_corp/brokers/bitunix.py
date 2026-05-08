"""BitUnix Futures broker — Phase 1 read-only.

Phase 1 ships `snapshot()` (account balance + open positions) and `quote()`
against the live BitUnix Futures API. `place_order` and `cancel_order` raise
`NotImplementedError` as a Phase 1 backstop — the live order path lands in
Phase 4 per `trading_corp_bitunix_vision.md` (gated on stop-loss strategy
and conviction → leverage map).

In PAPER mode (default) `trading_corp.main` wraps this broker in
`PaperExecutionBroker`, so snapshots return real BitUnix data while orders
simulate via `PaperBroker`. The `NotImplementedError` raise is defensive —
it only fires if someone constructs an unwrapped `BitunixBroker` and tries
to place an order, which shouldn't happen until Phase 4 lands.

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

import hashlib
import logging
import time
import uuid
from datetime import datetime, timezone

import httpx

from trading_corp.brokers.base import AccountSnapshot, Broker
from trading_corp.persistence.models import FillEvent, Position, ProposedOrder

log = logging.getLogger(__name__)

_BASE_URL = "https://fapi.bitunix.com"
_DEFAULT_TIMEOUT_S = 15.0

# Margin coins to query for total futures equity. Stablecoins only —
# treated as 1:1 USD without conversion. BTC/ETH-margined balances exist
# on BitUnix but need quote conversion to USD; defer until Phase 2+.
_STABLE_MARGIN_COINS = ("USDT", "USDC")


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
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self.name = "bitunix_futures"
        # Stub mode if either credential is missing
        self._stub = not bool(api_key and api_secret)
        self._client: httpx.AsyncClient | None = None
        self._connected = False

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

    async def snapshot(self) -> AccountSnapshot:
        if self._stub or not self._client or not self._api_key or not self._api_secret:
            return AccountSnapshot(
                account="bitunix-stub",
                equity=0.0,
                buying_power=0.0,
                cash=0.0,
                positions=[],
            )

        # ── account balance (sum across stablecoin margin coins) ──────────
        # BitUnix Futures supports per-coin sub-accounts (USDT, USDC, BTC...).
        # `/account` returns balance for one coin at a time; we sum stablecoins
        # to get total futures equity in USD-equivalent terms.
        #
        # Per-coin response shape (verified 2026-05-03 against live API):
        #   available  — free margin, ready to use
        #   frozen     — locked in pending orders
        #   margin     — locked in open positions
        #   transfer   — in-transit balance crediting the futures wallet (NOT
        #                a duplicate of `available`; user-confirmed $1250+$1250
        #                = $2500 reconciled with BitUnix UI)
        #   crossUnrealizedPNL / isolationUnrealizedPNL — floating PnL
        #   bonus      — promotional margin credit
        # All seven sum to total equity for that coin.
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
                continue
            d = ad.get("data") or {}
            if not d:
                continue
            coin_equity = (
                _to_float(d.get("available")) +
                _to_float(d.get("frozen")) +
                _to_float(d.get("margin")) +
                _to_float(d.get("transfer")) +
                _to_float(d.get("crossUnrealizedPNL")) +
                _to_float(d.get("isolationUnrealizedPNL")) +
                _to_float(d.get("bonus"))
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
                # Sign qty by side: SHORT positions render as negative qty
                # so the dashboard's downstream sum/PnL math is consistent.
                side = (p.get("side") or "").upper()
                if side == "SHORT":
                    qty = -abs(qty)
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

        return AccountSnapshot(
            account="bitunix-futures",
            equity=equity,
            buying_power=cash,
            cash=cash,
            positions=positions,
        )

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

    async def place_order(self, order: ProposedOrder) -> FillEvent:
        raise NotImplementedError(
            "BitunixBroker.place_order: Phase 1 is read-only. Live order "
            "placement lands in Phase 4 (gated on stop-loss strategy + "
            "conviction → leverage map). In PAPER mode the order should "
            "have routed to PaperBroker via PaperExecutionBroker — if you "
            "see this raise, the wrapping was bypassed."
        )

    async def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError(
            "BitunixBroker.cancel_order: Phase 1 is read-only. See place_order."
        )
