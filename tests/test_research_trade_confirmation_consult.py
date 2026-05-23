"""Tests for the TradeConfirmation consult helper (Phase 1e).

Covers every verdict branch + the SuggestedModifications applicator.

The consult is the consumer-side bridge from a division webhook handler
into the research firm. It MUST never raise (fail-open is the design
contract per Q11), so tests are oriented around "did we get the right
ConsultResult shape and did the right audit row land."
"""
from __future__ import annotations

import asyncio

import pytest

from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.research import schemas
from trading_corp.agents.research.engagement import ResearchFirmDeps
from trading_corp.agents.research.graph import build_engagement_graph
from trading_corp.agents.research.trade_confirmation_consult import (
    ConsultResult,
    apply_suggested_modifications_to_order,
    consult_research_for_trade_confirmation,
    consult_enabled,
    consult_timeout_seconds,
)
from trading_corp.persistence.db import init_db
from trading_corp.persistence.models import ProposedOrder

# Reuse the deterministic fakes from the e2e test module.
from tests.test_research_engagement_e2e import (
    FakeMacroExpert,
    FakeSentimentExpert,
    FakeTechnicalExpert,
)


@pytest.fixture
def initialized_db(tmp_db: str) -> str:
    init_db(tmp_db)
    return tmp_db


@pytest.fixture
def logger_agent(initialized_db: str) -> LoggerAgent:
    return LoggerAgent(initialized_db)


@pytest.fixture
def deps_bullish(logger_agent: LoggerAgent) -> ResearchFirmDeps:
    """All experts bullish — synthesis returns confirm verdict."""
    experts = {
        "technical": FakeTechnicalExpert(lean="bullish", confidence=0.7),
        "macro": FakeMacroExpert(lean="bullish", confidence=0.6),
        "sentiment": FakeSentimentExpert(lean="bullish", confidence=0.6),
    }
    graph = build_engagement_graph(
        logger_agent, experts=experts, checkpointer=None,
    )
    return ResearchFirmDeps(
        logger_agent=logger_agent, experts=experts, graph=graph,
    )


@pytest.fixture
def deps_bearish(logger_agent: LoggerAgent) -> ResearchFirmDeps:
    """All experts bearish on a buy proposal — synthesis returns push_back."""
    experts = {
        "technical": FakeTechnicalExpert(lean="bearish", confidence=0.8),
        "macro": FakeMacroExpert(lean="bearish", confidence=0.7),
        "sentiment": FakeSentimentExpert(lean="bearish", confidence=0.6),
    }
    graph = build_engagement_graph(
        logger_agent, experts=experts, checkpointer=None,
    )
    return ResearchFirmDeps(
        logger_agent=logger_agent, experts=experts, graph=graph,
    )


def _order(symbol: str = "BTC/USD", side: str = "buy", qty: float = 0.01) -> ProposedOrder:
    return ProposedOrder(
        strategy="lord_otter",
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        qty=qty,
        order_type="market",
        rationale="alert-driven",
        extra={"tier": "standard", "size_pct_equity": 0.015},
    )


def _payload() -> dict:
    return {
        "signal": "otter_buy",
        "symbol": "BTC/USD",
        "price": "65000.0",
        "time": "2026-05-01T20:00:00+00:00",
        "interval": "3",
    }


# ── Config readers ──────────────────────────────────────────────────────


def test_consult_enabled_default():
    """Repo config has trade_confirmation.enabled=true."""
    assert consult_enabled() is True


def test_consult_timeout_default():
    """Repo config has trade_confirmation.timeout_seconds=8.0."""
    assert consult_timeout_seconds() == 8.0


# ── apply_suggested_modifications_to_order: pure function ───────────────


def test_apply_mods_entry_price_only():
    order = _order()
    mods = schemas.SuggestedModifications(
        entry_price=64000.0,
        rationale="wait for 1.5% pullback",
    )
    new_order, applied = apply_suggested_modifications_to_order(
        order=order, mods=mods, account_equity=None, fallback_price=None,
    )
    assert new_order is not order  # deep-copied
    assert order.limit_price is None  # original untouched
    assert new_order.limit_price == 64000.0
    assert new_order.order_type == "limit"  # auto-promoted from market
    assert "entry_price" in applied
    assert applied["entry_price"]["before"] is None
    assert applied["entry_price"]["after"] == 64000.0
    assert "order_type" in applied
    assert "research_modification_rationale" in (new_order.extra or {})


