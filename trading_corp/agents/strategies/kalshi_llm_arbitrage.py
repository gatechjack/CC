"""Kalshi LLM Arbitrage strategy — Phase K6.1.

LLM-divergence detector mirroring `polymarket_arbitrage.py` but pointed at
Kalshi. Same substrate (calibrated YES probability vs. implied) — different
venue.

Per memory `trading_corp_kalshi.md`: this is the THIRD kalshi strategy after
the structural arb pair (tail-price + temporal/bucket). The structural arbs
detect mechanical mispricings (sum != 1, constraint violations); this LLM
strategy detects mispricings the model has an opinion on (calibrated
probability vs. market implied).

Per cycle (every `poll_interval_sec`, default 60s):

  1. Pull Kalshi markets via `KalshiBroker.list_markets()` (category-targeted
     discovery, cache-aware so multi-strategy doesn't double-fetch).
  2. Filter:
       - skip COLLECTION events (KXMVE* aggregates, not tradeable)
       - skip extreme tails (handled by `kalshi_tail_price_arb` already)
       - require min_volume_24h, min_hours_to_resolution, prob bounds
  3. Drop tickers in cooldown (per-ticker 6h TTL post-evaluation).
  4. Cap to K=20 survivors per cycle (cost ceiling on Anthropic).
  5. For each survivor: Anthropic call with the analyst-persona prompt
     (lifted from polymarket — generic enough to work; category priors
     mostly transferable). Warm-and-fan: serial first call to hydrate the
     prompt cache, then K-1 in parallel.
  6. Compute divergence vs implied probability. If
     `abs(divergence) >= min_divergence_pct`, emit a `ProposedOrder`.
  7. Mark all evaluated tickers in cooldown so we don't re-burn LLM calls
     on the same candidate within 6 hours.

Phase K6.1 ships PAPER-ONLY via the existing read-only `KalshiBroker` —
ProposedOrders log `would_have_placed` after the risk gate. Phase K7+ will
add a live order path.
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

from trading_corp.agents.llm import build_chat_model, extract_usage_metadata, is_llm_available
from trading_corp.agents.strategies._polymarket_prompts import ANALYST_SYSTEM_PROMPT
from trading_corp.persistence.db import (
    delete_agent_state, load_agent_state, set_agent_state,
)
from trading_corp.persistence.models import ProposedOrder

log = logging.getLogger(__name__)


_LAST_SCAN_KEY = "last_scan_ts"
_COOLDOWNS_KEY = "market_cooldowns"
_LAST_ESTIMATE_KEY = "market_last_estimate"  # {ticker: {"implied": float, "ts": iso}} — movement gate
# Re-estimate-on-movement gate: skip the LLM call for a ticker with a prior
# estimate whose implied price has moved <= this (absolute YES prob) since
# that estimate. 0.03 = 3 Kalshi cents. First calls (no prior) always run.
_REESTIMATE_MIN_MOVE = 0.03


@dataclass
class _ProbabilityEstimate:
    """LLM output, validated and bounded."""
    prob_yes: float                # in [0.01, 0.99]
    confidence: str                # "low" | "medium" | "high"
    reasoning: str
    key_unknowns: list[str]
    # Populated post-parse from the Anthropic response for cost / prompt-cache
    # observability (input / cache-read / cache-creation / output tokens);
    # None if extraction failed. Observational only — no effect on trading.
    usage: dict | None = None


class KalshiLLMArbitrageAgent:
    """Phase K6.1 LLM-divergence Kalshi strategy.

    Same orchestration shape as `PolymarketArbitrageAgent` — different
    broker (Kalshi), different ticker convention (Kalshi tickers like
    `KXNEXTPOPE-29-FA1`), different sizing formula (per-leg fixed USD).

    Strategy config in `strategies.yaml`:
        kalshi_llm_arbitrage:
          enabled: false                # Board-flip after audit-mode validation
          auto_execute: false           # Phase K7+ before this can flip
          division: kalshi_llm_arbitrage
          poll_interval_sec: 60
          discovery:
            categories: [Politics, Elections, Economics, Financials, Crypto, Climate and Weather]
            max_series_per_category: 30
            max_markets_per_series: 50
            cache_ttl_sec: 600
          k_markets_per_cycle: 20
          market_cooldown_hours: 6
          min_divergence_pct: 10.0
          time_horizon_max_days: 30
          # Universe filters (applied AFTER discovery + classification):
          filter:
            min_implied_probability: 0.05
            max_implied_probability: 0.95
            skip_event_types: [collection]   # already filtered by discovery, but explicit
          sizing:
            mode: fixed_usdc
            fixed_amount: 1.0
    """

    name = "kalshi_llm_arbitrage"

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
        # Discovery cache shared via broker.list_markets() cache_ttl. We keep
        # our own thin cache here so the strategy can re-use within a cycle.
        self._discovery_cache: Any = None
        self._discovery_ts: datetime | None = None
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
                    self._risk_cfg = (yaml.safe_load(f) or {}).get("kalshi", {}) or {}
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
        return str(self._strat_cfg.get("division", "kalshi_llm_arbitrage"))

    # ── Public scan entry point ───────────────────────────────────────

    async def run_scan_cycle(
        self,
        broker,
        *,
        logger_agent=None,
    ) -> list[ProposedOrder]:
        """One scanner cycle. Returns list of ProposedOrders the risk gate
        should evaluate.

        `broker` must be a `KalshiBroker` with `list_markets()`. Audit
        events fire via `logger_agent` when supplied.
        """
        from trading_corp.data.kalshi_market_map import EventType

        self._reload()
        if not self.enabled:
            return []

        # Read all caps + tunables.
        prob_lo = float(self._strat_cfg.get("filter", {}).get("min_implied_probability", 0.05))
        prob_hi = float(self._strat_cfg.get("filter", {}).get("max_implied_probability", 0.95))
        k_per_cycle = int(self._strat_cfg.get("k_markets_per_cycle", 20))
        cooldown_h = float(self._strat_cfg.get("market_cooldown_hours", 6))
        min_div_pct = float(self._strat_cfg.get("min_divergence_pct", 10.0))
        # Per-category gates (Fix 2026-05-14): retro on 190 historical trades
        # showed Economics + Financials threshold markets lose 76% of the
        # time at moderate confidence — the LLM has no info advantage vs
        # informed economist participants. Win rate flips positive (72.7%)
        # ONLY when LLM is extreme (≤0.15 or ≥0.85) AND divergence ≥ 30%.
        # These two gates encode that finding. Hot-reloadable via yaml.
        eco_fin_cats = set(self._strat_cfg.get("strict_categories",
                                               ["Economics", "Financials"]))
        eco_fin_min_div = float(self._strat_cfg.get("strict_min_divergence_pct", 30.0))
        eco_fin_llm_extreme_max = float(self._strat_cfg.get(
            "strict_llm_extreme_max", 0.15))  # llm_prob ≤ this OR ≥ (1 - this)
        max_days_ttr = float(self._strat_cfg.get("time_horizon_max_days", 30))
        sizing_cfg = self._strat_cfg.get("sizing") or {}
        sizing_mode = str(sizing_cfg.get("mode", "fixed_usdc"))
        fixed_usd = float(sizing_cfg.get("fixed_amount", 1.0))

        disc_cfg = self._strat_cfg.get("discovery") or {}
        cache_ttl = float(disc_cfg.get("cache_ttl_sec", 600))
        max_series = int(disc_cfg.get("max_series_per_category", 30))
        max_markets = int(disc_cfg.get("max_markets_per_series", 50))
        categories = tuple(disc_cfg.get("categories") or ())

        # Refresh discovery if cache stale.
        now = datetime.now(timezone.utc)
        need_refresh = (
            self._discovery_cache is None
            or self._discovery_ts is None
            or (now - self._discovery_ts).total_seconds() > cache_ttl
        )
        if need_refresh:
            try:
                self._discovery_cache = await broker.list_markets(
                    categories=categories or None,
                    max_series_per_category=max_series,
                    max_markets_per_series=max_markets,
                )
                self._discovery_ts = now
                if logger_agent is not None and self._discovery_cache is not None:
                    logger_agent.log_event(
                        self.name, "kalshi_discovery_refreshed",
                        {
                            "strategy": self.name,
                            "division": self.division,
                            **self._discovery_cache.audit_summary(),
                        },
                    )
            except Exception as e:
                log.warning("kalshi_llm_arbitrage: discovery failed: %s", e)
                return []

        if self._discovery_cache is None:
            return []

        # 1. Flatten discovery into a market list with implied prob + filter
        # by universe rules. Skip COLLECTION events (already non-tradeable).
        # Skip extreme-tail markets (handled by kalshi_tail_price_arb division).
        cooldowns = self._load_cooldowns()
        last_estimate = self._load_last_estimate()
        survivors: list[dict] = []
        n_pre_filter = 0
        n_skipped_collection = 0
        n_skipped_tail = 0
        n_skipped_prob_bounds = 0
        n_skipped_cooldown = 0
        n_skipped_ttr = 0
        for event in self._discovery_cache.events:
            if event.event_type == EventType.COLLECTION:
                # Counted at event-grain since collection markets are already
                # filtered at discovery; this is just defensive bookkeeping.
                n_skipped_collection += 1
                continue
            for m in event.markets:
                n_pre_filter += 1
                # Implied YES from yes ask (best price to BUY YES).
                # Use mid if both bid/ask available; else fall back to ask.
                if m.yes_bid > 0 and m.yes_ask > 0:
                    implied = (m.yes_bid + m.yes_ask) / 2
                elif m.yes_ask > 0:
                    implied = m.yes_ask
                elif m.yes_bid > 0:
                    implied = m.yes_bid
                else:
                    continue  # no price -> skip
                # Tail filter: skip extreme prices (other division handles those)
                if implied < prob_lo or implied > prob_hi:
                    n_skipped_prob_bounds += 1
                    continue
                # Cooldown
                if m.ticker in cooldowns:
                    try:
                        until_dt = datetime.fromisoformat(
                            cooldowns[m.ticker]
                        ).replace(tzinfo=timezone.utc)
                        if until_dt > now:
                            n_skipped_cooldown += 1
                            continue
                    except (TypeError, ValueError):
                        pass
                # Time-to-resolution upper bound
                if m.expected_expiration_time:
                    try:
                        exp_dt = datetime.fromisoformat(
                            m.expected_expiration_time.replace("Z", "+00:00")
                        )
                        days_to_exp = (exp_dt - now).total_seconds() / 86400
                        if days_to_exp > max_days_ttr or days_to_exp < 0:
                            n_skipped_ttr += 1
                            continue
                    except (TypeError, ValueError):
                        pass
                survivors.append({
                    "ticker": m.ticker,
                    "event_ticker": m.event_ticker,
                    "event_title": event.title,
                    "event_type": event.event_type.value,
                    "category": event.category,
                    # Per-market `title` carries the EXPLICIT threshold for
                    # binary-strike markets (e.g. "Will the temp in NYC be
                    # above 57.99° on May 11, 2026 at 1pm EDT?"). The parent
                    # event_title typically omits the threshold ("NYC temp
                    # on May 11 at 1pm EDT?") and `subtitle` is delta-encoded
                    # ("-1° or below") — which the LLM systematically
                    # mis-interprets. Surfacing market_title fixes the
                    # 15-trade KXTEMPNYCH -$6 loss pattern observed
                    # 2026-05-11.
                    "market_title": m.title,
                    "subtitle": m.subtitle,
                    "yes_bid": m.yes_bid,
                    "yes_ask": m.yes_ask,
                    "no_bid": m.no_bid,
                    "no_ask": m.no_ask,
                    "implied_prob_yes": implied,
                    "expected_expiration_time": m.expected_expiration_time,
                })

        # Cap to K per cycle. Order: events with smallest spread first
        # (tighter market = LLM call more useful).
        survivors.sort(
            key=lambda d: abs((d.get("yes_ask") or 1) - (d.get("yes_bid") or 0))
        )
        survivors = survivors[:k_per_cycle]

        if logger_agent is not None:
            logger_agent.log_event(
                self.name, "kalshi_llm_scan_cycle",
                {
                    "strategy": self.name,
                    "division": self.division,
                    "markets_pre_filter": n_pre_filter,
                    "survivors_post_filter": len(survivors),
                    "k_per_cycle": k_per_cycle,
                    "min_divergence_pct": min_div_pct,
                    "skipped_collection": n_skipped_collection,
                    "skipped_prob_bounds": n_skipped_prob_bounds,
                    "skipped_cooldown": n_skipped_cooldown,
                    "skipped_ttr": n_skipped_ttr,
                },
            )

        # 2. LLM call per survivor + ProposedOrder construction.
        # Warm-and-fan parallel pattern: serialize the FIRST call so the
        # Anthropic prompt cache (analyst-persona prefix, ~2.5K tokens
        # post-K2 expansion) is hot before fanning out the K-1 remaining
        # calls in parallel.
        orders: list[ProposedOrder] = []
        new_cooldowns = dict(cooldowns)
        new_last_estimate = dict(last_estimate)
        cooldown_until = (now + timedelta(hours=cooldown_h)).isoformat(timespec="seconds")

        # 2a. Re-estimate-on-movement gate. A ticker with a PRIOR estimate is
        # skipped (no LLM call) when its implied price has moved <=
        # _REESTIMATE_MIN_MOVE since that estimate. First calls (no prior)
        # always run. Skipped tickers still get their cooldown advanced via the
        # same `cooldown_until` used below (identical to the "always advance
        # cooldown" path), so they are not retried every cycle; their stored
        # last-estimate price/ts are left UNCHANGED so small moves accumulate
        # across cooldown windows.
        to_estimate: list[dict] = []
        for m in survivors:
            tk = m.get("ticker")
            prior = last_estimate.get(tk) if tk else None
            cur_impl = m.get("implied_prob_yes")
            delta = None
            if isinstance(prior, dict) and cur_impl is not None:
                try:
                    delta = abs(float(cur_impl) - float(prior.get("implied")))
                except (TypeError, ValueError):
                    delta = None
            if delta is not None and delta <= _REESTIMATE_MIN_MOVE:
                if tk:
                    new_cooldowns[tk] = cooldown_until
                if logger_agent is not None:
                    logger_agent.log_event(
                        self.name, "kalshi_llm_probability_skipped",
                        {
                            "strategy": self.name,
                            "division": self.division,
                            "ticker": tk,
                            "implied_prob_yes": float(cur_impl),
                            "last_implied_at_estimate": float(prior.get("implied")),
                            "price_delta_cents": round(delta * 100.0, 2),
                            "gate_cents": round(_REESTIMATE_MIN_MOVE * 100.0, 2),
                            "last_estimate_ts": prior.get("ts"),
                            "reason": "no_movement",
                        },
                    )
                continue
            to_estimate.append(m)

        # Concurrency cap on the LLM fan. Anthropic enforces a per-account
        # concurrent-connections limit (separate from RPM); when this
        # strategy's K=20 fan overlapped a polymarket K=20 fan we hit ~30+
        # simultaneous connections and 6/20 calls came back 429.
        # Semaphore bounds OUR contribution to the shared pool. Polymarket
        # is unbounded; if/when it grows we'll add the same pattern there.
        # Configurable via strategies.yaml `llm_concurrency`; default 8.
        llm_concurrency = int(self._strat_cfg.get("llm_concurrency", 8))
        sem = asyncio.Semaphore(max(1, llm_concurrency))

        async def _gated_estimate(m: dict) -> _ProbabilityEstimate | None:
            async with sem:
                return await self._estimate_probability(m)

        estimates: list[_ProbabilityEstimate | None] = []
        if to_estimate:
            try:
                estimates.append(await _gated_estimate(to_estimate[0]))
            except Exception as e:
                t0 = to_estimate[0].get("ticker") or ""
                log.warning("kalshi_llm_arbitrage: LLM call failed for %s: %s", t0, e)
                estimates.append(None)
            if len(to_estimate) > 1:
                rest_results = await asyncio.gather(
                    *(_gated_estimate(m) for m in to_estimate[1:]),
                    return_exceptions=True,
                )
                for m, r in zip(to_estimate[1:], rest_results):
                    if isinstance(r, BaseException):
                        log.warning(
                            "kalshi_llm_arbitrage: LLM call failed for %s: %s",
                            m.get("ticker") or "", r,
                        )
                        estimates.append(None)
                    else:
                        estimates.append(r)

        # Process all results sequentially.
        for m, est in zip(to_estimate, estimates):
            ticker = m["ticker"]

            # Always advance cooldown so a failing/non-divergent market
            # doesn't burn LLM calls on every cycle.
            if ticker:
                new_cooldowns[ticker] = cooldown_until

            if est is None:
                continue

            implied = float(m.get("implied_prob_yes") or 0.5)
            # Record the price at this successful estimate for the movement gate.
            if ticker:
                new_last_estimate[ticker] = {
                    "implied": implied,
                    "ts": now.isoformat(timespec="seconds"),
                }
            divergence = est.prob_yes - implied
            divergence_pct = abs(divergence) * 100.0

            if logger_agent is not None:
                # Full LLM output preserved here — reasoning text is the
                # most valuable part for future fine-tuning + post-mortem.
                logger_agent.log_event(
                    self.name, "kalshi_llm_probability_called",
                    {
                        "strategy": self.name,
                        "division": self.division,
                        "ticker": ticker,
                        "event_ticker": m.get("event_ticker"),
                        "event_title": m.get("event_title"),
                        "subtitle": m.get("subtitle"),
                        "category": m.get("category"),
                        "implied_prob_yes": implied,
                        "llm_prob_yes": est.prob_yes,
                        "llm_confidence": est.confidence,
                        "llm_reasoning": est.reasoning,
                        "key_unknowns": est.key_unknowns,
                        "divergence_pct": divergence_pct,
                        "min_divergence_pct": min_div_pct,
                        "would_emit": divergence_pct >= min_div_pct,
                        "expires_at": m.get("expected_expiration_time"),
                        "usage": est.usage or {},
                    },
                )

            if divergence_pct < min_div_pct:
                continue

            # Per-category strict gate (Eco/Fin): require higher divergence
            # AND LLM in extreme-confidence band. Logged as audit-only;
            # `would_emit` already captured the baseline-threshold pass.
            mkt_category = (m.get("category") or "").strip()
            if mkt_category in eco_fin_cats:
                llm_extreme = (
                    est.prob_yes <= eco_fin_llm_extreme_max
                    or est.prob_yes >= (1.0 - eco_fin_llm_extreme_max)
                )
                if divergence_pct < eco_fin_min_div or not llm_extreme:
                    if logger_agent is not None:
                        logger_agent.log_event(
                            self.name, "kalshi_llm_strict_gate_skip",
                            {"strategy": self.name, "division": self.division,
                             "ticker": ticker, "category": mkt_category,
                             "divergence_pct": divergence_pct,
                             "llm_prob_yes": est.prob_yes,
                             "required_min_div": eco_fin_min_div,
                             "required_llm_extreme_max": eco_fin_llm_extreme_max,
                             "reason": ("low_divergence"
                                        if divergence_pct < eco_fin_min_div
                                        else "llm_not_extreme")},
                        )
                    continue

            # 3. Build the ProposedOrder. Sizing: fixed-USD per leg.
            # If LLM thinks YES is underpriced -> BUY YES at yes_ask.
            # If LLM thinks YES is overpriced -> BUY NO at no_ask.
            outcome = "yes" if divergence > 0 else "no"
            if outcome == "yes":
                share_price = m.get("yes_ask") or 0
            else:
                share_price = m.get("no_ask") or 0
            if share_price <= 0 or share_price >= 1:
                continue
            if sizing_mode == "fixed_usdc":
                qty = fixed_usd / share_price
                max_dollar_risk = fixed_usd  # binary: max loss if outcome resolves opposite
            else:
                log.warning("kalshi_llm_arbitrage: sizing_mode=%r not yet implemented", sizing_mode)
                continue

            order = ProposedOrder(
                strategy=self.name,
                symbol=f"{ticker}:{outcome}",
                side="buy",
                qty=qty,
                order_type="limit",
                limit_price=share_price,
                rationale=(
                    f"LLM YES={est.prob_yes:.3f} vs implied {implied:.3f} "
                    f"(divergence {divergence_pct:.1f}%); buy {outcome.upper()} "
                    f"@ {share_price:.3f}"
                ),
                extra={
                    "outcome": outcome,
                    "ticker": ticker,
                    "event_ticker": m.get("event_ticker"),
                    "event_title": m.get("event_title"),
                    "subtitle": m.get("subtitle"),
                    "category": m.get("category"),
                    "implied_prob_at_entry": implied,
                    "llm_prob_estimate": est.prob_yes,
                    "llm_confidence": est.confidence,
                    "llm_reasoning": est.reasoning,
                    "key_unknowns": est.key_unknowns,
                    "divergence_pct": divergence_pct,
                    "expires_at": m.get("expected_expiration_time"),
                    "max_dollar_risk": max_dollar_risk,
                    "tier": "llm_divergence_fixed_usd",
                    "source_signal": "llm_divergence",
                    "is_prediction_market": True,
                },
            )
            orders.append(order)

        # 4. Persist updated cooldowns + last-estimate prices + last-scan ts.
        self._save_cooldowns(new_cooldowns, now=now)
        self._save_last_estimate(new_last_estimate, now=now)
        if self._db_url:
            set_agent_state(
                self.name, _LAST_SCAN_KEY,
                {"ts": now.isoformat(timespec="seconds")},
                db_url=self._db_url,
            )
        return orders

    # ── LLM call ──────────────────────────────────────────────────────

    async def _estimate_probability(self, market: dict) -> _ProbabilityEstimate | None:
        """Direct Anthropic call. Returns None on parse / availability
        failure so the caller can advance the cooldown without crashing.
        """
        if not is_llm_available():
            return None
        if self._chat is None:
            self._chat = build_chat_model(self.name, max_tokens=512)

        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

        # Cache the analyst-persona prefix; the per-market user prompt
        # is the only divergent token-cost portion. Reuse polymarket's
        # prompt — generic enough for cross-venue prediction-market work.
        sys = SystemMessage(content=[
            {
                "type": "text",
                "text": ANALYST_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            },
        ])
        ticker = market.get("ticker") or "(no ticker)"
        event_title = market.get("event_title") or "(no event title)"
        market_title = market.get("market_title") or ""
        subtitle = market.get("subtitle") or ""
        category = market.get("category") or "other"
        end_iso = market.get("expected_expiration_time") or "(no end date)"
        implied = market.get("implied_prob_yes")

        # Prefer per-market `title` when present — it carries the explicit
        # threshold in plain English ("Will the temp in NYC be above 57.99°
        # on May 11, 2026 at 1pm EDT?"). Fall back to event_title + subtitle
        # for legacy / malformed markets where title is empty.
        if market_title:
            question = market_title
        else:
            question = event_title
            if subtitle:
                question = f"{event_title} — outcome: {subtitle}"

        user_text = (
            f"Market ticker: {ticker}\n"
            f"Category: {category}\n"
            f"Question: {question}\n"
            f"Resolution date: {end_iso}\n"
            f"Implied YES probability (current Kalshi market): {implied}\n\n"
            f"Produce the JSON object as specified in your instructions. "
            f"Output JSON only, no surrounding prose."
        )
        user = HumanMessage(content=user_text)

        try:
            resp = await self._chat.ainvoke([sys, user])
        except Exception as e:
            log.warning("kalshi_llm_arbitrage LLM ainvoke failed: %s", e)
            return None

        raw = (resp.content or "") if hasattr(resp, "content") else ""
        if not isinstance(raw, str):
            try:
                raw = "".join(b.get("text", "") for b in raw if isinstance(b, dict))
            except Exception:
                return None

        est = self._parse_probability_response(raw)
        if est is not None:
            est.usage = extract_usage_metadata(resp)
        return est

    @staticmethod
    def _parse_probability_response(raw: str) -> _ProbabilityEstimate | None:
        """Permissive JSON extraction — same logic as polymarket."""
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

    # ── Cooldown persistence ──────────────────────────────────────────

    def _load_cooldowns(self) -> dict[str, str]:
        """Returns {ticker: until_iso}. Expired entries pruned."""
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
        for k, iso in value.items():
            try:
                dt = datetime.fromisoformat(iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > now:
                    cleaned[str(k)] = iso
            except (TypeError, ValueError):
                continue
        return cleaned

    def _save_cooldowns(self, cooldowns: dict[str, str], *, now: datetime) -> None:
        if not self._db_url:
            return
        cleaned = {
            k: iso for k, iso in cooldowns.items()
            if self._is_future(iso, now=now)
        }
        if cleaned:
            set_agent_state(
                self.name, _COOLDOWNS_KEY, cleaned, db_url=self._db_url,
            )
        else:
            delete_agent_state(self.name, _COOLDOWNS_KEY, db_url=self._db_url)

    def _load_last_estimate(self) -> dict[str, dict]:
        """Returns {ticker: {"implied": float, "ts": iso}} for the movement
        gate. Entries older than the max market horizon are pruned so the
        store stays bounded to the active universe."""
        if not self._db_url:
            return {}
        row = load_agent_state(self.name, _LAST_ESTIMATE_KEY, db_url=self._db_url)
        if row is None:
            return {}
        value, _ = row
        if not isinstance(value, dict):
            return {}
        cutoff = datetime.now(timezone.utc) - timedelta(days=45)
        cleaned: dict[str, dict] = {}
        for k, v in value.items():
            if not isinstance(v, dict) or "implied" not in v or "ts" not in v:
                continue
            try:
                dt = datetime.fromisoformat(str(v["ts"]))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > cutoff:
                    cleaned[str(k)] = {"implied": float(v["implied"]), "ts": str(v["ts"])}
            except (TypeError, ValueError):
                continue
        return cleaned

    def _save_last_estimate(self, last_estimate: dict[str, dict], *, now: datetime) -> None:
        if not self._db_url:
            return
        cutoff = now - timedelta(days=45)
        cleaned: dict[str, dict] = {}
        for k, v in last_estimate.items():
            if not isinstance(v, dict) or "ts" not in v:
                continue
            try:
                dt = datetime.fromisoformat(str(v["ts"]))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > cutoff:
                    cleaned[str(k)] = v
            except (TypeError, ValueError):
                continue
        if cleaned:
            set_agent_state(
                self.name, _LAST_ESTIMATE_KEY, cleaned, db_url=self._db_url,
            )
        else:
            delete_agent_state(self.name, _LAST_ESTIMATE_KEY, db_url=self._db_url)

    @staticmethod
    def _is_future(iso: str, *, now: datetime) -> bool:
        try:
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt > now
        except (TypeError, ValueError):
            return False


__all__ = ["KalshiLLMArbitrageAgent"]
