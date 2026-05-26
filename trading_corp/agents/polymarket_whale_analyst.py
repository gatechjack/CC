"""Plain-language verdict narration for the on-demand Polymarket whale audit.

Mirror of `RiskAgent.narrate()` (`agents/risk.py:437`): the deterministic
computation is canonical (computed by `data/polymarket_whale_audit.py`);
the LLM is fed the COMPUTED summary numbers and writes a 2-4 sentence
plain-language verdict. The LLM never performs arithmetic, never
overrides any of the deterministic flags, and never recommends an
action — it just describes what the numbers say in operator language.

Fail-soft. If the LLM is unavailable or the daily cost cap has fired,
`narrate()` returns `(None, 0.0, <null_reason>)` and the caller renders
the deterministic report without a verdict line. The `null_reason`
explains WHY the narration is missing — operator never sees a silent
None.

null_reason taxonomy (exhaustive — every None has exactly one reason):
  - "disabled_by_flag":  caller passed --no-llm (CLI) or narrator_enabled=False
  - "llm_unavailable":   ANTHROPIC_API_KEY unset OR langchain-anthropic uninstalled
  - "daily_cap_hit":     accumulated 24h LLM spend has exceeded the soft cap
  - "llm_error":         the API call raised — exception logged, narration falls back

Cost cap pattern mirrors the research firm's cost.py: each call computes
its own cost via `cost_for_anthropic_usage` and accumulates against a
daily-keyed `agent_state` slot. The cap is `$1.00 / day` by default
(~500 analyses at the per-call ~$0.0013 budget). When the cap fires,
subsequent calls in the same UTC day return None with reason
"daily_cap_hit" — the deterministic audit still runs.

Daily-cap slot:
  agent_state[agent='polymarket_whale_analyst',
              key='cost_today:YYYY-MM-DD'] = {'usd': <float>}
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from trading_corp.data.polymarket_whale_audit import WhaleAuditReport

log = logging.getLogger(__name__)

DEFAULT_DAILY_COST_CAP_USD = 1.00
DEFAULT_MAX_OUTPUT_TOKENS = 200

# Vocabulary cues fed to the LLM in the system prompt. Each tuple is
# (condition_description, narrator_phrase). The LLM is told to apply
# these cues when the conditions hold. Kept in one place so a future
# reviewer can tweak phrasing without re-deriving the thresholds.
_NARRATOR_VOCAB_CUES = (
    ("clustering_ratio > 3", "heavily clustered fills"),
    ("clustering_ratio in 1.5..3", "moderate fill clustering"),
    ("n_partial_sells / n_decisions > 0.3", "frequent partial exits"),
    ("share_above_85 > 0.5", "favorite-farming entry profile"),
    ("share_below_70 > 0.6", "sharp/contrarian entry profile"),
    ("largest_event_share > 0.5", "single-event concentration"),
    ("pnl_inflation_ratio > 0.5", "PnL largely from round-trip churn or partial exits, not held conviction"),
    ("pnl_inflation_ratio < 0.1", "PnL is mostly held-to-resolution cash flow"),
)

_SYSTEM_PROMPT = """You narrate a Polymarket whale's audit report in 2-4 plain-language sentences.

CRITICAL RULES:
- DO NOT perform arithmetic. Every number you cite must appear VERBATIM in the user message.
  If you need a percentage and only a ratio is provided, do not compute it — describe the ratio instead.
- Never override the deterministic flags. If the report says n_partial_sells=8, acknowledge 8
  partial sells; do not "explain them away."
- Tone: factual, dispassionate, like a quant analyst summarizing a Bloomberg screen for a busy operator.
  No hedging language ("seems", "perhaps", "might") unless the report's data is genuinely ambiguous.
- Lead with the most decision-relevant fact for the operator. Priority order:
  1) pnl_inflation_ratio if > 0.3 (the PnL is not what it looks like)
  2) largest_event_share if > 0.5 (single-event concentration risk)
  3) clustering_ratio if > 3 (sample-size illusion risk)
  4) edge-profile signal (sharp/contrarian vs favorite-farmer)
  5) clean baseline if none of the above
- Do not recommend an action. The operator decides; you describe.

Vocabulary cues (apply when the condition is satisfied):
- clustering_ratio > 3 → "heavily clustered fills"
- clustering_ratio 1.5-3 → "moderate fill clustering"
- partial-sell-share > 0.3 → "frequent partial exits"
- share_above_85 > 0.5 → "favorite-farming entry profile"
- share_below_70 > 0.6 → "sharp/contrarian entry profile"
- largest_event_share > 0.5 → "single-event concentration"
- pnl_inflation_ratio > 0.5 → "PnL largely from round-trip churn or partial exits, not held conviction"
- pnl_inflation_ratio < 0.1 → "PnL is mostly held-to-resolution cash flow"

