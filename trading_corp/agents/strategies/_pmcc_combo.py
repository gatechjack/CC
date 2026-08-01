"""PMCC atomic roll_short combo orchestration (Phase A, 2026-07-22).

A PMCC roll_short is a 2-leg diagonal — buy-to-close the current short +
sell-to-open a strictly-later short. Phase A tags the pair as ONE combo and
routes it through `data_exec.place_combo` -> `RobinhoodBroker.place_multi_leg`
(a single all-or-nothing POST) so B4's atomicity holds at the FILL layer, not
only the proposal layer.

This is the PMCC sibling of `_ic_orchestration`'s combo helpers — a deliberately
thin copy of the PROPOSE side, with PMCC-honest audit labels (no "IC" strings).
The iron-condor division is a precedent to read, NOT a file to edit. The DISPATCH
side reuses the generic, duck-typed `dispatch_approved_ic_combo` (place_combo +
on_combo_filled) unchanged — the web route passes `strategy=pmcc_agent`.

roll_leap is NOT handled here: it is advisory-manual (the operator executes LEAP
rolls) and is refused by the fail-closed `data_exec` dispatch guard.
"""
from __future__ import annotations

import logging
import uuid
from collections import Counter, defaultdict
from typing import Any

from trading_corp.persistence.models import (
    AccountState,
    ProposedOrder,
    StrategyState,
)

log = logging.getLogger(__name__)

_PMCC_SLUG = "robinhood_pmcc"

# Fixed namespace so a combo's ref_id is deterministic (uuid5, no randomness) —
# a transient retry of the SAME combo produces the SAME ref_id and dedupes at
# Robinhood instead of double-placing.
_COMBO_REF_NS = uuid.NAMESPACE_URL


def combo_ref_id(combo_id: str) -> str:
    """Deterministic Robinhood ref_id for a combo. order_option_spread otherwise
    mints a fresh uuid4 per call, so a retry would double-place; this keys the
    ref_id off the combo_id so the venue dedupes."""
    return str(uuid.uuid5(_COMBO_REF_NS, f"pmcc-combo:{combo_id}"))


def partition_combo_orders(
    orders: list[ProposedOrder],
) -> tuple[dict[str, list[ProposedOrder]], list[ProposedOrder]]:
    """Split a batch of ProposedOrders into combo groups (keyed by combo_id) and
    single-leg orders — the general case, not one special-cased combo.

    A leg joins a combo group iff it carries BOTH `extra.combo_id` AND
    `extra.is_multi_leg`. Everything else is a single.

    Raises ValueError (place NOTHING) on a malformed batch:
      - a combo group with < 2 legs (a combo_id that lost its partner), or
      - >1 untagged OPTION single SHARING AN UNDERLYING (looks like an un-tagged
        combo — the interim backstop that refuses to leg-in close_all / Scout OPEN
        until their legs are combo-tagged). Independent single-leg option actions
        on DIFFERENT underlyings in one batch are legitimate and pass through.
    """
    combo_groups: dict[str, list[ProposedOrder]] = defaultdict(list)
    singles: list[ProposedOrder] = []
    for o in orders:
        ex = o.extra or {}
        cid = ex.get("combo_id")
        if cid and ex.get("is_multi_leg"):
            combo_groups[cid].append(o)
        else:
            singles.append(o)
    for cid, legs in combo_groups.items():
        if len(legs) < 2:
            raise ValueError(
                f"malformed combo group {cid!r}: {len(legs)} leg(s); a combo "
                "needs >= 2 legs — refusing to place a partial combo"
            )
    # Narrowed backstop: only refuse when >1 untagged option single shares an
    # underlying (an un-tagged combo). Independent singles (distinct underlyings)
    # pass to the per-leg loop.
    by_underlying = Counter(
        ((o.extra or {}).get("underlying") or o.symbol)
        for o in singles if (o.extra or {}).get("is_option")
    )
    dup = [u for u, n in by_underlying.items() if n > 1]
    if dup:
        raise ValueError(
            f"{by_underlying[dup[0]]} untagged option legs share underlying "
            f"{dup[0]!r} — looks like an un-tagged combo; refusing to leg-in via "
            "the single-leg path (combo-tag them to route through place_combo)"
        )
    return dict(combo_groups), singles


