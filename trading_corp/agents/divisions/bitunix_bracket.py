"""Exchange-resting bracket-exit logic (pure, venue-agnostic).

The deterministic core of the bracket redesign (scope
reports/2026-06-16_bracket_exit_redesign_scope.md): given a filled entry's
qty + tp_plan, compute the reduce-only LIMIT take-profit legs to REST on the
exchange (SL stays the atomic market-stop attached at entry), and decide how to
MOVE the SL price as TP legs fill. Venue I/O lives in the broker/observer/
reconciler; this module is pure so it is exhaustively unit-testable.

Design (operator-confirmed venue behaviour, API-path confirmed by the Phase-C
live validation):
  * SL = market-stop, attached at entry (UNCHANGED). The bot moves its PRICE
    only; the venue auto-reduces its QTY as TPs fill.
  * TP1/2/3 = reduce-only LIMIT resting orders, split by the tp_plan fractions.
  * Native OCO: a TP fill leaves the SL (auto-reduced); the final close cancels
    the rest. The bot does NOT cancel counter-orders — it only moves the SL.

Board min-leg rule: every PLACED leg must be >= MIN_LEG_QTY_BTC (0.0003 BTC).
Too-small positions DEGRADE to fewer, larger legs — never a sub-min leg.

SL-move hybrid ((b)+(c), tp_plan default stop_action): TP1 filled -> SL to
breakeven; TP1+TP2 filled -> SL to TP1; never loosened (tighten-only).
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_LEG_QTY_BTC: float = 0.0003  # Board rule: no placed leg below this.


@dataclass(frozen=True)
class BracketLeg:
    leg: str          # "tp1" | "tp2" | "tp3"
    price: float
    qty: float


def _round_qty(q: float) -> float:
    # BTC-perp qty precision (entry qtys are ~7 dp, e.g. 0.0003768). Keep 7 dp;
    # the caller hands the remainder to the last leg so the sum == position qty.
    return round(q, 7)


def build_bracket_legs(
    entry_qty: float,
    tp_plan: list[dict],
    *,
    min_leg: float = MIN_LEG_QTY_BTC,
) -> tuple[list[BracketLeg], str]:
    """Split `entry_qty` into reduce-only TP legs at the tp_plan prices/fractions,
    degrading to fewer (larger) legs so EVERY placed leg >= `min_leg`.

    Returns (legs, degrade_note). The legs' qtys always sum to `entry_qty` (full
    TP coverage); the last leg absorbs rounding remainder. Empty list when the
    whole position is below `min_leg` (only the SL protects — caller must flag).

    With the default 0.25/0.50/0.25 fractions the binding constraint is the 0.25
    legs (need qty >= min/0.25 = 4*min for 3 full legs); below that we degrade:
      qty >= 4*min  -> 3 legs (tp1 .25 / tp2 .50 / tp3 .25)
      qty >= 2*min  -> 2 legs (tp1 .50 / tp3 .50)   [keep nearest + farthest]
      qty >= 1*min  -> 1 leg  (tp1, full qty)        [bank the win reliably]
      else          -> 0 legs (position too small; SL-only)
    """
    q = _round_qty(float(entry_qty))
    by = {str(l.get("leg")): l for l in (tp_plan or [])}
    tp1, tp2, tp3 = by.get("tp1"), by.get("tp2"), by.get("tp3")

    def _mk(legdef: dict, qty: float) -> BracketLeg:
        return BracketLeg(leg=str(legdef["leg"]), price=float(legdef["price"]),
                          qty=_round_qty(qty))

    if q < min_leg or tp1 is None:
        return [], f"position {q} < min_leg {min_leg} (or no tp_plan) — SL-only, NO TP legs"

    if q >= 4 * min_leg and tp2 is not None and tp3 is not None:
        # 3 legs by the configured fractions; last leg absorbs the remainder.
        l1 = _mk(tp1, q * float(tp1.get("fraction", 0.25)))
        l2 = _mk(tp2, q * float(tp2.get("fraction", 0.50)))
        l3 = BracketLeg(leg="tp3", price=float(tp3["price"]),
                        qty=_round_qty(q - l1.qty - l2.qty))
        return [l1, l2, l3], ""

    if q >= 2 * min_leg and tp3 is not None:
        # 2 legs: nearest (tp1) + farthest (tp3), half each.
        l1 = _mk(tp1, q * 0.5)
        l3 = BracketLeg(leg="tp3", price=float(tp3["price"]),
                        qty=_round_qty(q - l1.qty))
        return [l1, l3], (
            f"degraded to 2 legs (qty {q} < 4*min {4*min_leg}): tp1+tp3 half each"
        )

    # 1 leg: full qty at tp1 (closest → highest fill probability → banks a win).
    return [BracketLeg(leg="tp1", price=float(tp1["price"]), qty=q)], (
        f"degraded to 1 leg (qty {q} < 2*min {2*min_leg}): full qty at tp1"
    )


def decide_sl_move(
    *,
    side: str,
    entry_price: float,
    current_sl: float,
    tp1_price: float,
    entry_qty: float,
    current_qty: float,
) -> tuple[float | None, str]:
    """Hybrid SL-move on TP fill, keyed on the FRACTION of the position closed
    (robust to leg degradation). PRICE-ONLY — the venue auto-reduces SL qty.

    side: entry side ("buy" long / "sell" short).
    Returns (new_sl_price | None, reason). None => no move this tick.

    - closed >= ~75% (TP1+TP2) -> SL to tp1_price
    - closed >= ~25% (TP1)     -> SL to breakeven (entry)
    - else                     -> no move
    Tighten-ONLY: a long's SL only moves UP, a short's only DOWN; never loosen.
    """
    if entry_qty <= 0:
        return None, "entry_qty<=0"
    closed = (entry_qty - current_qty) / entry_qty
    if closed >= 0.75:
        target, why = tp1_price, "TP1+TP2 filled → SL to TP1"
    elif closed >= 0.25:
        target, why = entry_price, "TP1 filled → SL to breakeven"
    else:
        return None, f"closed {closed:.2%} < 25% — no SL move"

    s = (side or "").lower()
    # Tighten-only guard (never loosen the stop).
    if s == "buy":  # long: SL below price; tighter = higher
        if target <= current_sl:
            return None, f"{why} but target {target} not tighter than current {current_sl}"
    elif s == "sell":  # short: SL above price; tighter = lower
        if target >= current_sl:
            return None, f"{why} but target {target} not tighter than current {current_sl}"
    else:
        return None, f"unknown side {side!r}"
    return float(target), why
