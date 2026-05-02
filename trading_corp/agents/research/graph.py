"""LangGraph build for the research firm engagement subgraph (v3).

Phase 1a-1 topology — `CandidateRecommendation` is the only product
emitted; other product types route through scope check's phase-pointer
rejection.

    START
      ↓
    kill_switch_check  ─── present ──▶ END (kill_switch_aborted)
      ↓ absent + engagement_started_ts pinned
    scope_check        ─── Layer 1 invalid ──▶ END (out_of_scope)
      ↓ valid
    registry_lookup    (expert_roles populated from EXPERT_REGISTRY)
      ↓
    shortlist          ─── empty ────▶ END (no_action)
      ↓ candidates picked
    analyze            ─── cost-cap exceeded ────▶ END (no_action)
      ↓                                         (cost_cap_exceeded)
    synthesize         (per product_type — only candidate wired in 1a-1)
      ↓
    post_validate      ─── Layer 2 invalid ──▶ END (validation_failed)
      ↓
    route              (per product_type emit)
      ↓
    END (*_emitted)    ← engagement_completed_ts pinned

Audit-before-branch is enforced by every node. `research_data_fetch_attempted`
fires ONLY on FAILURE per Refinement 4. Every terminal audit row carries
both `engagement_started_ts` and `engagement_completed_ts` per Q11.

A separate compiled graph from `graph/ceo_graph.py:build_trade_graph(...)`.
Production builds with `checkpointer=None` per design §2.4 (post v2's
'database is locked' incident).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.research import schemas as rs
from trading_corp.agents.research.experts import (
    EXPERT_REGISTRY, FundamentalExpert, MacroExpert, SentimentExpert,
    TechnicalExpert, experts_for, stub_expert_report,
)
from trading_corp.agents.research.debate_gate import evaluate_debate_gate
from trading_corp.agents.research.experts.base import Expert
from trading_corp.agents.research.experts.debate import run_bear, run_bull, run_judge
from trading_corp.agents.research.kill_switch import is_kill_switch_present
from trading_corp.agents.research.schemas import (
    CandidateRecommendation, CandidateScope, EngagementSpec, ExpertReport,
    PositionContextScope, ThesisScope, TradeConfirmationScope,
)
from trading_corp.agents.research.state import EngagementState
from trading_corp.agents.research.synthesis.candidate import (
    synthesize_candidate_recommendation,
)
from trading_corp.agents.research.synthesis.thesis import synthesize_thesis
from trading_corp.agents.research.synthesis.position_context import (
    synthesize_position_context,
)
from trading_corp.agents.research.synthesis.trade_confirmation import (
    synthesize_trade_confirmation,
)
from trading_corp.utils.time import iso, now_utc

log = logging.getLogger(__name__)

_RESEARCH_YAML = Path("config/research.yaml")
_STRATEGIES_YAML = Path("config/strategies.yaml")
_STARTER_UNIVERSE_DIR = Path("data/research_starter_universes")


# ──────────────────────────────────────────────────────────────────────────
# Config helpers
# ──────────────────────────────────────────────────────────────────────────


def _load_research_yaml() -> dict:
    """Read config/research.yaml. Returns {} on any error (defaults apply)."""
    try:
        with _RESEARCH_YAML.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        log.warning("config/research.yaml missing — using defaults")
        return {}
    except Exception as e:
        log.warning("config/research.yaml load failed: %s — using defaults", e)
        return {}


def _cost_caps(product_type: str) -> tuple[float, float]:
    """Return (soft, hard) cost caps for a product type. Defaults from
    design §6.6 if config missing."""
    cfg = _load_research_yaml().get("cost_caps", {})
    block = cfg.get(product_type, {}) or {}
    defaults = {
        "candidate_recommendation": (1.00, 2.50),
        "trade_confirmation":       (0.30, 0.75),
        "position_context":         (0.50, 1.00),
        "thesis":                   (0.50, 1.50),
    }.get(product_type, (1.00, 2.50))
    soft = block.get("soft_dollars") if block.get("soft_dollars") is not None else defaults[0]
    hard = block.get("hard_dollars") if block.get("hard_dollars") is not None else defaults[1]
    return float(soft or 0.0), float(hard or 0.0)


def _strategies_universe_for_key(target_universe_key: str) -> list[str]:
    """Resolve a config path like `robinhood_pmcc.scout.universe` to its list.

    Kept for backwards compatibility with callers that pre-populate
    `current_holdings` from strategies.yaml. Returns empty list if the
    key doesn't resolve."""
    try:
        with _STRATEGIES_YAML.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        log.warning("strategies.yaml load failed: %s", e)
        return []
    cur = data
    for part in target_universe_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return []
        cur = cur[part]
    if isinstance(cur, list):
        return [str(s).upper() for s in cur if s]
    return []


def _load_starter_universe(key: str) -> list[str]:
    """Load `data/research_starter_universes/{key}.json` and return symbols.

    Returns empty list on any error — caller writes `out_of_scope` or
    `no_action`."""
    p = _STARTER_UNIVERSE_DIR / f"{key}.json"
    if not p.exists():
        log.warning("starter universe missing: %s", p)
        return []
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        symbols = data.get("symbols") or []
        return [str(s).upper() for s in symbols if s]
    except Exception as e:
        log.warning("starter universe %s parse failed: %s", p, e)
        return []


