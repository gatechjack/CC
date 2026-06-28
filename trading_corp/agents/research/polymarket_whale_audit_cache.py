"""TTL'd cache for `WhaleAuditReport` keyed on (wallet, activity_max_ts).

Mirrors `position_context_cache.py` shape (read/write/evict over
`agent_state`) but with one important property: the cache key embeds
`activity_max_ts`, so a whale with new fills since the last cached
analysis automatically misses — the cache is self-invalidating on the
operator-meaningful dimension. The TTL is just a fallback for the case
where the LLM model id changes or the prompt is reworded with no new
activity to drive an invalidation.

Namespace isolation (per the plan ratification):

  All entries here live under `agent_state.agent = 'polymarket_whale_analyst'`
  — a NAMESPACE COMPLETELY DISTINCT from the promotion-relevant slots,
  which all live under `agent_state.agent = 'polymarket_copy_trader'`:
    - polymarket_copy_trader.watch_only_whales
    - polymarket_copy_trader.selected_whales
    - polymarket_copy_trader.pinned_whales
    - polymarket_copy_trader.metrics_epoch

  No promotion-side code reads anything under `polymarket_whale_analyst`.
  No watch-list / refresh / dashboard query layer ever queries this
  namespace. The two-segment isolation (different `agent`, prefixed
  `key`) is structural — there's no way for an audit-cache write to
  shadow or collide with promotion state.

  This is the only `agent_state` write surface in the on-demand audit
  pipeline. The CLI / web route never touches any `polymarket_copy_trader`
  slot.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime, timezone
from typing import Any

from trading_corp.data.polymarket_whale_audit import (
    CategoryConcentrationReport,
    ClusteringReport,
    EdgeProfileReport,
    FlaggedDecision,
    RealizedPnLReport,
    SellFootprintReport,
    WhaleAuditReport,
)
from trading_corp.persistence.db import (
    delete_agent_state, load_agent_state, set_agent_state,
)

log = logging.getLogger(__name__)

AGENT_NAMESPACE = "polymarket_whale_analyst"
KEY_PREFIX = "polymarket_whale_audit:"
DEFAULT_TTL_SECONDS = 86400  # 24h fallback


def cache_key(proxy_wallet: str, activity_max_ts: int) -> str:
    """Cache key. Two segments after the prefix so a prefix-eviction
    (if ever needed) could target one wallet's stamps cleanly."""
    return f"{KEY_PREFIX}{proxy_wallet.lower()}:{activity_max_ts}"


def write_audit(report: WhaleAuditReport, *, db_url: str) -> None:
    """Persist a fresh report to the cache. No-op if `db_url` is None
    (caller can use this to disable caching without conditionals)."""
    if db_url is None:
        return
    payload = _report_to_dict(report)
    set_agent_state(
        AGENT_NAMESPACE,
        cache_key(report.proxy_wallet, report.activity_max_ts),
        payload,
        db_url=db_url,
    )


