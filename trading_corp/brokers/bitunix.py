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

import json

import httpx

from trading_corp.brokers.base import AccountSnapshot, Broker
from trading_corp.persistence import db
from trading_corp.persistence.models import FillEvent, OpenPosition, Position, ProposedOrder

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

    async def get_funding_rate(self, symbol: str) -> float | None:
        """Return current funding rate for `symbol` as a decimal per 8h
        period (e.g. 0.0001 = 0.01% per 8h). Public endpoint, no auth.

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