# ──────────────────────────────────────────────────────────────────────────
# Graph builder
# ──────────────────────────────────────────────────────────────────────────


def build_engagement_graph(
    logger: LoggerAgent,
    *,
    experts: dict[str, Expert] | None = None,
    checkpointer: Any | None = None,
):
    """Compile and return the engagement subgraph.

    Phase 1a-1 wires `CandidateRecommendation` end-to-end; Phase 1b
    adds `Thesis`. Other product types route to a no-op terminal via
    Layer 1's phase-pointer reject so callers don't crash if they
    request a not-yet-implemented type.

    `experts` maps role-string → Expert instance. Missing roles fall
    back to `stub_expert_report(role, ...)`. Post-Phase 1c default
    supplies real `technical`, `macro`, `fundamental`, `sentiment`
    (all yfinance-backed; fundamental + sentiment refuse on
    non-equity symbols).

    Production builds with `checkpointer=None` per design §2.4.
    """
    from langgraph.graph import END, START, StateGraph  # type: ignore

    if experts is None:
        experts = {
            "technical": TechnicalExpert(),
            "macro": MacroExpert(),
            "fundamental": FundamentalExpert(),
            "sentiment": SentimentExpert(),
        }

    g = StateGraph(EngagementState)

    # ── Helpers used inside nodes ────────────────────────────────────────

    def _audit(state: EngagementState, kind: str, payload: dict) -> int | None:
        """Write a research-firm audit row. Returns row id (best-effort —
        None if we can't read it back, which is fine; the design uses
        engagement_id to join, not row ids)."""
        merged = {
            "engagement_id": state.get("engagement_id"),
            "requesting_division": state.get("requesting_division"),
            "product_type": state.get("product_type"),
            "asset_class": state.get("asset_class"),
            "triggered_by": state.get("triggered_by"),
            **payload,
        }
        try:
            logger.log_event(actor=rs.RESEARCH_ACTOR, kind=kind, payload=merged)
        except Exception as e:
            log.warning("research audit write failed (%s): %s", kind, e)
        return None

    def _terminal_payload(state: EngagementState, completed_ts: str, **extra) -> dict:
        """Q11: terminal rows pin both started + completed ts in payload."""
        return {
            "engagement_started_ts": state.get("engagement_started_ts"),
            "engagement_completed_ts": completed_ts,
            **extra,
        }

    # ── Nodes ────────────────────────────────────────────────────────────

    async def kill_switch_check_node(state: EngagementState) -> EngagementState:
        present, info = is_kill_switch_present()
        if present:
            ts = iso(now_utc())
            _audit(
                state,
                rs.AUDIT_KIND_ENGAGEMENT_KILLSWITCH,
                _terminal_payload(state, ts, **info),
            )
            return {
                **state,
                "kill_switch_present": True,
                "engagement_completed_ts": ts,
                "final_status": "kill_switch_aborted",
                "final_reason": f"kill switch present at {info.get('path')}",
            }
        # Q11: pin engagement_started_ts here — this is the "engagement
        # is now actually running" boundary.
        started = iso(now_utc())
        _audit(
            state,
            rs.AUDIT_KIND_ENGAGEMENT_STARTED,
            {"engagement_started_ts": started},
        )
        return {
            **state,
            "kill_switch_present": False,
            "engagement_started_ts": started,
        }

    def kill_switch_route(state: EngagementState) -> str:
        return "scope_check" if not state.get("kill_switch_present") else "end"

    async def scope_check_node(state: EngagementState) -> EngagementState:
        spec_d = state["engagement_spec"]
        try:
            spec = EngagementSpec.model_validate(spec_d)
        except Exception as e:
            ts = iso(now_utc())
            reason = f"invalid spec: {e}"
            _audit(
                state,
                rs.AUDIT_KIND_ENGAGEMENT_OUT_OF_SCOPE,
                _terminal_payload(state, ts, reason=reason),
            )
            return {
                **state,
                "scope_ok": False,
                "scope_reject_reason": reason,
                "engagement_completed_ts": ts,
                "final_status": "out_of_scope",
                "final_reason": reason,
            }
        ok, reason = _validate_scope_layer1(spec)
        if not ok:
            ts = iso(now_utc())
            _audit(
                state,
                rs.AUDIT_KIND_ENGAGEMENT_OUT_OF_SCOPE,
                _terminal_payload(state, ts, reason=reason),
            )
            return {
                **state,
                "scope_ok": False,
                "scope_reject_reason": reason,
                "engagement_completed_ts": ts,
                "final_status": "out_of_scope",
                "final_reason": reason,
            }
        return {**state, "scope_ok": True}

    def scope_route(state: EngagementState) -> str:
        if not state.get("scope_ok"):
            return "end"
        return "registry_lookup"

    async def registry_lookup_node(state: EngagementState) -> EngagementState:
        """Resolve `(product_type, asset_class)` → list of expert roles.

        Single-symbol products (Thesis today; PositionContext +
        TradeConfirmation in later phases) populate `candidates` directly
        from the scope's symbol so the analyze node's per-symbol fan-out
        works uniformly. CandidateRecommendation populates `candidates`
        downstream in shortlist_node from the starter universe.
        """
        ptype = state.get("product_type") or ""
        aclass = state.get("asset_class") or ""
        try:
            roles = experts_for(ptype, aclass)
        except KeyError as e:
            ts = iso(now_utc())
            reason = str(e)
            _audit(
                state,
                rs.AUDIT_KIND_ENGAGEMENT_OUT_OF_SCOPE,
                _terminal_payload(state, ts, reason=reason),
            )
            return {
                **state,
                "scope_ok": False,
                "scope_reject_reason": reason,
                "engagement_completed_ts": ts,
                "final_status": "out_of_scope",
                "final_reason": reason,
            }
        update: dict = {"expert_roles": roles}
        if ptype in ("thesis", "position_context", "trade_confirmation"):
            try:
                spec = EngagementSpec.model_validate(state["engagement_spec"])
            except Exception:
                spec = None
            if spec is not None:
                if isinstance(spec.scope, (ThesisScope, PositionContextScope)):
                    update["candidates"] = [spec.scope.symbol]
                elif isinstance(spec.scope, TradeConfirmationScope):
                    sym = (spec.scope.proposed_action or {}).get("symbol")
                    if sym:
                        update["candidates"] = [str(sym)]
        return {**state, **update}

    def registry_route(state: EngagementState) -> str:
        if state.get("final_status") == "out_of_scope":
            return "end"
        ptype = state.get("product_type")
        if ptype == "candidate_recommendation":
            return "shortlist"
        # Thesis, PositionContext, and TradeConfirmation are single-symbol;
        # registry_lookup populated candidates already, so they go straight
        # to analyze. Other product types haven't shipped — fall through
        # to no_action.
        if ptype in ("thesis", "position_context", "trade_confirmation"):
            return "analyze"
        return "no_action"

    async def shortlist_node(state: EngagementState) -> EngagementState:
        """Pick N candidates from the starter universe for the
        CandidateRecommendation, excluding current_holdings + earnings-
        window violations.

        Sample 4× n_candidates upfront so the earnings filter has slack;
        seed by engagement_id for reproducibility within one run.
        """
        import random
        spec = EngagementSpec.model_validate(state["engagement_spec"])
        scope = spec.scope
        assert isinstance(scope, CandidateScope)

        if not scope.starter_universe_key:
            # Phase 1a-1 always passes a starter key for cost predictability.
            ts = iso(now_utc())
            _audit(state, rs.AUDIT_KIND_NO_ACTION, _terminal_payload(
                state, ts,
                reason="no starter_universe_key on CandidateScope (Phase 1a-1 requires one)",
            ))
            return {
                **state,
                "candidates": [],
                "engagement_completed_ts": ts,
                "final_status": "no_action",
                "final_reason": "no starter_universe_key (Phase 1a-1)",
            }

        starter = _load_starter_universe(scope.starter_universe_key)
        held = {s.upper() for s in scope.current_holdings}
        universe = [s for s in starter if s.upper() not in held]

        if not universe:
            ts = iso(now_utc())
            _audit(state, rs.AUDIT_KIND_NO_ACTION, _terminal_payload(
                state, ts,
                reason="shortlist empty after starter-universe + current_holdings filter",
                starter_count=len(starter),
                holdings_count=len(held),
            ))
            return {
                **state,
                "candidates": [],
                "engagement_completed_ts": ts,
                "final_status": "no_action",
                "final_reason": "no candidates after universe filter",
            }

        rng = random.Random(spec.engagement_id)
        pool_size = min(len(universe), max(scope.n_candidates * 4, 8))
        pool = rng.sample(universe, pool_size)

        # Cheap earnings filter on the small pool only (not the ~500-symbol
        # starter — that was an 8-min disaster in v2).
        from trading_corp.utils.market_data import get_next_earnings
        cutoff = scope.earnings_buffer_days
        kept: list[str] = []
        now = datetime.now(timezone.utc)
        for sym in pool:
            try:
                next_earn = get_next_earnings(sym)
            except Exception:
                next_earn = None
            if next_earn is None:
                kept.append(sym)
                continue
            days = (next_earn - now).total_seconds() / 86400.0
            if days >= cutoff:
                kept.append(sym)

        target = min(len(kept), scope.n_candidates * 2)
        candidates = kept[:target]

        if not candidates:
            ts = iso(now_utc())
            _audit(state, rs.AUDIT_KIND_NO_ACTION, _terminal_payload(
                state, ts,
                reason="shortlist empty after earnings filter",
                starter_count=len(starter),
                holdings_count=len(held),
            ))
            return {
                **state,
                "candidates": [],
                "engagement_completed_ts": ts,
                "final_status": "no_action",
                "final_reason": "no candidates after filtering",
            }
        return {**state, "candidates": candidates}

    def shortlist_route(state: EngagementState) -> str:
        return "analyze" if state.get("candidates") else "end"

    async def analyze_node(state: EngagementState) -> EngagementState:
        """Run all registered experts on each candidate.

        Per-candidate experts run in parallel via `asyncio.gather`. Cost
        cap is checked between candidates so a runaway aborts at the
        next boundary, not mid-fan-out.
        """
        spec = EngagementSpec.model_validate(state["engagement_spec"])
        scope = spec.scope
        candidates = list(state.get("candidates") or [])
        roles = list(state.get("expert_roles") or [])
        soft_cap, hard_cap = _cost_caps(state["product_type"])
        cost = float(state.get("cost_dollars") or 0.0)
        warned = bool(state.get("cost_warning_emitted"))

        # Build the per-expert context dict once per spec — same for all
        # symbols.
        context: dict = {
            "asset_class": spec.asset_class,
            "requesting_division": spec.requesting_division,
        }
        if isinstance(scope, CandidateScope):
            context.update({
                "mandate": scope.mandate,
                "capacity_dollars": scope.capacity_dollars,
                "earnings_buffer_days": scope.earnings_buffer_days,
            })

        all_reports: list[dict] = []

        def _emit_fetch_failure(symbol: str):
            def _cb(*, source: str, ok: bool, error: str | None = None) -> None:
                # Refinement 4: record only failures.
                if ok:
                    return
                _audit(state, rs.AUDIT_KIND_DATA_FETCH, {
                    "source": source, "ok": ok, "error": error, "symbol": symbol,
                })
            return _cb

        for sym in candidates:
            if cost >= hard_cap:
                ts = iso(now_utc())
                _audit(state, rs.AUDIT_KIND_NO_ACTION, _terminal_payload(
                    state, ts,
                    reason="cost_cap_exceeded",
                    cost_so_far_dollars=cost,
                    hard_cap_dollars=hard_cap,
                    stopped_after_symbol=all_reports[-1]["symbol"] if all_reports else None,
                ))
                return {
                    **state,
                    "expert_reports": all_reports,
                    "expert_audit_row_ids": [],
                    "cost_dollars": cost,
                    "cost_warning_emitted": warned,
                    "engagement_completed_ts": ts,
                    "final_status": "no_action",
                    "final_reason": "cost_cap_exceeded",
                }
            if cost >= soft_cap and not warned:
                _audit(state, rs.AUDIT_KIND_COST_WARNING, {
                    "cost_so_far_dollars": cost,
                    "soft_cap_dollars": soft_cap,
                    "hard_cap_dollars": hard_cap,
                })
                warned = True

            # Fan out registered experts. Real experts run via .analyze();
            # missing roles get stubbed.
            real_tasks: list[tuple[str, Any]] = []
            stub_reports: list[ExpertReport] = []
            for role in roles:
                expert = experts.get(role)
                if expert is None:
                    stub_reports.append(stub_expert_report(role, spec.engagement_id, sym))
                else:
                    real_tasks.append((role, expert.analyze(
                        engagement_id=spec.engagement_id,
                        symbol=sym,
                        context=context,
                        on_data_fetch=_emit_fetch_failure(sym),
                    )))

            real_results: list[tuple[ExpertReport, float]] = []
            if real_tasks:
                gathered = await asyncio.gather(
                    *(t for _, t in real_tasks), return_exceptions=False,
                )
                real_results = list(gathered)

            for (role, _), (report, role_cost) in zip(real_tasks, real_results):
                cost += float(role_cost)
                _record_report(state, _audit, all_reports, sym, report)

            for stub_report in stub_reports:
                _record_report(state, _audit, all_reports, sym, stub_report)

        return {
            **state,
            "expert_reports": all_reports,
            "expert_audit_row_ids": [],
            "cost_dollars": cost,
            "cost_warning_emitted": warned,
        }

    def analyze_route(state: EngagementState) -> str:
        return "end" if state.get("final_status") == "no_action" else "debate_gate"

    async def debate_gate_node(state: EngagementState) -> EngagementState:
        """Variance/disagreement gate (Phase 1f, design §2.1 + Q10).

        Implementation note: the design diagram shows DEBATE_GATE +
        BULL + BEAR + JUDGE as four separate boxes. Collapsed here into
        one node — the audit rows (`research_debate_invoked` /
        `research_debate_completed`) make the structure observable in
        the dashboard without leaking transient `bull_case`/`bear_case`
        keys through the graph state.

        Multi-symbol products (CandidateRecommendation) skip the gate
        for first-cut: per-candidate debates are high-cost and the
        existing single-DebateOutcome state shape doesn't accommodate
        per-symbol mappings cleanly. If per-candidate debates become
        useful later, that's a Phase 1g+ extension; for now,
        CandidateRecommendation.debate_audit_row_id stays None.
        """
        ptype = state.get("product_type") or ""
        if ptype not in ("trade_confirmation", "position_context", "thesis"):
            return {
                **state,
                "debate_invoked": False,
                "debate_invoked_reason": None,
                "debate_outcome": None,
                "debate_audit_row_id": None,
            }

        spec = EngagementSpec.model_validate(state["engagement_spec"])
        # Single-symbol resolution per scope shape.
        if isinstance(spec.scope, (ThesisScope, PositionContextScope)):
            symbol = spec.scope.symbol
        elif isinstance(spec.scope, TradeConfirmationScope):
            symbol = (spec.scope.proposed_action or {}).get("symbol", "")
        else:
            symbol = ""

        reports_dicts = state.get("expert_reports") or []
        reports = [
            ExpertReport.model_validate(d)
            for d in reports_dicts
            if d.get("symbol") == symbol
        ]

        should_fire, reason = evaluate_debate_gate(reports)
        if not should_fire:
            return {
                **state,
                "debate_invoked": False,
                "debate_invoked_reason": None,
                "debate_outcome": None,
                "debate_audit_row_id": None,
            }

        # Cost gate: don't fire the (Opus-judged) debate if we're
        # already at hard cap. The synthesis cost gate downstream
        # double-checks anyway, but skipping the debate spend here is
        # the cheapest mitigation when budget is exhausted.
        soft_cap, hard_cap = _cost_caps(ptype)
        cost = float(state.get("cost_dollars") or 0.0)
        if cost >= hard_cap:
            return {
                **state,
                "debate_invoked": False,
                "debate_invoked_reason": (
                    f"gate would have fired ({reason}) but cost cap "
                    f"reached pre-debate"
                ),
                "debate_outcome": None,
                "debate_audit_row_id": None,
            }

        # Audit the gate firing BEFORE running bull/bear/judge so a
        # mid-debate crash still leaves the trail showing why the
        # debate was attempted.
        _audit(state, rs.AUDIT_KIND_DEBATE_INVOKED, {
            "engagement_id": spec.engagement_id,
            "symbol": symbol,
            "reason": reason,
            "expert_count": len(reports),
        })

        # Run bull + bear in parallel; judge sequentially after.
        try:
            (bull_text, bull_cost), (bear_text, bear_cost) = await asyncio.gather(
                run_bull(symbol=symbol, invoked_reason=reason or "", reports=reports),
                run_bear(symbol=symbol, invoked_reason=reason or "", reports=reports),
            )
        except Exception as e:
            log.warning("debate_gate: bull/bear failed: %s", e)
            return {
                **state,
                "debate_invoked": True,
                "debate_invoked_reason": reason,
                "debate_outcome": None,
                "debate_audit_row_id": None,
                "cost_dollars": cost,
            }

        cost += float(bull_cost) + float(bear_cost)

        try:
            outcome, judge_cost = await run_judge(
                engagement_id=spec.engagement_id,
                symbol=symbol,
                invoked_reason=reason or "",
                bull_case=bull_text,
                bear_case=bear_text,
            )
        except Exception as e:
            log.warning("debate_gate: judge failed: %s", e)
            return {
                **state,
                "debate_invoked": True,
                "debate_invoked_reason": reason,
                "debate_outcome": None,
                "debate_audit_row_id": None,
                "cost_dollars": cost,
            }

        cost += float(judge_cost)
        outcome_d = outcome.model_dump()
        debate_row_id = _audit(state, rs.AUDIT_KIND_DEBATE_COMPLETED, {
            "engagement_id": spec.engagement_id,
            "symbol": symbol,
            "outcome": outcome_d,
            "cost_dollars_delta": float(bull_cost) + float(bear_cost) + float(judge_cost),
        })

        return {
            **state,
            "debate_invoked": True,
            "debate_invoked_reason": reason,
            "debate_outcome": outcome_d,
            "debate_audit_row_id": debate_row_id,
            "cost_dollars": cost,
        }

    def debate_gate_route(state: EngagementState) -> str:
        # debate_gate_node never aborts the engagement on its own —
        # synthesis runs whether or not the debate fired. (A debate
        # crash leaves debate_outcome=None; synthesis handles that.)
        return "synthesize"

    async def synthesize_node(state: EngagementState) -> EngagementState:
        """Dispatch synthesis per product_type. Phase 1a-1 only wires
        CandidateRecommendation; other types are caught upstream by
        Layer 1 phase-pointer reject."""
        spec = EngagementSpec.model_validate(state["engagement_spec"])

        reports_by_sym: dict[str, list[ExpertReport]] = {}
        for d in state.get("expert_reports") or []:
            r = ExpertReport.model_validate(d)
            reports_by_sym.setdefault(r.symbol, []).append(r)

        product_d: dict | None = None
        llm_cost = 0.0

        if state["product_type"] == "candidate_recommendation":
            rec, llm_cost = await synthesize_candidate_recommendation(
                spec=spec,
                reports_by_symbol=reports_by_sym,
                expert_audit_row_ids=list(state.get("expert_audit_row_ids") or []),
            )
            product_d = rec.model_dump()
        elif state["product_type"] == "thesis":
            # Thesis is single-symbol; flatten reports for the symbol.
            assert isinstance(spec.scope, ThesisScope)
            sym_reports = reports_by_sym.get(spec.scope.symbol, [])
            thesis, llm_cost = await synthesize_thesis(
                spec=spec,
                reports=sym_reports,
                expert_audit_row_ids=list(state.get("expert_audit_row_ids") or []),
            )
            product_d = thesis.model_dump()
        elif state["product_type"] == "position_context":
            # PositionContext is single-symbol; flatten reports for the symbol.
            assert isinstance(spec.scope, PositionContextScope)
            sym_reports = reports_by_sym.get(spec.scope.symbol, [])
            pc, llm_cost = await synthesize_position_context(
                spec=spec,
                reports=sym_reports,
                expert_audit_row_ids=list(state.get("expert_audit_row_ids") or []),
            )
            product_d = pc.model_dump()
        elif state["product_type"] == "trade_confirmation":
            # TradeConfirmation is single-symbol; symbol comes from the
            # proposed_action dict on the scope.
            assert isinstance(spec.scope, TradeConfirmationScope)
            symbol = (spec.scope.proposed_action or {}).get("symbol", "")
            sym_reports = reports_by_sym.get(symbol, [])
            tc, llm_cost = await synthesize_trade_confirmation(
                spec=spec,
                reports=sym_reports,
                expert_audit_row_ids=list(state.get("expert_audit_row_ids") or []),
            )
            product_d = tc.model_dump()
        else:
            # Defensive — Layer 1 should have rejected. Route to no_action.
            ts = iso(now_utc())
            reason = f"synthesis for product_type={state['product_type']!r} not implemented yet"
            _audit(state, rs.AUDIT_KIND_NO_ACTION, _terminal_payload(
                state, ts, reason=reason,
            ))
            return {
                **state,
                "engagement_completed_ts": ts,
                "final_status": "no_action",
                "final_reason": reason,
            }

        # Cost gate AFTER synthesis (synthesis is end of LLM spend curve;
        # counting it pre-synth would let synthesis bypass the cap).
        cost = float(state.get("cost_dollars") or 0.0) + float(llm_cost)
        soft_cap, hard_cap = _cost_caps(state["product_type"])
        warned = bool(state.get("cost_warning_emitted"))
        if cost >= soft_cap and not warned:
            _audit(state, rs.AUDIT_KIND_COST_WARNING, {
                "cost_so_far_dollars": cost,
                "soft_cap_dollars": soft_cap,
                "hard_cap_dollars": hard_cap,
            })
            warned = True

        return {
            **state,
            "product": product_d,
            "cost_dollars": cost,
            "cost_warning_emitted": warned,
        }

    async def post_validate_node(state: EngagementState) -> EngagementState:
        """Layer 2 validation — re-run shape + scope checks on the
        emitted product. LLM output cannot bypass."""
        if state.get("final_status") == "no_action":
            return state
        spec = EngagementSpec.model_validate(state["engagement_spec"])
        product_d = state.get("product")
        if product_d is None:
            ts = iso(now_utc())
            reason = "no product produced"
            _audit(state, rs.AUDIT_KIND_VALIDATION_FAILED, _terminal_payload(
                state, ts, reason=reason,
            ))
            return {
                **state,
                "engagement_completed_ts": ts,
                "final_status": "validation_failed",
                "final_reason": reason,
            }
        ok, reason = _validate_product_layer2(spec, product_d)
        if not ok:
            ts = iso(now_utc())
            _audit(state, rs.AUDIT_KIND_VALIDATION_FAILED, _terminal_payload(
                state, ts, reason=reason,
            ))
            return {
                **state,
                "engagement_completed_ts": ts,
                "final_status": "validation_failed",
                "final_reason": reason,
            }
        return state

    def post_validate_route(state: EngagementState) -> str:
        if state.get("final_status") in ("validation_failed", "no_action"):
            return "end"
        ptype = state.get("product_type")
        if ptype == "candidate_recommendation":
            return "emit_candidate"
        if ptype == "thesis":
            return "emit_thesis"
        if ptype == "position_context":
            return "emit_position_context"
        if ptype == "trade_confirmation":
            return "emit_trade_confirmation"
        return "no_action"

    async def emit_candidate_node(state: EngagementState) -> EngagementState:
        """Audit the product BEFORE marking final_status (CLAUDE.md §1,
        design §4.2). Q11: stamp engagement_completed_ts in this row."""
        ts = iso(now_utc())
        product_d = state.get("product") or {}
        _audit(
            state,
            rs.AUDIT_KIND_CANDIDATE_RECOMMENDATION_EMITTED,
            _terminal_payload(
                state, ts,
                product=product_d,
                cost_dollars=float(state.get("cost_dollars") or 0.0),
            ),
        )
        return {
            **state,
            "engagement_completed_ts": ts,
            "final_status": "candidate_recommendation_emitted",
            "final_reason": None,
        }

    async def emit_thesis_node(state: EngagementState) -> EngagementState:
        """Audit the Thesis product before marking final_status (Phase 1b)."""
        ts = iso(now_utc())
        product_d = state.get("product") or {}
        _audit(
            state,
            rs.AUDIT_KIND_THESIS_EMITTED,
            _terminal_payload(
                state, ts,
                product=product_d,
                cost_dollars=float(state.get("cost_dollars") or 0.0),
            ),
        )
        return {
            **state,
            "engagement_completed_ts": ts,
            "final_status": "thesis_emitted",
            "final_reason": None,
        }

    async def emit_position_context_node(state: EngagementState) -> EngagementState:
        """Audit the PositionContext product before marking final_status
        (Phase 1d). Universal-write rule from §3.4.5: audit fires
        regardless of whether the caller surfaces or consumes the
        result."""
        ts = iso(now_utc())
        product_d = state.get("product") or {}
        _audit(
            state,
            rs.AUDIT_KIND_POSITION_CONTEXT_EMITTED,
            _terminal_payload(
                state, ts,
                product=product_d,
                cost_dollars=float(state.get("cost_dollars") or 0.0),
            ),
        )
        return {
            **state,
            "engagement_completed_ts": ts,
            "final_status": "position_context_emitted",
            "final_reason": None,
        }

    async def emit_trade_confirmation_node(state: EngagementState) -> EngagementState:
        """Audit the TradeConfirmation product before marking final_status
        (Phase 1e). The verdict is recorded in the audit payload so
        downstream analysis can join engagement_id ↔ order outcome."""
        ts = iso(now_utc())
        product_d = state.get("product") or {}
        _audit(
            state,
            rs.AUDIT_KIND_TRADE_CONFIRMATION_EMITTED,
            _terminal_payload(
                state, ts,
                product=product_d,
                cost_dollars=float(state.get("cost_dollars") or 0.0),
            ),
        )
        return {
            **state,
            "engagement_completed_ts": ts,
            "final_status": "trade_confirmation_emitted",
            "final_reason": None,
        }

    async def no_action_node(state: EngagementState) -> EngagementState:
        if state.get("final_status") is None:
            ts = iso(now_utc())
            ptype = state.get("product_type")
            reason = (
                f"product_type={ptype} not implemented in Phase 1a-1"
                if ptype != "candidate_recommendation"
                else "no_action"
            )
            _audit(state, rs.AUDIT_KIND_NO_ACTION, _terminal_payload(
                state, ts, reason=reason,
            ))
            return {
                **state,
                "engagement_completed_ts": ts,
                "final_status": "no_action",
                "final_reason": reason,
            }
        return state

    # ── Wire up ──────────────────────────────────────────────────────────

    g.add_node("kill_switch_check", kill_switch_check_node)
    g.add_node("scope_check", scope_check_node)
    g.add_node("registry_lookup", registry_lookup_node)
    g.add_node("shortlist", shortlist_node)
    g.add_node("analyze", analyze_node)
    g.add_node("debate_gate", debate_gate_node)
    g.add_node("synthesize", synthesize_node)
    g.add_node("post_validate", post_validate_node)
    g.add_node("emit_candidate", emit_candidate_node)
    g.add_node("emit_thesis", emit_thesis_node)
    g.add_node("emit_position_context", emit_position_context_node)
    g.add_node("emit_trade_confirmation", emit_trade_confirmation_node)
    g.add_node("no_action", no_action_node)

    g.add_edge(START, "kill_switch_check")
    g.add_conditional_edges(
        "kill_switch_check", kill_switch_route,
        {"scope_check": "scope_check", "end": END},
    )
    g.add_conditional_edges(
        "scope_check", scope_route,
        {"registry_lookup": "registry_lookup", "end": END},
    )
    g.add_conditional_edges(
        "registry_lookup", registry_route,
        {"shortlist": "shortlist", "analyze": "analyze",
         "no_action": "no_action", "end": END},
    )
    g.add_conditional_edges(
        "shortlist", shortlist_route,
        {"analyze": "analyze", "end": END},
    )
    g.add_conditional_edges(
        "analyze", analyze_route,
        {"debate_gate": "debate_gate", "end": END},
    )
    g.add_conditional_edges(
        "debate_gate", debate_gate_route,
        {"synthesize": "synthesize"},
    )
    g.add_edge("synthesize", "post_validate")
    g.add_conditional_edges(
        "post_validate", post_validate_route,
        {"emit_candidate": "emit_candidate", "emit_thesis": "emit_thesis",
         "emit_position_context": "emit_position_context",
         "emit_trade_confirmation": "emit_trade_confirmation",
         "no_action": "no_action", "end": END},
    )
    g.add_edge("emit_candidate", END)
    g.add_edge("emit_thesis", END)
    g.add_edge("emit_position_context", END)
    g.add_edge("emit_trade_confirmation", END)
    g.add_edge("no_action", END)

    return (
        g.compile(checkpointer=checkpointer)
        if checkpointer is not None
        else g.compile()
    )


