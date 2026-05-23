"""Synchronous TradeConfirmation consultation from division webhook handlers.

Phase 1e. The consumer-side counterpart to
`agents/research/synthesis/trade_confirmation.py` (which is the in-graph
synthesis). This module is what division webhook handlers call between
`agent.on_alert()` returning a built ProposedOrder and the risk gate.

Architectural deviation from design §6.3 (worth noting):
  The design doc literally says "applies suggested_modifications to the
  action then calls _build_order". `agent.on_alert()` is sync (it's been
  sync since Phase 1) and making it async ripples through every caller +
  test. So this module instead applies modifications POST-`_build_order`,
  directly to the ProposedOrder fields. Functionally equivalent: the
  resulting order has the same shape that `_build_order` would have
  produced from the modified inputs. The qty recompute on
  size_pct_equity changes uses the same `account_equity * size_pct /
  price` formula as `_build_order`.

Verdict semantics (from design §3.5 / Q2):
  - confirm     → proceed with the original order
  - conditional → proceed with a modified order; audit
                  research_modifications_applied (with old/new snapshot)
  - push_back   → SKIP the order; audit research_tradeconf_pushback_acted_on;
                  caller is responsible for Telegram-notifying the Board
                  with the rationale
  - timeout     → fail-open; audit research_tradeconf_timeout; proceed
                  with the original order
  - error       → fail-open; audit research_tradeconf_error; proceed
                  with the original order
  - no_research → research_firm not wired (test envs); proceed silently

Returns a `ConsultResult` regardless of which path fires; this function
NEVER raises. The webhook handler just unpacks `result.decision` and
`result.order` and proceeds.

See planning/research_firm_design.md §1.3 (TradeConfirmation row),
§3.5, §Q2, §Q11, Phase 1e.
"""
from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

from trading_corp.agents.research import schemas as rs
from trading_corp.agents.research.engagement import (
    ResearchFirmDeps, run_engagement,
)
from trading_corp.agents.research.schemas import (
    EngagementSpec, SuggestedModifications, TradeConfirmation,
    TradeConfirmationScope,
)
from trading_corp.persistence.models import ProposedOrder

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RESEARCH_YAML = _REPO_ROOT / "config" / "research.yaml"

_DEFAULT_TIMEOUT_SECONDS = 8.0

VerdictKind = Literal[
    "confirm", "conditional", "push_back", "timeout", "error", "no_research",
]
Decision = Literal["proceed", "skip"]


@dataclass
class ConsultResult:
    decision: Decision
    """proceed: caller continues with `order`. skip: caller MUST NOT
    place the order (push_back path)."""

    order: ProposedOrder | None
    """The order to use downstream (possibly modified). None when
    decision='skip'."""

    verdict_kind: VerdictKind
    """Which terminal path fired. Webhook handler uses this to choose
    log/notify behavior."""

    confirmation: TradeConfirmation | None
    """The full product when the engagement returned one, else None."""

    rationale: str
    """Human-readable string for Telegram notify + division-side audit
    payload. Always populated."""

    applied_changes: dict = field(default_factory=dict)
    """When verdict_kind='conditional', captures the before/after of
    each modified field for the audit row. Empty otherwise."""


def _load_consult_config() -> dict:
    """Read trade_confirmation block from research.yaml. Returns {} on
    any error (defaults apply)."""
    try:
        with _RESEARCH_YAML.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("trade_confirmation_consult: yaml read failed: %s", e)
        return {}
    return cfg.get("trade_confirmation") or {}


def consult_enabled() -> bool:
    """Global kill-switch. Set `trade_confirmation.enabled: false` in
    research.yaml to bypass the consult entirely (e.g. in case of
    research-firm flapping)."""
    cfg = _load_consult_config()
    val = cfg.get("enabled")
    if val is None:
        return True
    return bool(val)


def consult_timeout_seconds() -> float:
    cfg = _load_consult_config()
    val = cfg.get("timeout_seconds")
    if isinstance(val, (int, float)) and val > 0:
        return float(val)
    return _DEFAULT_TIMEOUT_SECONDS


