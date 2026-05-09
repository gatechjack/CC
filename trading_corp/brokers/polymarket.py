"""Polymarket broker — Phase 1 read-only.

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

import logging
from datetime import datetime, timezone

import httpx

from trading_corp.brokers.base import AccountSnapshot, ReadOnlyBroker
from trading_corp.persistence.models import Position

log = logging.getLogger(__name__)


# Native USDC on Polygon mainnet (NOT USDC.e bridged; Polymarket migrated
# to native USDC). 6 decimals.
_USDC_CONTRACT = "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"
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