# ──────────────────────────────────────────────────────────────────────────
# Per-report bookkeeping helper
# ──────────────────────────────────────────────────────────────────────────


def _record_report(
    state: EngagementState,
    audit: Any,
    all_reports: list[dict],
    sym: str,
    report: ExpertReport,
) -> None:
    """Append `report.model_dump()` to all_reports and write the
    completed/refused audit row."""
    all_reports.append(report.model_dump())
    if report.data_sufficiency:
        audit(state, rs.AUDIT_KIND_EXPERT_COMPLETED, {
            "expert_role": report.role,
            "symbol": sym,
            "confidence_score": report.confidence_score,
            "directional_lean": report.directional_lean,
            "evidence_count": len(report.key_evidence),
        })
    else:
        audit(state, rs.AUDIT_KIND_EXPERT_REFUSED, {
            "expert_role": report.role,
            "symbol": sym,
            "refusal_reason": report.refusal_reason,
        })


# ──────────────────────────────────────────────────────────────────────────
# Layer 1 + Layer 2 validators (design §6.3)
# ──────────────────────────────────────────────────────────────────────────


_KNOWN_DIVISIONS = {
    "robinhood_pmcc", "robinhood_ira", "robinhood_joint",
    "lord_otter", "market_cypher", "fidelity_options", "board",
}


