"""LangGraph build for the CEO trade-approval flow.

Node flow for a single proposed order:

  proposed_order
        │
        ▼
   risk_node ─── reject ──▶ log + END
        │
       approve / resize
        │
        ▼
   approval_node  ── interrupt() ──▶ Board (Telegram/CLI)
        │
       resume(decision)
        │
   ┌────┴────┐
   │         │
 approve   reject/modify
   │         │
   ▼         ▼
 executor  log + END (or re-route on modify)
   │
   ▼
 logger + END
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypedDict

import yaml

from trading_corp.agents.data_exec import DataExecAgent
from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.risk import RiskAgent, RiskVerdict
from trading_corp.graph.interrupts import ApprovalRequest, request_board_approval
from trading_corp.persistence.models import (
    AccountState, ProposedOrder, StrategyState,
)

log = logging.getLogger(__name__)

_STRATEGIES_YAML = Path("config/strategies.yaml")

# ── Action classifications (used to apply per-action caps + approval triggers) ──
# Keys are substrings tested against order.extra["action"].

# Actions that close (sell) the LEAP itself — always require Board approval
# when `closing_any_leap` is in require_approval_for.
_LEAP_CLOSE_ACTIONS: set[str] = {
    "close_leap_urgent", "roll_leap_close", "roll_leap_open_replace",
}
# Actions that open a new LEAP (start a new PMCC on a symbol)
_LEAP_OPEN_ACTIONS: set[str] = {"open_leap"}
# Roll actions (the buy-to-close leg of a roll, which is a debit)
_ROLL_DEBIT_ACTIONS: set[str] = {
    "roll_short_call_close", "roll_leap_close_short",
}
# Close-only debits (buy-to-close a losing short, urgent close, etc.)
_CLOSE_DEBIT_ACTIONS: set[str] = {
    "close_short_urgent", "close_leap_urgent",
}


def _load_strategies_cfg() -> dict:
    """Load config/strategies.yaml. Returns {} on any error (safe fallback)."""
    try:
        with _STRATEGIES_YAML.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _order_notional(order: ProposedOrder) -> float:
    """Dollar notional for an order. Options get the ×100 multiplier."""
    limit = order.limit_price or 0.0
    is_option = bool((order.extra or {}).get("is_option"))
    return order.qty * limit * (100.0 if is_option else 1.0)


def _is_debit(order: ProposedOrder) -> bool:
    """Buy-side option/equity orders are debits; sell-side are credits."""
    return order.side == "buy"


def _today_auto_executed(logger: LoggerAgent | None) -> tuple[int, float]:
    """Return (count, total_debit_dollars) of auto-executed orders today."""
    if logger is None:
        return (0, 0.0)
    try:
        from datetime import datetime, timezone
        events = logger.recent_events(limit=200)
        today = datetime.now(timezone.utc).date().isoformat()
        count = 0
        debit = 0.0
        for e in events:
            if e.get("kind") != "auto_executed":
                continue
            ts = e.get("ts") or ""
            if not ts.startswith(today):
                continue
            count += 1
            payload = e.get("payload") or {}
            debit += float(payload.get("debit_dollars") or 0.0)
        return (count, debit)
    except Exception:
        return (0, 0.0)


def _check_auto_execute(
    order: ProposedOrder,
    logger: LoggerAgent | None = None,
) -> tuple[bool, str]:
    """Return (should_auto_execute, reason_str).

    Reads `config/strategies.yaml` for the per-strategy `auto_execute_caps`
    structure and enforces:
      1. `auto_execute: true` is set
      2. The order's symbol/action does NOT match any `require_approval_for` rule
      3. Per-action dollar caps (roll / new-position / close debit caps)
      4. Daily aggregate caps (max actions, total debit) — best-effort via logger

    Risk Agent gating still applies regardless of this flag.
    Falls back to legacy flat `auto_max_notional` if `auto_execute_caps` is absent.
    """
    cfg = _load_strategies_cfg()
    strategy_cfg = (cfg.get(order.strategy) or {})

    if not strategy_cfg.get("auto_execute", False):
        return False, f"auto_execute=false for strategy '{order.strategy}'"

    caps = strategy_cfg.get("auto_execute_caps") or {}
    action = (order.extra or {}).get("action", "")
    notional = _order_notional(order)
    is_debit = _is_debit(order)

    # ── 1. require_approval_for triggers (always escalate) ──
    require = set(caps.get("require_approval_for") or [])

    if "any_action_on_black_sheep_symbols" in require:
        bs = _black_sheep_set(strategy_cfg)
        if order.symbol.upper() in bs:
            return False, (
                f"black sheep symbol {order.symbol} requires Board approval"
            )

    if "closing_any_leap" in require and action in _LEAP_CLOSE_ACTIONS:
        return False, f"closing LEAP ({action}) requires Board approval"

    if "opening_new_pmcc_on_new_symbol" in require and action in _LEAP_OPEN_ACTIONS:
        return False, f"opening new PMCC ({action}) requires Board approval"

    if "any_neutral_strategy_open_or_close" in require:
        # Treat strategies other than robinhood_pmcc (e.g. fidelity_options
        # iron condors / verticals) as "neutral strategies" for this rule.
        if order.strategy != "robinhood_pmcc":
            return False, "neutral strategy requires Board approval"

    # VIX gate — fetch spot ^VIX via yfinance (cached 5 min). If unavailable,
    # fail safe: escalate to Board.
    if "any_action_when_vix_above_30" in require:
        from trading_corp.utils.market_data import get_vix
        vix = get_vix()
        if vix is None:
            return False, "VIX feed unavailable; Board approval required (fail-safe)"
        if vix > 30.0:
            return False, f"VIX={vix:.2f} > 30; high-vol regime requires Board approval"
        log.debug("auto_execute: VIX=%.2f below 30 threshold; rule passes", vix)

    # Roll-debit-vs-LEAP-value gate. Reads the LEAP value cache populated by
    # PMCCAgent.detect_existing_legs during scan. Stale/missing → fail-safe.
    # Only fires on the buy-to-close (debit) leg of a roll, not credits.
    if (
        "rolling_for_debit_above_5_pct_of_long" in require
        and is_debit
        and action in _ROLL_DEBIT_ACTIONS
    ):
        from trading_corp.utils.market_data import get_cached_leap_value
        leap_value = get_cached_leap_value(order.symbol)
        if leap_value is None:
            return False, (
                f"LEAP value cache stale/missing for {order.symbol}; "
                "Board approval required (fail-safe)"
            )
        # notional already includes the ×100 multiplier; LEAP value is also
        # per-contract (mark_per_share × 100), so the per-contract math holds
        # regardless of contract count.
        debit_per_contract = (order.limit_price or 0.0) * 100.0
        pct = (debit_per_contract / leap_value) if leap_value > 0 else 1.0
        if pct > 0.05:
            return False, (
                f"roll debit ${debit_per_contract:,.2f}/contract is "
                f"{pct*100:.1f}% of LEAP value ${leap_value:,.2f} (>5% threshold)"
            )
        log.debug(
            "auto_execute: 5%%-of-long gate passed for %s (%.1f%% of $%s)",
            order.symbol, pct * 100, f"{leap_value:,.2f}",
        )

    # ── 2. Per-action dollar caps ──
    # Note: caps with value `None` mean "no cap" (e.g. credits).
    if action in _LEAP_OPEN_ACTIONS:
        cap = caps.get("max_new_position_debit_dollars")
        if cap is not None and notional > float(cap):
            return False, (
                f"open-LEAP debit ${notional:,.2f} exceeds "
                f"max_new_position_debit_dollars ${float(cap):,.2f}"
            )
    elif action in _ROLL_DEBIT_ACTIONS and is_debit:
        cap = caps.get("max_roll_debit_dollars")
        if cap is not None and notional > float(cap):
            return False, (
                f"roll debit ${notional:,.2f} exceeds "
                f"max_roll_debit_dollars ${float(cap):,.2f}"
            )
    elif action in _CLOSE_DEBIT_ACTIONS and is_debit:
        cap = caps.get("max_close_debit_dollars")
        if cap is not None and notional > float(cap):
            return False, (
                f"close debit ${notional:,.2f} exceeds "
                f"max_close_debit_dollars ${float(cap):,.2f}"
            )
    else:
        # Credits (sell-to-open new short etc.) — only the optional credit cap applies.
        if not is_debit:
            cap = caps.get("max_single_short_credit_dollars")
            if cap is not None and notional > float(cap):
                return False, (
                    f"short credit ${notional:,.2f} exceeds "
                    f"max_single_short_credit_dollars ${float(cap):,.2f}"
                )

    # ── 3. Daily aggregate caps ──
    if logger is not None and (caps.get("max_daily_actions") or caps.get("max_daily_total_debit_dollars")):
        actions_today, debit_today = _today_auto_executed(logger)
        max_actions = caps.get("max_daily_actions")
        if max_actions is not None and actions_today >= int(max_actions):
            return False, (
                f"daily auto-action cap reached ({actions_today}/{int(max_actions)})"
            )
        max_daily_debit = caps.get("max_daily_total_debit_dollars")
        if max_daily_debit is not None and is_debit:
            projected = debit_today + notional
            if projected > float(max_daily_debit):
                return False, (
                    f"daily debit ${projected:,.2f} would exceed "
                    f"max_daily_total_debit_dollars ${float(max_daily_debit):,.2f}"
                )

    # ── 4. Legacy flat cap (back-compat when auto_execute_caps absent) ──
    if not caps:
        max_notional = float(strategy_cfg.get("auto_max_notional") or 0)
        if max_notional <= 0:
            return False, f"auto_max_notional not set for strategy '{order.strategy}'"
        if notional > max_notional:
            return False, (
                f"notional ${notional:,.2f} > auto_max_notional ${max_notional:,.2f}"
            )

    return True, (
        f"auto_execute approved (action={action or 'n/a'}, "
        f"{'debit' if is_debit else 'credit'} ${notional:,.2f}, "
        f"strategy='{order.strategy}')"
    )


def _black_sheep_set(strategy_cfg: dict) -> set[str]:
    """Pull the black-sheep symbol set from a strategy config block."""
    bs = (strategy_cfg.get("strategy") or {}).get("black_sheep") or {}
    out: set[str] = set()
    for entry in (bs.get("symbols") or []):
        sym = entry.get("symbol") if isinstance(entry, dict) else entry
        if isinstance(sym, str):
            out.add(sym.upper())
    return out


class TradeFlowState(TypedDict, total=False):
    """State for one proposed-order trade flow."""
    proposed_order: dict           # ProposedOrder.to_db_row()-ish dict
    division: str
    account: dict                  # AccountState as dict
    strategy_state: dict           # StrategyState as dict
    regime: str
    realized_vol: float
    risk_verdict: dict | None
    board_decision: dict | None
    fill: dict | None
    final_status: str | None       # 'risk_rejected'|'board_rejected'|'filled'|'cancelled'


def _order_from_state(s: TradeFlowState) -> ProposedOrder:
    po = s["proposed_order"]
    return ProposedOrder(
        id=po["id"],
        ts=po["ts"],
        strategy=po["strategy"],
        symbol=po["symbol"],
        side=po["side"],
        qty=po["qty"],
        order_type=po.get("order_type", "market"),
        limit_price=po.get("limit_price"),
        rationale=po.get("rationale", ""),
        status=po.get("status", "proposed"),
        risk_reason=po.get("risk_reason"),
        board_reason=po.get("board_reason"),
        fill_price=po.get("fill_price"),
        fill_ts=po.get("fill_ts"),
        extra=po.get("extra", {}) or {},
    )


def build_trade_graph(
    risk: RiskAgent,
    data_exec: DataExecAgent,
    logger: LoggerAgent,
    *,
    checkpointer: Any | None = None,
):
    """Compile and return the trade-flow graph.

    `checkpointer` is a LangGraph Saver (e.g. AsyncSqliteSaver). Pass None for
    in-memory mode (tests).
    """
    from langgraph.graph import END, START, StateGraph  # type: ignore

    g = StateGraph(TradeFlowState)

    async def risk_node(state: TradeFlowState) -> TradeFlowState:
        order = _order_from_state(state)
        account = AccountState(**state.get("account", {})) if state.get("account") else AccountState(
            account="paper", equity=100_000.0, peak_equity=100_000.0,
        )
        strategy_state = StrategyState(**state.get("strategy_state", {})) if state.get("strategy_state") else StrategyState(strategy=order.strategy)
        regime = state.get("regime")
        rvol = state.get("realized_vol")
        verdict: RiskVerdict = risk.evaluate(order, account, strategy_state, regime, rvol)
        # Optionally narrate (best-effort).
        try:
            verdict = await risk.narrate(order, verdict)
        except Exception as e:
            log.warning("risk narration failed: %s", e)

        # Persist verdict on the order object.
        if verdict.verdict == "resize" and verdict.new_qty is not None:
            order.qty = verdict.new_qty
        order.risk_reason = verdict.reason
        order.status = "risk_approved" if verdict.verdict != "reject" else "risk_rejected"
        logger.log_proposed_order(order)
        logger.log_event(
            actor="risk",
            kind=order.status,
            payload={
                "order_id": order.id, "verdict": verdict.verdict,
                "reason": verdict.reason, "narration": verdict.narration,
                "new_qty": verdict.new_qty,
                "flatten_account": verdict.flatten_account,
                "halt_strategy": verdict.halt_strategy,
            },
        )
        return {
            **state,
            "proposed_order": order.to_db_row() | {"extra": order.extra},
            "risk_verdict": {
                "verdict": verdict.verdict, "reason": verdict.reason,
                "narration": verdict.narration, "new_qty": verdict.new_qty,
                "flatten_account": verdict.flatten_account,
                "halt_strategy": verdict.halt_strategy,
            },
            "final_status": "risk_rejected" if verdict.verdict == "reject" else None,
        }

    def risk_route(state: TradeFlowState) -> str:
        v = (state.get("risk_verdict") or {}).get("verdict")
        return "approval" if v in ("approve", "resize") else "end_rejected"

    async def approval_node(state: TradeFlowState) -> TradeFlowState:
        order = _order_from_state(state)

        # ── Auto-execute check (skips Board interrupt when configured) ──────
        auto_exec, auto_reason = _check_auto_execute(order, logger=logger)
        if auto_exec:
            log.info(
                "approval_node: AUTO-EXECUTING order %s (%s %s %s) — %s",
                order.id, order.strategy, order.side, order.symbol, auto_reason,
            )
            notional = _order_notional(order)
            debit = notional if _is_debit(order) else 0.0
            logger.log_event(
                actor="board", kind="auto_executed",
                payload={
                    "order_id": order.id,
                    "symbol": order.symbol,
                    "strategy": order.strategy,
                    "side": order.side,
                    "qty": order.qty,
                    "action": (order.extra or {}).get("action", ""),
                    "notional_dollars": notional,
                    "debit_dollars": debit,    # used by daily-aggregate cap
                    "reason": auto_reason,
                },
            )
            return {**state, "board_decision": {
                "decision": "approve",
                "reason": f"[AUTO-EXECUTED] {auto_reason}",
                "new_qty": None,
            }}

        # ── Normal Board approval (LangGraph interrupt — waits for human) ───
        rv = state.get("risk_verdict") or {}
        from trading_corp.comms.approval_format import format_approval_message
        # Phase 1: rich multi-line body. Position context is None for now;
        # ceo_graph doesn't have direct broker / audit-log access here.
        # When we want LEAP details + prior-roll context, populate
        # `position_context` upstream (PMCC agent) and stash on
        # order.extra["position_context"] — the formatter reads it
        # transparently.
        position_ctx = (order.extra or {}).get("position_context")
        summary = format_approval_message(
            order=order,
            risk_verdict=rv,
            division=state.get("division") or order.strategy,
            position_context=position_ctx,
        )
        decision = request_board_approval(ApprovalRequest(
            order_id=order.id,
            summary=summary,
            detail={
                "order": order.to_db_row(),
                "risk_verdict": rv,
                "division": state.get("division", "default"),
            },
        ))
        return {**state, "board_decision": {
            "decision": decision.decision,
            "reason": decision.reason,
            "new_qty": decision.new_qty,
            "new_limit_price": decision.new_limit_price,
        }}

    def approval_route(state: TradeFlowState) -> str:
        d = (state.get("board_decision") or {}).get("decision")
        if d == "approve":
            return "execute"
        if d == "modify":
            # Re-evaluate risk on the modified qty.
            return "modify_then_risk"
        return "end_rejected"

    async def modify_then_risk_node(state: TradeFlowState) -> TradeFlowState:
        order = _order_from_state(state)
        bd = state.get("board_decision") or {}
        new_qty = bd.get("new_qty")
        new_limit_price = bd.get("new_limit_price")
        # B.5 — at least one of new_qty / new_limit_price must be supplied.
        # Both being None means the modify carried no actual change → reject
        # rather than silently re-running risk on the same shape (which would
        # loop forever if the Board kept hitting Modify with no inputs).
        if new_qty is None and new_limit_price is None:
            return {**state, "final_status": "board_rejected"}
        changes: list[str] = []
        if new_qty is not None:
            if new_qty <= 0:
                return {**state, "final_status": "board_rejected"}
            order.qty = float(new_qty)
            changes.append(f"qty={float(new_qty):g}")
        if new_limit_price is not None:
            if new_limit_price <= 0:
                return {**state, "final_status": "board_rejected"}
            order.limit_price = float(new_limit_price)
            changes.append(f"limit=${float(new_limit_price):.2f}")
        order.rationale = (
            order.rationale + f" | board-modified ({', '.join(changes)})"
        ).strip()
        return {
            **state,
            "proposed_order": order.to_db_row() | {"extra": order.extra},
            "risk_verdict": None,         # force re-evaluation
            "board_decision": None,
        }

    async def execute_node(state: TradeFlowState) -> TradeFlowState:
        order = _order_from_state(state)
        bd = state.get("board_decision") or {}
        order.board_reason = bd.get("reason") or "approved"
        order.status = "board_approved"
        logger.log_proposed_order(order)
        logger.log_event(
            actor="board", kind="board_approved",
            payload={"order_id": order.id, "reason": order.board_reason},
        )
        try:
            fill = await data_exec.place(order, division=state.get("division", "default"))
            return {
                **state,
                "fill": {
                    "order_id": fill.order_id, "symbol": fill.symbol,
                    "side": fill.side, "qty": fill.qty, "price": fill.price,
                    "ts": fill.ts, "venue": fill.venue,
                },
                "final_status": "filled",
            }
        except Exception as e:
            order.status = "cancelled"
            logger.log_proposed_order(order)
            logger.log_event(
                actor="data_exec", kind="execution_error",
                payload={"order_id": order.id, "error": str(e)},
            )
            return {**state, "final_status": "cancelled"}

    async def end_rejected_node(state: TradeFlowState) -> TradeFlowState:
        order = _order_from_state(state)
        bd = state.get("board_decision") or {}
        if bd.get("decision") in ("reject", "modify"):
            order.status = "board_rejected"
            order.board_reason = bd.get("reason") or "board rejected"
            logger.log_proposed_order(order)
            logger.log_event(
                actor="board", kind="board_rejected",
                payload={"order_id": order.id, "reason": order.board_reason},
            )
            return {**state, "final_status": "board_rejected"}
        # Risk-rejected case
        return {**state, "final_status": state.get("final_status") or "risk_rejected"}

    g.add_node("risk", risk_node)
    g.add_node("approval", approval_node)
    g.add_node("modify_then_risk", modify_then_risk_node)
    g.add_node("execute", execute_node)
    g.add_node("end_rejected", end_rejected_node)

    def modify_then_risk_route(state: TradeFlowState) -> str:
        # B.5 — when modify_then_risk_node bails (no usable new fields, or
        # invalid values), it pre-sets final_status='board_rejected' and
        # we route directly to end_rejected. Otherwise the modified order
        # re-runs through the risk gate as before. Without this branch
        # the unconditional edge to "risk" overwrote final_status and
        # the graph re-paused at approval — silently looping on no-op
        # modifies.
        if state.get("final_status") == "board_rejected":
            return "end_rejected"
        return "risk"

    g.add_edge(START, "risk")
    g.add_conditional_edges("risk", risk_route, {
        "approval": "approval", "end_rejected": "end_rejected",
    })
    g.add_conditional_edges("approval", approval_route, {
        "execute": "execute",
        "modify_then_risk": "modify_then_risk",
        "end_rejected": "end_rejected",
    })
    g.add_conditional_edges("modify_then_risk", modify_then_risk_route, {
        "risk": "risk",
        "end_rejected": "end_rejected",
    })
    g.add_edge("execute", END)
    g.add_edge("end_rejected", END)

    return g.compile(checkpointer=checkpointer) if checkpointer is not None else g.compile()