def _net_tick_for_price(leg_quotes: list, price: float, *, def_above: float,
                        def_below: float, def_cutoff: float) -> float:
    """Determine the min tick governing the NET spread price. RH validates the net
    against a tick rule and the two legs' `min_ticks` can differ — pick the
    COARSEST tick any leg requires at this price magnitude, so the net is valid for
    every leg (a too-fine net is rejected). Each leg quote may carry
    `below_tick`/`above_tick`/`cutoff`; missing → the standard 0.05≥$3 / 0.01 rule.
    Deterministic on mismatch (max of the per-leg applicable ticks).

    ★ Live-validation checklist item: confirm RH's exact net-tick rule for a
    diagonal spread against a real reject."""
    ticks = []
    for q in (leg_quotes or [{}]):
        q = q or {}
        below = q.get("below_tick") or def_below
        above = q.get("above_tick") or def_above
        cut = q.get("cutoff") or def_cutoff
        ticks.append(above if abs(price) >= cut else below)
    return max(ticks) if ticks else def_below


async def reprice_combo_from_quotes(
    legs: list[ProposedOrder], broker: Any, *, give_up: float,
    above_tick: float = 0.05, below_tick: float = 0.01, cutoff: float = 3.00,
    max_spread_pct: float | None = None, min_sell_bid: float = 0.0,
    min_spread_abs: float = 0.10,
) -> tuple[str, float]:
    """Recompute the combo's marketable net limit from LIVE quotes AT DISPATCH —
    the proposal-time mid (`_propose_roll_short` tags ~1.30 mark) is stale and
    non-marketable by approval, so submitted as-is it rests and never fills.

    natural = Σ bid(sell legs) − Σ ask(buy legs)   (what you'd get crossing the spread)
      credit (natural >= 0): limit = natural − give_up
      debit  (natural <  0): limit = |natural| + give_up
    ★ A large give_up (urgent close_all) can push a credit natural BELOW zero — the
    order then flips to a small DEBIT (you PAY to get out) rather than resting as a
    too-optimistic credit. Then rounded to the NET min tick, floored at one tick.

    Mutates each leg's `extra.combo_direction` / `extra.net_limit_price` and
    returns (direction, limit). FAIL-SAFE: if any leg quote is missing, it leaves
    the proposal-time tags untouched and returns them unchanged.
    """
    quotes: dict[str, tuple[float, float, dict]] = {}
    for o in legs:
        ex = o.extra or {}
        try:
            q = await broker.get_option_quote(
                ex.get("underlying") or o.symbol, ex.get("expiration"),
                float(ex.get("strike")), ex.get("option_type", "call"),
            )
        except Exception as e:      # noqa: BLE001 — any quote failure is fail-safe
            log.warning("reprice_combo: get_option_quote raised for %s: %s", o.symbol, e)
            q = None
        bid = (q or {}).get("bid")
        ask = (q or {}).get("ask")
        if bid is None or ask is None:
            first = legs[0].extra or {}
            log.warning(
                "reprice_combo: missing quote for %s leg — keeping proposal-time "
                "net_limit %s (%s)", o.symbol, first.get("net_limit_price"),
                first.get("combo_direction"),
            )
            return first.get("combo_direction"), first.get("net_limit_price")
        quotes[o.id] = (float(bid), float(ask), q or {})

    # Stale/wide-quote guard: opening-rotation quotes can be implausibly wide, or
    # zero-bid on the sell leg. Repricing off that garbage yields a nonsense limit,
    # so HOLD (keep the proposal-time tag) and mark the legs so the dispatch
    # consent guard bails instead of placing. (2026-07-24 opening rotation.)
    hold_reason = None
    for o in legs:
        bid, ask, _q = quotes[o.id]
        mid = (bid + ask) / 2.0
        if o.side == "sell" and bid <= float(min_sell_bid):
            hold_reason = f"{o.symbol} sell-leg bid {bid:.2f} <= {float(min_sell_bid):.2f}"
            break
        if (max_spread_pct is not None and mid > 0
                and (ask - bid) > float(min_spread_abs)
                and (ask - bid) / mid > float(max_spread_pct)):
            hold_reason = (
                f"{o.symbol} leg spread {ask - bid:.2f} = "
                f"{(ask - bid) / mid * 100:.0f}% of mid > {float(max_spread_pct) * 100:.0f}%"
            )
            break
    if hold_reason is not None:
        first = legs[0].extra or {}
        log.warning(
            "reprice_combo: HOLD (%s) — keeping proposal-time net_limit %s (%s)",
            hold_reason, first.get("net_limit_price"), first.get("combo_direction"),
        )
        for o in legs:
            (o.extra or {})["reprice_hold"] = hold_reason
        return first.get("combo_direction"), first.get("net_limit_price")

    net = 0.0
    for o in legs:
        bid, ask, _ = quotes[o.id]
        ratio = int((o.extra or {}).get("ratio_quantity", 1))
        net += (bid if o.side == "sell" else -ask) * ratio

    # Direction + limit, allowing give_up to cross a credit into a debit.
    if net >= 0:
        signed = net - give_up
        direction, limit = ("credit", signed) if signed >= 0 else ("debit", -signed)
    else:
        direction, limit = "debit", (-net) + give_up

    # Round to the NET tick (coarsest across legs at this price magnitude).
    leg_quotes = [q for (_, _, q) in quotes.values()]
    tick = _net_tick_for_price(leg_quotes, limit, def_above=above_tick,
                               def_below=below_tick, def_cutoff=cutoff)
    limit = round(round(limit / tick) * tick, 2)
    if limit < tick:
        limit = tick
    for o in legs:
        (o.extra or {})["combo_direction"] = direction
        (o.extra or {})["net_limit_price"] = limit
    return direction, limit