def test_apply_mods_size_pct_equity_with_equity_recomputes_qty():
    order = _order(qty=0.01)
    order.limit_price = 65000.0
    mods = schemas.SuggestedModifications(
        size_pct_equity=0.01,
        rationale="dial back to 1%",
    )
    new_order, applied = apply_suggested_modifications_to_order(
        order=order, mods=mods, account_equity=100_000.0, fallback_price=None,
    )
    # qty = (100_000 * 0.01) / 65_000 = ~0.01538
    assert abs(new_order.qty - (1000.0 / 65000.0)) < 1e-9
    assert applied["qty"]["before"] == 0.01
    assert applied["qty"]["size_pct_equity"] == 0.01


def test_apply_mods_size_pct_equity_without_equity_keeps_qty():
    """No account_equity available -> qty unchanged + audit notes the
    skip reason. Caller can still proceed with the original size."""
    order = _order(qty=0.01)
    mods = schemas.SuggestedModifications(
        size_pct_equity=0.005,
        rationale="halve it",
    )
    new_order, applied = apply_suggested_modifications_to_order(
        order=order, mods=mods, account_equity=None, fallback_price=None,
    )
    assert new_order.qty == 0.01
    assert applied["qty"]["after"] == 0.01
    assert "skipped_reason" in applied["qty"]


def test_apply_mods_side_flip_is_blocked():
    """LLM cannot reverse the originating signal's direction. The side mod
    must be dropped; original side is preserved; applied records the block."""
    order = _order(side="buy")
    mods = schemas.SuggestedModifications(
        side="sell",
        rationale="reverse direction",
    )
    new_order, applied = apply_suggested_modifications_to_order(
        order=order, mods=mods, account_equity=None, fallback_price=None,
    )
    # Side must NOT have been flipped.
    assert new_order.side == "buy"
    assert order.side == "buy"  # original untouched
    # Block recorded in applied for the audit row.
    assert applied.get("side_flip_blocked") == {"requested": "sell", "original": "buy"}


def test_apply_mods_no_changes_when_only_rationale():
    """Mods with all override fields None (rationale only) leaves order
    unchanged. Edge case: synthesis returned conditional but didn't
    actually specify what to change."""
    order = _order()
    mods = schemas.SuggestedModifications(rationale="be careful")
    new_order, applied = apply_suggested_modifications_to_order(
        order=order, mods=mods, account_equity=None, fallback_price=None,
    )
    assert new_order.symbol == order.symbol
    assert new_order.side == order.side
    assert new_order.qty == order.qty
    # Rationale still propagates to extras for downstream visibility.
    assert (new_order.extra or {}).get("research_modification_rationale") == "be careful"


# ── consult_research_for_trade_confirmation: full async paths ───────────


async def test_consult_no_research_firm_proceeds_silently(
    logger_agent: LoggerAgent, initialized_db: str,
):
    result = await consult_research_for_trade_confirmation(
        order=_order(),
        payload=_payload(),
        research_firm=None,
        logger_agent=logger_agent,
        division_slug="lord_otter",
        asset_class="crypto_spot",
    )
    assert result.decision == "proceed"
    assert result.verdict_kind == "no_research"
    assert result.order is not None
    assert result.confirmation is None
    # No audit row written for the no-research case.
    kinds = {e["kind"] for e in logger_agent.recent_events(limit=20)}
    assert "research_tradeconf_pushback_acted_on" not in kinds
    assert "research_modifications_applied" not in kinds


async def test_consult_disabled_via_config_proceeds_silently(
    logger_agent: LoggerAgent, deps_bullish: ResearchFirmDeps, monkeypatch,
):
    """Global kill-switch (research.yaml trade_confirmation.enabled=false)
    bypasses the consult entirely."""
    from trading_corp.agents.research import trade_confirmation_consult as mod
    monkeypatch.setattr(mod, "consult_enabled", lambda: False)

    result = await consult_research_for_trade_confirmation(
        order=_order(),
        payload=_payload(),
        research_firm=deps_bullish,
        logger_agent=logger_agent,
        division_slug="lord_otter",
        asset_class="crypto_spot",
    )
    assert result.decision == "proceed"
    assert result.verdict_kind == "no_research"


