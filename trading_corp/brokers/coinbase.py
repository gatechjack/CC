"""Coinbase Advanced Trade broker — Phase A: spot read-only via ccxt.

Two operating modes hit different APIs and require SEPARATE API keys:

  mode="spot"     — ccxt's coinbase exchange driver (Advanced Trade API)
                    creds: COINBASE_API_KEY / COINBASE_API_SECRET
                    Status: Phase A wired — real connect/snapshot/quote.
                            place_order still stubbed (Phase B).

  mode="futures"  — Coinbase nano BTC/ETH FCM futures
                    creds: COINBASE_FUTURES_API_KEY / COINBASE_FUTURES_API_SECRET
                    Status: stub. Real wiring waits for Phase C, which uses
                            the official `coinbase-advanced-py` SDK because
                            ccxt's coinbase driver doesn't fully cover US FCM
                            futures (different endpoint, position model,
                            margin semantics).

The credential split is intentional: a spot key gets 401-rejected on futures
endpoints (different portfolio scope at the Coinbase level), and vice versa.
Selection of which credential set to use happens in
trading_corp.main._build_broker_for_division based on the division's
account_filter ("spot" | "futures") — this constructor just receives whatever
key/secret the caller passed.

CDP key format note:
  Coinbase's new CDP keys are EC P-256 private keys in PEM format. The .env
  file typically stores the multi-line PEM with literal `\\n` escapes on a
  single line, since dotenv doesn't natively support multi-line values.
  We normalize that back to real newlines in `_normalize_pem` so ccxt sees
  a valid PEM string.

PASSPHRASE is only used for legacy Coinbase Pro keys; new CDP-style keys
leave it empty.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from trading_corp.brokers.base import AccountSnapshot, Broker
from trading_corp.persistence.models import FillEvent, Position, ProposedOrder

log = logging.getLogger(__name__)


# Stable-coin / fiat symbols treated as "cash" rather than positions on the
# spot snapshot. We sum these into AccountSnapshot.cash so the dashboard
# matches Coinbase's own "USD value" tile without surfacing $1.00 USDC as a
# tradeable position.
_CASH_LIKE = {"USD", "USDC", "USDT", "DAI", "PYUSD"}

# Positions worth less than this in USD are filtered out of snapshots.
# Coinbase accounts often accumulate dust (sub-cent amounts of obscure
# tokens from old airdrops/promotions). Including them clutters the
# dashboard and confuses the portfolio view. The cutoff is conservative
# — anything you can actually trade clears it easily.
_DUST_THRESHOLD_USD = 1.00


def _normalize_pem(secret: str) -> str:
    """Convert a single-line `\\n`-escaped PEM string to a real multi-line PEM.

    .env files commonly store PEM keys as a single line with literal `\\n`
    escapes because dotenv's parser doesn't handle multi-line values. ccxt
    expects the raw PEM (real newlines), so we expand. If the input is
    already multi-line (real newlines), it passes through unchanged.
    """
    if not secret:
        return secret
    if "\\n" in secret and "\n" not in secret:
        return secret.replace("\\n", "\n")
    return secret


class CoinbaseBroker(Broker):
    """One Coinbase broker instance per mode (spot / futures).

    The two modes use different credentials and (in Phase C) different SDK
    paths. They are decoupled at the broker level so spot can ship to LIVE
    without waiting on FCM futures wiring.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        passphrase: str | None = None,
        mode: str = "spot",
    ) -> None:
        self._api_key = api_key
        self._api_secret = _normalize_pem(api_secret) if api_secret else api_secret
        self._passphrase = passphrase
        self._mode = mode.lower().strip()
        if self._mode not in ("spot", "futures"):
            raise ValueError(f"CoinbaseBroker mode must be 'spot' or 'futures', got {mode!r}")
        self.name = f"coinbase_{self._mode}"
        # Paper-mode flag kept on instance because both stub and real instances
        # may want to claim "paper" depending on credentials.
        self.paper = not bool(api_key and api_secret)
        self._exchange = None  # ccxt async client; None when in stub mode
        self._connected = False
        self._markets_loaded = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if not self._api_key or not self._api_secret:
            # No credentials → connect as a stub. Snapshot returns zeros so
            # the dashboard shows "online · $0" rather than "not_wired".
            self._connected = True
            log.info(
                "CoinbaseBroker(%s) connected as STUB (no credentials)",
                self._mode,
            )
            return

        if self._mode == "futures":
            # FCM futures live behind a different API surface than ccxt's
            # coinbase driver covers (different signing scope, different
            # position model). Keep this as a stub until Phase C wires
            # coinbase-advanced-py. The credentials are validated *now*
            # only at the format level — actual auth check happens when we
            # first call the FCM endpoint in Phase C.
            self._connected = True
            log.info(
                "CoinbaseBroker(futures) connected as STUB "
                "(FCM impl pending — credentials present but not used yet)"
            )
            return

        # Spot path: real ccxt connection.
        import ccxt.async_support as ccxt_async  # local import; keeps cold-start cheap

        self._exchange = ccxt_async.coinbase({
            "apiKey": self._api_key,
            "secret": self._api_secret,
            "enableRateLimit": True,
            # Market-BUY semantics intentionally LEFT AT DEFAULT (True).
            # We tried `createMarketBuyOrderRequiresPrice: False` so ccxt
            # would send `base_size` for market buys — that path is rejected
            # by Coinbase retail spot accounts with UNSUPPORTED_ORDER_
            # CONFIGURATION (only `quote_size` is accepted there). With the
            # default, the caller must pass a `price` arg for market buys
            # and ccxt computes quote_size = amount * price. We fetch a
            # live mark in place_order() to satisfy this — see comment
            # there for the full pattern.
        })
        # load_markets() is required before fetch_ticker on most ccxt drivers
        # so symbols can be looked up by their unified format ("BTC/USD").
        # We do it here (not lazily) so any auth/network failure surfaces at
        # startup, not silently mid-snapshot.
        try:
            await self._exchange.load_markets()
            self._markets_loaded = True
        except Exception as e:
            log.warning(
                "CoinbaseBroker(spot) load_markets failed: %s — will retry on first call",
                e,
            )
            # Don't block startup; snapshot/quote will retry. The dashboard
            # will show $0 with a warning until the first successful fetch.
        self._connected = True
        log.info(
            "CoinbaseBroker(spot) connected (markets_loaded=%s)",
            self._markets_loaded,
        )

    async def disconnect(self) -> None:
        if self._exchange is not None:
            try:
                await self._exchange.close()
            except Exception:
                pass
        self._connected = False

    # ------------------------------------------------------------------
    # Account data
    # ------------------------------------------------------------------

    async def snapshot(self) -> AccountSnapshot:
        """Return an account snapshot.

        Stub mode (no creds, OR futures-not-yet-wired) → empty snapshot so
        the dashboard tile renders "online · $0" instead of "not_wired".
        Spot mode with creds → real fetch_balance + per-asset USD valuation.
        """
        if not self._connected:
            raise RuntimeError(f"CoinbaseBroker({self._mode}) not connected")

        # Stub fallback covers: (a) no creds, (b) futures mode pre-Phase-C.
        if self._exchange is None:
            return AccountSnapshot(
                account=f"coinbase_{self._mode}",
                equity=0.0,
                buying_power=0.0,
                cash=0.0,
                positions=[],
            )

        # Lazy-load markets if startup retry is needed.
        if not self._markets_loaded:
            try:
                await self._exchange.load_markets()
                self._markets_loaded = True
            except Exception as e:
                log.warning("Coinbase load_markets retry failed: %s", e)

        # Pull balances. ccxt returns a dict where keys are asset codes
        # (BTC, ETH, USD, ...) and values are {"free": x, "used": y, "total": z}.
        # The "info" / "free" / "used" / "total" keys are aggregate views we skip.
        try:
            bal = await self._exchange.fetch_balance()
        except Exception as e:
            log.warning("Coinbase fetch_balance failed: %s", e)
            return AccountSnapshot(
                account=f"coinbase_{self._mode}",
                equity=0.0,
                buying_power=0.0,
                cash=0.0,
                positions=[],
            )

        cash_usd = 0.0
        for cash_code in _CASH_LIKE:
            entry = bal.get(cash_code)
            if isinstance(entry, dict):
                try:
                    cash_usd += float(entry.get("total") or 0.0)
                except (TypeError, ValueError):
                    pass

        # Walk non-cash holdings, value each in USD via fetch_ticker.
        positions: list[Position] = []
        total_position_value = 0.0
        opened_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

        for code, entry in bal.items():
            if not isinstance(entry, dict):
                continue
            if code in {"info", "free", "used", "total", "timestamp", "datetime"}:
                continue
            if code in _CASH_LIKE:
                continue
            try:
                qty = float(entry.get("total") or 0.0)
            except (TypeError, ValueError):
                continue
            if qty <= 0:
                continue

            # Coinbase typically lists USD pairs as e.g. "BTC/USD" in ccxt's
            # unified symbol format. If a pair doesn't exist, value=0 (the
            # asset will show as "qty held, $0 valued" — better than crashing).
            unified = f"{code}/USD"
            last = 0.0
            try:
                ticker = await self._exchange.fetch_ticker(unified)
                last = float(ticker.get("last") or ticker.get("close") or 0.0)
            except Exception:
                # Some assets only trade vs USDC/USDT/BTC. Try USDC then BTC.
                for fallback in (f"{code}/USDC", f"{code}/USDT"):
                    try:
                        ticker = await self._exchange.fetch_ticker(fallback)
                        last = float(ticker.get("last") or ticker.get("close") or 0.0)
                        if last > 0:
                            break
                    except Exception:
                        continue

            value = qty * last
            # Skip dust — sub-$1 positions are typically airdrop/promo
            # leftovers that don't belong on the dashboard. We still count
            # them in equity (above) only if their value is non-trivial; a
            # $0.00 dust position contributes nothing to equity anyway.
            if value < _DUST_THRESHOLD_USD:
                continue
            total_position_value += value

            positions.append(Position(
                account=f"coinbase_{self._mode}",
                symbol=unified,
                qty=qty,
                # ccxt fetch_balance doesn't return cost basis. Using mark
                # price means "unrealized P&L" calculations elsewhere will
                # show $0 — that's correct for a balance-only snapshot. Real
                # cost basis would require fetch_my_trades + FIFO replay,
                # which we'll add when we start placing orders (Phase B —
                # we'll have our own fill records by then).
                avg_price=last,
                opened_ts=opened_ts,
                extra={
                    "market_value_usd": value,
                    "asset": code,
                    "venue": "coinbase",
                    "asset_type": "crypto",
                },
            ))

        equity = cash_usd + total_position_value
        return AccountSnapshot(
            account=f"coinbase_{self._mode}",
            equity=equity,
            # Spot has no margin: buying power = available cash.
            buying_power=cash_usd,
            cash=cash_usd,
            positions=positions,
        )

    # ------------------------------------------------------------------
    # Order placement (Phase B for spot; Phase C will add futures)
    # ------------------------------------------------------------------

    async def place_order(self, order: ProposedOrder) -> FillEvent:
        """Submit a spot order via ccxt's unified create_order interface.

        Supports market and limit orders. Symbol can be either Coinbase
        native ("BTC-USD") or ccxt unified ("BTC/USD"); we normalize to
        the unified format for ccxt.

        Returns a FillEvent constructed from ccxt's response. We do NOT
        poll for fill — for limit orders the response is "open" and the
        FillEvent carries qty=0/the limit_price (caller treats as "order
        accepted, waiting for fill"). For market orders Coinbase typically
        returns a filled response within the create_order call itself.
        Matches the pattern in RobinhoodBroker.

        Raises:
            RuntimeError    if not connected
            NotImplementedError if futures mode (Phase C) or stub mode
            ValueError      if order params are invalid
        """
        if not self._connected:
            raise RuntimeError(f"CoinbaseBroker({self._mode}) not connected")

        if self._exchange is None:
            # Either futures-mode-pre-Phase-C, or no creds. Either way we
            # cannot place an order. Raising here surfaces the gap clearly
            # rather than silently succeeding with a stub fill.
            raise NotImplementedError(
                f"CoinbaseBroker({self._mode}).place_order unavailable: "
                + ("futures wiring is Phase C" if self._mode == "futures"
                   else "broker is in stub mode (no credentials)")
            )

        # Normalize symbol. Strategy code may emit "BTC-USD" (Coinbase native
        # display format) but ccxt expects "BTC/USD" (its unified format).
        # Accept both. Uppercase for safety.
        symbol = order.symbol.replace("-", "/").upper()

        side = (order.side or "").lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"Invalid order side: {order.side!r}")

        order_type = (order.order_type or "market").lower()
        if order_type not in ("market", "limit"):
            raise ValueError(
                f"Unsupported order type for Coinbase: {order.order_type!r} "
                "(expected 'market' or 'limit')"
            )

        if order_type == "limit" and not order.limit_price:
            raise ValueError("Limit order requires limit_price")

        # Round qty to the symbol's step size. Coinbase rejects orders with
        # too-precise quantities (e.g., 1e-12 BTC). ccxt's amount_to_precision
        # uses the market metadata loaded by load_markets() to pick the right
        # number of decimals. Falling back to the raw qty is acceptable —
        # the broker will reject with a clear error if it's wrong.
        try:
            amount = float(self._exchange.amount_to_precision(symbol, order.qty))
        except Exception:
            amount = float(order.qty)

        if amount <= 0:
            raise ValueError(
                f"Order qty {order.qty} rounded to {amount} (below symbol minimum)"
            )

        # Resolve the price we send to ccxt:
        #   - limit orders     → the user-supplied limit price
        #   - market BUY (spot)→ a freshly-fetched mark price. Coinbase
        #                        retail spot only accepts quote_size (USD)
        #                        for market buys; ccxt computes that as
        #                        amount * price under the hood. Without a
        #                        mark, ccxt either errors out (default
        #                        behavior) or sends base_size which
        #                        Coinbase rejects with
        #                        UNSUPPORTED_ORDER_CONFIGURATION. So we
        #                        fetch a live ticker right before placement.
        #   - market SELL      → ccxt sends base_size (qty in BTC), no
        #                        price needed. Coinbase accepts this for
        #                        sells.
        if order_type == "limit":
            price_arg: float | None = order.limit_price
        elif side == "buy":
            try:
                ticker = await self._exchange.fetch_ticker(symbol)
                mark_price = float(
                    ticker.get("last") or ticker.get("close") or 0.0
                )
            except Exception as e:
                raise RuntimeError(
                    f"Market BUY: could not fetch mark price for {symbol}: {e}"
                )
            if mark_price <= 0:
                raise RuntimeError(
                    f"Market BUY: invalid mark price {mark_price} for {symbol}"
                )
            price_arg = mark_price
        else:
            price_arg = None  # market sell

        log.info(
            "CoinbaseBroker(spot) placing order: %s %s %s %.8f @ %s [order_id=%s]",
            order_type, side, symbol, amount,
            f"${price_arg}" if price_arg else "MKT",
            order.id,
        )

        try:
            result = await self._exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side,
                amount=amount,
                price=price_arg,
            )
        except Exception as e:
            log.error(
                "CoinbaseBroker(spot) create_order failed [order_id=%s]: %s",
                order.id, e,
            )
            raise

        # Parse ccxt's response. Field availability varies by exchange:
        #   "id"       — venue's order ID (always)
        #   "status"   — "open" / "closed" / "canceled"
        #   "filled"   — amount filled so far (may be 0 for fresh limit orders)
        #   "average"  — VWAP of fills (None if nothing filled yet)
        #   "price"    — limit price (for limit orders) or last trade
        # Fall through to limit_price → 0.0 for the rare case of a market
        # order that returned no price info. The caller (DataExecAgent) logs
        # whatever we return verbatim, so 0.0 is detectable.
        result = result or {}
        venue_order_id = result.get("id", "")
        status = result.get("status", "unknown")
        fill_price = float(
            result.get("average")
            or result.get("price")
            or order.limit_price
            or 0.0
        )
        fill_qty = float(result.get("filled") or 0.0)
        # For limit orders that haven't filled yet, return the requested qty
        # rather than 0 — the FillEvent represents "order placed for X qty",
        # and downstream logging shouldn't show qty=0 just because the book
        # hasn't crossed yet. This matches Robinhood's behavior.
        if fill_qty == 0:
            fill_qty = amount

        log.info(
            "CoinbaseBroker(spot) order accepted: venue_id=%s status=%s "
            "filled=%.8f avg=$%.4f [order_id=%s]",
            venue_order_id, status, float(result.get("filled") or 0.0),
            fill_price, order.id,
        )

        # Encode ccxt status in the venue suffix so downstream renderers
        # can distinguish "really filled" from "accepted, on the book".
        # Without this every limit order shows up as "FILLED" even when
        # it's just sitting on the order book waiting for a counterparty
        # — which is misleading for the Board's audit view. Convention:
        #   coinbase_spot           → fully filled (status=closed)
        #   coinbase_spot:open      → accepted, resting on book
        #   coinbase_spot:dry-run   → DataExecAgent dry-run synthetic
        #   coinbase_spot:<other>   → unusual ccxt status (canceled, etc.)
        # Matches the pre-existing :dry-run convention so existing renderer
        # logic that splits on ":" still works.
        venue = f"coinbase_{self._mode}"
        if status and status not in ("closed", "filled", None):
            venue = f"{venue}:{status}"

        return FillEvent(
            order_id=order.id,
            symbol=symbol,
            side=order.side,
            qty=fill_qty,
            price=fill_price,
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            venue=venue,
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by its venue ID.

        Returns True on success, False on any failure. We don't raise here
        because cancel_order is often called as cleanup in error paths and
        the caller usually doesn't have a meaningful recovery path.
        """
        if not self._connected or self._exchange is None:
            return False
        try:
            # ccxt's coinbase driver supports cancel-by-ID without requiring
            # the symbol. Other ccxt drivers do require it; if we ever swap
            # exchanges and this breaks, we'll need to track symbol→order_id.
            await self._exchange.cancel_order(order_id)
            log.info("CoinbaseBroker(spot) canceled order %s", order_id)
            return True
        except Exception as e:
            log.warning(
                "CoinbaseBroker(spot) cancel_order(%s) failed: %s",
                order_id, e,
            )
            return False

    async def quote(self, symbol: str) -> float:
        """Return the last/close price for a unified symbol (e.g. 'BTC/USD').

        Returns 0.0 in stub mode, or if the ticker can't be fetched. Callers
        already treat 0.0 as "no quote" elsewhere in the codebase.
        """
        if not self._connected or self._exchange is None:
            return 0.0
        try:
            ticker = await self._exchange.fetch_ticker(symbol)
            return float(ticker.get("last") or ticker.get("close") or 0.0)
        except Exception as e:
            log.warning("Coinbase quote failed for %s: %s", symbol, e)
            return 0.0