async def estimate_roll_from_quotes(
    legs: list[ProposedOrder], broker: Any,
) -> dict | None:
    """Non-mutating LIVE-quote estimate of a roll_short's debit / credit / net for
    the consent card (Enhancement B, 2026-07-28).

    CONSENT INTEGRITY: sources from the SAME `broker.get_option_quote` and the SAME
    natural formula (Σ bid(sell) − Σ ask(buy)) as `reprice_combo_from_quotes` uses
    at DISPATCH, so the number shown is the pre-give_up natural the placed order
    derives from — NOT a separate/independent calc that could diverge. The give_up
    shave + net-tick rounding (the "actual fill will differ slightly") are applied
    only at dispatch, not here.

      DEBIT  = ask of the buy-to-close (current short) leg  — you pay the ask to close.
      CREDIT = bid of the sell-to-open (new short) leg      — you collect the bid.
      NET    = Σ bid(sell)·ratio − Σ ask(buy)·ratio         — credit if ≥0 else debit.

    Returns None (→ caller shows the reworded abort/"no estimate" text) when either
    leg is missing or any live quote is unavailable. Never raises. Does NOT mutate
    the legs (the pending combo must reach dispatch untouched)."""
    buy_leg = next((o for o in legs if o.side == "buy"), None)
    sell_leg = next((o for o in legs if o.side == "sell"), None)
    if buy_leg is None or sell_leg is None:
        return None

    async def _quote(o: ProposedOrder) -> dict | None:
        ex = o.extra or {}
        try:
            return await broker.get_option_quote(
                ex.get("underlying") or o.symbol, ex.get("expiration"),
                float(ex.get("strike")), ex.get("option_type", "call"),
            )
        except Exception as e:      # noqa: BLE001 — any quote failure → no estimate
            log.warning("estimate_roll: get_option_quote raised for %s: %s", o.symbol, e)
            return None

    bq = await _quote(buy_leg)
    sq = await _quote(sell_leg)
    debit = (bq or {}).get("ask")      # buy-to-close pays the ask
    credit = (sq or {}).get("bid")     # sell-to-open collects the bid
    if debit is None or credit is None:
        return None
    debit = float(debit)
    credit = float(credit)
    r_buy = int((buy_leg.extra or {}).get("ratio_quantity", 1) or 1)
    r_sell = int((sell_leg.extra or {}).get("ratio_quantity", 1) or 1)
    net = round(credit * r_sell - debit * r_buy, 2)     # == reprice natural
    ce = buy_leg.extra or {}
    oe = sell_leg.extra or {}
    return {
        "debit": round(debit, 2),
        "credit": round(credit, 2),
        "net": net,
        "net_abs": round(abs(net), 2),
        "direction": "credit" if net >= 0 else "debit",
        "close_strike": ce.get("strike"),
        "close_expiration": ce.get("expiration"),
        "open_strike": oe.get("strike"),
        "open_expiration": oe.get("expiration"),
    }


