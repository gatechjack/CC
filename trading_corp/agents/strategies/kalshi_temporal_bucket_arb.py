"""Kalshi Temporal + Bucket arb strategy — Phase K2.2.

Two related intra-Kalshi arb patterns sharing one strategy class:

  TEMPORAL ARB
    For events with markets like "Will X happen by D1" + "Will X happen by D2"
    (D2 > D1), the constraint P_yes(D1) ≤ P_yes(D2) must hold (because if X
    happens by D1, it has also happened by D2 — the later-dated market is a
    superset). When violated (P_yes(D1) > P_yes(D2)), arb position:
        BUY NO on early-dated  (cost: 1 - yes_ask_early)
        BUY YES on late-dated  (cost: yes_ask_late)
    Min payout = $1 (always one leg wins), so profit = yes_ask_early - yes_ask_late
    minus fees (~2-4¢ typical taker round-trip across both legs).

  BUCKET ARB
    For events with N markets partitioning outcome space (e.g. quarterly
    recession start dates, monthly-resolved bins), the sum of yes_ask across
    all markets must equal $1 (exactly one bucket wins). When sum < $1 - fee
    threshold, buy YES on every market: cost = sum_yes_asks, guaranteed
    payout = $1, profit = 1 - sum_yes_asks. Threshold must clear N × per-leg
    fees (~5¢ for a 4-bucket market at ~25¢ legs).

Per K2 fee research (memory `trading_corp_kalshi.md`): temporal arb is the
single most attractive intra-Kalshi pattern — windows last minutes-to-hours
(not the 200ms windows of YES+NO sniping), no head-to-head bot competition
documented, and dislocations of tens of bps to >1% are observable on
related-market pairs. Bucket arb is structurally clean but rarer.

Phase K2.2 ships paper-only. Each detection emits a SET of ProposedOrders
(2 legs for temporal, N legs for bucket) linked via `kalshi_arb_set_id`.
Both legs flow through `risk_agent.evaluate()`. No live placement until K5+.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from trading_corp.persistence.db import load_agent_state, set_agent_state
from trading_corp.persistence.models import ProposedOrder

log = logging.getLogger(__name__)


_LAST_DISCOVERY_KEY = "last_discovery_ts"
_COOLDOWNS_KEY = "tb_arb_cooldowns"


# ── Subtitle date parsing ──────────────────────────────────────────────
#
# Empirically-observed Kalshi temporal subtitle formats (2026-05-10 sample):
#   "Before 2027"
#   "Before July 2026"
#   "Before Jan 20, 2029"
#   "Before Aug 1, 2027"
#   "Before Apr 1, 2027"
#   "Before Jan 1, 2028"
#   "On or before <date>"
# We parse to a `date` (YYYY-MM-DD); failure returns None and the market is
# skipped from temporal pairing (still considered for bucket sum if relevant).

_MONTH_NAME_TO_NUM = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_DATE_RE_FULL = re.compile(
    r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})",
)
_DATE_RE_MONTH_YEAR = re.compile(r"(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})")
_DATE_RE_YEAR = re.compile(r"\b(?P<year>20\d{2})\b")
_DATE_RE_ISO = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b")
_DATE_RE_QUARTER = re.compile(r"\bQ(?P<q>[1-4])\s*(?P<year>\d{4})\b", re.IGNORECASE)


def parse_subtitle_date(subtitle: str) -> date | None:
    """Best-effort extract a `date` from a Kalshi subtitle.

    Returns the LATEST possible interpretable date so "Before July 2026" gets
    treated as "by end of July 2026" -> 2026-07-31. This matches the temporal
    semantics: P("by July") = P(event happens at any point through end-of-July).

    Returns None if no date pattern matches.
    """
    s = subtitle.strip()
    if not s:
        return None

    # ISO date
    m = _DATE_RE_ISO.search(s)
    if m:
        try:
            return date(int(m["year"]), int(m["month"]), int(m["day"]))
        except ValueError:
            pass

    # Quarter (e.g. "Q1 2026" -> end of Q1 = March 31)
    m = _DATE_RE_QUARTER.search(s)
    if m:
        q = int(m["q"])
        y = int(m["year"])
        end_month = q * 3
        # Last day of the end month — simplification: use day 30 to avoid
        # month-length math (good enough for ordering, not for cash flow).
        return date(y, end_month, 28)

    # "Month Day, Year" (e.g. "Jan 20, 2029" or "Aug 1 2027")
    m = _DATE_RE_FULL.search(s)
    if m:
        mo = _MONTH_NAME_TO_NUM.get(m["month"].lower())
        if mo:
            try:
                return date(int(m["year"]), mo, int(m["day"]))
            except ValueError:
                pass

    # "Month Year" (e.g. "July 2026" -> end of month)
    m = _DATE_RE_MONTH_YEAR.search(s)
    if m:
        mo = _MONTH_NAME_TO_NUM.get(m["month"].lower())
        if mo:
            return date(int(m["year"]), mo, 28)

    # Year only (e.g. "Before 2027" -> end of year)
    m = _DATE_RE_YEAR.search(s)
    if m:
        return date(int(m["year"]), 12, 31)

    return None


# ── Detection ──────────────────────────────────────────────────────────


@dataclass
class _TemporalOpportunity:
    """One temporal pair where P(early) > P(late)."""
    event_ticker: str
    early_ticker: str
    early_date: date
    early_yes_ask: float
    late_ticker: str
    late_date: date
    late_yes_ask: float
    edge_dollars: float    # = yes_ask_early - yes_ask_late
    title: str


@dataclass
class _BucketOpportunity:
    """One bucket event where sum of yes_asks < 1 - threshold."""
    event_ticker: str
    legs: list[tuple[str, float]]   # [(ticker, yes_ask), ...]
    sum_yes_asks: float
    edge_dollars: float             # = 1 - sum_yes_asks
    title: str


def _detect_temporal_violations(
    event,
    min_edge_cents: float,
    horizon_cutoff: date | None = None,
) -> list[_TemporalOpportunity]:
    """Find pair-wise constraint violations on a TEMPORAL event.

    Markets are sorted by parsed subtitle date. For each pair (early, late),
    the constraint is P_yes(early) ≤ P_yes(late). Emit when the gap exceeds
    `min_edge_cents` (cleared after fees by the cap).
    """
    dated_markets: list[tuple[date, Any]] = []
    for m in event.markets:
        d = parse_subtitle_date(m.subtitle)
        if d is None:
            continue
        # 60-day horizon cap: drop markets resolving beyond the cutoff so no
        # temporal pair's late leg locks capital too long. Both legs end up
        # <= cutoff since late >= early. See temporal.max_horizon_days.
        if horizon_cutoff is not None and d > horizon_cutoff:
            continue
        if m.yes_ask <= 0:
            continue
        dated_markets.append((d, m))

    # Sort by date ascending
    dated_markets.sort(key=lambda x: x[0])

    out: list[_TemporalOpportunity] = []
    min_edge_dollars = min_edge_cents / 100.0
    # All pairs (i, j) with j > i
    for i in range(len(dated_markets)):
        d_early, m_early = dated_markets[i]
        for j in range(i + 1, len(dated_markets)):
            d_late, m_late = dated_markets[j]
            if d_late <= d_early:
                continue  # tied dates — skip
            if m_late.yes_ask <= 0:
                continue
            # Violation: P(early) > P(late) by enough to clear fees
            edge = m_early.yes_ask - m_late.yes_ask
            if edge < min_edge_dollars:
                continue
            out.append(_TemporalOpportunity(
                event_ticker=event.event_ticker,
                early_ticker=m_early.ticker,
                early_date=d_early,
                early_yes_ask=m_early.yes_ask,
                late_ticker=m_late.ticker,
                late_date=d_late,
                late_yes_ask=m_late.yes_ask,
                edge_dollars=edge,
                title=event.title,
            ))
    return out


def _detect_bucket_violations(
    event,
    min_edge_cents: float,
) -> _BucketOpportunity | None:
    """If sum(yes_ask) across all markets in event < 1 - threshold, emit.

    Returns one opportunity (or None) — the entire event is one arb set
    (buy YES on every leg).
    """
    legs: list[tuple[str, float]] = []
    sum_y = 0.0
    for m in event.markets:
        if m.yes_ask <= 0:
            return None  # one-sided book on any leg -> no arb
        legs.append((m.ticker, m.yes_ask))
        sum_y += m.yes_ask
    if not legs:
        return None
    edge = 1.0 - sum_y
    min_edge_dollars = min_edge_cents / 100.0
    if edge < min_edge_dollars:
        return None
    return _BucketOpportunity(
        event_ticker=event.event_ticker,
        legs=legs,
        sum_yes_asks=sum_y,
        edge_dollars=edge,
        title=event.title,
    )


# ── Agent ──────────────────────────────────────────────────────────────


class KalshiTemporalBucketArbAgent:
    """Phase K2.2 detector.

    Strategy config in `strategies.yaml`:
        kalshi_temporal_bucket_arb:
          enabled: false
          auto_execute: false
          division: kalshi_arbitrage
          poll_interval_sec: 300
          discovery:
            categories: [...]
            max_series_per_category: 30
            max_markets_per_series: 50
            cache_ttl_sec: 600
          temporal:
            enabled: true
            min_edge_cents: 4              # clears 2-leg taker fees ~2-4¢
          bucket:
            enabled: true
            min_edge_cents: 5              # clears N-leg fees on multi-bucket events
          sizing:
            fixed_usd_per_leg: 1.0
          per_cycle:
            max_sets: 5
            cooldown_minutes: 60
    """

    name = "kalshi_temporal_bucket_arb"

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
        self._discovery_cache: Any = None
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

    # ── Cooldown persistence (per-event-ticker) ──────────────────────────

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
        kept: dict[str, str] = {}
        for k, until in cooldowns.items():
            try:
                if datetime.fromisoformat(until).replace(tzinfo=timezone.utc) > now:
                    kept[k] = until
            except (TypeError, ValueError):
                pass
        try:
            set_agent_state(self.name, _COOLDOWNS_KEY, kept, db_url=self._db_url)
        except Exception as e:
            log.warning("kalshi_temporal_bucket_arb: persist cooldowns failed: %s", e)

    # ── Public scan entry point ────────────────────────────────────────

    async def run_scan_cycle(
        self,
        broker,
        *,
        logger_agent=None,
    ) -> list[ProposedOrder]:
        """One scan cycle. Returns ProposedOrder SETS (2 legs for temporal,
        N legs for bucket) linked via `kalshi_arb_set_id`.
        """
        from trading_corp.data.kalshi_market_map import EventType  # deferred import

        self._reload()
        if not self.enabled:
            return []

        disc_cfg = self._strat_cfg.get("discovery") or {}
        temporal_cfg = self._strat_cfg.get("temporal") or {}
        bucket_cfg = self._strat_cfg.get("bucket") or {}
        sizing_cfg = self._strat_cfg.get("sizing") or {}
        per_cycle = self._strat_cfg.get("per_cycle") or {}

        cache_ttl = float(disc_cfg.get("cache_ttl_sec", 600))
        max_series = int(disc_cfg.get("max_series_per_category", 30))
        max_markets = int(disc_cfg.get("max_markets_per_series", 50))
        categories = tuple(disc_cfg.get("categories") or ())

        temporal_enabled = bool(temporal_cfg.get("enabled", True))
        temporal_min_edge_cents = float(temporal_cfg.get("min_edge_cents", 4.0))
        temporal_max_horizon_days = int(temporal_cfg.get("max_horizon_days", 60))
        bucket_enabled = bool(bucket_cfg.get("enabled", True))
        bucket_min_edge_cents = float(bucket_cfg.get("min_edge_cents", 5.0))

        fixed_usd = float(sizing_cfg.get("fixed_usd_per_leg", 1.0))
        max_sets = int(per_cycle.get("max_sets", 5))
        cooldown_minutes = float(per_cycle.get("cooldown_minutes", 60))

        # Refresh discovery if cache stale.
        now = datetime.now(timezone.utc)
        # 60-day horizon cap on temporal pairs (0 disables). Applied in
        # detection AND the parallel per-pair audit walk below, so the rail
        # and the emitted opps agree.
        horizon_cutoff = (
            now.date() + timedelta(days=temporal_max_horizon_days)
            if temporal_max_horizon_days > 0 else None
        )
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
                log.warning("kalshi_temporal_bucket_arb: discovery failed: %s", e)
                return []

        if self._discovery_cache is None:
            return []

        cooldowns = self._load_cooldowns()
        cooldown_until = (now + timedelta(minutes=cooldown_minutes)).isoformat(timespec="seconds")
        new_cooldowns = dict(cooldowns)

        # Walk events, gather opportunities.
        n_temporal_events = 0
        n_bucket_events = 0
        temporal_opps: list[_TemporalOpportunity] = []
        bucket_opps: list[_BucketOpportunity] = []
        # Per-pair / per-event examined list for top-N audit emission.
        # Same purpose as kalshi_tail_price_arb's `examined` — gives the
        # rail per-pair grain even when 0 violations clear the threshold.
        examined_pairs: list[dict] = []
        examined_buckets: list[dict] = []
        for event in self._discovery_cache.events:
            # Cooldown check at the EVENT level (not per pair) — once we've
            # acted on an event, don't re-emit on the same event for cooldown.
            if event.event_ticker in cooldowns:
                try:
                    until_dt = datetime.fromisoformat(
                        cooldowns[event.event_ticker]
                    ).replace(tzinfo=timezone.utc)
                    if until_dt > now:
                        continue
                except (TypeError, ValueError):
                    pass
            if temporal_enabled and event.event_type == EventType.TEMPORAL:
                n_temporal_events += 1
                temporal_opps.extend(_detect_temporal_violations(event, temporal_min_edge_cents, horizon_cutoff))
                # Walk same pairs to build per-pair audit data (positive OR negative edge).
                dated = []
                for m in event.markets:
                    d = parse_subtitle_date(m.subtitle)
                    if d is None or m.yes_ask <= 0:
                        continue
                    if horizon_cutoff is not None and d > horizon_cutoff:
                        continue
                    dated.append((d, m))
                dated.sort(key=lambda x: x[0])
                min_edge_dollars_t = temporal_min_edge_cents / 100.0
                for i in range(len(dated)):
                    d_e, m_e = dated[i]
                    for j in range(i + 1, len(dated)):
                        d_l, m_l = dated[j]
                        if d_l <= d_e or m_l.yes_ask <= 0:
                            continue
                        edge_eval = m_e.yes_ask - m_l.yes_ask
                        examined_pairs.append({
                            "event_ticker": event.event_ticker,
                            "event_title": event.title,
                            "category": event.category,
                            "early_ticker": m_e.ticker,
                            "early_subtitle": m_e.subtitle,
                            "early_date": d_e.isoformat(),
                            "early_yes_ask": m_e.yes_ask,
                            "late_ticker": m_l.ticker,
                            "late_subtitle": m_l.subtitle,
                            "late_date": d_l.isoformat(),
                            "late_yes_ask": m_l.yes_ask,
                            "edge_dollars": edge_eval,
                            "edge_cents": round(edge_eval * 100, 2),
                            "would_emit": edge_eval >= min_edge_dollars_t,
                            "min_edge_cents": temporal_min_edge_cents,
                        })
            elif bucket_enabled and event.event_type == EventType.BUCKET:
                n_bucket_events += 1
                opp = _detect_bucket_violations(event, bucket_min_edge_cents)
                if opp is not None:
                    bucket_opps.append(opp)
                # Per-event audit data regardless of violation.
                sum_y = sum(m.yes_ask for m in event.markets if m.yes_ask > 0)
                edge_eval = 1.0 - sum_y if sum_y > 0 else None
                if edge_eval is not None:
                    examined_buckets.append({
                        "event_ticker": event.event_ticker,
                        "event_title": event.title,
                        "category": event.category,
                        "n_legs": len(event.markets),
                        "sum_yes_asks": sum_y,
                        "edge_dollars": edge_eval,
                        "edge_cents": round(edge_eval * 100, 2),
                        "would_emit": edge_eval >= (bucket_min_edge_cents / 100.0),
                        "min_edge_cents": bucket_min_edge_cents,
                    })

        # Rank by edge descending, cap total sets per cycle.
        all_opps: list[Any] = sorted(
            temporal_opps + bucket_opps,
            key=lambda o: o.edge_dollars,
            reverse=True,
        )[:max_sets]

        if logger_agent is not None:
            logger_agent.log_event(
                self.name, "kalshi_temporal_bucket_scan",
                {
                    "strategy": self.name,
                    "division": self.division,
                    "n_temporal_events_scanned": n_temporal_events,
                    "n_bucket_events_scanned": n_bucket_events,
                    "n_temporal_opportunities": len(temporal_opps),
                    "n_bucket_opportunities": len(bucket_opps),
                    "n_emitted_after_cap": len(all_opps),
                    "n_pairs_examined": len(examined_pairs),
                    "n_buckets_examined": len(examined_buckets),
                    "temporal_min_edge_cents": temporal_min_edge_cents,
                    "bucket_min_edge_cents": bucket_min_edge_cents,
                },
            )

            # Per-pair / per-event audit events for top-N narrowest-misses.
            # Same UX pattern as kalshi_market_evaluated in K2.1 — gives the
            # rail per-pair grain instead of aggregate scan summaries only.
            top_n = int(self._strat_cfg.get("audit_top_n_candidates", 5))
            top_pairs = sorted(examined_pairs, key=lambda d: d["edge_dollars"], reverse=True)[:top_n]
            for pair in top_pairs:
                logger_agent.log_event(
                    self.name, "kalshi_pair_evaluated",
                    {
                        "strategy": self.name,
                        "division": self.division,
                        **pair,
                    },
                )
            top_buckets = sorted(examined_buckets, key=lambda d: d["edge_dollars"], reverse=True)[:top_n]
            for bucket in top_buckets:
                logger_agent.log_event(
                    self.name, "kalshi_bucket_evaluated",
                    {
                        "strategy": self.name,
                        "division": self.division,
                        **bucket,
                    },
                )

        # Build ProposedOrders.
        orders: list[ProposedOrder] = []
        for opp in all_opps:
            set_id = uuid.uuid4().hex[:12]
            new_cooldowns[opp.event_ticker] = cooldown_until

            if isinstance(opp, _TemporalOpportunity):
                # Two legs: BUY NO on early, BUY YES on late.
                no_price_early = max(0.01, 1.0 - opp.early_yes_ask)
                qty_no_early = fixed_usd / no_price_early
                qty_yes_late = fixed_usd / opp.late_yes_ask
                if qty_no_early <= 0 or qty_yes_late <= 0:
                    continue
                common = {
                    "kalshi_arb_set_id": set_id,
                    "kalshi_arb_type": "temporal",
                    "event_ticker": opp.event_ticker,
                    "event_title": opp.title,
                    "edge_dollars": opp.edge_dollars,
                    "edge_cents": round(opp.edge_dollars * 100, 2),
                    "max_dollar_risk": fixed_usd * 2,
                    "tier": "temporal_arb_fixed_usd",
                    "source_signal": "temporal_constraint_violation",
                    "is_prediction_market": True,
                }
                orders.append(ProposedOrder(
                    strategy=self.name,
                    symbol=f"{opp.early_ticker}:no",
                    side="buy",
                    qty=qty_no_early,
                    order_type="limit",
                    limit_price=no_price_early,
                    rationale=(
                        f"Temporal arb on {opp.event_ticker}: "
                        f"P(early {opp.early_date.isoformat()})={opp.early_yes_ask:.3f} > "
                        f"P(late {opp.late_date.isoformat()})={opp.late_yes_ask:.3f} "
                        f"(edge {opp.edge_dollars*100:.2f}c)"
                    ),
                    extra={**common, "leg": "no_early", "ticker": opp.early_ticker,
                           "leg_date": opp.early_date.isoformat()},
                ))
                orders.append(ProposedOrder(
                    strategy=self.name,
                    symbol=f"{opp.late_ticker}:yes",
                    side="buy",
                    qty=qty_yes_late,
                    order_type="limit",
                    limit_price=opp.late_yes_ask,
                    rationale=(
                        f"Temporal arb on {opp.event_ticker} "
                        f"(yes_late leg of set {set_id})"
                    ),
                    extra={**common, "leg": "yes_late", "ticker": opp.late_ticker,
                           "leg_date": opp.late_date.isoformat()},
                ))
            elif isinstance(opp, _BucketOpportunity):
                # N legs: BUY YES on every leg.
                common = {
                    "kalshi_arb_set_id": set_id,
                    "kalshi_arb_type": "bucket",
                    "event_ticker": opp.event_ticker,
                    "event_title": opp.title,
                    "edge_dollars": opp.edge_dollars,
                    "edge_cents": round(opp.edge_dollars * 100, 2),
                    "sum_yes_asks": opp.sum_yes_asks,
                    "max_dollar_risk": fixed_usd * len(opp.legs),
                    "tier": "bucket_arb_fixed_usd",
                    "source_signal": "bucket_sum_violation",
                    "is_prediction_market": True,
                }
                for ticker, yes_ask in opp.legs:
                    qty = fixed_usd / yes_ask
                    if qty <= 0:
                        continue
                    orders.append(ProposedOrder(
                        strategy=self.name,
                        symbol=f"{ticker}:yes",
                        side="buy",
                        qty=qty,
                        order_type="limit",
                        limit_price=yes_ask,
                        rationale=(
                            f"Bucket arb on {opp.event_ticker}: "
                            f"sum(yes_ask)={opp.sum_yes_asks:.3f} "
                            f"(edge {opp.edge_dollars*100:.2f}c, leg of set {set_id})"
                        ),
                        extra={**common, "leg": f"yes_{ticker}", "ticker": ticker},
                    ))

        self._save_cooldowns(new_cooldowns, now=now)
        return orders


__all__ = [
    "KalshiTemporalBucketArbAgent",
    "parse_subtitle_date",
]
