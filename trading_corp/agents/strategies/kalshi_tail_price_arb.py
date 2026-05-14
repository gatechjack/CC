"""Kalshi Tail-Price YES+NO arbitrage strategy — Phase K2.1.

Detects same-market arb opportunities at price tails (yes_mid ≤ 5¢ or
≥95¢) where the per-fill rounding floor on Kalshi's fee formula
(`roundup(0.07 × C × P × (1-C))`, min 1¢) makes the round-trip cost
collapse to ~2¢ instead of 3.5¢ at mid prices.

Per K2 fee research (memory `trading_corp_kalshi.md`):
  - C = 0.50 round-trip = 4¢ at 1 contract / 3.5¢ at scale  (DEAD at mid)
  - C = 0.10 round-trip = 2¢ at 1 contract / 1.26¢ at scale (LIVE at tails)
  - C = 0.05 round-trip = 2¢ at any size                    (LIVE at tails)

A YES+NO arb exists when `(yes_ask + no_ask) < 1.00 - threshold`. Buying
both legs locks $1 payout (one wins, one expires worthless) — settlement
is safe under Kalshi's binary-resolution identity (Cardi B halftime case).

Phase K2.1 ships paper-only. Each detection emits a PAIR of ProposedOrders
(buy YES + buy NO, linked via `kalshi_pair_id`) routed through the existing
risk gate + audit log. No live placement until Phase K5+ (after observed
positive-EV across paper trades).

Loop pattern mirrors `polymarket_arbitrage.py`:
  1. Read config from strategies.yaml (mtime-cached, hot-reloadable).
  2. If `enabled: false` -> no-op.
  3. Refresh discovery via `broker.list_markets()` (cached per ttl).
  4. Walk binary events, check tail + arb edge per market.
  5. Cooldown to avoid re-emitting on the same market every cycle.
  6. Emit ProposedOrder pairs.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from trading_corp.persistence.db import load_agent_state, set_agent_state
from trading_corp.persistence.models import ProposedOrder

log = logging.getLogger(__name__)


_LAST_DISCOVERY_KEY = "last_discovery_ts"
_COOLDOWNS_KEY = "tail_arb_cooldowns"


@dataclass
class _TailOpportunity:
    """One detected tail-price arb opportunity (pre-ProposedOrder)."""
    ticker: str
    event_ticker: str
    yes_ask: float       # dollars
    no_ask: float
    yes_bid: float
    no_bid: float
    sum_asks: float      # = yes_ask + no_ask
    edge_dollars: float  # = 1.0 - sum_asks  (positive = arb edge)
    title: str
    expected_expiration_time: str | None


class KalshiTailPriceArbAgent:
    """Phase K2.1 detector. Scan-driven, stateless across construction.

    Strategy config in `strategies.yaml`:
        kalshi_tail_price_arb:
          enabled: false                    # Board-flipped to true post-greenlight
          auto_execute: false               # Phase K5+ before this can flip
          division: kalshi_arbitrage
          poll_interval_sec: 300            # 5 min — tail prices don't churn
          discovery:
            categories: [Politics, Economics, Crypto, ...]
            max_series_per_category: 30
            max_markets_per_series: 50
            cache_ttl_sec: 600              # re-discover every 10 min
          tail:
            yes_max_for_yes_tail: 0.05      # mid YES ≤ this -> "yes is the long shot"
            yes_min_for_no_tail: 0.95       # mid YES ≥ this -> "no is the long shot"
            min_edge_cents: 1               # ≥ 1¢ under $1 to consider
          sizing:
            fixed_usd_per_leg: 1.0          # $1 buy on each side -> $2 max risk
          per_cycle:
            max_pairs: 5                    # cap pairs emitted per cycle
            cooldown_minutes: 60            # don't re-emit same market within 60min
    """

    name = "kalshi_tail_price_arb"

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
        self._strat_cfg: dict[str, Any] = {}
        # Discovery cache — refreshed per ttl in run_scan_cycle.
        self._discovery_cache: Any = None  # DiscoveryResult | None
        self._discovery_ts: datetime | None = None
        self._reload()

    def _reload(self) -> None:
        try:
            sm = self._strategies_yaml.stat().st_mtime
            if sm != self._strat_mtime:
                with self._strategies_yaml.open("r", encoding="utf-8") as f:
                    self._strat_cfg = (yaml.safe_load(f) or {}).get(self.name, {}) or {}
                self._strat_mtime = sm
        except FileNotFoundError:
            self._strat_cfg = {}

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
        return str(self._strat_cfg.get("division", "kalshi_arbitrage"))

    # ── Cooldown persistence ──────────────────────────────────────────

    def _load_cooldowns(self) -> dict[str, str]:
        if not self._db_url:
            return {}
        try:
            row = load_agent_state(self.name, _COOLDOWNS_KEY, db_url=self._db_url)
        except Exception:
            return {}
        if not row:
            return {}
        try:
            return dict(row[0]) if isinstance(row[0], dict) else {}
        except Exception:
            return {}

    def _save_cooldowns(self, cooldowns: dict[str, str], *, now: datetime) -> None:
        if not self._db_url:
            return
        # Drop expired entries to keep the blob small.
        kept: dict[str, str] = {}
        for k, until in cooldowns.items():
            try:
                if datetime.fromisoformat(until).replace(tzinfo=timezone.utc) > now:
                    kept[k] = until
            except (TypeError, ValueError):
                pass  # malformed -> drop
        try:
            set_agent_state(self.name, _COOLDOWNS_KEY, kept, db_url=self._db_url)
        except Exception as e:
            log.warning("kalshi_tail_price_arb: persist cooldowns failed: %s", e)

    # ── Public scan entry point ────────────────────────────────────────

    async def run_scan_cycle(
        self,
        broker,
        *,
        logger_agent=None,
    ) -> list[ProposedOrder]:
        """One scanner cycle. Returns ProposedOrder PAIRS (BUY YES + BUY NO)
        for each detected tail-price arb opportunity, capped per cycle.

        `broker` must be a `KalshiBroker` with `list_markets()`. In stub
        mode the broker returns an empty DiscoveryResult and we no-op.
        """
        from trading_corp.data.kalshi_market_map import EventType  # local import to defer

        self._reload()
        if not self.enabled:
            return []

        # Read config knobs.
        disc_cfg = self._strat_cfg.get("discovery") or {}
        tail_cfg = self._strat_cfg.get("tail") or {}
        sizing_cfg = self._strat_cfg.get("sizing") or {}
        per_cycle = self._strat_cfg.get("per_cycle") or {}

        cache_ttl = float(disc_cfg.get("cache_ttl_sec", 600))
        max_series = int(disc_cfg.get("max_series_per_category", 30))
        max_markets = int(disc_cfg.get("max_markets_per_series", 50))
        categories = tuple(disc_cfg.get("categories") or ())  # empty -> broker default

        yes_max_yes_tail = float(tail_cfg.get("yes_max_for_yes_tail", 0.05))
        yes_min_no_tail = float(tail_cfg.get("yes_min_for_no_tail", 0.95))
        min_edge_cents = float(tail_cfg.get("min_edge_cents", 1.0))
        min_edge_dollars = min_edge_cents / 100.0

        fixed_usd = float(sizing_cfg.get("fixed_usd_per_leg", 1.0))
        max_pairs = int(per_cycle.get("max_pairs", 5))
        cooldown_minutes = float(per_cycle.get("cooldown_minutes", 60))

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
                if self._db_url:
                    try:
                        set_agent_state(
                            self.name, _LAST_DISCOVERY_KEY,
                            {"ts": now.isoformat(timespec="seconds")},
                            db_url=self._db_url,
                        )
                    except Exception:
                        pass
            except Exception as e:
                log.warning("kalshi_tail_price_arb: discovery failed: %s", e)
                return []

        if self._discovery_cache is None:
            return []

        # Walk binary events, find tail opportunities.
        cooldowns = self._load_cooldowns()
        cooldown_until = (now + timedelta(minutes=cooldown_minutes)).isoformat(timespec="seconds")
        new_cooldowns = dict(cooldowns)

        # Tail-price arb is a per-MARKET property: any market with binary
        # YES/NO outcomes can show YES_ask + NO_ask < $1 regardless of
        # whether the parent event is single-market binary or part of a
        # multi-outcome / temporal / bucket event group. Only COLLECTION
        # events (KXMVE* aggregate containers) are skipped — those aren't
        # directly tradeable.
        #
        # We collect ALL examined tail candidates (positive OR negative edge)
        # so that downstream we can emit per-candidate audit events for the
        # top-N narrowest-misses — gives the dashboard rail per-market grain
        # like polymarket has, even when 0 candidates clear the threshold.
        opportunities: list[_TailOpportunity] = []
        examined: list[dict] = []  # all tail candidates with full context
        n_markets_scanned = 0
        n_tail_candidates = 0
        for event in self._discovery_cache.events:
            if event.event_type == EventType.COLLECTION:
                continue
            for m in event.markets:
                n_markets_scanned += 1
                yes_mid = m.yes_mid
                if yes_mid <= 0:
                    continue
                # Tail filter: either yes is a long shot or no is a long shot.
                in_yes_tail = yes_mid <= yes_max_yes_tail
                in_no_tail = yes_mid >= yes_min_no_tail
                if not (in_yes_tail or in_no_tail):
                    continue
                n_tail_candidates += 1
                # Edge check: need ASK prices (what we'd pay), not mid.
                # If either ask is 0 (one-sided book) the arb can't be filled.
                if m.yes_ask <= 0 or m.no_ask <= 0:
                    continue
                sum_asks = m.yes_ask + m.no_ask
                # Track this candidate (positive OR negative edge) for top-N audit.
                edge_eval = 1.0 - sum_asks
                examined.append({
                    "ticker": m.ticker,
                    "event_ticker": m.event_ticker,
                    "event_title": event.title,
                    "category": event.category,
                    "subtitle": m.subtitle,
                    "yes_ask": m.yes_ask,
                    "no_ask": m.no_ask,
                    "yes_bid": m.yes_bid,
                    "no_bid": m.no_bid,
                    "sum_asks": sum_asks,
                    "edge_dollars": edge_eval,
                    "edge_cents": round(edge_eval * 100, 2),
                    "in_yes_tail": in_yes_tail,
                    "in_no_tail": in_no_tail,
                    "would_emit": edge_eval >= min_edge_dollars,
                    "min_edge_cents": min_edge_cents,
                    "expires_at": m.expected_expiration_time,
                })
                edge = 1.0 - sum_asks
                if edge < min_edge_dollars:
                    continue
                # Cooldown
                if m.ticker in cooldowns:
                    try:
                        until_dt = datetime.fromisoformat(
                            cooldowns[m.ticker]
                        ).replace(tzinfo=timezone.utc)
                        if until_dt > now:
                            continue
                    except (TypeError, ValueError):
                        pass
                opportunities.append(_TailOpportunity(
                    ticker=m.ticker,
                    event_ticker=m.event_ticker,
                    yes_ask=m.yes_ask, no_ask=m.no_ask,
                    yes_bid=m.yes_bid, no_bid=m.no_bid,
                    sum_asks=sum_asks,
                    edge_dollars=edge,
                    title=m.title,
                    expected_expiration_time=m.expected_expiration_time,
                ))

        # Rank by edge descending (best arbs first), cap per cycle.
        opportunities.sort(key=lambda o: o.edge_dollars, reverse=True)
        opportunities = opportunities[:max_pairs]

        if logger_agent is not None:
            logger_agent.log_event(
                self.name, "kalshi_tail_arb_scan",
                {
                    "strategy": self.name,
                    "division": self.division,
                    "n_markets_scanned": n_markets_scanned,
                    "n_tail_candidates": n_tail_candidates,
                    "n_opportunities_above_threshold": len(opportunities),
                    "n_examined": len(examined),
                    "min_edge_cents": min_edge_cents,
                    "yes_max_for_yes_tail": yes_max_yes_tail,
                    "yes_min_for_no_tail": yes_min_no_tail,
                },
            )

            # Per-candidate audit events for the top-N narrowest-misses (and
            # any genuine arbs). Same pattern as polymarket_llm_probability_called
            # but without the LLM cost — pure structural data. N is bounded by
            # `audit_top_n_candidates` config knob (default 5).
            top_n = int(self._strat_cfg.get("audit_top_n_candidates", 5))
            top_examined = sorted(examined, key=lambda d: d["edge_dollars"], reverse=True)[:top_n]
            for cand in top_examined:
                logger_agent.log_event(
                    self.name, "kalshi_market_evaluated",
                    {
                        "strategy": self.name,
                        "division": self.division,
                        **cand,
                    },
                )

        # Build ProposedOrder pairs.
        orders: list[ProposedOrder] = []
        for opp in opportunities:
            pair_id = uuid.uuid4().hex[:12]
            new_cooldowns[opp.ticker] = cooldown_until

            # Sizing: fixed USD per leg. qty = dollars / share_price.
            yes_qty = fixed_usd / opp.yes_ask if opp.yes_ask > 0 else 0
            no_qty = fixed_usd / opp.no_ask if opp.no_ask > 0 else 0
            if yes_qty <= 0 or no_qty <= 0:
                continue

            common_extra = {
                "kalshi_pair_id": pair_id,
                "ticker": opp.ticker,
                "event_ticker": opp.event_ticker,
                "event_title": opp.title,
                "yes_ask": opp.yes_ask,
                "no_ask": opp.no_ask,
                "sum_asks": opp.sum_asks,
                "edge_dollars": opp.edge_dollars,
                "edge_cents": round(opp.edge_dollars * 100, 2),
                "max_dollar_risk": fixed_usd * 2,  # both legs
                "expires_at": opp.expected_expiration_time,
                "tier": "tail_arb_fixed_usd",
                "source_signal": "tail_price_arb",
                "is_prediction_market": True,
            }

            orders.append(ProposedOrder(
                strategy=self.name,
                symbol=f"{opp.ticker}:yes",
                side="buy",
                qty=yes_qty,
                order_type="limit",
                limit_price=opp.yes_ask,
                rationale=(
                    f"Tail arb on {opp.ticker}: yes_ask={opp.yes_ask:.3f} + "
                    f"no_ask={opp.no_ask:.3f} = {opp.sum_asks:.3f} "
                    f"(edge {opp.edge_dollars*100:.2f}¢)"
                ),
                extra={**common_extra, "leg": "yes"},
            ))
            orders.append(ProposedOrder(
                strategy=self.name,
                symbol=f"{opp.ticker}:no",
                side="buy",
                qty=no_qty,
                order_type="limit",
                limit_price=opp.no_ask,
                rationale=(
                    f"Tail arb on {opp.ticker} (no leg of pair {pair_id})"
                ),
                extra={**common_extra, "leg": "no"},
            ))

        self._save_cooldowns(new_cooldowns, now=now)
        return orders


__all__ = ["KalshiTailPriceArbAgent"]
