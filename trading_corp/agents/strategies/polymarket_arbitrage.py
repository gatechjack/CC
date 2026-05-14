"""Polymarket Arbitrage strategy — Phase 2a.

Scan-driven LLM-divergence detector. Every poll cycle:

  1. Pull open markets via PolymarketBroker.list_markets (deterministic
     pre-filter for volume / spread / time-to-resolution / accepting-orders).
  2. Drop markets in cooldown (per-market 6h TTL after evaluation).
  3. Cap to K=10 survivors per cycle (cost ceiling on Anthropic calls).
  4. For each survivor: ask Anthropic for a calibrated YES probability
     using the shared analyst-persona system prompt
     (`_polymarket_prompts.ANALYST_SYSTEM_PROMPT`, prompt-cached).
  5. Compute divergence vs implied probability. If
     `abs(divergence) >= min_divergence_pct`, emit a ProposedOrder
     with full metadata for the risk gate + audit log.
  6. Mark all evaluated markets in cooldown so we don't re-burn LLM
     calls on the same candidate within 6 hours.

State persistence: `agent_state` table, keyed `(polymarket_arbitrage,
last_scan_ts)` and `(polymarket_arbitrage, market_cooldowns)`. Cooldowns
are stored as a single JSON blob; expired entries are cleaned at each
cycle start (no schema for "deleted").

Phase 2a ships with `enabled: false` in `strategies.yaml` — the
scheduler tick is a no-op until the Board flips the flag. `auto_execute`
will stay false even when enabled; orders flow through HITL approval
on the web app per CLAUDE.md.

LLM call posture: direct Anthropic via `agents.llm.build_chat_model`
(NOT through the Research firm — Path B chosen 2026-05-09 because
the firm's Thesis schema doesn't fit prediction-market probability
queries and Polymarket arbitrage is single-division decision logic,
not cross-division knowledge work).

Cost ceiling math: K=10 markets × $0.05-0.20 per Thesis-equivalent
call × 2880 cycles/day worst case ≈ $1,440-$5,760/day if every cycle
hits K. Cooldown reduces realistic per-day unique calls to 50-200
markets, so $2-50/day is the operational range. Combined with the
prompt cache (≥1024-token shared prefix, 5-min ephemeral TTL on
Anthropic), the input-token cost on the K-1 follow-up calls per
cycle drops by ~85%.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from trading_corp.agents.llm import build_chat_model, is_llm_available
from trading_corp.agents.strategies._polymarket_prompts import ANALYST_SYSTEM_PROMPT
from trading_corp.persistence.db import (
    delete_agent_state, load_agent_state, set_agent_state,
)
from trading_corp.persistence.models import ProposedOrder

log = logging.getLogger(__name__)


_LAST_SCAN_KEY = "last_scan_ts"
_COOLDOWNS_KEY = "market_cooldowns"


@dataclass
class _ProbabilityEstimate:
    """LLM output, validated and bounded."""
    prob_yes: float                # in [0.01, 0.99]
    confidence: str                # "low" | "medium" | "high"
    reasoning: str
    key_unknowns: list[str]


class PolymarketArbitrageAgent:
    """Scan-driven Polymarket arbitrage agent.

    The agent itself is stateless across construction — it loads config
    + cooldowns from disk on each cycle. The orchestrator owns the poll
    schedule and the broker handle.
    """

    name = "polymarket_arbitrage"

    def __init__(
        self,
        strategies_yaml: Path = Path("config/strategies.yaml"),
        risk_yaml: Path = Path("config/risk.yaml"),
        db_url: str | None = None,
    ) -> None:
        self._strategies_yaml = Path(strategies_yaml)
        self._risk_yaml = Path(risk_yaml)
        self._db_url = db_url
        self._strat_mtime: float = 0.0
        self._risk_mtime: float = 0.0
        self._strat_cfg: dict[str, Any] = {}
        self._risk_cfg: dict[str, Any] = {}
        self._chat: Any = None  # built lazily, only when scanner actually fires
        self._reload()

    # ── Config (mtime-cached, hot-reloadable) ─────────────────────────

    def _reload(self) -> None:
        try:
            sm = self._strategies_yaml.stat().st_mtime
            if sm != self._strat_mtime:
                with self._strategies_yaml.open("r", encoding="utf-8") as f:
                    self._strat_cfg = (yaml.safe_load(f) or {}).get(self.name, {}) or {}
                self._strat_mtime = sm
        except FileNotFoundError:
            self._strat_cfg = {}
        try:
            rm = self._risk_yaml.stat().st_mtime
            if rm != self._risk_mtime:
                with self._risk_yaml.open("r", encoding="utf-8") as f:
                    self._risk_cfg = (yaml.safe_load(f) or {}).get("polymarket", {}) or {}
                self._risk_mtime = rm
        except FileNotFoundError:
            self._risk_cfg = {}

    @property
    def enabled(self) -> bool:
        self._reload()
        return bool(self._strat_cfg.get("enabled", False))

    @property
    def auto_execute(self) -> bool:
        self._reload()
        return bool(self._strat_cfg.get("auto_execute", False))

    @property
    def division(self) -> str:
        self._reload()
        return str(self._strat_cfg.get("division", "polymarket_arbitrage"))

    # ── Public scan entry point ───────────────────────────────────────

    async def run_scan_cycle(
        self,
        broker,
        *,
        logger_agent=None,
    ) -> list[ProposedOrder]:
        """One scanner cycle. Returns the list of ProposedOrders the
        risk gate should evaluate.

        `logger_agent` is optional; when supplied, audit rows for
        scan-level events fire here (cycle start, LLM call result,
        skip reasons, divergence emission). The orchestrator may also
        write its own audit rows downstream of the returned orders.
        """
        self._reload()
        if not self.enabled:
            return []

        # Read all caps + tunables. risk.yaml is the source of truth for
        # the universe filter; strategies.yaml owns the strategy-specific
        # knobs (K, cooldown, divergence threshold, sizing).
        min_volume = float(self._risk_cfg.get("min_market_24h_volume_usd", 50_000.0))
        max_spread_cents = float(self._risk_cfg.get("max_spread_cents", 3.0))
        min_hours_ttr = float(self._risk_cfg.get("min_hours_to_resolution", 24.0))
        max_days_ttr = float(self._strat_cfg.get("time_horizon_max_days", 7))
        prob_lo = float(self._risk_cfg.get("min_implied_probability", 0.05))
        prob_hi = float(self._risk_cfg.get("max_implied_probability", 0.95))
        k_per_cycle = int(self._strat_cfg.get("k_markets_per_cycle", 10))
        cooldown_h = float(self._strat_cfg.get("market_cooldown_hours", 6))
        min_div_pct = float(self._strat_cfg.get("min_divergence_pct", 10.0))
        sizing_cfg = self._strat_cfg.get("sizing") or {}
        sizing_mode = str(sizing_cfg.get("mode", "fixed_usdc"))
        fixed_usdc = float(sizing_cfg.get("fixed_amount", 1.0))

        # 1. Pull eligible markets (deterministic pre-filter).
        try:
            markets = await broker.list_markets(
                min_volume_24h_usd=min_volume,
                max_spread_cents=max_spread_cents,
                min_hours_to_resolution=min_hours_ttr,
                max_days_to_resolution=max_days_ttr,
                limit=200,
            )
        except Exception as e:
            log.warning("polymarket_arbitrage: list_markets failed: %s", e)
            return []

        # 2. Drop markets in cooldown + filter on implied-probability bounds.
        cooldowns = self._load_cooldowns()
        now = datetime.now(timezone.utc)
        survivors: list[dict] = []
        for m in markets:
            cid = m.get("conditionId") or m.get("condition_id")
            if cid and cid in cooldowns:
                # Skip if cooldown not yet expired
                until = cooldowns[cid]
                try:
                    until_dt = datetime.fromisoformat(until)
                    if until_dt.tzinfo is None:
                        until_dt = until_dt.replace(tzinfo=timezone.utc)
                    if until_dt > now:
                        continue
                except (TypeError, ValueError):
                    pass  # malformed cooldown entry → re-evaluate
            # Implied-probability bounds. Prefer last-trade-price for the
            # YES outcome if surfaced; fall back to outcomePrices[0].
            implied = self._extract_implied_prob_yes(m)
            if implied is None or implied < prob_lo or implied > prob_hi:
                continue
            m["_implied_prob_yes"] = implied
            survivors.append(m)

        # 3. Cap to K per cycle. Future: rank by some prior on
        # divergence likelihood (volume-weighted, recency, etc.) so we
        # spend the K Anthropic calls on the best candidates.
        # For Phase 2a we just take the first K — predictable, easy to
        # reason about. Cooldown prevents re-burning the same prefix.
        survivors = survivors[:k_per_cycle]

        if logger_agent is not None:
            logger_agent.log_event(
                self.name, "polymarket_scan_cycle",
                {
                    "strategy": self.name,
                    "division": self.division,
                    "markets_pre_filter": len(markets),
                    "survivors_post_filter": len(survivors),
                    "k_per_cycle": k_per_cycle,
                    "min_divergence_pct": min_div_pct,
                },
            )

        # 4. LLM call per survivor + ProposedOrder construction.
        # Warm-and-fan parallel pattern: serialize the FIRST call so the
        # Anthropic prompt cache (analyst-persona prefix, ~1554 tokens)
        # is hot before fanning out the K-1 remaining calls in parallel.
        # Without warming, parallel calls race the cache write and most
        # would miss → ~5-10x input-token cost spike. With warming, the
        # K-1 follow-ups all hit the cache. Cycle time: ~5s (first) +
        # ~5s (parallel rest) = ~10s vs ~50s sequential.
        orders: list[ProposedOrder] = []
        new_cooldowns = dict(cooldowns)  # mutate a copy
        cooldown_until = (now + timedelta(hours=cooldown_h)).isoformat(timespec="seconds")

        # Concurrency cap on the LLM fan (Phase K7). Anthropic enforces a
        # per-account concurrent-connections limit separate from RPM/TPM;
        # when this strategy's K=20 fan overlapped kalshi_llm_arbitrage's
        # K=20 fan on 2026-05-11 01:02 UTC we hit ~38 simultaneous
        # connections and 6/20 calls came back 429. Kalshi was capped first;
        # this is the polymarket-side defensive cap. Configurable via
        # strategies.yaml `llm_concurrency`; default 8. See memory
        # `anthropic_concurrent_connections.md`.
        llm_concurrency = int(self._strat_cfg.get("llm_concurrency", 8))
        sem = asyncio.Semaphore(max(1, llm_concurrency))

        async def _gated_estimate(m: dict) -> _ProbabilityEstimate | None:
            async with sem:
                return await self._estimate_probability(m)

        estimates: list[_ProbabilityEstimate | None] = []
        if survivors:
            # Step 1: warm the cache with one serial call.
            try:
                estimates.append(await _gated_estimate(survivors[0]))
            except Exception as e:
                cid0 = survivors[0].get("conditionId") or survivors[0].get("condition_id") or ""
                log.warning("polymarket_arbitrage: LLM call failed for %s: %s", cid0, e)
                estimates.append(None)
            # Step 2: fan out the remaining K-1 in parallel, gated by the
            # semaphore so we never burst above llm_concurrency simultaneously.
            if len(survivors) > 1:
                rest_results = await asyncio.gather(
                    *(_gated_estimate(m) for m in survivors[1:]),
                    return_exceptions=True,
                )
                for m, r in zip(survivors[1:], rest_results):
                    if isinstance(r, BaseException):
                        cid = m.get("conditionId") or m.get("condition_id") or ""
                        log.warning("polymarket_arbitrage: LLM call failed for %s: %s", cid, r)
                        estimates.append(None)
                    else:
                        estimates.append(r)

        # Process all results sequentially — keeps audit-row ordering
        # deterministic + lets the per-market cooldown / divergence /
        # ProposedOrder logic stay simple. The expensive work (LLM calls)
        # already happened in parallel above.
        for m, est in zip(survivors, estimates):
            cid = m.get("conditionId") or m.get("condition_id") or ""

            # Always advance cooldown so a failing/non-divergent market
            # doesn't burn LLM calls on every cycle.
            if cid:
                new_cooldowns[cid] = cooldown_until

            if est is None:
                continue

            implied = float(m.get("_implied_prob_yes") or 0.5)
            # divergence convention: + means LLM thinks YES is underpriced
            # by the market (we'd buy YES); - means YES is overpriced
            # (we'd buy NO).
            divergence = est.prob_yes - implied
            divergence_pct = abs(divergence) * 100.0

            if logger_agent is not None:
                # Full LLM output preserved here — reasoning text is the
                # most valuable part for future fine-tuning + post-mortem
                # of bad calls. Don't trim. Storage is sqlite + cheap;
                # token-truncation already happens at the LLM layer
                # (max_tokens=512 in build_chat_model).
                logger_agent.log_event(
                    self.name, "polymarket_llm_probability_called",
                    {
                        "strategy": self.name,
                        "division": self.division,
                        "condition_id": cid,
                        "slug": m.get("slug"),
                        "question": m.get("question"),       # full market question for context
                        "category": m.get("category"),
                        "series": m.get("series"),
                        "implied_prob_yes": implied,
                        "llm_prob_yes": est.prob_yes,
                        "llm_confidence": est.confidence,
                        "llm_reasoning": est.reasoning,       # FULL reasoning text
                        "key_unknowns": est.key_unknowns,
                        "divergence_pct": divergence_pct,
                        "min_divergence_pct": min_div_pct,
                        "would_emit": divergence_pct >= min_div_pct,
                        "resolves_at": m.get("endDate") or m.get("end_date"),
                    },
                )

            if divergence_pct < min_div_pct:
                continue

            # 5. Build the ProposedOrder. Sizing in fixed-USDC mode:
            # buy `fixed_usdc / share_price` shares at `share_price`.
            outcome = "yes" if divergence > 0 else "no"
            share_price = implied if outcome == "yes" else (1.0 - implied)
            if share_price <= 0:
                continue
            if sizing_mode == "fixed_usdc":
                qty = fixed_usdc / share_price
                max_dollar_risk = fixed_usdc  # binary: max loss if outcome resolves opposite
            else:
                # `pct_with_confidence` reserved for Phase 4+; not built today.
                log.warning("polymarket_arbitrage: sizing_mode=%r not yet implemented", sizing_mode)
                continue

            slug = m.get("slug") or "unknown"
            symbol = f"{slug}:{outcome}"
            order = ProposedOrder(
                strategy=self.name,
                symbol=symbol,
                side="buy",
                qty=qty,
                order_type="limit",
                limit_price=share_price,
                rationale=(
                    f"LLM YES={est.prob_yes:.3f} vs implied {implied:.3f} "
                    f"(divergence {divergence_pct:.1f}%); buy {outcome.upper()} @ {share_price:.3f}"
                ),
                extra={
                    "outcome": outcome,
                    "category": m.get("category"),       # top-level bucket (sports/politics/...)
                    "series": m.get("series"),           # sub-tag (mlb/atp/eurovision-2026)
                    "market_slug": slug,
                    "market_question": m.get("question"),  # full text for downstream display
                    "condition_id": cid,
                    "outcome_index": 0 if outcome == "yes" else 1,
                    "market_id": m.get("id") or m.get("market_id"),
                    "implied_prob_at_entry": implied,
                    "llm_prob_estimate": est.prob_yes,
                    "llm_confidence": est.confidence,
                    "llm_reasoning": est.reasoning,        # full LLM justification text
                    "key_unknowns": est.key_unknowns,      # info gaps the LLM flagged
                    "divergence_pct": divergence_pct,
                    "resolves_at": m.get("endDate") or m.get("end_date"),
                    "max_dollar_risk": max_dollar_risk,
                    "tier": "shakedown_fixed_1usdc",
                    "source_signal": "llm_divergence",
                    "is_prediction_market": True,   # tag for risk-gate routing
                },
            )
            orders.append(order)

        # 6. Persist updated cooldowns + last-scan ts.
        self._save_cooldowns(new_cooldowns, now=now)
        if self._db_url:
            set_agent_state(
                self.name, _LAST_SCAN_KEY,
                {"ts": now.isoformat(timespec="seconds")},
                db_url=self._db_url,
            )
        return orders

    # ── LLM call ──────────────────────────────────────────────────────

    async def _estimate_probability(self, market: dict) -> _ProbabilityEstimate | None:
        """Direct Anthropic call. Returns None on any parse / availability
        failure so the caller can advance the cooldown without crashing
        the cycle."""
        if not is_llm_available():
            return None
        if self._chat is None:
            self._chat = build_chat_model(self.name, max_tokens=512)

        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

        # Cache the analyst-persona prefix; the per-market user prompt
        # is the only divergent token-cost portion.
        sys = SystemMessage(content=[
            {
                "type": "text",
                "text": ANALYST_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            },
        ])
        question = market.get("question") or "(no question text)"
        slug = market.get("slug") or "(no slug)"
        end_iso = market.get("endDate") or market.get("end_date") or "(no end date)"
        # Two-layer category surfaced from list_markets: top bucket for
        # base-rate priors (e.g. sports games are typically near-50/50);
        # series for finer-grained context (e.g. atp = tennis ATP tour).
        category = market.get("category") or "other"
        series = market.get("series") or ""
        implied = market.get("_implied_prob_yes")
        description = market.get("description") or ""
        # Cap description length to bound user-prompt tokens.
        if len(description) > 1200:
            description = description[:1200] + "…"

        user_text = (
            f"Market slug: {slug}\n"
            f"Category: {category}"
            + (f" ({series})" if series else "")
            + "\n"
            f"Question: {question}\n"
            f"Resolution date: {end_iso}\n"
            f"Implied YES probability (current market): {implied}\n"
            f"Description: {description}\n\n"
            f"Produce the JSON object as specified in your instructions. "
            f"Output JSON only, no surrounding prose."
        )
        user = HumanMessage(content=user_text)

        try:
            resp = await self._chat.ainvoke([sys, user])
        except Exception as e:
            log.warning("polymarket_arbitrage LLM ainvoke failed: %s", e)
            return None

        raw = (resp.content or "") if hasattr(resp, "content") else ""
        if not isinstance(raw, str):
            # langchain may return a list of structured content blocks;
            # collapse to text.
            try:
                raw = "".join(b.get("text", "") for b in raw if isinstance(b, dict))
            except Exception:
                return None

        return self._parse_probability_response(raw)

    @staticmethod
    def _parse_probability_response(raw: str) -> _ProbabilityEstimate | None:
        """Permissive JSON extraction. Models occasionally wrap JSON in
        prose despite instructions; pull the first {...} block."""
        if not raw:
            return None
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            prob = float(data.get("prob_yes"))
        except (TypeError, ValueError):
            return None
        # Bound to the model's own stated range.
        if not (0.01 <= prob <= 0.99):
            prob = max(0.01, min(0.99, prob))
        confidence = str(data.get("confidence", "medium")).lower()
        if confidence not in ("low", "medium", "high"):
            confidence = "medium"
        reasoning = str(data.get("reasoning", "")).strip()
        ku = data.get("key_unknowns") or []
        if not isinstance(ku, list):
            ku = []
        ku = [str(x) for x in ku][:5]
        return _ProbabilityEstimate(
            prob_yes=prob, confidence=confidence,
            reasoning=reasoning, key_unknowns=ku,
        )

    # ── Implied-probability extraction ────────────────────────────────

    @staticmethod
    def _extract_implied_prob_yes(market: dict) -> float | None:
        """Pull current YES price from gamma-api market dict.

        Field-name shape: gamma-api's `outcomePrices` is typically a
        JSON-encoded string of floats — `["0.62","0.38"]`. Index 0 is
        YES, 1 is NO. Some endpoints expose a single `price` (last
        trade) or `lastTradePrice`. Defensive parse against both.
        """
        # Direct float fields
        for k in ("lastTradePrice", "last_trade_price", "price"):
            v = market.get(k)
            if v is not None:
                try:
                    f = float(v)
                    if 0.0 < f < 1.0:
                        return f
                except (TypeError, ValueError):
                    pass
        # Array form
        op = market.get("outcomePrices") or market.get("outcome_prices")
        if isinstance(op, str):
            try:
                op = json.loads(op)
            except (json.JSONDecodeError, ValueError):
                op = None
        if isinstance(op, list) and op:
            try:
                f = float(op[0])
                if 0.0 < f < 1.0:
                    return f
            except (TypeError, ValueError):
                pass
        return None

    # ── Cooldown persistence ──────────────────────────────────────────

    def _load_cooldowns(self) -> dict[str, str]:
        """Returns {condition_id: until_iso}. Expired entries pruned."""
        if not self._db_url:
            return {}
        row = load_agent_state(self.name, _COOLDOWNS_KEY, db_url=self._db_url)
        if row is None:
            return {}
        value, _ = row
        if not isinstance(value, dict):
            return {}
        now = datetime.now(timezone.utc)
        cleaned: dict[str, str] = {}
        for cid, iso in value.items():
            try:
                dt = datetime.fromisoformat(iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > now:
                    cleaned[str(cid)] = iso
            except (TypeError, ValueError):
                continue
        return cleaned

    def _save_cooldowns(self, cooldowns: dict[str, str], *, now: datetime) -> None:
        if not self._db_url:
            return
        # Final cleanup pass on save — drop anything expired right now.
        cleaned = {
            cid: iso for cid, iso in cooldowns.items()
            if self._is_future(iso, now=now)
        }
        if cleaned:
            set_agent_state(
                self.name, _COOLDOWNS_KEY, cleaned, db_url=self._db_url,
            )
        else:
            # Empty dict — clear the row so we don't accumulate
            # zombie state.
            delete_agent_state(self.name, _COOLDOWNS_KEY, db_url=self._db_url)

    @staticmethod
    def _is_future(iso: str, *, now: datetime) -> bool:
        try:
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt > now
        except (TypeError, ValueError):
            return False