def _validate_scope_layer1(spec: EngagementSpec) -> tuple[bool, str]:
    """Pre-cycle scope validator. Deterministic; LLM cannot bypass.

    See design §6.3 Layer 1.
    """
    if spec.requesting_division not in _KNOWN_DIVISIONS:
        return False, f"unknown requesting_division={spec.requesting_division!r}"

    # (product_type, asset_class) must resolve in the registry.
    if (spec.product_type, spec.asset_class) not in EXPERT_REGISTRY:
        return False, (
            f"no expert set registered for "
            f"({spec.product_type!r}, {spec.asset_class!r})"
        )

    if isinstance(spec.scope, CandidateScope):
        scope = spec.scope
        # Defensive (Pydantic enforces).
        if scope.n_candidates > 5:
            return False, f"n_candidates={scope.n_candidates} exceeds hard cap of 5"
        if scope.n_candidates < 1:
            return False, "n_candidates must be ≥ 1"
        if scope.capacity_dollars < 0:
            return False, "capacity_dollars must be ≥ 0"
        # starter_universe_key must point at a real file when present.
        if scope.starter_universe_key:
            starter_path = _STARTER_UNIVERSE_DIR / f"{scope.starter_universe_key}.json"
            if not starter_path.exists():
                return False, (
                    f"starter_universe_key={scope.starter_universe_key!r} → file "
                    f"{starter_path} not found"
                )
        return True, ""

    if isinstance(spec.scope, TradeConfirmationScope):
        action = spec.scope.proposed_action or {}
        if not action.get("symbol"):
            return False, "TradeConfirmationScope.proposed_action.symbol required"
        if not action.get("side"):
            return False, "TradeConfirmationScope.proposed_action.side required"
        if action.get("side") not in ("buy", "sell"):
            return False, (
                f"TradeConfirmationScope.proposed_action.side must be "
                f"'buy' or 'sell', got {action.get('side')!r}"
            )
        return True, ""

    if isinstance(spec.scope, PositionContextScope):
        scope = spec.scope
        if not (scope.symbol or "").strip():
            return False, "PositionContextScope.symbol required"
        return True, ""

    if isinstance(spec.scope, ThesisScope):
        scope = spec.scope
        if not (scope.symbol or "").strip():
            return False, "ThesisScope.symbol required"
        return True, ""

    return False, f"unknown scope type {type(spec.scope).__name__}"


