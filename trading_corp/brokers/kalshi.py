"""Kalshi broker — Phase K1 read-only.

Subclasses `ReadOnlyBroker`: there is no `place_order` method on this class.
A code path that tries to place orders against a Kalshi adapter is a static
type error, not a runtime exception. Live order placement is Phase K5+ work
(gated on observed paper PnL > 0 expectancy across Phase K2 arb + Phase K3
copy trading) and will land as a separate `KalshiLiveBroker(Broker)` when
greenlit.

Architecture (Phase K1):

    Account balance  <-- pykalshi AsyncPortfolio.get_balance()
    Positions        <-- pykalshi AsyncPortfolio.get_positions()
    Last price       <-- pykalshi client.get_market(ticker).get_orderbook()

Built on `pykalshi` (arshka/pykalshi, MIT). pykalshi handles RSA-PSS request
signing, rate limiting, and exposes both sync and async clients — we use
async to match Trading Corp's FastAPI/asyncio core.

Auth pattern: Kalshi requires RSA private-key signing on every request.
pykalshi's `AsyncKalshiClient(api_key_id, private_key_path)` takes a
filesystem path to the PEM file. We accept the PEM as a string (from KV
or .env) and materialize it into a restricted-perms tempfile at
`connect()` time, deleting on `disconnect()`. The PEM never lands on disk
in the repo or in the deploy artifact — only in /tmp during process
lifetime.

Stub mode: if either `api_key_id` or `private_key_pem` is missing, the
broker initializes as a STUB returning $0 / no positions. This matches
the BitUnix and Polymarket bring-up patterns — the dashboard tile renders
"online · $0" rather than "not_wired", and the adapter goes live the
moment the KV secrets land.

Demo vs production: pykalshi defaults to production (kalshi.com). Pass
`demo=True` to the constructor to point at the demo environment for
testing without real funds. We default to production; demo is opt-in via
`KALSHI_USE_DEMO=1` env var (handled in main.py wiring, not here).
"""
from __future__ import annotations

import logging
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from trading_corp.brokers.base import AccountSnapshot, ReadOnlyBroker
from trading_corp.persistence.models import Position


@dataclass(frozen=True)
class KalshiPublicTrade:
    """One row of Kalshi's public trade tape for a market.

    Anonymous at the trader level — exposes `taker_side` (the side that
    lifted the resting bid/ask) but no trader identifier. Used by
    `kalshi_copy_trader` for size-match side inference when a tracked
    whale opens a new position.
    """
    ticker: str
    count: int
    yes_price_dollars: float
    no_price_dollars: float
    taker_side: str  # "yes" | "no" | ""
    time: datetime

log = logging.getLogger(__name__)

# pykalshi returns balances in integer cents; divide by 100 for dollars.
_CENTS_PER_DOLLAR = 100