Output: 2-4 sentences of prose. No bullets, no headings, no markdown."""


def _build_user_content(report: WhaleAuditReport) -> str:
    """Serialize the audit report as a structured plaintext block.

    All numbers used by the narrator come from this string — there is
    no other channel. The format is stable so the LLM never has to
    interpret schema variations across calls.
    """
    span_sec = (report.activity_max_ts - report.activity_min_ts) if report.activity_max_ts > 0 else 0
    span_days = span_sec / 86400.0
    c = report.clustering
    s = report.sell_footprint
    e = report.edge
    cat = report.category
    p = report.realized_pnl

    partial_share = (s.n_partial_sells / s.n_decisions_total) if s.n_decisions_total else 0.0

    top_3_event_text = ", ".join(f"{slug}={n}" for slug, n in cat.top_3_event_slugs) or "(none)"

    lines = [
        f"Whale: {report.user_name or '<no name>'} ({report.proxy_wallet[:10]}...)",
        f"Window: {report.n_resolved_decisions} resolved decisions over {span_days:.1f} days "
        f"({report.n_raw_rows_examined} raw activity rows examined)",
        "",
        "Clustering:",
        f"  n_raw_fills={c.n_raw_fills}  n_decisions={c.n_decisions}  clustering_ratio={c.clustering_ratio}",
        f"  decisions_with_5_or_more_fills={c.decisions_with_ge_5_fills}",
        "",
        "Sell footprint:",
        f"  n_decisions={s.n_decisions_total}  n_with_sells={s.n_decisions_with_sells}",
        f"  n_round_trips={s.n_round_trips}  (sell_share >= 0.95 — fully exited)",
        f"  n_partial_sells={s.n_partial_sells}  (sell_share >= {s.partial_sell_threshold:.2f}, "
        f"includes round-trips; partial_share={partial_share:.4f})",
        f"  n_held_cleanly={s.n_held_cleanly}",
        "",
        "Edge profile:",
        f"  avg_entry_price_decision_weighted={e.avg_entry_price_decision_weighted}",
        f"  share_below_70={e.share_below_70}  share_above_85={e.share_above_85}",
        f"  entry p25/p50/p75 = {e.p25_entry} / {e.p50_entry} / {e.p75_entry}",
        "",
        "Category concentration:",
        f"  n_distinct_event_slugs={cat.n_distinct_event_slugs}",
        f"  largest_event_share={cat.largest_event_share}",
        f"  top_3_event_slugs: {top_3_event_text}",
        "",
        "Realized PnL (REDEEM-grounded):",
        f"  realized_pnl_usdc={p.realized_pnl_usdc}",
        f"  held_to_resolution_pnl_usdc={p.held_to_resolution_pnl_usdc}  "
        "(watchlist's view; assumes hold-to-resolution)",
        f"  pnl_inflation_usdc={p.pnl_inflation_usdc}  pnl_inflation_ratio={p.pnl_inflation_ratio}",
        f"  pnl_from_clean_holds_usdc={p.pnl_from_clean_holds_usdc}",
        f"  pnl_from_partial_sells_usdc={p.pnl_from_partial_sells_usdc}",
        "",
        "Write 2-4 sentences.",
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class NarrationResult:
    """What `WhaleAnalyst.narrate` returns to the caller."""
    narration: str | None
    null_reason: str | None
    cost_usd: float
    tokens_in: int
    tokens_out: int

    @property
    def emitted(self) -> bool:
        return self.narration is not None


class WhaleAnalyst:
    """LLM narrator for `WhaleAuditReport`.

    Optional dependency-injection for tests: pass `chat=<fake>` to
    bypass the langchain ChatAnthropic build. The fake just needs an
    awaitable `ainvoke([sys, user])` returning an object with
    `.content` and `.response_metadata` / `.usage_metadata`.

    The `db_url` is optional — when provided, the daily cost cap is
    enforced against `agent_state`. When None, the cap check is skipped
    (useful for unit tests that don't want a sqlite dependency).
    """

    AGENT_NAME = "polymarket_whale_analyst"
    AGENT_STATE_KEY_PREFIX = "cost_today:"

    def __init__(
        self,
        *,
        narrator_enabled: bool = True,
        daily_cost_cap_usd: float = DEFAULT_DAILY_COST_CAP_USD,
        chat: object | None = None,
        db_url: str | None = None,
    ) -> None:
        self._narrator_enabled = narrator_enabled
        self._daily_cost_cap_usd = daily_cost_cap_usd
        self._chat = chat
        self._db_url = db_url

    async def narrate(self, report: WhaleAuditReport) -> NarrationResult:
        # Gate 1: disabled by caller
        if not self._narrator_enabled:
            return NarrationResult(
                narration=None, null_reason="disabled_by_flag",
                cost_usd=0.0, tokens_in=0, tokens_out=0,
            )
        # Gate 2: daily cost cap (if db_url provided)
        if self._db_url is not None and self._is_daily_cap_hit():
            return NarrationResult(
                narration=None, null_reason="daily_cap_hit",
                cost_usd=0.0, tokens_in=0, tokens_out=0,
            )
        # Gate 3: LLM availability
        if self._chat is None:
            from trading_corp.agents.llm import build_chat_model, is_llm_available
            if not is_llm_available():
                return NarrationResult(
                    narration=None, null_reason="llm_unavailable",
                    cost_usd=0.0, tokens_in=0, tokens_out=0,
                )
            try:
                self._chat = build_chat_model(
                    self.AGENT_NAME, max_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                )
            except Exception as e:
                log.warning("Failed to build chat model for whale_analyst: %s", e)
                return NarrationResult(
                    narration=None, null_reason="llm_unavailable",
                    cost_usd=0.0, tokens_in=0, tokens_out=0,
                )
        # Gate 4: actually call the LLM
        try:
            from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore
            sys = SystemMessage(content=_SYSTEM_PROMPT)
            user = HumanMessage(content=_build_user_content(report))
            resp = await self._chat.ainvoke([sys, user])  # type: ignore[attr-defined]
            narration_text = ""
            if hasattr(resp, "content") and resp.content:
                narration_text = str(resp.content).strip()
            usage = self._extract_usage(resp)
            tokens_in = int(usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0) or 0)
            tokens_out = int(usage.get("output_tokens", 0) or usage.get("completion_tokens", 0) or 0)
            cost = self._compute_cost(usage)
            if self._db_url is not None:
                self._add_to_daily_cost(cost)
            if not narration_text:
                # The LLM returned empty content — treat as error so the
                # caller renders the deterministic report without a
                # spurious blank verdict.
                return NarrationResult(
                    narration=None, null_reason="llm_error",
                    cost_usd=cost, tokens_in=tokens_in, tokens_out=tokens_out,
                )
            return NarrationResult(
                narration=narration_text, null_reason=None,
                cost_usd=cost, tokens_in=tokens_in, tokens_out=tokens_out,
            )
        except Exception as e:
            log.warning("Whale narration failed: %s", e)
            return NarrationResult(
                narration=None, null_reason="llm_error",
                cost_usd=0.0, tokens_in=0, tokens_out=0,
            )

    # ── cap accounting (uses agent_state when db_url is set) ──────────

    def _today_key(self) -> str:
        return self.AGENT_STATE_KEY_PREFIX + datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _is_daily_cap_hit(self) -> bool:
        try:
            from trading_corp.persistence.db import load_agent_state
            loaded = load_agent_state(self.AGENT_NAME, self._today_key(), db_url=self._db_url)
        except Exception as e:
            log.warning("whale_analyst daily-cap read failed: %s", e)
            return False
        if not loaded:
            return False
        value = loaded[0] if isinstance(loaded, tuple) else loaded
        try:
            usd = float(value.get("usd", 0.0)) if isinstance(value, dict) else 0.0
        except (TypeError, ValueError):
            usd = 0.0
        return usd >= self._daily_cost_cap_usd

    def _add_to_daily_cost(self, delta_usd: float) -> None:
        try:
            from trading_corp.persistence.db import load_agent_state, set_agent_state
            loaded = load_agent_state(self.AGENT_NAME, self._today_key(), db_url=self._db_url)
            current = 0.0
            if loaded:
                value = loaded[0] if isinstance(loaded, tuple) else loaded
                if isinstance(value, dict):
                    try:
                        current = float(value.get("usd", 0.0))
                    except (TypeError, ValueError):
                        current = 0.0
            new_value = {"usd": current + delta_usd}
            set_agent_state(
                self.AGENT_NAME, self._today_key(), new_value, db_url=self._db_url,
            )
        except Exception as e:
            log.warning("whale_analyst daily-cap write failed: %s", e)

    # ── usage / cost helpers ──────────────────────────────────────────

    @staticmethod
    def _extract_usage(resp: object) -> dict:
        """Pull a usage-metadata dict off the langchain response.

        ChatAnthropic puts the Anthropic-native usage on
        `.response_metadata['usage']` and a normalized usage on
        `.usage_metadata`. Both supported by `cost_for_anthropic_usage`.
        Prefer the native one when present (more keys: cache_read,
        cache_creation).
        """
        if hasattr(resp, "response_metadata"):
            rm = getattr(resp, "response_metadata", None) or {}
            if isinstance(rm, dict):
                usage = rm.get("usage")
                if isinstance(usage, dict) and usage:
                    return usage
        if hasattr(resp, "usage_metadata"):
            um = getattr(resp, "usage_metadata", None) or {}
            if isinstance(um, dict):
                return um
        return {}

    @staticmethod
    def _compute_cost(usage: dict) -> float:
        from trading_corp.agents.llm import get_model_for
        from trading_corp.agents.research.cost import cost_for_anthropic_usage
        model, _ = get_model_for(WhaleAnalyst.AGENT_NAME)
        return cost_for_anthropic_usage(model, usage)


__all__ = ["WhaleAnalyst", "NarrationResult", "DEFAULT_DAILY_COST_CAP_USD"]
