"""Consent-card extras for the PMCC roll approval page (2026-07-28).

Two DISPLAY/CONSENT enhancements, both read-only (no order/auto_execute/SQL path):

  A. Earnings-imminent state — driven off `PMCCAgent.earnings_card_state`, which
     uses the SAME `_earnings_gate_state`/`resolve_earnings` the backend roll path
     uses, so the card and the gate can never disagree. BLOCKED hides Approve and
     shows the "let it expire" recommendation; UNVERIFIED keeps Approve with a
     prominent flag; CLEAR is a normal rollable card.

  B. Live debit/credit/net estimate — sourced from `estimate_roll_from_quotes`,
     which reuses the SAME broker.get_option_quote + natural formula the dispatch
     reprice uses, so the number shown is what Approve will attempt. When the roll
     can't be quoted, no estimate is shown — the reworded "no order sent" reason is
     shown instead.

The route handler (`/approvals/pmcc-combos/{id}`) calls `build_pmcc_roll_card_extras`
and merges the result into the template context.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Reworded, reassuring "no estimate" text — mirrors the abort-alert tone
# (2026-07-24: "no order sent, position unchanged") so an operator never reads a
# missing estimate as a failure.
_NO_ESTIMATE_REASON = (
    "Live estimate unavailable — quotes not returned for the target contracts "
    "(the market may be closed); no order sent, the roll re-prices at approval."
)

_CLEAR_EARNINGS = {
    "kind": "clear", "date": None, "verified": False, "source": None,
    "recommendation": None, "flag": None, "caveat": None, "offer_roll": True,
}


async def build_pmcc_roll_card_extras(
    entry: Any, broker: Any, pmcc_agent: Any,
) -> dict:
    """Return {'earnings': {...}, 'estimate': {...}|None, 'estimate_reason': str|None}
    for one pending PMCC roll combo. Never raises — a failure degrades to a clear
    earnings state and no estimate (the card still renders; dispatch keeps its own
    guards)."""
    orders = list(getattr(entry, "orders", None) or [])
    symbol = getattr(entry, "underlying", "") or (orders[0].symbol if orders else "")

    close_leg = next(
        (o for o in orders if (o.extra or {}).get("position_effect") == "close"),
        None,
    )
    short_strike = (close_leg.extra or {}).get("strike") if close_leg else None

    spot = None
    if broker is not None:
        try:
            spot = await broker.quote(symbol)
        except Exception as e:      # noqa: BLE001 — spot is best-effort (ITM caveat only)
            log.debug("roll-card: spot quote failed for %s: %s", symbol, e)
            spot = None

    earnings = dict(_CLEAR_EARNINGS)
    if pmcc_agent is not None and hasattr(pmcc_agent, "earnings_card_state"):
        try:
            earnings = pmcc_agent.earnings_card_state(
                symbol, short_strike=short_strike, spot=spot,
            )
        except Exception as e:      # noqa: BLE001 — never break the card on earnings read
            log.warning("roll-card: earnings_card_state failed for %s: %s", symbol, e)
            earnings = dict(_CLEAR_EARNINGS)

    estimate: dict | None = None
    estimate_reason: str | None = None
    # The debit/credit/net estimate depends on the action's SHAPE:
    #   - 2-leg roll (1 buy-to-close + 1 sell-to-open)  -> estimate_roll_from_quotes
    #   - single short-side leg (P3b: close_short buy-to-close / open_short sell-cover)
    #     -> estimate_single_leg_from_quote (DEBIT=ask / CREDIT=bid, same source)
    # A 4-leg roll_leap or any LEAP-touching action would pair the wrong legs, so we
    # guard on the shape — the division panel calls this helper for ANY action without
    # a nonsense estimate. (The removed /approvals combos were always 2-leg rolls.)
    _single_action = (orders[0].extra or {}).get("action") if len(orders) == 1 else None
    n_buy = sum(1 for o in orders if getattr(o, "side", None) == "buy")
    n_sell = sum(1 for o in orders if getattr(o, "side", None) == "sell")
    is_two_leg_roll = len(orders) == 2 and n_buy == 1 and n_sell == 1
    is_single_short_side = _single_action in ("close_short_urgent", "open_short_call")
    # A 2-leg roll is suppressed on a BLOCKED earnings card (we're recommending "let it
    # expire", not roll). A single short-side leg is priced REGARDLESS of the earnings
    # roll-block — an urgent buy-to-close must never be suppressed, and the panel shows
    # the earnings state informationally alongside it.
    if broker is not None and orders and (
        (is_two_leg_roll and earnings.get("offer_roll", True)) or is_single_short_side
    ):
        try:
            if is_two_leg_roll:
                from trading_corp.agents.strategies._pmcc_combo import (
                    estimate_roll_from_quotes,
                )
                estimate = await estimate_roll_from_quotes(orders, broker)
            else:
                from trading_corp.agents.strategies._pmcc_combo import (
                    estimate_single_leg_from_quote,
                )
                estimate = await estimate_single_leg_from_quote(orders[0], broker)
        except Exception as e:      # noqa: BLE001 — degrade to "no estimate"
            log.warning("roll-card: estimate failed for %s: %s", symbol, e)
            estimate = None
        if estimate is None:
            estimate_reason = _NO_ESTIMATE_REASON

    return {
        "earnings": earnings,
        "estimate": estimate,
        "estimate_reason": estimate_reason,
    }
