"""Fidelity Options Division — vertical spreads, iron condors, straddles, calendars.

Strategy selection — IV-rank × regime matrix:

           │ uptrend          │ downtrend        │ neutral
───────────┼──────────────────┼──────────────────┼─────────────────────
 High IV   │ Bull Put Spread  │ Bear Call Spread  │ Iron Condor
 (≥ 0.50)  │ (sell put spread │ (sell call spread │ (sell both sides)
           │  below market)   │  above market)    │
───────────┼──────────────────┼──────────────────┼─────────────────────
 Low IV    │ Bull Call Spread │ Bear Put Spread   │ Calendar Spread
 (< 0.50)  │ (debit)          │ (debit)           │ (time-decay play)

Target DTE: 30–45 days for all strategies.
Sizing: max_loss ≤ per_trade_risk_pct × equity (from risk.yaml).
All orders flow through the standard Risk → CEO → Board approval gate.
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from trading_corp.brokers.base import Broker
from trading_corp.persistence.models import ProposedOrder
from trading_corp.utils.iv import calc_iv_rank as _calc_iv_rank

log = logging.getLogger(__name__)

_RISK_FREE_RATE = 0.045    # approximate; update with current T-bill rate
_TARGET_DTE_LOW = 25
_TARGET_DTE_HIGH = 50
_CAL_NEAR_LOW = 7
_CAL_NEAR_HIGH = 21


# ---------------------------------------------------------------------------
# Black-Scholes delta (stdlib only — no scipy)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_delta(S: float, K: float, T: float, sigma: float, option_type: str) -> float:
    """Black-Scholes delta. T in years, sigma annualized."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (
        math.log(S / K) + (_RISK_FREE_RATE + 0.5 * sigma ** 2) * T
    ) / (sigma * math.sqrt(T))
    return _norm_cdf(d1) if option_type == "call" else _norm_cdf(d1) - 1.0


# ---------------------------------------------------------------------------
# Market data helpers (all via yfinance)
# ---------------------------------------------------------------------------

def _dte(expiry: str) -> int:
    try:
        return max(0, (date.fromisoformat(expiry) - date.today()).days)
    except (ValueError, TypeError):
        return 0


def _pick_expiry(
    expirations: tuple,
    dte_low: int = _TARGET_DTE_LOW,
    dte_high: int = _TARGET_DTE_HIGH,
) -> str | None:
    """Choose the expiry closest to the midpoint of [dte_low, dte_high]."""
    target = (dte_low + dte_high) // 2
    candidates = [e for e in expirations if dte_low <= _dte(e) <= dte_high]
    if candidates:
        return min(candidates, key=lambda e: abs(_dte(e) - target))
    # Expand: nearest date at or above dte_low
    future = sorted((e for e in expirations if _dte(e) >= dte_low), key=_dte)
    return future[0] if future else None


async def _get_expirations(symbol: str) -> tuple:
    import yfinance as yf  # type: ignore
    return await asyncio.to_thread(lambda: yf.Ticker(symbol).options)


async def _get_price(symbol: str) -> float:
    import yfinance as yf  # type: ignore
    def _fn() -> float:
        info = yf.Ticker(symbol).fast_info
        p = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
        return float(p) if p else 0.0
    return await asyncio.to_thread(_fn)


async def _get_chain(symbol: str, expiry: str) -> tuple[list[dict], list[dict]]:
    """Return (calls, puts) as lists of dicts with delta already computed."""
    import yfinance as yf  # type: ignore

    spot = await _get_price(symbol)
    T = _dte(expiry) / 365.0

    def _fn():
        chain = yf.Ticker(symbol).option_chain(expiry)
        return chain.calls, chain.puts

    calls_df, puts_df = await asyncio.to_thread(_fn)

    def _to_list(df, option_type: str) -> list[dict]:
        rows = []
        for _, r in df.iterrows():
            iv = float(r.impliedVolatility or 0)
            if iv <= 0:
                continue
            mid = (float(r.bid or 0) + float(r.ask or 0)) / 2
            if mid <= 0:
                mid = float(r.lastPrice or 0)
            rows.append({
                "strike": float(r.strike),
                "bid": float(r.bid or 0),
                "ask": float(r.ask or 0),
                "mid": mid,
                "iv": iv,
                "volume": int(r.volume or 0),
                "open_interest": int(r.openInterest or 0),
                "in_the_money": bool(r.inTheMoney),
                "delta": _bs_delta(spot, float(r.strike), T, iv, option_type),
            })
        return rows

    return _to_list(calls_df, "call"), _to_list(puts_df, "put")



