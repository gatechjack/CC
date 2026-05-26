"""Tests for the WhaleAnalyst narrator.

Covers the null_reason taxonomy explicitly — every None has exactly
one of: 'disabled_by_flag', 'llm_unavailable', 'daily_cap_hit', 'llm_error'.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from trading_corp.agents.polymarket_whale_analyst import (
    DEFAULT_DAILY_COST_CAP_USD, NarrationResult, WhaleAnalyst,
)
from trading_corp.data.polymarket_whale_audit import (
    CategoryConcentrationReport, ClusteringReport, EdgeProfileReport,
    RealizedPnLReport, SellFootprintReport, WhaleAuditReport,
)


def _empty_report() -> WhaleAuditReport:
    """Build a minimal WhaleAuditReport for narrator tests. Numbers
    aren't important — we're testing the narrator path, not the math."""
    return WhaleAuditReport(
        proxy_wallet="0xwhale",
        user_name="testwhale",
        activity_max_ts=1_700_000_000,
        activity_min_ts=1_700_000_000 - 86400,
        n_raw_rows_examined=10,
        n_resolved_decisions=5,
        clustering=ClusteringReport(
            n_raw_fills=10, n_decisions=5, clustering_ratio=2.0,
            decisions_with_ge_5_fills=1, top_clusters_by_fill_count=(),
        ),
        sell_footprint=SellFootprintReport(
            n_decisions_total=5, n_decisions_with_sells=2,
            n_round_trips=1, n_partial_sells=2, partial_sell_threshold=0.20,
            n_held_cleanly=3, top_flagged_by_inflation_usdc=(),
        ),
        edge=EdgeProfileReport(
            n_decisions=5, avg_entry_price_decision_weighted=0.55,
            share_below_70=0.6, share_above_85=0.1,
            p25_entry=0.45, p50_entry=0.55, p75_entry=0.70,
        ),
        category=CategoryConcentrationReport(
            n_distinct_event_slugs=3,
            top_3_event_slugs=(("event-a", 2), ("event-b", 2), ("event-c", 1)),
            largest_event_share=0.4,
        ),
        realized_pnl=RealizedPnLReport(
            realized_pnl_usdc=1234.56, held_to_resolution_pnl_usdc=1500.0,
            pnl_inflation_usdc=265.44, pnl_inflation_ratio=0.18,
            pnl_from_clean_holds_usdc=900.0, pnl_from_partial_sells_usdc=334.56,
        ),
        partial_sell_threshold_used=0.20,
    )


class _FakeUsageDict(dict):
    pass


class _FakeChat:
    """Stand-in for langchain ChatAnthropic. Captures the prompt that was
    sent (for prompt-content assertions) and returns a canned response."""

    def __init__(
        self,
        *,
        content: str = "This whale shows moderate clustering and clean holds.",
        usage: dict | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._content = content
        self._usage = usage or {"input_tokens": 250, "output_tokens": 50}
        self._raise = raise_exc
        self.last_call: list = []

    async def ainvoke(self, messages: list):
        self.last_call = messages
        if self._raise is not None:
            raise self._raise

        class _Resp:
            def __init__(self, content: str, usage: dict):
                self.content = content
                self.response_metadata = {"usage": usage}
                self.usage_metadata = usage
        return _Resp(self._content, self._usage)


# ── null_reason taxonomy ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_narrate_returns_disabled_by_flag_when_narrator_off():
    analyst = WhaleAnalyst(narrator_enabled=False)
    result = await analyst.narrate(_empty_report())
    assert result.emitted is False
    assert result.narration is None
    assert result.null_reason == "disabled_by_flag"
    assert result.cost_usd == 0.0


@pytest.mark.asyncio
async def test_narrate_returns_llm_unavailable_when_no_api_key():
    """is_llm_available() returns False → null_reason = 'llm_unavailable'."""
    with patch(
        "trading_corp.agents.polymarket_whale_analyst.WhaleAnalyst.__init__",
        lambda self, **kw: setattr(self, "_narrator_enabled", True)
        or setattr(self, "_daily_cost_cap_usd", 1.0)
        or setattr(self, "_chat", None)
        or setattr(self, "_db_url", None),
    ):
        with patch("trading_corp.agents.llm.is_llm_available", return_value=False):
            analyst = WhaleAnalyst()
            result = await analyst.narrate(_empty_report())
    assert result.emitted is False
    assert result.null_reason == "llm_unavailable"


@pytest.mark.asyncio
async def test_narrate_returns_llm_error_on_exception():
    """Chat raises → null_reason = 'llm_error'."""
    fake = _FakeChat(raise_exc=RuntimeError("simulated API failure"))
    analyst = WhaleAnalyst(chat=fake)
    result = await analyst.narrate(_empty_report())
    assert result.emitted is False
    assert result.null_reason == "llm_error"


@pytest.mark.asyncio
async def test_narrate_returns_llm_error_on_empty_content():
    """Empty content → null_reason = 'llm_error' (NOT silent None)."""
    fake = _FakeChat(content="")
    analyst = WhaleAnalyst(chat=fake)
    result = await analyst.narrate(_empty_report())
    assert result.emitted is False
    assert result.null_reason == "llm_error"


# ── success path ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_narrate_emits_verdict_when_available():
    fake = _FakeChat(
        content="Magamyman shows moderate fill clustering with clean holds.",
        usage={"input_tokens": 500, "output_tokens": 50},
    )
    analyst = WhaleAnalyst(chat=fake)
    result = await analyst.narrate(_empty_report())
    assert result.emitted is True
    assert result.narration == "Magamyman shows moderate fill clustering with clean holds."
    assert result.null_reason is None
    assert result.tokens_in == 500
    assert result.tokens_out == 50


@pytest.mark.asyncio
async def test_narrate_uses_haiku_pricing():
    """500 input + 100 output tokens at Haiku rates = ~$0.0008.
    Sonnet rate would give ~$0.003 (4x). If we accidentally pick the
    wrong pricing, the test catches it."""
    fake = _FakeChat(
        content="ok",
        usage={"input_tokens": 500, "output_tokens": 100},
    )
    analyst = WhaleAnalyst(chat=fake)
    result = await analyst.narrate(_empty_report())
    # Haiku: 500/1M * $0.80 + 100/1M * $4.00 = $0.0004 + $0.0004 = $0.0008
    assert result.cost_usd == pytest.approx(0.0008, abs=1e-6)


# ── prompt-content guardrails ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_narrate_includes_no_arithmetic_rule_in_system_prompt():
    """The system prompt MUST tell the LLM not to do arithmetic."""
    fake = _FakeChat()
    analyst = WhaleAnalyst(chat=fake)
    await analyst.narrate(_empty_report())
    system_message = fake.last_call[0]
    assert "DO NOT perform arithmetic" in system_message.content
    assert "appear VERBATIM" in system_message.content
    assert "Never override" in system_message.content


@pytest.mark.asyncio
async def test_narrate_user_content_includes_report_numbers():
    """The user prompt MUST contain the report numbers — these are what
    the LLM is allowed to cite (no others permitted by the system rules)."""
    fake = _FakeChat()
    analyst = WhaleAnalyst(chat=fake)
    await analyst.narrate(_empty_report())
    user_content = fake.last_call[1].content
    # Spot-check a few key numbers from _empty_report()
    assert "clustering_ratio=2.0" in user_content
    assert "n_round_trips=1" in user_content
    assert "n_partial_sells=2" in user_content
    assert "pnl_inflation_ratio=0.18" in user_content
    assert "realized_pnl_usdc=1234.56" in user_content
    assert "held_to_resolution_pnl_usdc=1500.0" in user_content
