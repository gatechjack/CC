"""Dual EV-at-fill: taker (at ask) + maker (resting at bid, conservative fill).

Fees via the canonical _sports_math.kalshi_fee on EVERY fill. EV uses the
canonical compute_ev_at_fill_b_directional. A directional bet on the model's
predicted side: side='yes' (UP) with P(win)=model_p, or side='no' (DOWN) with
P(win)=1-model_p.

MAKER FILL MODEL (operator-approved 2026-08-01): resting at the best bid counts
as FILLED iff a later trade prints THROUGH the resting price by >= 1 tick before
window close; otherwise no-fill (excluded). We have 1m candles, not L2 tape
(deferred), so 'trade prints through' is approximated by: a subsequent candle
(ts < window_close) whose side-price LOW <= bid - tick with volume > 0.
★ Every maker-EV aggregate MUST be reported WITH its fill rate (fills/attempts);
   aggregate_maker() enforces this by returning both together.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))))  # repo root for trading_corp import
from trading_corp.agents.strategies._sports_math import (  # noqa: E402
    LegFill, compute_ev_at_fill_b_directional, kalshi_fee,
)

DEFAULT_TICK = 0.01


def _side_prob(model_p: float, side: str) -> float:
    return model_p if side == "yes" else 1.0 - model_p


def _bet_ev(model_p: float, side: str, price: float, qty: float) -> dict | None:
    if price is None or not (0.0 < price <= 1.0):
        return None
    leg = LegFill(venue="kalshi", side=side, qty=qty, price_per_unit=price,
                  fee=kalshi_fee(qty, price))
    r = compute_ev_at_fill_b_directional(leg, _side_prob(model_p, side))
    return {"ev": r.ev_dollars, "cost": r.cost_paid, "price": price, "fee": leg.fee}


def taker_ev(model_p: float, side: str, yes_ask: float | None,
             no_ask: float | None, qty: float = 1.0) -> dict | None:
    """EV buying the predicted side at the ASK (taker)."""
    return _bet_ev(model_p, side, yes_ask if side == "yes" else no_ask, qty)


def maker_filled(side: str, bid_price: float | None, post_candles: list[dict],
                 window_close_ts: int, tick: float = DEFAULT_TICK) -> bool:
    """True iff a later candle trades THROUGH the resting bid by >= 1 tick before close.
    post_candles: [{'ts','yes_low','no_low','volume'}], ts in the same units as window_close_ts."""
    if bid_price is None or bid_price <= 0:
        return False
    thr = bid_price - tick
    for c in post_candles:
        if c.get("ts", 0) >= window_close_ts:
            break
        low = c.get("yes_low") if side == "yes" else c.get("no_low")
        if low is not None and (c.get("volume") or 0) > 0 and low <= thr:
            return True
    return False


def maker_ev(model_p: float, side: str, bid_price: float | None,
             post_candles: list[dict], window_close_ts: int,
             tick: float = DEFAULT_TICK, qty: float = 1.0) -> dict:
    """Attempt a maker fill at the bid. Returns attempted/filled + ev (only if filled)."""
    filled = maker_filled(side, bid_price, post_candles, window_close_ts, tick)
    out = {"attempted": True, "filled": filled, "ev": None, "cost": None, "price": bid_price}
    if filled:
        b = _bet_ev(model_p, side, bid_price, qty)
        if b:
            out.update(ev=b["ev"], cost=b["cost"], fee=b["fee"])
        else:
            out["filled"] = False   # unusable bid price -> treat as no-fill
    return out


def aggregate_maker(maker_results: list[dict]) -> dict:
    """Aggregate maker outcomes. ALWAYS returns fill_rate alongside mean EV — a
    maker-EV number without its fill rate is not a reportable result."""
    attempts = [m for m in maker_results if m.get("attempted")]
    fills = [m for m in attempts if m.get("filled") and m.get("ev") is not None]
    n_att, n_fill = len(attempts), len(fills)
    mean_ev = (sum(m["ev"] for m in fills) / n_fill) if n_fill else float("nan")
    return {"n_attempts": n_att, "n_fills": n_fill,
            "fill_rate": (n_fill / n_att) if n_att else float("nan"),
            "mean_ev_on_fills": mean_ev}


def aggregate_taker(taker_results: list[dict]) -> dict:
    evs = [t["ev"] for t in taker_results if t and t.get("ev") is not None]
    return {"n": len(evs), "mean_ev": (sum(evs) / len(evs)) if evs else float("nan")}


@dataclass
class DualEV:
    taker: dict | None
    maker: dict