async def test_consult_confirm_proceeds_with_original_order(
    logger_agent: LoggerAgent, deps_bullish: ResearchFirmDeps,
):
    order = _order()
    result = await consult_research_for_trade_confirmation(
        order=order,
        payload=_payload(),
        research_firm=deps_bullish,
        logger_agent=logger_agent,
        division_slug="lord_otter",
        asset_class="crypto_spot",
    )
    assert result.decision == "proceed"
    assert result.verdict_kind == "confirm"
    assert result.order is order  # not deep-copied on confirm path
    assert result.confirmation is not None
    assert result.confirmation.verdict == "confirm"
    # Engagement audit row was written by the graph.
    kinds = [e["kind"] for e in logger_agent.recent_events(limit=40)]
    assert "research_trade_confirmation_emitted" in kinds


async def test_consult_push_back_skips_and_audits(
    logger_agent: LoggerAgent, deps_bearish: ResearchFirmDeps,
):
    order = _order()
    result = await consult_research_for_trade_confirmation(
        order=order,
        payload=_payload(),
        research_firm=deps_bearish,
        logger_agent=logger_agent,
        division_slug="lord_otter",
        asset_class="crypto_spot",
    )
    assert result.decision == "skip"
    assert result.verdict_kind == "push_back"
    assert result.order is None
    assert result.confirmation is not None
    assert result.confirmation.verdict == "push_back"
    assert result.rationale  # populated for telegram notify

    events = logger_agent.recent_events(limit=40)
    pushback = [
        e for e in events
        if e["kind"] == "research_tradeconf_pushback_acted_on"
    ]
    assert len(pushback) == 1
    payload = pushback[0]["payload"] or {}
    assert payload.get("symbol") == "BTC/USD"
    assert payload.get("side") == "buy"
    assert payload.get("order_id") == order.id
    assert payload.get("engagement_id")  # joinable to engagement-side row


async def test_consult_timeout_fails_open(
    logger_agent: LoggerAgent, deps_bullish: ResearchFirmDeps, monkeypatch,
):
    """Force a timeout by patching run_engagement to sleep past the
    timeout. Webhook handler must get back the original order."""
    from trading_corp.agents.research import trade_confirmation_consult as mod

    async def slow_engagement(spec, *, deps):
        await asyncio.sleep(2.0)
        return None  # never reaches here

    monkeypatch.setattr(mod, "run_engagement", slow_engagement)

    order = _order()
    result = await consult_research_for_trade_confirmation(
        order=order,
        payload=_payload(),
        research_firm=deps_bullish,
        logger_agent=logger_agent,
        division_slug="lord_otter",
        asset_class="crypto_spot",
        timeout_s=0.1,
    )
    assert result.decision == "proceed"
    assert result.verdict_kind == "timeout"
    assert result.order is order  # original, unmodified
    assert result.confirmation is None
    kinds = [e["kind"] for e in logger_agent.recent_events(limit=20)]
    assert "research_tradeconf_timeout" in kinds


async def test_consult_engagement_raises_fails_open(
    logger_agent: LoggerAgent, deps_bullish: ResearchFirmDeps, monkeypatch,
):
    from trading_corp.agents.research import trade_confirmation_consult as mod

    async def boom(spec, *, deps):
        raise RuntimeError("simulated graph failure")

    monkeypatch.setattr(mod, "run_engagement", boom)

    order = _order()
    result = await consult_research_for_trade_confirmation(
        order=order,
        payload=_payload(),
        research_firm=deps_bullish,
        logger_agent=logger_agent,
        division_slug="lord_otter",
        asset_class="crypto_spot",
    )
    assert result.decision == "proceed"
    assert result.verdict_kind == "error"
    assert result.order is order
    kinds = [e["kind"] for e in logger_agent.recent_events(limit=20)]
    assert "research_tradeconf_error" in kinds


async def test_consult_engagement_returns_none_fails_open(
    logger_agent: LoggerAgent, deps_bullish: ResearchFirmDeps, monkeypatch,
):
    """Engagement graph aborted (e.g. kill switch / out_of_scope) returns
    None or a non-TradeConfirmation product. The consult must fail-open
    and audit the surprise."""
    from trading_corp.agents.research import trade_confirmation_consult as mod

    async def returns_none(spec, *, deps):
        return None

    monkeypatch.setattr(mod, "run_engagement", returns_none)

    order = _order()
    result = await consult_research_for_trade_confirmation(
        order=order,
        payload=_payload(),
        research_firm=deps_bullish,
        logger_agent=logger_agent,
        division_slug="lord_otter",
        asset_class="crypto_spot",
    )
    assert result.decision == "proceed"
    assert result.verdict_kind == "error"
    assert result.order is order


