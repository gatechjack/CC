"""Render-payload builder for IC combo approval cards.

Decouples the visual concern from the registry and template. Given a
`PendingComboEntry`, returns a dict the Jinja template renders into a
single approval card.

Per-intent visual structure:

  open / catastrophic_stop / hard_stop / late_dte_force_close / etc.:
    - All 4 legs in one "Legs" block.
    - Direction + net (credit on open, debit on full-close).

  close_tested_side:
    - 2 legs (short + long of the tested side), one block.
    - Debit close.

  adjustment_1:
    - 4 legs split into "Closing untested" (2) + "Opening new untested" (2).
    - Net credit/debit added.

The builder is broker-agnostic and works against the ProposedOrder
shape established by the IC strategy in step 9 (extra carries
combo_id, combo_role, position_effect, strike, option_type, etc.).
"""
from __future__ import annotations

from typing import Any

from trading_corp.comms.pending_combo_registry import PendingComboEntry


def build_combo_card_payload(entry: PendingComboEntry) -> dict[str, Any]:
    """Convert a PendingComboEntry to a Jinja-ready dict.

    Returned shape (keys consumed by `approval_combo_detail.html`):

      {
        "combo_id":   "<full uuid>",
        "short_id":   "<first 8 chars>",
        "symbol":     "SPY",
        "strategy":   "robinhood_joint_iron_condor",
        "division":   "robinhood_joint",
        "intent":     "open" | "close_tested_side" | "adjustment_1" | ...,
        "intent_label": "Open" | "Close (tested side)" | "Adjust" | ...,
        "direction":  "credit" | "debit",
        "net_price":  1.20,
        "contracts":  1,
        "leg_groups": [
            {"label": "Legs",           "legs": [...4 leg dicts...]},     # for opens / full closes
            # OR (for adjustments):
            {"label": "Closing untested", "legs": [...2 leg dicts...]},
            {"label": "Opening new",      "legs": [...2 leg dicts...]},
        ],
        "added_at":   <datetime>,
      }

    Each leg dict in `legs`:
      {
        "order_id":   "<uuid>",
        "role":       "short_put"|"long_put"|"short_call"|"long_call"|...,
        "role_label": "Short Put" etc.,
        "side":       "buy"|"sell",
        "side_label": "Sell to Open" / "Buy to Close" etc.,
        "strike":     430.0,
        "option_type":"put"|"call",
        "position_effect": "open"|"close",
        "limit_price": 0.55,
        "expiration": "2026-06-19",
      }
    """
    orders = entry.orders
    first = orders[0]
    extra0 = first.extra or {}

    contracts = int(first.qty)
    net_price = entry.net_limit_price
    intent = entry.intent
    intent_label = _INTENT_LABELS.get(intent, intent.replace("_", " ").title())

    leg_dicts = [_to_leg_dict(o) for o in orders]

    # Group legs by position_effect for the adjustment view; everything
    # else uses a single "Legs" group.
    if intent == "adjustment_1":
        closing = [d for d in leg_dicts if d["position_effect"] == "close"]
        opening = [d for d in leg_dicts if d["position_effect"] == "open"]
        leg_groups = [
            {"label": "Closing untested", "legs": closing},
            {"label": "Opening new untested", "legs": opening},
        ]
    else:
        leg_groups = [{"label": "Legs", "legs": leg_dicts}]

    return {
        "combo_id": entry.combo_id,
        "short_id": entry.combo_id[:8],
        "symbol": entry.underlying,
        "strategy": entry.strategy_slug,
        "division": entry.division,
        "intent": intent,
        "intent_label": intent_label,
        "direction": entry.direction,
        "net_price": net_price,
        "contracts": contracts,
        "leg_groups": leg_groups,
        "added_at": entry.added_at,
        "leg_count": len(orders),
    }


def _to_leg_dict(o: Any) -> dict[str, Any]:
    extra = o.extra or {}
    role = extra.get("combo_role") or ""
    effect = extra.get("position_effect") or "open"
    side = o.side
    return {
        "order_id": o.id,
        "role": role,
        "role_label": _ROLE_LABELS.get(role, role.replace("_", " ").title()),
        "side": side,
        "side_label": _side_label(side, effect),
        "strike": extra.get("strike"),
        "option_type": extra.get("option_type"),
        "position_effect": effect,
        "limit_price": o.limit_price,
        "expiration": extra.get("expiration"),
    }


def _side_label(side: str, effect: str) -> str:
    if side == "sell" and effect == "open":
        return "Sell to Open"
    if side == "sell" and effect == "close":
        return "Sell to Close"
    if side == "buy" and effect == "open":
        return "Buy to Open"
    if side == "buy" and effect == "close":
        return "Buy to Close"
    return f"{side} ({effect})"


_ROLE_LABELS = {
    "short_put": "Short Put",
    "long_put": "Long Put",
    "short_call": "Short Call",
    "long_call": "Long Call",
    "old_short_call": "Old Short Call",
    "old_long_call": "Old Long Call",
    "old_short_put": "Old Short Put",
    "old_long_put": "Old Long Put",
    "new_short_call": "New Short Call",
    "new_long_call": "New Long Call",
    "new_short_put": "New Short Put",
    "new_long_put": "New Long Put",
}

_INTENT_LABELS = {
    "open": "Open",
    "close_tested_side": "Close Tested Side",
    "adjustment_1": "Adjust (Roll Untested)",
    "profit_target": "Close (50% Profit Target)",
    "force_close_dte": "Close (21 DTE)",
    "late_dte_force_close": "Close (Late DTE Force)",
    "ex_div_force_close": "Close (Ex-Div)",
    "hard_stop": "Close (Hard Stop)",
    "catastrophic_stop": "Close (Catastrophic Stop)",
    "startup_catchup": "Startup Catch-up",
}