def _by_delta(chain: list[dict], target_delta: float) -> dict | None:
    """Contract with delta closest to target_delta (uses abs for puts)."""
    pool = [(abs(c["delta"] - abs(target_delta)), c) for c in chain]
    if not pool:
        return None
    pool.sort(key=lambda x: x[0])
    return pool[0][1]


# ---------------------------------------------------------------------------
# ProposedOrder factory
# ---------------------------------------------------------------------------

@dataclass
class _Leg:
    side: str
    option_type: str
    strike: float
    expiry: str
    qty: int
    position_effect: str = "open"


def _spread_order(
    underlying: str,
    legs: list[_Leg],
    strategy: str,
    variant: str,
    net: float,           # positive = credit, negative = debit
    max_loss: float,
    max_gain: float,
    iv_rank: float,
    regime: str,
    rationale: str,
) -> ProposedOrder:
    side = "sell" if net >= 0 else "buy"
    return ProposedOrder(
        strategy="fidelity_options",
        symbol=underlying,
        side=side,   # type: ignore[arg-type]
        qty=float(legs[0].qty),
        order_type="limit",
        limit_price=round(abs(net), 2),
        rationale=rationale,
        extra={
            "is_option": True,
            "is_spread": True,
            "underlying": underlying,
            "strategy": strategy,
            "strategy_variant": variant,
            "legs": [
                {
                    "side": lg.side,
                    "option_type": lg.option_type,
                    "strike": lg.strike,
                    "expiry": lg.expiry,
                    "qty": lg.qty,
                    "position_effect": lg.position_effect,
                }
                for lg in legs
            ],
            "net_debit_credit": round(net, 4),
            "max_loss": round(max_loss, 2),
            "max_gain": round(max_gain, 2),
            "iv_rank": round(iv_rank, 3),
            "regime": regime,
        },
    )


# ---------------------------------------------------------------------------
# FidelityOptionsAgent
# ---------------------------------------------------------------------------