def _validate_product_layer2(spec: EngagementSpec, product_d: dict) -> tuple[bool, str]:
    """Post-product validator (between synthesis and routing).

    Re-validates the product against the spec — LLM output cannot bypass.
    See design §6.3 Layer 2. Modifications fields are validated for
    structural plausibility ONLY; the actual policy gate is
    `RiskAgent.evaluate()` on the constructed ProposedOrder downstream.
    """
    if spec.product_type == "candidate_recommendation":
        try:
            rec = CandidateRecommendation.model_validate(product_d)
        except Exception as e:
            return False, f"product fails CandidateRecommendation schema: {e}"

        if not isinstance(spec.scope, CandidateScope):
            return False, "scope/product type mismatch"
        scope = spec.scope

        if rec.requesting_division != spec.requesting_division:
            return False, (
                f"requesting_division drift: spec={spec.requesting_division} "
                f"product={rec.requesting_division}"
            )
        if rec.asset_class != spec.asset_class:
            return False, (
                f"asset_class drift: spec={spec.asset_class} "
                f"product={rec.asset_class}"
            )
        if len(rec.candidates) > scope.n_candidates:
            return False, (
                f"product has {len(rec.candidates)} candidates; "
                f"scope.n_candidates={scope.n_candidates}"
            )
        held = {s.upper() for s in scope.current_holdings}
        for c in rec.candidates:
            if c.symbol.upper() in held:
                return False, (
                    f"candidate {c.symbol!r} appears in scope.current_holdings"
                )
        return True, ""

    if spec.product_type == "trade_confirmation":
        # Forward-compat structural check — Phase 1e wires synthesis.
        from trading_corp.agents.research.schemas import (
            SuggestedModifications, TradeConfirmation,
        )
        try:
            tc = TradeConfirmation.model_validate(product_d)
        except Exception as e:
            return False, f"product fails TradeConfirmation schema: {e}"
        if not isinstance(spec.scope, TradeConfirmationScope):
            return False, "scope/product type mismatch"
        if tc.subject_action.get("symbol") != spec.scope.proposed_action.get("symbol"):
            return False, (
                "subject_action.symbol drift: "
                f"spec={spec.scope.proposed_action.get('symbol')} "
                f"product={tc.subject_action.get('symbol')}"
            )
        # SuggestedModifications structural plausibility (already enforced by
        # Pydantic; re-checks defensively).
        sm: SuggestedModifications | None = tc.suggested_modifications
        if sm is not None:
            if sm.entry_price is not None and sm.entry_price <= 0:
                return False, "suggested_modifications.entry_price must be > 0"
            if sm.side is not None and sm.side not in ("buy", "sell"):
                return False, f"invalid side {sm.side!r}"
            if not sm.rationale:
                return False, "suggested_modifications.rationale required"
        return True, ""

    if spec.product_type == "position_context":
        from trading_corp.agents.research.schemas import PositionContext
        try:
            pc = PositionContext.model_validate(product_d)
        except Exception as e:
            return False, f"product fails PositionContext schema: {e}"
        if not isinstance(spec.scope, PositionContextScope):
            return False, "scope/product type mismatch"
        if pc.symbol != spec.scope.symbol:
            return False, (
                f"symbol drift: spec={spec.scope.symbol} product={pc.symbol}"
            )
        return True, ""

    if spec.product_type == "thesis":
        from trading_corp.agents.research.schemas import Thesis
        try:
            t = Thesis.model_validate(product_d)
        except Exception as e:
            return False, f"product fails Thesis schema: {e}"
        if not isinstance(spec.scope, ThesisScope):
            return False, "scope/product type mismatch"
        if t.symbol != spec.scope.symbol:
            return False, (
                f"symbol drift: spec={spec.scope.symbol} product={t.symbol}"
            )
        return True, ""

    return True, ""