def read_audit(
    proxy_wallet: str,
    activity_max_ts: int,
    *,
    db_url: str,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> WhaleAuditReport | None:
    """Return the cached report if present and within TTL, else None.

    Stale entries are deleted on read (lazy GC, same pattern as
    `position_context_cache`).

    Returns None on any deserialization failure — treat as cache miss
    and let the next analysis overwrite. This is the schema-drift
    safety valve.
    """
    if db_url is None:
        return None
    key = cache_key(proxy_wallet, activity_max_ts)
    try:
        loaded = load_agent_state(AGENT_NAMESPACE, key, db_url=db_url)
    except Exception as e:
        log.warning(
            "whale_audit_cache read failed wallet=%s ts=%s: %s",
            proxy_wallet[:10], activity_max_ts, e,
        )
        return None
    if loaded is None:
        return None
    value, updated_at = loaded
    now_ = now or datetime.now(timezone.utc)
    age_s = (now_ - updated_at).total_seconds()
    if age_s > ttl_seconds:
        try:
            delete_agent_state(AGENT_NAMESPACE, key, db_url=db_url)
        except Exception:
            pass
        return None
    try:
        return _dict_to_report(value)
    except Exception as e:
        log.warning(
            "whale_audit_cache deserialize failed wallet=%s ts=%s: %s "
            "— treating as miss",
            proxy_wallet[:10], activity_max_ts, e,
        )
        return None


def evict_audit(
    proxy_wallet: str,
    activity_max_ts: int,
    *,
    db_url: str,
) -> None:
    """Manual eviction of one specific report. Used by `--force` CLI
    flag and (in the planned Phase B) the dashboard "Re-analyze" link."""
    if db_url is None:
        return
    try:
        delete_agent_state(
            AGENT_NAMESPACE,
            cache_key(proxy_wallet, activity_max_ts),
            db_url=db_url,
        )
    except Exception as e:
        log.warning(
            "whale_audit_cache evict failed wallet=%s ts=%s: %s",
            proxy_wallet[:10], activity_max_ts, e,
        )


# ── (de)serialization ────────────────────────────────────────────────────


# Each sub-report is a frozen dataclass; we serialize via `asdict` and
# rehydrate by name. Tuples become lists in JSON; we convert back on read
# so the rehydrated report is structurally identical to a fresh one.

_TUPLE_FIELDS = {
    # Field paths within the report that are tuples on the dataclass side
    # but lists once round-tripped through JSON. Each entry: (sub-report
    # field name on WhaleAuditReport, field name within the sub-report).
    ("clustering", "top_clusters_by_fill_count"),
    ("sell_footprint", "top_flagged_by_inflation_usdc"),
    ("category", "top_3_event_slugs"),
}


def _report_to_dict(report: WhaleAuditReport) -> dict[str, Any]:
    """Convert frozen dataclass tree to a JSON-safe dict."""
    return asdict(report)


def _dict_to_report(value: Any) -> WhaleAuditReport:
    """Rehydrate. Raises on structural mismatch — caller treats as miss."""
    if not isinstance(value, dict):
        raise TypeError(f"expected dict, got {type(value).__name__}")

    clustering = ClusteringReport(
        **{**value["clustering"],
           "top_clusters_by_fill_count": tuple(
               tuple(t) for t in value["clustering"]["top_clusters_by_fill_count"]
           )},
    )
    flagged = tuple(
        FlaggedDecision(**fd)
        for fd in value["sell_footprint"]["top_flagged_by_inflation_usdc"]
    )
    sell_footprint = SellFootprintReport(
        **{**value["sell_footprint"],
           "top_flagged_by_inflation_usdc": flagged},
    )
    edge = EdgeProfileReport(**value["edge"])
    category = CategoryConcentrationReport(
        **{**value["category"],
           "top_3_event_slugs": tuple(
               tuple(t) for t in value["category"]["top_3_event_slugs"]
           )},
    )
    realized_pnl = RealizedPnLReport(**value["realized_pnl"])

    # Pull the top-level fields that aren't nested sub-reports.
    top_level = {
        k: value[k]
        for k in (
            "proxy_wallet", "user_name", "activity_max_ts", "activity_min_ts",
            "n_raw_rows_examined", "n_resolved_decisions",
            "partial_sell_threshold_used", "verdict_narration",
            "verdict_null_reason", "llm_cost_usd", "llm_tokens_in",
            "llm_tokens_out",
        )
    }
    return WhaleAuditReport(
        **top_level,
        clustering=clustering,
        sell_footprint=sell_footprint,
        edge=edge,
        category=category,
        realized_pnl=realized_pnl,
    )


__all__ = [
    "AGENT_NAMESPACE",
    "KEY_PREFIX",
    "DEFAULT_TTL_SECONDS",
    "cache_key",
    "write_audit",
    "read_audit",
    "evict_audit",
]