async def consult_research_for_trade_confirmation(
    *,
    order: ProposedOrder,
    payload: dict,
    research_firm: ResearchFirmDeps | None,
    logger_agent: Any,
    division_slug: str,
    asset_class: str,
    account_equity: float | None = None,
    timeout_s: float | None = None,
) -> ConsultResult:
    """Run a synchronous TradeConfirmation consult for `order`.

    Caller side:
      - `order` is the ProposedOrder returned by `agent.on_alert()`.
      - `payload` is the original alert payload (forwarded to the engagement
        as scope.context for traceability).
      - `research_firm=None` returns a no_research result so test envs and
        partial wirings work transparently.
      - `account_equity` is required for size_pct_equity modifications;
        if None, those modifications are flagged unsupported in the audit
        but the rest still apply.
      - `timeout_s=None` reads from config/research.yaml; pass an explicit
        value to override (used by tests).

    Never raises. Always returns a ConsultResult.
    """
    # Test envs / disabled state — proceed silently.
    if research_firm is None:
        return ConsultResult(
            decision="proceed",
            order=order,
            verdict_kind="no_research",
            confirmation=None,
            rationale="research_firm not wired; proceeding without consult",
        )
    if not consult_enabled():
        return ConsultResult(
            decision="proceed",
            order=order,
            verdict_kind="no_research",
            confirmation=None,
            rationale=(
                "trade_confirmation consult disabled via research.yaml; "
                "proceeding without consult"
            ),
        )

    timeout = timeout_s if timeout_s is not None else consult_timeout_seconds()

    proposed_action = _proposed_action_from_order(order)

    spec = EngagementSpec(
        requesting_division=division_slug,  # type: ignore[arg-type]
        product_type="trade_confirmation",
        asset_class=asset_class,  # type: ignore[arg-type]
        scope=TradeConfirmationScope(
            proposed_action=proposed_action,
            context={
                "alert_signal": payload.get("signal"),
                "alert_price": payload.get("price"),
                "alert_time": payload.get("time"),
                "alert_interval": payload.get("interval"),
            },
        ),
        triggered_by="division_agent",
        triggered_ts=datetime.now(timezone.utc).isoformat(),
    )

    # Run the engagement with a hard timeout. asyncio.wait_for cancels
    # the task on timeout; the engagement graph's own audit rows for
    # `research_engagement_started` etc. will already be on disk.
    try:
        product = await asyncio.wait_for(
            run_engagement(spec, deps=research_firm),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        _audit_division_side(
            logger_agent,
            actor=division_slug,
            kind=rs.AUDIT_KIND_TRADECONF_TIMEOUT,
            payload={
                "engagement_id": spec.engagement_id,
                "order_id": order.id,
                "symbol": order.symbol,
                "side": order.side,
                "timeout_seconds": timeout,
                "reason": "tradeconf_timeout",
            },
        )
        return ConsultResult(
            decision="proceed",
            order=order,
            verdict_kind="timeout",
            confirmation=None,
            rationale=(
                f"research consult timed out after {timeout:.1f}s; "
                f"fail-open with original order"
            ),
        )
    except Exception as e:
        log.exception("trade_confirmation_consult: run_engagement raised")
        _audit_division_side(
            logger_agent,
            actor=division_slug,
            kind=rs.AUDIT_KIND_TRADECONF_ERROR,
            payload={
                "engagement_id": spec.engagement_id,
                "order_id": order.id,
                "symbol": order.symbol,
                "error": str(e),
            },
        )
        return ConsultResult(
            decision="proceed",
            order=order,
            verdict_kind="error",
            confirmation=None,
            rationale=(
                f"research consult raised ({type(e).__name__}); "
                f"fail-open with original order"
            ),
        )

    if not isinstance(product, TradeConfirmation):
        # Engagement aborted (kill switch / out_of_scope / validation_failed
        # / no_action). The engagement-side audit already captured the
        # reason; the division side just notes it didn't get a verdict.
        _audit_division_side(
            logger_agent,
            actor=division_slug,
            kind=rs.AUDIT_KIND_TRADECONF_ERROR,
            payload={
                "engagement_id": spec.engagement_id,
                "order_id": order.id,
                "symbol": order.symbol,
                "error": (
                    f"engagement returned {type(product).__name__} "
                    f"instead of TradeConfirmation"
                ),
            },
        )
        return ConsultResult(
            decision="proceed",
            order=order,
            verdict_kind="error",
            confirmation=None,
            rationale=(
                "research engagement did not return a TradeConfirmation; "
                "fail-open with original order"
            ),
        )

    # ── Verdict branching ────────────────────────────────────────────────

    if product.verdict == "confirm":
        return ConsultResult(
            decision="proceed",
            order=order,
            verdict_kind="confirm",
            confirmation=product,
            rationale=product.rationale,
        )

    if product.verdict == "push_back":
        _audit_division_side(
            logger_agent,
            actor=division_slug,
            kind=rs.AUDIT_KIND_TRADECONF_PUSHBACK_ACTED_ON,
            payload={
                "engagement_id": product.engagement_id,
                "order_id": order.id,
                "symbol": order.symbol,
                "side": order.side,
                "rationale": product.rationale,
                "risks_flagged": product.risks_flagged,
            },
        )
        return ConsultResult(
            decision="skip",
            order=None,
            verdict_kind="push_back",
            confirmation=product,
            rationale=product.rationale,
        )

    # conditional
    if product.suggested_modifications is None:
        # Schema model_validator + synthesis defense-in-depth should make
        # this unreachable, but if it ever happens, treat as confirm.
        log.warning(
            "trade_confirmation_consult: conditional verdict with "
            "no suggested_modifications — treating as confirm",
        )
        return ConsultResult(
            decision="proceed",
            order=order,
            verdict_kind="confirm",
            confirmation=product,
            rationale=product.rationale,
        )

    modified_order, applied_changes = apply_suggested_modifications_to_order(
        order=order,
        mods=product.suggested_modifications,
        account_equity=account_equity,
        fallback_price=_payload_price(payload),
    )
    if applied_changes.get("side_flip_blocked"):
        _audit_division_side(
            logger_agent,
            actor=division_slug,
            kind="research_side_flip_blocked",
            payload={
                "engagement_id": product.engagement_id,
                "order_id": order.id,
                "symbol": order.symbol,
                "originating_side": applied_changes["side_flip_blocked"]["original"],
                "requested_side": applied_changes["side_flip_blocked"]["requested"],
                "rationale": product.rationale,
            },
        )
    _audit_division_side(
        logger_agent,
        actor=division_slug,
        kind=rs.AUDIT_KIND_TRADECONF_MODIFICATIONS_APPLIED,
        payload={
            "engagement_id": product.engagement_id,
            "order_id": order.id,
            "symbol": order.symbol,
            "rationale": product.rationale,
            "mods_rationale": product.suggested_modifications.rationale,
            "applied_changes": applied_changes,
        },
    )
    return ConsultResult(
        decision="proceed",
        order=modified_order,
        verdict_kind="conditional",
        confirmation=product,
        rationale=product.rationale,
        applied_changes=applied_changes,
    )


# ── Helpers ──────────────────────────────────────────────────────────────


def _proposed_action_from_order(order: ProposedOrder) -> dict:
    """Build the free-form proposed_action dict the synthesis prompt
    expects. Mirrors the post-_build_order shape so synthesis sees the
    full picture (price, size hints, rationale, division-specific extras
    via order.extra)."""
    action: dict[str, Any] = {
        "symbol": order.symbol,
        "side": order.side,
        "qty": order.qty,
        "order_type": order.order_type,
        "rationale": order.rationale,
        "strategy": order.strategy,
    }
    if order.limit_price is not None:
        action["entry_price"] = order.limit_price
    # Surface tier / size_pct_equity / etc. from the agent's own extras.
    extra = order.extra or {}
    for k in ("tier", "size_pct_equity", "stop_price", "stop_basis"):
        if k in extra and extra[k] is not None:
            action[k] = extra[k]
    return action


def _payload_price(payload: dict) -> float | None:
    raw = payload.get("price")
    if raw is None:
        return None
    try:
        val = float(raw)
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


def apply_suggested_modifications_to_order(
    *,
    order: ProposedOrder,
    mods: SuggestedModifications,
    account_equity: float | None,
    fallback_price: float | None,
) -> tuple[ProposedOrder, dict]:
    """Return a deep-copied ProposedOrder with `mods` applied + a dict
    capturing what changed. Pure: caller decides whether to use the
    result."""
    new_order = deepcopy(order)
    applied: dict[str, dict] = {}

    if mods.entry_price is not None:
        before = new_order.limit_price
        new_order.limit_price = float(mods.entry_price)
        # If the agent had emitted a market order, switch to limit so the
        # entry_price actually has effect downstream.
        if new_order.order_type != "limit":
            applied["order_type"] = {
                "before": new_order.order_type, "after": "limit",
            }
            new_order.order_type = "limit"
        applied["entry_price"] = {"before": before, "after": new_order.limit_price}

    if mods.side is not None:
        before = new_order.side
        if mods.side != before:
            # Side flip BLOCKED (capital-risk path). LLM cannot reverse
            # the originating signal's direction; the consult is a
            # narrator, not a decision-maker. Drop the mod; preserve
            # original side; surface in applied for the audit row.
            applied["side_flip_blocked"] = {
                "requested": mods.side, "original": before,
            }
            # Do NOT mutate new_order.side.

    if mods.size_pct_equity is not None:
        before_qty = new_order.qty
        # Recompute qty using the same formula _build_order uses:
        # notional = account_equity * size_pct; qty = notional / price.
        # If we don't know account_equity, leave qty alone but flag that
        # the size mod was unsupported.
        ref_price = (
            new_order.limit_price
            if new_order.limit_price is not None
            else fallback_price
        )
        if account_equity and account_equity > 0 and ref_price and ref_price > 0:
            new_qty = (float(account_equity) * float(mods.size_pct_equity)) / float(ref_price)
            new_order.qty = new_qty
            applied["qty"] = {
                "before": before_qty,
                "after": new_qty,
                "size_pct_equity": float(mods.size_pct_equity),
                "account_equity_basis": float(account_equity),
                "price_basis": float(ref_price),
            }
        else:
            applied["qty"] = {
                "before": before_qty,
                "after": before_qty,
                "size_pct_equity_requested": float(mods.size_pct_equity),
                "skipped_reason": (
                    "missing account_equity or price reference; qty unchanged"
                ),
            }
        # Also surface the requested size in extras for downstream visibility.
        extra = dict(new_order.extra or {})
        extra["research_size_pct_equity"] = float(mods.size_pct_equity)
        new_order.extra = extra

    # Always tag the engagement_id on the order so dashboard joins work
    # (per design Q12 — division does NOT modify CEO graph audit, just
    # propagates engagement_id via order.extra).
    extra = dict(new_order.extra or {})
    if "research_modification_rationale" not in extra and mods.rationale:
        extra["research_modification_rationale"] = mods.rationale
    new_order.extra = extra

    return new_order, applied


def _audit_division_side(
    logger_agent: Any,
    *,
    actor: str,
    kind: str,
    payload: dict,
) -> None:
    """Best-effort audit write. Failures here are logged but never
    raise — the consult must always return a ConsultResult."""
    if logger_agent is None:
        return
    try:
        logger_agent.log_event(actor=actor, kind=kind, payload=payload)
    except Exception as e:
        log.warning(
            "trade_confirmation_consult: audit write failed (kind=%s): %s",
            kind, e,
        )
