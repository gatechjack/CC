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

Board min-leg rule: every PLACED leg must be >= MIN_LEG_QTY_BTC (0.0001 BTC).
Too-small positions DEGRADE to fewer, larger legs — never a sub-min leg.
When only ONE leg fits, it rests at the FULL-PROFIT target (farthest tp), not
a near fee-covering TP (Board 2026-07-06).

SL-move hybrid ((b)+(c), tp_plan default stop_action): TP1 filled -> SL to
breakeven; TP1+TP2 filled -> SL to TP1; never loosened (tighten-only).
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_LEG_QTY_BTC: float = 0.0001  # Board rule 2026-07-06: venue min order size (was 0.0003).


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
      qty >= 1*min  -> 1 leg  (FULL-PROFIT tp, full qty)  [Board 2026-07-06]
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

    # 1 leg: full qty at the FARTHEST available target (full profit) — Board
    # 2026-07-06: when only one leg fits, bank at FULL profit, never a near
    # fee-covering TP. Prefer tp3, then tp2, then tp1. SFP passes only tp1 (its
    # own full-profit price) so SFP is unchanged; futures gets tp3.
    full = tp3 or tp2 or tp1
    return [BracketLeg(leg=str(full["leg"]), price=float(full["price"]), qty=q)], (
        f"degraded to 1 leg (qty {q} < 2*min {2*min_leg}): full qty at {full['leg']} (full-profit)"
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


def classify_result(*, net_pnl: float | None, gross_pnl: float) -> str:
    """Win/loss from the booked PnL SIGN — never a literal (the P2 auto-book bug
    hard-coded 'loss', mis-signing genuine wins; report 2026-06-19_p2_classifier).

    NET basis when available (the real-fill path records fees; a 'win' should mean
    the trade netted positive), else gross (the known-level estimate path has no
    fee). Zero → 'loss', matching the paper-replay convention
    (`win if actual_r > 0 else loss`).
    """
    basis = net_pnl if net_pnl is not None else gross_pnl
    return "win" if (basis is not None and basis > 0) else "loss"


def classify_exit_kind(
    *,
    side: str,
    vwap_fill: float,
    stop_level: float,
    tp_prices: list[float],
    close_order_ids: list[str] | None = None,
    tp_order_ids: list[str] | None = None,
    sl_order_id: str | None = None,
    tol_pct: float = 0.0005,
) -> str:
    """Classify a close as ``'tp'`` / ``'stop'`` / ``'unknown'`` from the ACTUAL
    fill — NEVER defaulting to 'stop' when ambiguous (the auto-book bug stamped
    every close 'stop', mislabeling TP fills).

    1. **Order-id match (most robust):** a close fill whose venue order-id is one
       of the resting TP legs → ``'tp'``; the position-SL order → ``'stop'``.
       Available now that the /tpsl/ rebuild tracks ``bracket_tp_order_ids`` +
       ``bracket_position_sl_order_id``.
    2. **Price inference (no id match):** for the trade's side, a fill that
       reached a TP level (favorable, at/past the nearest TP) → ``'tp'``; a fill
       at/beyond the stop level → ``'stop'``; anything else — favorable but
       short of a TP (a trailed-stop-in-profit / time exit) — → ``'unknown'``.
    """
    cids = {str(c) for c in (close_order_ids or []) if c}
    tids = {str(t) for t in (tp_order_ids or []) if t}
    if cids and tids and (cids & tids):
        return "tp"
    if cids and sl_order_id and str(sl_order_id) in cids:
        return "stop"

    s = (side or "").lower()
    tps = [float(p) for p in (tp_prices or []) if p and float(p) > 0]
    v = float(vwap_fill or 0.0)
    sl = float(stop_level) if stop_level else 0.0
    if v > 0:
        # TP and stop checks are independent (either level may be absent): a fill
        # that reached a TP → 'tp'; one at/beyond the stop → 'stop'; else unknown.
        if s == "sell":   # short: TPs below entry (nearest = highest), stop above
            if tps and v <= max(tps) * (1.0 + tol_pct):
                return "tp"
            if sl > 0 and v >= sl * (1.0 - tol_pct):
                return "stop"
        elif s == "buy":  # long: TPs above entry (nearest = lowest), stop below
            if tps and v >= min(tps) * (1.0 - tol_pct):
                return "tp"
            if sl > 0 and v <= sl * (1.0 + tol_pct):
                return "stop"
    return "unknown"