async def test_consult_conditional_applies_modifications(
    logger_agent: LoggerAgent, deps_bullish: ResearchFirmDeps, monkeypatch,
):
    """Force a conditional verdict via a stub synthesizer and assert the
    order gets modified + audit row carries before/after."""
    from trading_corp.agents.research import graph as graph_mod
    from trading_corp.agents.research.schemas import SuggestedModifications

    async def conditional_synth(*, spec, reports, expert_audit_row_ids, **_kwargs):
        tc = schemas.TradeConfirmation(
            engagement_id=spec.engagement_id,
            requesting_division=spec.requesting_division,
            subject_action=dict(spec.scope.proposed_action),
            verdict="conditional",
            rationale="size feels heavy given vol regime",
            risks_flagged=["macro: VIX elevated"],
            suggested_modifications=SuggestedModifications(
                size_pct_equity=0.01,
                rationale="dial back to 1% from 1.5%",
            ),
        )
        return tc, 0.0

    monkeypatch.setattr(
        graph_mod, "synthesize_trade_confirmation", conditional_synth,
    )
    # Rebuild the graph to pick up the patched synthesizer.
    deps_bullish.graph = build_engagement_graph(
        logger_agent,
        experts=deps_bullish.experts,
        checkpointer=None,
    )

    order = _order(qty=0.01)
    order.limit_price = 65000.0
    result = await consult_research_for_trade_confirmation(
        order=order,
        payload=_payload(),
        research_firm=deps_bullish,
        logger_agent=logger_agent,
        division_slug="lord_otter",
        asset_class="crypto_spot",
        account_equity=100_000.0,
    )
    assert result.decision == "proceed"
    assert result.verdict_kind == "conditional"
    assert result.order is not None
    assert result.order is not order  # deep-copied
    # qty recomputed from new size_pct: (100k * 0.01) / 65000 = ~0.0154
    assert abs(result.order.qty - (1000.0 / 65000.0)) < 1e-9

    events = logger_agent.recent_events(limit=40)
    applied = [
        e for e in events
        if e["kind"] == "research_modifications_applied"
    ]
    assert len(applied) == 1
    assert applied[0]["payload"].get("applied_changes", {}).get("qty")


async def test_consult_conditional_with_side_flip_blocks_and_audits(
    logger_agent: LoggerAgent, deps_bullish: ResearchFirmDeps, monkeypatch,
):
    """When the LLM returns conditional with a side flip in suggested_modifications,
    the flip must be blocked (original side preserved) and a
    research_side_flip_blocked audit row must be written."""
    from trading_corp.agents.research import graph as graph_mod
    from trading_corp.agents.research.schemas import SuggestedModifications

    async def side_flip_synth(*, spec, reports, expert_audit_row_ids, **_kwargs):
        tc = schemas.TradeConfirmation(
            engagement_id=spec.engagement_id,
            requesting_division=spec.requesting_division,
            subject_action=dict(spec.scope.proposed_action),
            verdict="conditional",
            rationale="flip to sell instead",
            risks_flagged=[],
            suggested_modifications=SuggestedModifications(
                side="sell",
                rationale="reverse direction based on macro",
            ),
        )
        return tc, 0.0

    monkeypatch.setattr(
        graph_mod, "synthesize_trade_confirmation", side_flip_synth,
    )
    deps_bullish.graph = build_engagement_graph(
        logger_agent,
        experts=deps_bullish.experts,
        checkpointer=None,
    )

    order = _order(side="buy")
    result = await consult_research_for_trade_confirmation(
        order=order,
        payload=_payload(),
        research_firm=deps_bullish,
        logger_agent=logger_agent,
        division_slug="lord_otter",
        asset_class="crypto_spot",
        account_equity=100_000.0,
    )

    # Order must proceed (conditional path) with original side preserved.
    assert result.decision == "proceed"
    assert result.verdict_kind == "conditional"
    assert result.order is not None
    assert result.order.side == "buy"  # NOT flipped to "sell"

    # applied_changes must record the block.
    assert result.applied_changes.get("side_flip_blocked") == {
        "requested": "sell", "original": "buy",
    }

    # research_side_flip_blocked audit row must exist.
    events = logger_agent.recent_events(limit=60)
    flip_blocked = [
        e for e in events
        if e["kind"] == "research_side_flip_blocked"
    ]
    assert len(flip_blocked) == 1, (
        f"Expected 1 research_side_flip_blocked row, got {len(flip_blocked)}"
    )
    fb_payload = flip_blocked[0]["payload"]
    assert fb_payload.get("originating_side") == "buy"
    assert fb_payload.get("requested_side") == "sell"
    assert fb_payload.get("order_id") == order.id