async def estimate_single_leg_from_quote(
    order: ProposedOrder, broker: Any,
) -> dict | None:
    """LIVE-quote estimate for a SINGLE-leg PMCC SHORT-side action, in the same dict
    shape (debit/credit/net/direction/strikes) `estimate_roll_from_quotes` returns:

      buy-to-close (close_short) -> DEBIT  = ask · ratio  (you pay the ask to close)
      sell-to-open (open_short)  -> CREDIT = bid · ratio  (you collect the bid)

    Same `broker.get_option_quote` source + same ask/bid convention as the roll
    estimate, so the panel number matches what dispatch prices. Per-share (× contracts
    × 100 = total $, applied at display/notional, exactly as the roll card does).
    Returns None on a missing quote; never raises; NON-mutating (the order reaches
    dispatch untouched). LEAP-mandate: only ever called on short-call legs — it reads
    the leg's own strike/expiration and never constructs a LEAP order."""
    if order is None or getattr(order, "side", None) not in ("buy", "sell"):
        return None
    ex = order.extra or {}
    try:
        q = await broker.get_option_quote(
            ex.get("underlying") or order.symbol, ex.get("expiration"),
            float(ex.get("strike")), ex.get("option_type", "call"),
        )
    except Exception as e:      # noqa: BLE001 — any quote failure → no estimate
        log.warning("estimate_single_leg: get_option_quote raised for %s: %s", order.symbol, e)
        return None
    q = q or {}
    ratio = int(ex.get("ratio_quantity", 1) or 1)
    if order.side == "buy":                     # buy-to-close → DEBIT (pay the ask)
        ask = q.get("ask")
        if ask is None:
            return None
        debit = round(float(ask) * ratio, 2)
        return {
            "debit": debit, "credit": 0.0, "net": round(-debit, 2), "net_abs": debit,
            "direction": "debit",
            "close_strike": ex.get("strike"), "close_expiration": ex.get("expiration"),
            "open_strike": None, "open_expiration": None,
        }
    bid = q.get("bid")                          # sell-to-open cover → CREDIT (collect the bid)
    if bid is None:
        return None
    credit = round(float(bid) * ratio, 2)
    return {
        "debit": 0.0, "credit": credit, "net": credit, "net_abs": credit,
        "direction": "credit",
        "open_strike": ex.get("strike"), "open_expiration": ex.get("expiration"),
        "close_strike": None, "close_expiration": None,
    }


def snapshot_combo_for_consent(legs: list[ProposedOrder]) -> dict:
    """Capture the operator-APPROVED combo shape BEFORE dispatch reprice mutates
    it, so the consent guard can detect an adverse drift at dispatch time."""
    first = (legs[0].extra or {}) if legs else {}
    return {
        "direction": first.get("combo_direction"),
        "net_limit_price": first.get("net_limit_price"),
        "strikes": {o.id: (o.extra or {}).get("strike") for o in legs},
    }


def assess_combo_reprice_consent(
    legs: list[ProposedOrder], snapshot: dict, *,
    max_adverse_net_deviation: float,
) -> tuple[bool, str]:
    """Defense-in-depth consent check comparing the DISPATCH-repriced combo to the
    operator-APPROVED `snapshot`. Returns (ok, reason); ok=False => do NOT place —
    re-surface for re-approval (the next scan re-proposes). Guards:
      - stale/wide quotes  (reprice set extra['reprice_hold'])
      - sign flip          (credit approval repriced to a debit limit)
      - strike drift       (strike changed vs approved; defensive)
      - credit collapse    (credit worse than approved by > max_adverse_net_deviation)
    """
    if not legs:
        return False, "empty combo"
    first = legs[0].extra or {}

    hold = first.get("reprice_hold")
    if hold:
        return False, f"stale/wide quotes: {hold}"

    snap_dir = snapshot.get("direction")
    cur_dir = first.get("combo_direction")
    if snap_dir == "credit" and cur_dir == "debit":
        return False, "credit proposal repriced to a DEBIT limit"

    snap_strikes = snapshot.get("strikes") or {}
    for o in legs:
        if o.id in snap_strikes and (o.extra or {}).get("strike") != snap_strikes[o.id]:
            return False, (
                f"strike changed vs approved on leg {o.id}: "
                f"{snap_strikes[o.id]} -> {(o.extra or {}).get('strike')}"
            )

    try:
        snap_net = float(snapshot.get("net_limit_price"))
        cur_net = float(first.get("net_limit_price"))
    except (TypeError, ValueError):
        return True, ""           # incomparable -> do not block on missing data
    if snap_dir == "credit" and (snap_net - cur_net) > float(max_adverse_net_deviation):
        return False, (
            f"credit collapsed vs approved: approved {snap_net:.2f}, dispatch "
            f"{cur_net:.2f} (drop {snap_net - cur_net:.2f} > "
            f"{float(max_adverse_net_deviation):.2f})"
        )
    return True, ""


def _combo_is_bp_consuming(combo: "list[ProposedOrder]") -> bool:
    """A combo consumes NEW buying power only if it OPENS a net-DEBIT position with no
    offsetting close (a fresh long/opening buy). A roll_short (buy-to-close +
    sell-to-open, net credit, covered by the LEAP) and any protective close are NOT
    BP-consuming and must not be blocked by low BP (the #4 carve-out)."""
    if not combo:
        return False
    first = combo[0].extra or {}
    direction = first.get("combo_direction")
    has_close = any((o.extra or {}).get("position_effect") == "close" for o in combo)
    has_open = any((o.extra or {}).get("position_effect") == "open" for o in combo)
    return bool(has_open and not has_close and direction == "debit")


