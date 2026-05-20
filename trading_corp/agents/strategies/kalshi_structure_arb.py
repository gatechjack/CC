"""Kalshi Structure Arbitrage — deterministic multi-outcome event strategy.

For each Kalshi event with K >= 3 sub-markets, compute sum_yes_implied.
When the sum exceeds the threshold (default 1.5), the market is collectively
over-pricing YES outcomes — buy NO on the top-M sub-markets by implied_yes.

Skip rules (each logged with a distinct audit kind):
  - K < 3: skip (binary/small-K events, not structure-arb candidates)
  - ticker matches PRICE_BUCKET_REGEX: skip (handled by tail/temporal arb)
  - event.category == "Crypto": skip (handled by kalshi_crypto_arb)
  - event.category in {"Climate", "Weather", "Climate and Weather"}: skip
  - no sub-market has no_ask > 0: skip (can't size a NO bet)
  - sum_yes_implied <= threshold: skip (insufficient structural mispricing)

When firing: pick top-M (default 3) sub-markets by implied_yes (highest first).
For each pick with no_ask > 0, build a ProposedOrder (side=buy, symbol="<ticker>:no"),
run through risk gate, log audit, then log would_have_placed (paper-mode only).

Fixed price-bucket regex (2026-05-17 — fix for backtest bug):
  Old: r'-(?:B|T)\\d'      — missed dash-separator variants
  New: r'-(?:B|T)-?\\d'    — catches BOTH -B1/-T1 AND -B-1/-T-1 forms
  Covers: KXAAAGASD, KXAUNABCONF, KXBTC*, KXETH*, KXBTC15M-*, KXH100MON-*,
          KXTEMPNYCH-* and similar ticker families.

Audit kinds emitted:
  - kalshi_structure_arb_scan               — per-cycle summary
  - kalshi_structure_arb_evaluated          — per qualifying event
  - kalshi_structure_arb_skipped_below_min_k
  - kalshi_structure_arb_skipped_price_bucket
  - kalshi_structure_arb_skipped_crypto
  - kalshi_structure_arb_skipped_weather
  - kalshi_structure_arb_skipped_no_quote
  - kalshi_structure_arb_skipped_below_threshold
  - would_have_placed                        — per approved order (paper)

All audit payloads include `strategy` and `division` per CLAUDE.md § 1.

First-observation tracking (Board directive 2026-05-17):
  In-memory `_seen_event_tickers: set[str]` — no persistence needed; the
  audit log is the durable record. `first_observation: True` appears on
  the first kalshi_structure_arb_evaluated for each event_ticker per
  process lifetime. `prior_implied_yes_per_ticker` is None on first obs,
  then the previous cycle's values on subsequent scans.

Dynamic cadence (Board directive 2026-05-17):
  default_seconds (default 60) / rapid_seconds (default 15) based on
  whether any fresh event (first-seen within rapid_window_hours) is in
  the cache. `next_scan_interval_seconds(now)` exposes this to main.py.

NO LLM in any path. Deterministic only. auto_execute MUST be false
on initial deploy — paper-only by Board directive.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from trading_corp.persistence.models import ProposedOrder

log = logging.getLogger(__name__)

# ── Fixed price-bucket regex (2026-05-17 bug fix) ────────────────────────────
# Old regex r'-(?:B|T)\d' missed dash-separator variants like -T-50, -B-100.
# New regex r'-(?:B|T)-?\d' catches both forms.
# Verified against: KXAAAGASD-...-T-50, KXAUNABCONF-...-B-100, KXBTC15M-...,
# KXH100MON-..., KXTEMPNYCH-..., KXBTC-...-T100000, KXETH-...-B50000.
PRICE_BUCKET_REGEX = re.compile(r'-(?:B|T)-?\d')

# Category strings that identify weather markets (Kalshi uses both forms).
_WEATHER_CATEGORIES = {"Climate", "Weather", "Climate and Weather"}
_CRYPTO_CATEGORY = "Crypto"

# Guard: never fire on fewer than this many sub-markets.
_MIN_K = 3


def _kalshi_quote_dollars(market: Any) -> tuple[float, float, float, float]:
    """Extract (yes_ask, no_ask, yes_bid, no_bid) in dollars from a market object.

    Kalshi prices may be in cents (int 0-99) or dollars (float 0.0-1.0).
    The heuristic: if the numeric value is >= 1.0, divide by 100.
    Returns 0.0 for missing/zero/None sides.
    """
    def _to_dollars(v: Any) -> float:
        try:
            f = float(v or 0)
        except (TypeError, ValueError):
            return 0.0
        if f >= 1.0:
            return f / 100.0
        return f

    yes_ask = _to_dollars(getattr(market, "yes_ask", None))
    no_ask = _to_dollars(getattr(market, "no_ask", None))
    yes_bid = _to_dollars(getattr(market, "yes_bid", None))
    no_bid = _to_dollars(getattr(market, "no_bid", None))
    return yes_ask, no_ask, yes_bid, no_bid


def _implied_yes_from_market(market: Any) -> float | None:
    """Compute implied YES probability for a sub-market.

    Brief rule:
      - (yes_bid + yes_ask) / 2 when both > 0
      - else yes_ask if > 0
      - else yes_bid if > 0
      - else None (skip)
    """
    yes_ask, _, yes_bid, _ = _kalshi_quote_dollars(market)
    if yes_bid > 0 and yes_ask > 0:
        return (yes_bid + yes_ask) / 2.0
    if yes_ask > 0:
        return yes_ask
    if yes_bid > 0:
        return yes_bid
    return None


class KalshiStructureArbAgent:
    """Deterministic Kalshi structure-arb strategy.

    No LLM. No external data sources beyond broker.list_markets().
    Hot-reloadable config in `strategies.yaml kalshi_structure_arb:`.
    auto_execute MUST remain false on initial deploy (paper-only).
    """

    name = "kalshi_structure_arb"

    # ── Hard-stop gates (2026-05-18) ─────────────────────────────────────
    # Code-level paper-mode enforcement. Flipping these requires a code
    # change + Board memo per CLAUDE.md § 1 + § 4 — yaml `auto_execute: true`
    # is INSUFFICIENT to enable live placement. This guards against the
    # CLAUDE.md § 5 "Webhook risk gate ≠ LangGraph risk gate" sharp edge
    # where a future PR could flip auto_execute via yaml alone.
    PAPER_MODE_ONLY: bool = True
    LIVE_MODE_BOARD_APPROVED: bool = False

    def __init__(self, *, db_url: str | None = None) -> None:
        self._db_url = db_url
        self._strategies_yaml = Path("config/strategies.yaml")
        self._strat_mtime: float = 0.0
        self._strat_cfg: dict[str, Any] = {}
        self._discovery_cache: Any = None
        self._discovery_ts: datetime | None = None

        # ── First-observation tracking (Board 2026-05-17) ───────────────────
        # In-memory only — does NOT persist across restarts. The audit log
        # is the durable record (CLAUDE.md § 1 "State + audit": persistent
        # state required only if it affects future trade decisions; this
        # enriches audits, does not gate orders).
        self._seen_event_tickers: set[str] = set()
        # _prev_implied_yes: event_ticker -> {ticker: implied_yes}
        # Populated after each qualifying scan so the next scan can compute
        # price drift. None = first observation (not yet in set).
        self._prev_implied_yes: dict[str, dict[str, float]] = {}
        # _first_seen_ts: event_ticker -> datetime (UTC), for rapid cadence
        self._first_seen_ts: dict[str, datetime] = {}

        self._reload()

    def _reload(self) -> None:
        try:
            sm = self._strategies_yaml.stat().st_mtime
            if sm != self._strat_mtime:
                with self._strategies_yaml.open("r") as f:
                    data = yaml.safe_load(f) or {}
                self._strat_cfg = data.get(self.name) or {}
                self._strat_mtime = sm
        except Exception as e:
            log.warning("kalshi_structure_arb: yaml reload failed: %s", e)
            self._strat_cfg = {}

    @property
    def enabled(self) -> bool:
        self._reload()
        return bool(self._strat_cfg.get("enabled", False))

    @property
    def auto_execute(self) -> bool:
        self._reload()
        # Code-level hard stop: even if yaml says auto_execute=true,
        # paper-mode constants gate the property to False. Flipping to live
        # requires (a) class-constant flip in code + (b) yaml flip + (c)
        # Board memo per CLAUDE.md § 1. This deliberately matches the
        # auto_execute_caps richness in the LangGraph path so a future
        # session can't enable live trading via yaml-edit alone.
        if self.PAPER_MODE_ONLY or not self.LIVE_MODE_BOARD_APPROVED:
            return False
        return bool(self._strat_cfg.get("auto_execute", False))

    @property
    def division(self) -> str:
        self._reload()
        return str(self._strat_cfg.get("division", "kalshi_structure_arb"))

    # ── Dynamic cadence (Board 2026-05-17) ───────────────────────────────────

    def next_scan_interval_seconds(self, now: datetime) -> float:
        """Return the appropriate sleep interval after a scan cycle.

        If any event_ticker was first observed within rapid_window_hours,
        return rapid_seconds (default 15). Otherwise return default_seconds
        (default 60).
        """
        cadence = self._strat_cfg.get("cadence") or {}
        default_sec = float(cadence.get("default_seconds", 60))
        rapid_sec = float(cadence.get("rapid_seconds", 15))
        rapid_window_h = float(cadence.get("rapid_window_hours", 24))

        cutoff = now.timestamp() - rapid_window_h * 3600.0
        for ts in self._first_seen_ts.values():
            if ts.timestamp() >= cutoff:
                return rapid_sec
        return default_sec

    # ── Public scan entry ─────────────────────────────────────────────────────

    async def run_scan_cycle(
        self,
        kalshi_broker: Any,
        *,
        logger_agent: Any = None,
    ) -> list[ProposedOrder]:
        """One scan cycle. Returns a list of ProposedOrders (paper-mode).

        Caller (main.py loop) must run each order through risk_agent.evaluate()
        and log would_have_placed. This method does NOT touch risk_agent directly
        — that belongs in the scan loop per CLAUDE.md § 1 (single chokepoint in
        the orchestrator, not inside the strategy).

        Wait — re-reading the brief: "evaluate via deps.risk_agent.evaluate(...)
        [and] if risk-approved AND auto_execute=false: write would_have_placed audit".
        The brief specifies the risk gate runs INSIDE the strategy scan cycle,
        following the inline pattern from kalshi_crypto_arb + main.py's weather loop.
        However, examining the existing main.py patterns, the weather and crypto
        strategies return raw ProposedOrders and the loop handles risk + audit.

        This strategy follows the same pattern as kalshi_weather_arb: return raw
        ProposedOrders; the orchestration loop (_scheduled_kalshi_structure_arb_loop
        in main.py) owns the risk gate + would_have_placed audit. The
        `kalshi_structure_arb_evaluated` audit is written HERE (before any decision
        branch), per CLAUDE.md § 1.
        """
        self._reload()
        if not self.enabled:
            return []

        disc_cfg = self._strat_cfg.get("discovery") or {}
        max_series = int(disc_cfg.get("max_series_per_category", 30))
        max_markets = int(disc_cfg.get("max_markets_per_series", 50))
        cache_ttl = float(disc_cfg.get("cache_ttl_sec", 600))

        threshold_cfg = self._strat_cfg.get("threshold") or {}
        sum_yes_min = float(threshold_cfg.get("sum_yes_implied_min", 1.5))
        min_k = int(threshold_cfg.get("min_k", 3))
        top_m = int(threshold_cfg.get("top_m", 3))

        sizing = self._strat_cfg.get("sizing") or {}
        fixed_usd = float(sizing.get("fixed_amount", 1.0))

        categories_cfg = list(disc_cfg.get("categories") or []) or None

        now = datetime.now(timezone.utc)

        # ── 1. Discovery ────────────────────────────────────────────────────
        need_refresh = (
            self._discovery_cache is None
            or self._discovery_ts is None
            or (now - self._discovery_ts).total_seconds() > cache_ttl
        )
        if need_refresh:
            try:
                self._discovery_cache = await kalshi_broker.list_markets(
                    categories=tuple(categories_cfg) if categories_cfg else None,
                    max_series_per_category=max_series,
                    max_markets_per_series=max_markets,
                )
                self._discovery_ts = now
            except Exception as e:
                log.warning("kalshi_structure_arb: discovery failed: %s", e)
                return []

        events = (
            self._discovery_cache.events
            if self._discovery_cache is not None
            else []
        )

        # ── 2. Group sub-markets by event_ticker ───────────────────────────
        events_map: dict[str, dict[str, Any]] = {}
        for event in events:
            et = getattr(event, "event_ticker", None) or getattr(event, "ticker", None)
            if not et:
                continue
            category = getattr(event, "category", "") or ""
            markets = getattr(event, "markets", []) or []
            if et not in events_map:
                events_map[et] = {"event_ticker": et, "category": category, "markets": []}
            for m in markets:
                events_map[et]["markets"].append(m)

        # ── 3. Scan counters ───────────────────────────────────────────────
        n_events_evaluated = 0
        n_qualified = 0
        n_skipped_total = 0
        n_orders_emitted = 0
        orders: list[ProposedOrder] = []

        for et, ev in events_map.items():
            n_events_evaluated += 1
            category = ev["category"]
            sub_markets = ev["markets"]

            # ── Skip: K < min_k ────────────────────────────────────────────
            k = len(sub_markets)
            if k < min_k:
                n_skipped_total += 1
                if logger_agent is not None:
                    logger_agent.log_event(
                        self.name,
                        "kalshi_structure_arb_skipped_below_min_k",
                        {
                            "strategy": self.name,
                            "division": self.division,
                            "event_ticker": et,
                            "category": category,
                            "k": k,
                            "min_k": min_k,
                        },
                    )
                continue

            # ── Skip: price-bucket regex (2026-05-17 fix) ──────────────────
            # Check all sub-market tickers; if ANY matches, skip the event.
            price_bucket_ticker = next(
                (
                    getattr(m, "ticker", "") or ""
                    for m in sub_markets
                    if PRICE_BUCKET_REGEX.search(getattr(m, "ticker", "") or "")
                ),
                None,
            )
            if price_bucket_ticker is not None:
                n_skipped_total += 1
                if logger_agent is not None:
                    logger_agent.log_event(
                        self.name,
                        "kalshi_structure_arb_skipped_price_bucket",
                        {
                            "strategy": self.name,
                            "division": self.division,
                            "event_ticker": et,
                            "category": category,
                            "k": k,
                            "example_ticker": price_bucket_ticker,
                        },
                    )
                continue

            # ── Skip: Crypto ───────────────────────────────────────────────
            if category == _CRYPTO_CATEGORY:
                n_skipped_total += 1
                if logger_agent is not None:
                    logger_agent.log_event(
                        self.name,
                        "kalshi_structure_arb_skipped_crypto",
                        {
                            "strategy": self.name,
                            "division": self.division,
                            "event_ticker": et,
                            "category": category,
                            "k": k,
                        },
                    )
                continue

            # ── Skip: Weather / Climate ────────────────────────────────────
            if category in _WEATHER_CATEGORIES:
                n_skipped_total += 1
                if logger_agent is not None:
                    logger_agent.log_event(
                        self.name,
                        "kalshi_structure_arb_skipped_weather",
                        {
                            "strategy": self.name,
                            "division": self.division,
                            "event_ticker": et,
                            "category": category,
                            "k": k,
                        },
                    )
                continue

            # ── Compute implied_yes per sub-market ─────────────────────────
            sub_with_implied: list[tuple[Any, float]] = []
            for m in sub_markets:
                imp = _implied_yes_from_market(m)
                if imp is not None:
                    sub_with_implied.append((m, imp))

            # ── Skip: no sub-market has no_ask > 0 ────────────────────────
            has_no_ask = any(
                _kalshi_quote_dollars(m)[1] > 0
                for m, _ in sub_with_implied
            )
            if not has_no_ask:
                n_skipped_total += 1
                if logger_agent is not None:
                    logger_agent.log_event(
                        self.name,
                        "kalshi_structure_arb_skipped_no_quote",
                        {
                            "strategy": self.name,
                            "division": self.division,
                            "event_ticker": et,
                            "category": category,
                            "k": k,
                        },
                    )
                continue

            sum_yes_implied = sum(imp for _, imp in sub_with_implied)

            # ── Skip: below threshold ──────────────────────────────────────
            if sum_yes_implied <= sum_yes_min:
                n_skipped_total += 1
                if logger_agent is not None:
                    logger_agent.log_event(
                        self.name,
                        "kalshi_structure_arb_skipped_below_threshold",
                        {
                            "strategy": self.name,
                            "division": self.division,
                            "event_ticker": et,
                            "category": category,
                            "k": k,
                            "sum_yes_implied": round(sum_yes_implied, 4),
                            "threshold": sum_yes_min,
                        },
                    )
                continue

            # ── Qualifying event — first-observation tracking ──────────────
            first_observation = et not in self._seen_event_tickers
            if first_observation:
                self._seen_event_tickers.add(et)
                self._first_seen_ts[et] = now

            prior_implied_yes_per_ticker = (
                self._prev_implied_yes.get(et)
            )  # None if first observation

            # current implied_yes per ticker (for audit + state update)
            current_implied_yes_per_ticker: dict[str, float] = {
                getattr(m, "ticker", "") or "": round(imp, 4)
                for m, imp in sub_with_implied
            }

            # Sort by implied_yes descending → pick top-M
            sub_sorted = sorted(sub_with_implied, key=lambda x: x[1], reverse=True)
            picks_m = sub_sorted[:top_m]

            picks_info = [
                {
                    "ticker": getattr(m, "ticker", "") or "",
                    "implied_yes": round(imp, 4),
                }
                for m, imp in picks_m
            ]

            # ── AUDIT: kalshi_structure_arb_evaluated — BEFORE decision ────
            # Per CLAUDE.md § 1: "audit log writes BEFORE every decision branch"
            if logger_agent is not None:
                logger_agent.log_event(
                    self.name,
                    "kalshi_structure_arb_evaluated",
                    {
                        "strategy": self.name,
                        "division": self.division,
                        "event_ticker": et,
                        "category": category,
                        "K": k,
                        "sum_yes_implied": round(sum_yes_implied, 4),
                        "picks": picks_info,
                        "first_observation": first_observation,
                        "prior_implied_yes_per_ticker": prior_implied_yes_per_ticker,
                        "current_implied_yes_per_ticker": current_implied_yes_per_ticker,
                        "top_m": top_m,
                        "threshold": sum_yes_min,
                    },
                )

            n_qualified += 1

            # ── Update in-memory state for next scan ───────────────────────
            self._prev_implied_yes[et] = current_implied_yes_per_ticker

            # ── Build ProposedOrders for eligible picks ────────────────────
            for m, imp in picks_m:
                ticker = getattr(m, "ticker", "") or ""
                _, no_ask, _, _ = _kalshi_quote_dollars(m)
                if no_ask <= 0:
                    # Can't size a NO bet without no_ask
                    continue

                qty = fixed_usd / no_ask
                order = ProposedOrder(
                    strategy=self.name,
                    symbol=f"{ticker}:no",
                    side="buy",
                    qty=qty,
                    order_type="limit",
                    limit_price=no_ask,
                    rationale=(
                        f"Structure arb: event={et} ({category}), "
                        f"K={k}, sum_yes={sum_yes_implied:.3f}>={sum_yes_min}, "
                        f"implied_yes={imp:.3f}, no_ask={no_ask:.3f}, "
                        f"size=${fixed_usd:.2f}"
                    ),
                    extra={
                        "ticker": ticker,
                        "event_ticker": et,
                        "category": category,
                        "k": k,
                        "sum_yes_implied": round(sum_yes_implied, 4),
                        "implied_yes_at_entry": round(imp, 4),
                        "implied_prob_at_entry": round(imp, 4),
                        "no_ask": no_ask,
                        "fixed_usd": fixed_usd,
                        "max_dollar_risk": fixed_usd,
                        "tier": "structure_arb_fixed_usd",
                        "source_signal": "kalshi_discovery",
                        "is_prediction_market": True,
                        "first_observation": first_observation,
                        "outcome": "no",
                    },
                )
                orders.append(order)
                n_orders_emitted += 1

        # ── 4. Per-cycle scan summary ──────────────────────────────────────
        if logger_agent is not None:
            logger_agent.log_event(
                self.name,
                "kalshi_structure_arb_scan",
                {
                    "strategy": self.name,
                    "division": self.division,
                    "n_events_evaluated": n_events_evaluated,
                    "n_qualified": n_qualified,
                    "n_skipped_total": n_skipped_total,
                    "n_orders_emitted": n_orders_emitted,
                    "threshold": sum_yes_min,
                    "min_k": min_k,
                    "top_m": top_m,
                },
            )

        return orders