def _to_float(value: object) -> float:
    """Coerce a pykalshi numeric field (often a string like '10.00') to float;
    0.0 on None / empty / non-numeric."""
    try:
        return float(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


class KalshiBroker(ReadOnlyBroker):
    """Read-only Kalshi broker (Phase K1).

    Constructed with API key ID + RSA private key PEM. Without either,
    the broker initializes as a STUB — `snapshot()` returns zeros so the
    dashboard renders "online · $0" rather than "not_wired".
    """

    paper = False  # this broker reads real data; orders are Phase K5+

    def __init__(
        self,
        api_key_id: str | None = None,
        private_key_pem: str | None = None,
        demo: bool = False,
        api_base: str | None = None,
    ) -> None:
        self._api_key_id = api_key_id
        self._private_key_pem = private_key_pem
        self._demo = demo
        # api_base override (K5·1b): None -> pykalshi's built-in default
        # (api.elections.kalshi.com prod / demo-api.kalshi.co demo). The live
        # broker passes the recommended dedicated host (external-api.kalshi.com /
        # external-api.demo.kalshi.co). Existing read-only adapters pass None ->
        # byte-identical behavior.
        self._api_base = api_base
        self.name = "kalshi"
        self._stub = not bool(api_key_id and private_key_pem)
        self._client = None  # AsyncKalshiClient | None
        self._key_path: Path | None = None  # tempfile holding PEM during process lifetime
        self._connected = False

    async def connect(self) -> None:
        if self._stub:
            self._connected = True
            log.info("KalshiBroker connected as STUB (no credentials)")
            return

        # pykalshi import deferred so the rest of the system loads even if
        # the package isn't installed (matches the polymarket pattern).
        from pykalshi import AsyncKalshiClient

        # Materialize the PEM into a restricted-perms tempfile. pykalshi
        # takes a filesystem path, not PEM bytes.
        fd, key_path_str = tempfile.mkstemp(prefix="kalshi_", suffix=".pem")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(self._private_key_pem)
            self._key_path = Path(key_path_str)
            # Restrict perms — owner read/write only. POSIX-only; no-op on Windows.
            try:
                os.chmod(self._key_path, stat.S_IRUSR | stat.S_IWUSR)
            except (OSError, NotImplementedError):
                pass

            self._client = AsyncKalshiClient(
                api_key_id=self._api_key_id,
                private_key_path=str(self._key_path),
                demo=self._demo,
                api_base=self._api_base,
            )

            # Smoke-check: surface auth errors at startup but don't raise —
            # keep the broker functional in stub-fallback mode if Kalshi is
            # unreachable / credentials rejected, so the rest of the system
            # comes up cleanly.
            try:
                bal = await self._client.portfolio.get_balance()
                env_label = "demo" if self._demo else "prod"
                log.info(
                    "KalshiBroker connected (%s) — balance=$%.2f portfolio=$%.2f",
                    env_label,
                    bal.balance / _CENTS_PER_DOLLAR,
                    bal.portfolio_value / _CENTS_PER_DOLLAR,
                )
            except Exception as e:
                log.warning("KalshiBroker connected but smoke-check failed: %s", e)

            self._connected = True
        except Exception:
            # Cleanup tempfile if anything fails before we successfully connect.
            self._cleanup_keyfile()
            raise

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as e:
                log.warning("KalshiBroker aclose failed: %s", e)
            self._client = None
        self._cleanup_keyfile()
        self._connected = False

    def _cleanup_keyfile(self) -> None:
        if self._key_path is not None:
            try:
                self._key_path.unlink(missing_ok=True)
            except Exception as e:
                log.warning("Failed to delete Kalshi PEM tempfile %s: %s", self._key_path, e)
            self._key_path = None

    async def snapshot(self) -> AccountSnapshot:
        """Return account snapshot (cash balance + portfolio value + positions).

        Kalshi's `BalanceModel.balance` is the settled USD cash balance (in
        cents). `portfolio_value` is the sum of mark-to-market on open
        positions, also in cents. Equity = balance + portfolio_value.
        """
        if self._stub or self._client is None:
            return AccountSnapshot(
                account="kalshi:stub",
                equity=0.0,
                buying_power=0.0,
                cash=0.0,
                positions=[],
            )

        try:
            bal = await self._client.portfolio.get_balance()
        except Exception as e:
            log.warning("Kalshi get_balance failed: %s — returning zeros", e)
            return AccountSnapshot(
                account="kalshi",
                equity=0.0,
                buying_power=0.0,
                cash=0.0,
                positions=[],
            )

        cash_usd = bal.balance / _CENTS_PER_DOLLAR
        portfolio_usd = bal.portfolio_value / _CENTS_PER_DOLLAR
        equity_usd = cash_usd + portfolio_usd

        positions = await self._fetch_positions()

        return AccountSnapshot(
            account="kalshi",
            equity=equity_usd,
            buying_power=cash_usd,  # Kalshi has no margin on prediction contracts
            cash=cash_usd,
            positions=positions,
        )

    async def _fetch_positions(self) -> list[Position]:
        """Fetch open positions and map to Trading Corp's Position model.

        Phase K1 returns a best-effort mapping. The pykalshi PositionModel
        shape is verified empirically on first non-empty response from a
        funded account; if the mapping below misses fields, fix here rather
        than working around downstream.
        """
        try:
            df_list = await self._client.portfolio.get_positions(fetch_all=True)
        except Exception as e:
            log.warning("Kalshi get_positions failed: %s — returning empty list", e)
            return []

        out: list[Position] = []
        for p in df_list:
            try:
                # PositionModel fields (pykalshi 1.0.6): ticker, position_fp
                # (signed contract count, fixed-point STRING), market_exposure_dollars
                # (dollar STRING — NOT cents), realized_pnl_dollars,
                # total_traded_dollars, fees_paid_dollars, resting_orders_count,
                # last_updated_ts. (The prior code read `position`/`market_exposure`
                # — nonexistent on 1.0.6 — and built Position with nonexistent
                # `avg_entry_price`/`market_value` kwargs while omitting the required
                # `account`/`opened_ts`; the bare except swallowed the TypeError so
                # this returned [] for every funded account. K5·1 fixes all three.)
                ticker = getattr(p, "ticker", "") or ""
                qty = _to_float(getattr(p, "position_fp", 0))
                if qty == 0:
                    continue
                exposure = _to_float(getattr(p, "market_exposure_dollars", 0))
                avg_price = (exposure / abs(qty)) if qty else 0.0
                out.append(Position(
                    account="kalshi",
                    symbol=ticker,
                    qty=qty,
                    avg_price=avg_price,
                    opened_ts="",  # PositionModel exposes last_updated_ts, not an open ts
                    extra={
                        "market_exposure_dollars": exposure,
                        "realized_pnl_dollars": _to_float(getattr(p, "realized_pnl_dollars", 0)),
                    },
                ))
            except Exception as e:
                log.debug("Failed to map Kalshi position %r: %s", p, e)
                continue
        return out

    async def quote(self, symbol: str) -> float:
        """Return the current mid price for `symbol` (a Kalshi market ticker).

        Kalshi tickers look like `KXBTC-26MAY1218-T100000` (event ticker +
        market suffix). The orderbook gives us bid/ask; we return the mid.
        Returns 0.0 in stub mode or on error — callers must guard against
        zero quotes if they're sizing on price.
        """
        if self._stub or self._client is None:
            return 0.0

        try:
            market = await self._client.get_market(symbol)
            ob = await market.get_orderbook(depth=1)
        except Exception as e:
            log.warning("Kalshi quote failed for %s: %s", symbol, e)
            return 0.0

        # Orderbook structure (pykalshi): yes_bids / yes_asks / no_bids / no_asks
        # arrays of (price_cents, count) tuples, sorted best-first. Mid = mean
        # of best yes-bid and best yes-ask, in dollars. Falls back to whichever
        # side exists if the book is one-sided.
        yes_bid = _best_price(getattr(ob, "yes_bids", None))
        yes_ask = _best_price(getattr(ob, "yes_asks", None))
        if yes_bid is not None and yes_ask is not None:
            return (yes_bid + yes_ask) / 2 / _CENTS_PER_DOLLAR
        if yes_bid is not None:
            return yes_bid / _CENTS_PER_DOLLAR
        if yes_ask is not None:
            return yes_ask / _CENTS_PER_DOLLAR
        return 0.0


    async def get_market_resolution(self, ticker: str) -> dict:
        """Look up resolution status for a Kalshi market by ticker.

        Used by `trading_corp.agents.kalshi_resolver` to score paper trades
        against actual outcomes. Mirrors `PolymarketBroker.get_market_resolution`.

        Returns dict:
            {
              "status":          "resolved" | "pending" | "void" | "not_found",
              "result":          "yes" | "no" | "void" | None,
              "ticker":          str,
              "close_time":      str,      # ISO; '' if unknown
              "expiration_time": str,      # ISO; '' if unknown (expected
                                           # expiration, else scheduled expiration)
            }

        Kalshi resolution decoding: a settled market exposes a non-empty
        `result` field on the MarketModel — values "yes" or "no" for binary
        outcomes, "void" for cancelled markets. While the market is active
        or merely closed-pending-determination, `result` is the empty string.
        We treat empty `result` as "pending" regardless of MarketStatus enum
        value so a market that has stopped trading but isn't yet settled
        keeps retrying on later ticks.

        Stub mode (no credentials) returns `not_found` — the caller skips
        the row, no progress made, no error path tripped.
        """
        if self._stub or self._client is None:
            return {"status": "not_found", "result": None,
                    "ticker": ticker, "close_time": "", "expiration_time": ""}
        try:
            m = await self._client.get_market(ticker)
        except Exception as e:
            log.warning(
                "KalshiBroker.get_market_resolution failed for %s: %s",
                ticker, e,
            )
            return {"status": "not_found", "result": None,
                    "ticker": ticker, "close_time": "", "expiration_time": ""}

        raw_result = (getattr(m, "result", "") or "").strip().lower()
        close_time = getattr(m, "close_time", "") or ""
        # Expected (probabilistic) expiration when the market carries one, else
        # the scheduled expiration. Surfaced so the copy-trader can skip
        # ultra-short markets it can't exit on a 10-min poll. Additive —
        # existing callers ignore it.
        exp_time = (getattr(m, "expected_expiration_time", "")
                    or getattr(m, "expiration_time", "") or "")
        if raw_result in ("yes", "no"):
            return {"status": "resolved", "result": raw_result,
                    "ticker": ticker, "close_time": close_time,
                    "expiration_time": exp_time}
        if raw_result == "void":
            return {"status": "void", "result": "void",
                    "ticker": ticker, "close_time": close_time,
                    "expiration_time": exp_time}
        return {"status": "pending", "result": None,
                "ticker": ticker, "close_time": close_time,
                "expiration_time": exp_time}


    async def list_markets(
        self,
        *,
        categories: tuple[str, ...] | None = None,
        max_series_per_category: int = 30,
        max_markets_per_series: int = 50,
        series_filter: tuple[str, ...] | frozenset[str] | None = None,
    ):
        """Discovery: category -> series -> markets, classified by structural type.

        Returns a `DiscoveryResult` from `kalshi_market_map`. This is the
        broker-level abstraction that strategies (Phase K2.1+) consume —
        same role as `PolymarketBroker.list_markets()` in the polymarket
        pattern. Strategies don't talk to pykalshi directly.

        `series_filter` constrains discovery to an exact-match set of
        series tickers within the requested category(ies). See
        `discover_by_categories` for the rationale.

        Empty result in stub mode (no credentials).
        """
        # Imported here to avoid load-time dependency on kalshi_market_map
        # in environments where this broker is stub-only (e.g. local tests).
        from trading_corp.data.kalshi_market_map import (
            discover_by_categories, DEFAULT_DISCOVERY_CATEGORIES, DiscoveryResult,
        )
        if self._stub or self._client is None:
            return DiscoveryResult(
                events=[], n_markets_total=0, n_markets_filtered_collection=0,
                n_events_total=0, by_type={},
            )
        return await discover_by_categories(
            self._client,
            categories=categories or DEFAULT_DISCOVERY_CATEGORIES,
            max_series_per_category=max_series_per_category,
            max_markets_per_series=max_markets_per_series,
            series_filter=series_filter,
        )


    async def get_market_trades(
        self,
        ticker: str,
        *,
        since: datetime,
        until: datetime,
        limit: int = 100,
    ) -> list[KalshiPublicTrade]:
        """Public trade tape for `ticker` in window [since, until].

        Used by Phase K3 (kalshi_copy_trader) for size-matching side
        detection — when a tracked whale opens a new position with N
        contracts, we look for matching N-contract trades on the public
        tape in the inter-poll window and read `taker_side`.

        Anonymous at trader level. `taker_side` only — no trader identifier.
        Empty list in stub mode or on any pykalshi error (caller short-
        circuits to confidence='low' rather than raising).
        """
        if self._stub or self._client is None:
            return []
        min_ts = int(since.timestamp())
        max_ts = int(until.timestamp())
        try:
            market = await self._client.get_market(ticker)
            trades_df = await market.get_trades(
                min_ts=min_ts, max_ts=max_ts, limit=limit,
            )
        except Exception as e:
            log.warning("KalshiBroker.get_market_trades(%s) failed: %s", ticker, e)
            return []

        out: list[KalshiPublicTrade] = []
        for t in trades_df:
            ts_val = getattr(t, "ts", None)
            if isinstance(ts_val, (int, float)) and ts_val > 0:
                t_dt = datetime.fromtimestamp(int(ts_val), tz=timezone.utc)
            else:
                ct = getattr(t, "created_time", None)
                if isinstance(ct, str) and ct:
                    try:
                        t_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                        if t_dt.tzinfo is None:
                            t_dt = t_dt.replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
                else:
                    continue
            try:
                count = int(float(getattr(t, "count_fp", 0) or 0))
            except (TypeError, ValueError):
                count = 0
            try:
                yes_p = float(getattr(t, "yes_price_dollars", 0) or 0)
            except (TypeError, ValueError):
                yes_p = 0.0
            try:
                no_p = float(getattr(t, "no_price_dollars", 0) or 0)
            except (TypeError, ValueError):
                no_p = 0.0
            side = (getattr(t, "taker_side", "") or "").lower()
            out.append(KalshiPublicTrade(
                ticker=ticker, count=count,
                yes_price_dollars=yes_p, no_price_dollars=no_p,
                taker_side=side, time=t_dt,
            ))
        return out


def _best_price(side) -> int | None:
    """Pull best price (cents) from one side of an orderbook level array."""
    if not side:
        return None
    try:
        first = side[0]
        # Could be a tuple/list (price, count) or a model with `.price`.
        if isinstance(first, (list, tuple)) and first:
            return int(first[0])
        if hasattr(first, "price"):
            return int(first.price)
    except (IndexError, TypeError, ValueError):
        pass
    return None


__all__ = ["KalshiBroker", "KalshiPublicTrade"]