async def propose_pmcc_combo(
    combo: list[ProposedOrder],
    *,
    risk_agent: Any,
    logger_agent: Any,
    pending_combo_registry: Any | None = None,
    division: str = "robinhood_pmcc",
    db_url: str | None = None,
    account_equity: float | None = None,
) -> bool:
    """Risk-gate a combo-tagged PMCC roll_short pair and register it for HITL.

    Returns True iff every leg passed risk and the combo was queued. A single leg
    REJECT aborts the WHOLE combo (no partial state) — mirroring the IC combo
    contract and, unlike the old parallel single-leg path, making an independent
    per-leg resize/reject that could unbalance the roll impossible.

    Risk basis is IDENTICAL to the single-leg ceo_graph path: `risk_node` uses a
    default paper `AccountState(equity=100k)` when `_run_order` passes no account,
    plus `StrategyState.from_persistence(strategy)`. We rebuild exactly that so
    routing through `place_combo` does not change what risk sees. `resize`
    verdicts are ignored (a combo cannot resize one leg); only `reject` gates.
    """
    if not combo:
        return False
    combo_id = (combo[0].extra or {}).get("combo_id")
    if not combo_id:
        log.warning("propose_pmcc_combo: missing combo_id on leg 0 — skipping")
        return False

    # #4 (2026-07-24): the risk gate must see REAL buying power for a genuinely
    # BP-consuming OPEN (so an oversized order on a thin account is caught), but a
    # defensive/credit roll or protective close must NOT be blocked by low BP —
    # they're covered / net-credit. CARVE-OUT: real equity only for a BP-consuming
    # open; permissive 100k otherwise (preserves the prior roll_short behaviour).
    _bp_consuming = _combo_is_bp_consuming(combo)
    _gate_equity = (float(account_equity)
                    if (_bp_consuming and account_equity is not None) else 100_000.0)
    account = AccountState(account="paper", equity=_gate_equity, peak_equity=_gate_equity)
    strategy_state = StrategyState.from_persistence(_PMCC_SLUG, db_url=db_url)
    try:
        logger_agent.log_event(
            "pmcc", "combo_risk_basis",
            {"combo_id": combo_id, "division": division, "bp_consuming": _bp_consuming,
             "gate_equity": _gate_equity, "real_equity": account_equity},
        )
    except Exception:
        pass

    for leg in combo:
        try:
            v = risk_agent.evaluate(
                leg, account, strategy_state, "unknown", None, db_url=db_url,
            )
        except Exception:
            log.exception(
                "propose_pmcc_combo: risk evaluate raised for combo %s leg %s "
                "— aborting whole combo (fail-closed)",
                combo_id, leg.id,
            )
            return False
        if getattr(v, "verdict", "") == "reject":
            logger_agent.log_event(
                "pmcc", "combo_rejected_by_risk",
                {
                    "combo_id": combo_id,
                    "division": division,
                    "rejected_leg_action": (leg.extra or {}).get("action"),
                    "risk_reason": getattr(v, "reason", None),
                },
            )
            log.info(
                "PMCC combo %s: risk REJECT leg %s — %s",
                combo_id, (leg.extra or {}).get("action"), getattr(v, "reason", None),
            )
            return False

    first_extra = combo[0].extra or {}
    logger_agent.log_event(
        "pmcc", "combo_proposed",
        {
            "combo_id": combo_id,
            "strategy": _PMCC_SLUG,
            "division": division,
            "intent": "roll_short",
            "direction": first_extra.get("combo_direction"),
            "net_limit_price": first_extra.get("net_limit_price"),
            "underlying": first_extra.get("underlying") or combo[0].symbol,
            "leg_count": len(combo),
            "legs": [
                {
                    "order_id": leg.id,
                    "side": leg.side,
                    "qty": float(leg.qty),
                    "symbol": leg.symbol,
                    "action": (leg.extra or {}).get("action"),
                    "strike": (leg.extra or {}).get("strike"),
                    "expiration": (leg.extra or {}).get("expiration"),
                    "position_effect": (leg.extra or {}).get("position_effect"),
                    "limit_price": leg.limit_price,
                }
                for leg in combo
            ],
        },
    )

    if pending_combo_registry is not None:
        try:
            pending_combo_registry.propose(
                combo_id, combo,
                intent="roll_short", strategy_slug=_PMCC_SLUG, division=division,
            )
        except Exception:
            log.exception(
                "propose_pmcc_combo: registry.propose raised — combo audit "
                "written but not queued; the next scan re-proposes."
            )
            return False
    return True
