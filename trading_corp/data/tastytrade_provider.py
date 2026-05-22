"""Tastytrade-backed MarketDataProvider — primary production provider.

Auth: env vars TASTYTRADE_PROVIDER_SECRET and TASTYTRADE_REFRESH_TOKEN.
Fail-fast on missing credentials (raises ValueError at construction time,
not lazily).

Uses the `tastyware/tastytrade` SDK (>= 12.4).  One session is created at
construction and reused.  Token refresh is handled by the SDK's session
object.

Chain fetching uses `tastytrade.instruments.get_option_chain` (module-level
function, returns dict[date, list[Option]] with ALL strikes) — NOT
NestedOptionChain.get which returns a narrow view.  Full chain depth is
required so delta-selection can find the actual 16-delta wing; narrow chains
corrupt strike selection.

60-second TTL instance cache: {(method_name, args_tuple): (value, ts)}.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from trading_corp.data._iv_math import _hv_to_rank
from trading_corp.data.market_data_provider import (
    MarketDataProvider,
    OptionContract,
    _is_degenerate_iv,
)

log = logging.getLogger(__name__)

_CACHE_TTL_SEC = 60.0
_GREEKS_STREAM_TIMEOUT_SEC = 25.0


class TastytradeDataProvider(MarketDataProvider):
    """Primary market-data provider backed by Tastytrade / dxFeed.

    Construction fails immediately if the required environment variables
    are missing (TASTYTRADE_PROVIDER_SECRET, TASTYTRADE_REFRESH_TOKEN).
    """

    def __init__(
        self,
        provider_secret: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        ps = provider_secret or os.environ.get("TASTYTRADE_PROVIDER_SECRET")
        rt = refresh_token or os.environ.get("TASTYTRADE_REFRESH_TOKEN")
        if not ps:
            raise ValueError(
                "TastytradeDataProvider requires TASTYTRADE_PROVIDER_SECRET env var "
                "(or provider_secret constructor arg)"
            )
        if not rt:
            raise ValueError(
                "TastytradeDataProvider requires TASTYTRADE_REFRESH_TOKEN env var "
                "(or refresh_token constructor arg)"
            )
        self._provider_secret = ps
        self._refresh_token = rt
        self._session: Any = None
        self._cache: dict[tuple, tuple[Any, float]] = {}

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _get_session(self) -> Any:
        """Return (or lazily create) a Tastytrade session.

        Calls session.validate() on first use; SDK handles token refresh.
        """
        if self._session is None:
            from tastytrade import Session  # type: ignore
            self._session = await asyncio.to_thread(
                Session,
                provider_secret=self._provider_secret,
                refresh_token=self._refresh_token,
            )
        return self._session

    # ------------------------------------------------------------------
    # TTL cache helpers
    # ------------------------------------------------------------------

    def _cache_get(self, key: tuple) -> tuple[bool, Any]:
        entry = self._cache.get(key)
        if entry is None:
            return False, None
        value, ts = entry
        if (time.monotonic() - ts) < _CACHE_TTL_SEC:
            return True, value
        return False, None

    def _cache_set(self, key: tuple, value: Any) -> None:
        self._cache[key] = (value, time.monotonic())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_option_chain(
        self,
        symbol: str,
        expiration: date,
    ) -> list[OptionContract]:
        """Fetch full option chain for `symbol` at `expiration`.

        Uses `tastytrade.instruments.get_option_chain` (module-level
        function) to get ALL strikes — NOT NestedOptionChain.get.
        Subscribes to dxFeed Greeks via DXLinkStreamer with a
        ~25s timeout.  Drops rows whose IV is degenerate.
        """
        cache_key = ("get_option_chain", symbol, expiration.isoformat())
        hit, val = self._cache_get(cache_key)
        if hit:
            return val  # type: ignore[return-value]

        result = await self._fetch_chain(symbol, expiration)
        self._cache_set(cache_key, result)
        return result

    async def _fetch_chain(
        self,
        symbol: str,
        expiration: date,
    ) -> list[OptionContract]:
        try:
            from tastytrade.instruments import get_option_chain  # type: ignore
            from tastytrade.dxfeed import Greeks  # type: ignore
            from tastytrade import DXLinkStreamer  # type: ignore

            session = await self._get_session()

            # get_option_chain returns dict[date, list[Option]] with ALL strikes
            chain_dict = await get_option_chain(session, symbol)
            options_for_exp = chain_dict.get(expiration, [])
            if not options_for_exp:
                log.info(
                    "TastytradeDataProvider: no options for %s %s", symbol, expiration
                )
                return []

            # Subscribe to Greeks for each option via DXLinkStreamer
            streamer_symbols = [o.streamer_symbol for o in options_for_exp]
            greeks_map: dict[str, Any] = {}

            try:
                async with DXLinkStreamer(session) as streamer:
                    await streamer.subscribe(Greeks, streamer_symbols)
                    deadline = asyncio.get_event_loop().time() + _GREEKS_STREAM_TIMEOUT_SEC
                    while len(greeks_map) < len(streamer_symbols):
                        remaining = deadline - asyncio.get_event_loop().time()
                        if remaining <= 0:
                            log.warning(
                                "TastytradeDataProvider: Greeks timeout for %s "
                                "(%d/%d received)",
                                symbol, len(greeks_map), len(streamer_symbols),
                            )
                            break
                        try:
                            greeks = await asyncio.wait_for(
                                streamer.get_event(Greeks),
                                timeout=min(remaining, 5.0),
                            )
                            greeks_map[greeks.event_symbol] = greeks
                        except asyncio.TimeoutError:
                            break
            except Exception as e:
                log.warning(
                    "TastytradeDataProvider: DXLink Greeks failed for %s: %s",
                    symbol, e,
                )

            today = date.today()
            contracts: list[OptionContract] = []
            for opt in options_for_exp:
                g = greeks_map.get(opt.streamer_symbol)
                iv_raw = float(g.volatility) if g and g.volatility is not None else None
                # Boundary guard: drop degenerate IV rows
                if _is_degenerate_iv(iv_raw):
                    iv_val = None
                else:
                    iv_val = iv_raw

                delta_val = float(g.delta) if g and g.delta is not None else None
                gamma_val = float(g.gamma) if g and g.gamma is not None else None
                theta_val = float(g.theta) if g and g.theta is not None else None
                vega_val = float(g.vega) if g and g.vega is not None else None

                try:
                    exp_date = opt.expiration_date
                    if isinstance(exp_date, str):
                        exp_str = exp_date
                    else:
                        exp_str = exp_date.isoformat()
                    dte_val = (date.fromisoformat(exp_str) - today).days
                except Exception:
                    dte_val = None
                    exp_str = expiration.isoformat()

                contracts.append(OptionContract(
                    option_id=opt.streamer_symbol,
                    expiration_date=exp_str,
                    strike=float(opt.strike_price) if opt.strike_price is not None else None,
                    option_type="call" if str(opt.option_type).lower() in ("c", "call") else "put",
                    delta=delta_val,
                    gamma=gamma_val,
                    theta=theta_val,
                    vega=vega_val,
                    iv=iv_val,
                    mark=float(g.price) if g and g.price is not None else None,
                    bid=None,
                    ask=None,
                    bid_size=None,
                    ask_size=None,
                    open_interest=None,
                    volume=None,
                    dte=float(dte_val) if dte_val is not None else None,
                ))
            return contracts

        except Exception as e:
            log.warning(
                "TastytradeDataProvider._fetch_chain: %s %s failed: %s",
                symbol, expiration, e,
            )
            return []

    async def get_atm_iv(
        self,
        symbol: str,
        target_dte: int,
        tolerance_days: int = 7,
    ) -> float | None:
        """Return ATM IV from Tastytrade.

        Picks expiration closest to target_dte within ±tolerance_days.
        Filters degenerate IV via `_is_degenerate_iv`.
        """
        cache_key = ("get_atm_iv", symbol, target_dte, tolerance_days)
        hit, val = self._cache_get(cache_key)
        if hit:
            return val  # type: ignore[return-value]

        result = await self._compute_atm_iv(symbol, target_dte, tolerance_days)
        self._cache_set(cache_key, result)
        return result

    async def _compute_atm_iv(
        self,
        symbol: str,
        target_dte: int,
        tolerance_days: int,
    ) -> float | None:
        try:
            from tastytrade.instruments import get_option_chain  # type: ignore

            session = await self._get_session()
            chain_dict = await get_option_chain(session, symbol)
            if not chain_dict:
                return None

            today = date.today()
            candidates = [
                (exp, abs((exp - today).days - target_dte))
                for exp in chain_dict
                if abs((exp - today).days - target_dte) <= tolerance_days
            ]
            if not candidates:
                return None
            best_exp = min(candidates, key=lambda x: x[1])[0]

            underlying_price = await self.get_underlying_price(symbol)
            if underlying_price is None or underlying_price <= 0:
                return None

            options = chain_dict[best_exp]
            calls = [o for o in options if str(o.option_type).lower() in ("c", "call")]
            if not calls:
                return None

            # ATM = call strike closest to underlying
            atm_opt = min(calls, key=lambda o: abs(float(o.strike_price) - underlying_price))

            # Get Greeks for this one option
            chain_contracts = await self.get_option_chain(symbol, best_exp)
            atm_str = str(float(atm_opt.strike_price))
            for contract in chain_contracts:
                if (
                    contract.option_type == "call"
                    and contract.strike is not None
                    and str(contract.strike) == atm_str
                ):
                    if _is_degenerate_iv(contract.iv):
                        return None
                    return contract.iv

            return None

        except Exception as e:
            log.warning(
                "TastytradeDataProvider.get_atm_iv: %s target_dte=%d ±%d failed: %s",
                symbol, target_dte, tolerance_days, e,
            )
            return None

    async def get_iv_rank(self, symbol: str) -> float | None:
        """Return HV-proxy IV rank.

        Fetches 1-year daily history from Tastytrade if available;
        falls back to yfinance history for the HV series computation only.
        Note: the yfinance fallback here is for raw price history to feed
        into `_hv_to_rank` — NOT for option-chain IV.  The same HV math
        is applied in both paths.

        Returns float | None (None on insufficient history or failure —
        no 0.5 sentinel).

        IMPORTANT: `_is_degenerate_iv` is NOT called on the output of this
        method — IVR is a [0, 1] rank, not an IV value.
        """
        cache_key = ("get_iv_rank", symbol)
        hit, val = self._cache_get(cache_key)
        if hit:
            return val  # type: ignore[return-value]

        result = await self._compute_iv_rank(symbol)
        self._cache_set(cache_key, result)
        return result

    async def _compute_iv_rank(self, symbol: str) -> float | None:
        # Attempt Tastytrade market-data history first; fall back to yfinance
        # for the price series (history-only, not options).
        closes = await self._fetch_close_series(symbol)
        if closes is None:
            return None
        return _hv_to_rank(closes)

    async def _fetch_close_series(self, symbol: str):  # type: ignore[return]
        """Try Tastytrade history endpoint; fall back to yfinance prices."""
        try:
            from tastytrade.market_data import get_history  # type: ignore
            session = await self._get_session()
            bars = await asyncio.to_thread(
                get_history, session, symbol, timeframe="1d", period="1y"
            )
            if bars:
                import pandas as pd  # type: ignore
                closes = pd.Series([b.close for b in bars], dtype=float)
                if len(closes) >= 35:
                    return closes
        except Exception:
            pass

        # Fall back to yfinance for price history only (not for options/IV)
        try:
            def _yf_closes():
                import yfinance as yf  # type: ignore
                hist = yf.Ticker(symbol).history(period="1y")
                return hist["Close"] if len(hist) >= 35 else None

            return await asyncio.to_thread(_yf_closes)
        except Exception as e:
            log.warning(
                "TastytradeDataProvider._fetch_close_series: %s failed: %s",
                symbol, e,
            )
            return None

    async def get_underlying_price(self, symbol: str) -> float | None:
        """Return current underlying price via Tastytrade quote API."""
        cache_key = ("get_underlying_price", symbol)
        hit, val = self._cache_get(cache_key)
        if hit:
            return val  # type: ignore[return-value]

        result = await self._fetch_underlying_price(symbol)
        self._cache_set(cache_key, result)
        return result

    async def _fetch_underlying_price(self, symbol: str) -> float | None:
        # SDK 12.4 has no `get_quote`; `get_market_data(session, symbol,
        # InstrumentType.EQUITY)` returns a `MarketData` with Decimal
        # last/mark/bid/ask fields. EQUITY is correct for the ETFs that
        # currently route through this provider (SPY/IWM/TLT/QQQ).
        try:
            from tastytrade.market_data import get_market_data  # type: ignore
            from tastytrade.order import InstrumentType  # type: ignore
            session = await self._get_session()
            md = await get_market_data(session, symbol, InstrumentType.EQUITY)
            if md is None:
                return None
            price = float(md.last or md.mark or 0)
            return price if price > 0 else None
        except Exception as e:
            log.warning(
                "TastytradeDataProvider.get_underlying_price: %s failed: %s",
                symbol, e,
            )
            return None

    async def get_greeks(
        self,
        option_id: str,
    ) -> dict[str, float | None] | None:
        """Return Greeks for a single option by streamer_symbol."""
        try:
            from tastytrade.dxfeed import Greeks  # type: ignore
            from tastytrade import DXLinkStreamer  # type: ignore

            session = await self._get_session()
            result: dict[str, float | None] = {}

            async with DXLinkStreamer(session) as streamer:
                await streamer.subscribe(Greeks, [option_id])
                try:
                    greeks = await asyncio.wait_for(
                        streamer.get_event(Greeks),
                        timeout=_GREEKS_STREAM_TIMEOUT_SEC,
                    )
                    iv_raw = float(greeks.volatility) if greeks.volatility is not None else None
                    result = {
                        "delta": float(greeks.delta) if greeks.delta is not None else None,
                        "gamma": float(greeks.gamma) if greeks.gamma is not None else None,
                        "theta": float(greeks.theta) if greeks.theta is not None else None,
                        "vega": float(greeks.vega) if greeks.vega is not None else None,
                        "iv": iv_raw if not _is_degenerate_iv(iv_raw) else None,
                        "mark_price": float(greeks.price) if greeks.price is not None else None,
                    }
                except asyncio.TimeoutError:
                    log.warning(
                        "TastytradeDataProvider.get_greeks: timeout for %s", option_id
                    )
                    return None
            return result
        except Exception as e:
            log.warning(
                "TastytradeDataProvider.get_greeks: %s failed: %s", option_id, e
            )
            return None
