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
from typing import Any

from trading_corp.persistence.models import (
    AccountState,
    ProposedOrder,
    StrategyState,
)

log = logging.getLogger(__name__)

_PMCC_SLUG = "robinhood_pmcc"


async def propose_pmcc_combo(
    combo: list[ProposedOrder],
    *,
    risk_agent: Any,
    logger_agent: Any,
    pending_combo_registry: Any | None = None,
    division: str = "robinhood_pmcc",
    db_url: str | None = None,
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

    # Same risk basis as ceo_graph.risk_node's no-account default (main._run_order
    # passes no account/strategy_state to the graph for PMCC single-leg orders).
    account = AccountState(account="paper", equity=100_000.0, peak_equity=100_000.0)
    strategy_state = StrategyState.from_persistence(_PMCC_SLUG, db_url=db_url)

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