class FidelityOptionsAgent:
    def __init__(
        self,
        strategies_yaml: Path = Path("config/strategies.yaml"),
        risk_yaml: Path = Path("config/risk.yaml"),
    ) -> None:
        self._strategies_yaml = strategies_yaml
        self._risk_yaml = risk_yaml
        self._cfg: dict = {}
        self._risk: dict = {}
        self._reload()

    def _reload(self) -> None:
        try:
            with self._strategies_yaml.open("r", encoding="utf-8") as f:
                self._cfg = (yaml.safe_load(f) or {}).get("fidelity_options", {}) or {}
        except Exception as e:
            log.warning("FidelityOptionsAgent: strategies.yaml load failed: %s", e)
        try:
            with self._risk_yaml.open("r", encoding="utf-8") as f:
                self._risk = yaml.safe_load(f) or {}
        except Exception as e:
            log.warning("FidelityOptionsAgent: risk.yaml load failed: %s", e)

    # -- Config accessors ----------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self._cfg.get("enabled", False))

    @property
    def watchlist(self) -> list[str]:
        return list(self._cfg.get("watchlist", ["SPY", "QQQ", "IWM"]) or [])

    @property
    def _iv_threshold(self) -> float:
        return float(self._cfg.get("iv_rank_threshold", 0.50))

    @property
    def _short_delta(self) -> float:
        return float(self._cfg.get("short_strike_delta", 0.30))

    @property
    def _wing_delta(self) -> float:
        return float(self._cfg.get("long_strike_delta", 0.15))

    @property
    def _risk_pct(self) -> float:
        return float(
            self._risk.get("global", {}).get("per_trade_risk_pct", 0.015)
        )

    # -- Main scan -----------------------------------------------------------

    async def scan(self, broker: Broker, regime: str = "neutral") -> list[ProposedOrder]:
        """Scan the watchlist and propose options strategies.

        `regime` should be the current Trend Agent reading:
        "uptrend" | "downtrend" | "neutral" (or variants "bull"/"bear").
        """
        self._reload()

        if not self.enabled:
            log.info("FidelityOptionsAgent: division disabled in strategies.yaml")
            return []

        snap = await broker.snapshot()
        equity = snap.equity

        orders: list[ProposedOrder] = []
        for sym in self.watchlist:
            try:
                new = await self._scan_symbol(sym, equity, regime)
                orders.extend(new)
            except Exception as e:
                log.warning("FidelityOptionsAgent: %s scan failed: %s", sym, e)


        log.info(
            "FidelityOptionsAgent scan complete: %d order(s) proposed across %s",
            len(orders), self.watchlist,
        )
        return orders

    async def _scan_symbol(
        self, symbol: str, equity: float, regime: str
    ) -> list[ProposedOrder]:
        price, iv_rank, expirations = await asyncio.gather(
            _get_price(symbol),
            _calc_iv_rank(symbol),
            _get_expirations(symbol),
        )
        if price <= 0:
            log.warning("FidelityOptionsAgent: no price for %s; skipping", symbol)
            return []
        if not expirations:
            log.warning("FidelityOptionsAgent: no option expirations for %s; skipping", symbol)
            return []
        if iv_rank is None:
            log.info(
                "FidelityOptionsAgent: IV rank unavailable for %s; skipping", symbol
            )
            return []

        strategy = self._choose_strategy(regime, iv_rank)
        log.info(
            "FidelityOptionsAgent: %s price=%.2f iv_rank=%.2f regime=%s → %s",
            symbol, price, iv_rank, regime, strategy,
        )

        dispatch = {
            "bull_put_spread":   lambda: self._vertical_credit(symbol, price, expirations, iv_rank, regime, equity, "put",  "bull_put_spread"),
            "bear_call_spread":  lambda: self._vertical_credit(symbol, price, expirations, iv_rank, regime, equity, "call", "bear_call_spread"),
            "iron_condor":       lambda: self._iron_condor(symbol, price, expirations, iv_rank, regime, equity),
            "bull_call_spread":  lambda: self._vertical_debit(symbol, price, expirations, iv_rank, regime, equity, "call", "bull_call_spread"),
            "bear_put_spread":   lambda: self._vertical_debit(symbol, price, expirations, iv_rank, regime, equity, "put",  "bear_put_spread"),
            "calendar_spread":   lambda: self._calendar(symbol, price, expirations, iv_rank, regime, equity),
        }
        fn = dispatch.get(strategy)
        return await fn() if fn else []

    def _choose_strategy(self, regime: str, iv_rank: float) -> str:
        high_iv = iv_rank >= self._iv_threshold
        reg = regime.lower()
        if reg in ("uptrend", "bull", "up"):
            return "bull_put_spread" if high_iv else "bull_call_spread"
        if reg in ("downtrend", "bear", "down"):
            return "bear_call_spread" if high_iv else "bear_put_spread"
        return "iron_condor" if high_iv else "calendar_spread"

    # -- Credit vertical spread (sell premium) --------------------------------

    async def _vertical_credit(
        self,
        symbol: str,
        spot: float,
        expirations: tuple,
        iv_rank: float,
        regime: str,
        equity: float,
        option_type: str,
        variant: str,
    ) -> list[ProposedOrder]:
        expiry = _pick_expiry(expirations)
        if not expiry:
            return []
        calls, puts = await _get_chain(symbol, expiry)
        chain = puts if option_type == "put" else calls

        short = _by_delta(chain, self._short_delta)
        long  = _by_delta(chain, self._wing_delta)
        if not short or not long or short["strike"] == long["strike"]:
            return []

        # Ensure correct leg ordering
        if option_type == "put":
            # Bull put: short higher strike, long lower
            if short["strike"] < long["strike"]:
                short, long = long, short
        else:
            # Bear call: short lower strike, long higher
            if short["strike"] > long["strike"]:
                short, long = long, short

        credit = short["mid"] - long["mid"]
        if credit <= 0.05:   # minimum 5¢ credit to bother
            return []

        width = abs(short["strike"] - long["strike"])
        loss_pc = (width - credit) * 100
        gain_pc = credit * 100
        qty = self._size(loss_pc, equity=equity)

        legs = [
            _Leg("sell", option_type, short["strike"], expiry, qty),
            _Leg("buy",  option_type, long["strike"],  expiry, qty),
        ]
        dte = _dte(expiry)
        return [_spread_order(
            underlying=symbol,
            legs=legs,
            strategy="vertical_spread",
            variant=variant,
            net=credit,
            max_loss=loss_pc * qty,
            max_gain=gain_pc * qty,
            iv_rank=iv_rank,
            regime=regime,
            rationale=(
                f"{variant.replace('_', ' ').title()}: {symbol} {expiry} ({dte}DTE) "
                f"short {option_type[0].upper()}{short['strike']:.2f} / "
                f"long {option_type[0].upper()}{long['strike']:.2f} "
                f"@ ${credit:.2f} credit, max_loss=${loss_pc:.2f}/ct, "
                f"IV_rank={iv_rank:.0%}"
            ),
        )]

    # -- Debit vertical spread (buy direction) --------------------------------

    async def _vertical_debit(
        self,
        symbol: str,
        spot: float,
        expirations: tuple,
        iv_rank: float,
        regime: str,
        equity: float,
        option_type: str,
        variant: str,
    ) -> list[ProposedOrder]:
        expiry = _pick_expiry(expirations)
        if not expiry:
            return []
        calls, puts = await _get_chain(symbol, expiry)
        chain = calls if option_type == "call" else puts

        # Long ATM (~0.50 delta), short OTM (~0.30 delta)
        long_c  = _by_delta(chain, 0.50)
        short_c = _by_delta(chain, self._short_delta)
        if not long_c or not short_c or long_c["strike"] == short_c["strike"]:
            return []

        # Ordering: for bull call long < short; for bear put long > short
        if option_type == "call" and long_c["strike"] >= short_c["strike"]:
            return []
        if option_type == "put"  and long_c["strike"] <= short_c["strike"]:
            return []

        debit = long_c["mid"] - short_c["mid"]
        if debit <= 0.05:
            return []

        width = abs(long_c["strike"] - short_c["strike"])
        loss_pc = debit * 100
        gain_pc = (width - debit) * 100
        qty = self._size(loss_pc, equity=equity)

        legs = [
            _Leg("buy",  option_type, long_c["strike"],  expiry, qty),
            _Leg("sell", option_type, short_c["strike"], expiry, qty),
        ]
        dte = _dte(expiry)
        return [_spread_order(
            underlying=symbol,
            legs=legs,
            strategy="vertical_spread",
            variant=variant,
            net=-debit,
            max_loss=loss_pc * qty,
            max_gain=gain_pc * qty,
            iv_rank=iv_rank,
            regime=regime,
            rationale=(
                f"{variant.replace('_', ' ').title()}: {symbol} {expiry} ({dte}DTE) "
                f"long {option_type[0].upper()}{long_c['strike']:.2f} / "
                f"short {option_type[0].upper()}{short_c['strike']:.2f} "
                f"@ ${debit:.2f} debit, max_loss=${loss_pc:.2f}/ct, "
                f"IV_rank={iv_rank:.0%}"
            ),
        )]

    # -- Iron Condor ----------------------------------------------------------

    async def _iron_condor(
        self,
        symbol: str,
        spot: float,
        expirations: tuple,
        iv_rank: float,
        regime: str,
        equity: float,
    ) -> list[ProposedOrder]:
        expiry = _pick_expiry(expirations)
        if not expiry:
            return []
        calls, puts = await _get_chain(symbol, expiry)

        sp = _by_delta(puts,  self._short_delta)   # short put
        lp = _by_delta(puts,  self._wing_delta)    # long put (further OTM)
        sc = _by_delta(calls, self._short_delta)   # short call
        lc = _by_delta(calls, self._wing_delta)    # long call (further OTM)

        if not all([sp, lp, sc, lc]):
            return []

        # Put side: short higher strike, long lower
        if sp["strike"] < lp["strike"]:
            sp, lp = lp, sp
        # Call side: short lower strike, long higher
        if sc["strike"] > lc["strike"]:
            sc, lc = lc, sc

        put_credit  = sp["mid"] - lp["mid"]
        call_credit = sc["mid"] - lc["mid"]
        total_credit = put_credit + call_credit
        if total_credit <= 0.10:
            return []

        put_width  = abs(sp["strike"] - lp["strike"])
        call_width = abs(lc["strike"] - sc["strike"])
        max_width  = max(put_width, call_width)
        loss_pc = (max_width - total_credit) * 100
        gain_pc = total_credit * 100
        qty = self._size(loss_pc, equity=equity)

        legs = [
            _Leg("sell", "put",  sp["strike"], expiry, qty),
            _Leg("buy",  "put",  lp["strike"], expiry, qty),
            _Leg("sell", "call", sc["strike"], expiry, qty),
            _Leg("buy",  "call", lc["strike"], expiry, qty),
        ]
        dte = _dte(expiry)
        return [_spread_order(
            underlying=symbol,
            legs=legs,
            strategy="iron_condor",
            variant="iron_condor",
            net=total_credit,
            max_loss=loss_pc * qty,
            max_gain=gain_pc * qty,
            iv_rank=iv_rank,
            regime=regime,
            rationale=(
                f"Iron Condor: {symbol} {expiry} ({dte}DTE) "
                f"P{lp['strike']:.2f}/{sp['strike']:.2f}–"
                f"{sc['strike']:.2f}/{lc['strike']:.2f}C "
                f"@ ${total_credit:.2f} credit, max_loss=${loss_pc:.2f}/ct, "
                f"IV_rank={iv_rank:.0%}"
            ),
        )]

    # -- Calendar Spread ------------------------------------------------------

    async def _calendar(
        self,
        symbol: str,
        spot: float,
        expirations: tuple,
        iv_rank: float,
        regime: str,
        equity: float,
    ) -> list[ProposedOrder]:
        near_expiry = _pick_expiry(expirations, _CAL_NEAR_LOW, _CAL_NEAR_HIGH)
        far_expiry  = _pick_expiry(expirations)
        if not near_expiry or not far_expiry or near_expiry == far_expiry:
            return []

        near_calls, _ = await _get_chain(symbol, near_expiry)
        far_calls,  _ = await _get_chain(symbol, far_expiry)
        if not near_calls or not far_calls:
            return []

        # ATM strike for both legs
        atm_near = min(near_calls, key=lambda c: abs(c["strike"] - spot))
        strike = atm_near["strike"]
        atm_far  = next((c for c in far_calls if c["strike"] == strike), None)
        if not atm_far:
            atm_far = min(far_calls, key=lambda c: abs(c["strike"] - spot))

        debit = atm_far["mid"] - atm_near["mid"]
        if debit <= 0.05:
            return []

        loss_pc = debit * 100
        # Max gain on a calendar is hard to cap; approximate as 1× debit
        gain_pc = debit * 100
        qty = self._size(loss_pc, equity=equity)

        legs = [
            _Leg("sell", "call", strike, near_expiry, qty),
            _Leg("buy",  "call", strike, far_expiry,  qty),
        ]
        near_dte = _dte(near_expiry)
        far_dte  = _dte(far_expiry)
        return [_spread_order(
            underlying=symbol,
            legs=legs,
            strategy="calendar_spread",
            variant="calendar_spread",
            net=-debit,
            max_loss=loss_pc * qty,
            max_gain=gain_pc * qty,
            iv_rank=iv_rank,
            regime=regime,
            rationale=(
                f"Calendar Spread: {symbol} C{strike:.2f} "
                f"sell {near_expiry} ({near_dte}DTE) / buy {far_expiry} ({far_dte}DTE) "
                f"@ ${debit:.2f} debit, max_loss=${loss_pc:.2f}/ct, "
                f"IV_rank={iv_rank:.0%}"
            ),
        )]

    # -- Sizing ---------------------------------------------------------------

    def _size(self, max_loss_per_contract: float, equity: float | None) -> int:
        """Contracts s.t. total max_loss ≤ per_trade_risk_pct × equity.

        equity=None uses a conservative $50k placeholder when the real equity
        is not threaded through (e.g. in unit tests).
        """
        if max_loss_per_contract <= 0:
            return 1
        eq = equity if equity and equity > 0 else 50_000.0
        budget = eq * self._risk_pct
        return max(1, int(budget / max_loss_per_contract))
