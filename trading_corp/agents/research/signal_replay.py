"""Manual research-firm replay for past TradingView signals.

Lets the Board (you) point at any historical `webhook_received` /
`alert_ignored` audit row and ask the research firm "what would you
have said about this signal?". Two motivations:

  1. **Inspect the consult code path** without committing to an
     always-on consult of every ignored alert. The TV→agent filter
     handles 95% of bear-no-position / bull-no-bias cases correctly;
     consulting the firm on every one is expensive and noisy.

  2. **Spot-check interesting signals** (e.g. multi-indicator
     convergences on the same bar) where the agent's tier classifier
     said "no" but the trader instinct says "but look at this."

Public API:
  - `synthesize_order_from_payload(payload, audit_row=None)` →
    ProposedOrder. Best-effort reconstruction of what an order WOULD
    look like if this signal had qualified — symbol/side from payload,
    placeholder qty + a `synthetic=True` flag in extra so audit
    consumers don't confuse this with a real order.
  - `replay_signal_research(audit_row, research_firm, logger_agent)` →
    ConsultResult. Builds the synthetic order, calls the existing
    `consult_research_for_trade_confirmation`, writes a
    `research_replay_completed` audit row tagged with the original
    audit_event_id so the trail is followable.

Never raises — caller-friendly. On failure, writes a
`research_replay_failed` audit row and returns a stub ConsultResult.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from trading_corp.agents.research.trade_confirmation_consult import (
    ConsultResult,
    consult_research_for_trade_confirmation,
)
from trading_corp.persistence.models import ProposedOrder

log = logging.getLogger(__name__)

# Bear-leaning signal-name fragments. Used to infer side='sell' for
# the synthetic order so the firm sees a plausibly-shaped trade
# proposal. Bull signals → 'buy'. Anything not matched defaults to
# 'buy' (long-bias is the default for the spot strategies today).
_BEAR_SIGNAL_FRAGMENTS = (
    "bear", "top", "red_diamond", "sell_circle", "spoon_bear",
    "money_bag_top",
)


def _infer_side(signal: str) -> str:
    s = (signal or "").lower()
    return "sell" if any(frag in s for frag in _BEAR_SIGNAL_FRAGMENTS) else "buy"


def synthesize_order_from_payload(
    payload: dict,
    *,
    audit_event_id: int | None = None,
) -> ProposedOrder:
    """Reconstruct a ProposedOrder shape from a TV webhook payload.

    The payload is what the webhook emitter wrote into the audit row
    (signal, symbol, price, time, etc.). Output is a ProposedOrder
    with `extra.synthetic=True` so anyone reading the order knows
    it didn't come from the live agent path.

    Quantity: fixed 0.01 placeholder. The research firm reasons about
    the signal/setup, not the size — sizing wouldn't affect the
    verdict and using a fixed placeholder avoids hand-rolling tier
    math that the live agent's filter already declined.
    """
    sig = str(payload.get("signal") or "")
    sym = str(payload.get("symbol") or payload.get("ticker") or "BTC/USD")
    price = payload.get("price")

    extra: dict = {
        "synthetic": True,
        "synthetic_source": "research_replay",
        "synthetic_audit_event_id": audit_event_id,
        "source_signal": sig,
        "tier": "replay_synth",                    # marker tier
    }
    if price is not None:
        extra["entry_reference_price"] = float(price)
    if "time" in payload:
        extra["alert_time"] = payload["time"]
    if "interval" in payload:
        extra["alert_interval"] = payload["interval"]

    return ProposedOrder(
        strategy=str(payload.get("strategy") or "unknown"),
        symbol=sym.upper(),
        side=_infer_side(sig),
        qty=0.01,
        rationale=f"replay-synth from signal '{sig}'",
        extra=extra,
    )


async def replay_signal_research(
    audit_row: dict,
    *,
    research_firm: Any,
    logger_agent: Any,
    division_slug: str | None = None,
) -> ConsultResult:
    """Run the research-firm consult against a synthetic order built
    from `audit_row.payload_json`. Writes a `research_replay_completed`
    audit row tagged with the original audit_event id.

    `audit_row` is a dict with keys: id, ts, actor, kind, payload_json
    (str). Typically loaded straight from a SELECT * FROM audit_event
    WHERE id = ?.

    Never raises; on synthesis or consult failure writes a
    `research_replay_failed` audit row and returns a stub ConsultResult
    with verdict_kind='error'.
    """
    audit_id = audit_row.get("id")
    try:
        payload_raw = audit_row.get("payload_json") or audit_row.get("payload") or "{}"
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else dict(payload_raw)
    except Exception as e:
        log.warning("signal_replay: payload parse failed for audit=%s: %s", audit_id, e)
        _write_failure_audit(logger_agent, audit_id, f"payload parse: {e}")
        return ConsultResult(
            decision="skip", order=None,
            verdict_kind="error", confirmation=None,
            rationale=f"replay failed: payload parse: {e}",
        )

    order = synthesize_order_from_payload(payload, audit_event_id=audit_id)

    # Asset class is hard-coded to crypto_spot for now — the only
    # divisions that emit webhook_received / alert_ignored are
    # lord_otter and market_cypher, both on coinbase_spot. When
    # equity-side TV strategies land, this needs to flex per division.
    asset_class = "crypto_spot"
    # `requesting_division` on EngagementSpec is misnamed — it's actually
    # the STRATEGY/agent slug (lord_otter / market_cypher / robinhood_pmcc
    # / etc.), not the broker-account division slug. Use payload.strategy
    # (or the audit row's actor as fallback) to satisfy pydantic's literal
    # enum.
    requester = (
        payload.get("strategy")
        or audit_row.get("actor")
        or "lord_otter"   # last-ditch default
    )

    try:
        result = await consult_research_for_trade_confirmation(
            order=order,
            payload=payload,
            research_firm=research_firm,
            logger_agent=logger_agent,
            division_slug=requester,
            asset_class=asset_class,
            # Replay isn't on the live order path — no urgency to bound at 8s
            # like the webhook consult does. Multi-expert engagements typically
            # take 15-30s; 60s gives them room to finish without timing out.
            timeout_s=60.0,
        )
    except Exception as e:
        log.exception("signal_replay: consult raised for audit=%s", audit_id)
        _write_failure_audit(logger_agent, audit_id, f"consult raised: {type(e).__name__}: {e}")
        return ConsultResult(
            decision="skip", order=None,
            verdict_kind="error", confirmation=None,
            rationale=f"replay failed: consult raised {type(e).__name__}: {e}",
        )

    # Audit completion. Always write — even on no_research / skip /
    # timeout outcomes — so the dashboard can show "we tried, and
    # here's what happened."
    try:
        logger_agent.log_event(
            actor="research_replay",
            kind="research_replay_completed",
            payload={
                "source_audit_event_id": audit_id,
                "verdict_kind": result.verdict_kind,
                "decision": result.decision,
                "rationale": (result.rationale or "")[:500],
                "applied_changes": result.applied_changes or {},
                "synthetic_order_id": order.id,
                "signal": payload.get("signal"),
                "symbol": payload.get("symbol"),
                "alert_price": payload.get("price"),
                "alert_time": payload.get("time"),
            },
        )
    except Exception:
        log.exception("signal_replay: audit-completed write failed")

    return result


def _write_failure_audit(logger_agent: Any, audit_id: Any, reason: str) -> None:
    try:
        logger_agent.log_event(
            actor="research_replay",
            kind="research_replay_failed",
            payload={
                "source_audit_event_id": audit_id,
                "reason": reason[:500],
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        log.exception("signal_replay: failure-audit write itself failed")
