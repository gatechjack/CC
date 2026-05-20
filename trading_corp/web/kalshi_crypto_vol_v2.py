"""Kalshi crypto vol-v2 dashboard data layer.

Owns the post-vol-v2 metric block populated when the kalshi_crypto
division is selected. See runbooks/deploy_log.md 2026-05-20 05:52 UTC
and memory `kalshi-crypto-vol-v2-deployed` for the deploy context.

The vol-v2 / divergence-cap deploy went live at the 2026-05-20 05:52:09
UTC service restart, replacing the hardcoded ANNUAL_VOLS constants with
14d/5m Coinbase realized vol and adding `max_divergence_pct: 35.0` to
the kalshi_crypto_arb strategy.

The accompanying SQL VIEW (`kalshi_crypto_vol_v2_round_trips`) joins
kalshi_round_trips to kalshi_crypto_evaluated audit rows under a ±2s
tolerance window. Min inter-audit gap on the same ticker is 60s
(30x safety margin). Off-by-1s misses were observed under exact-ts
equality before the tolerance was added.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trading_corp.persistence import db


KALSHI_CRYPTO_VOL_V2_CUTOFF = "2026-05-20T05:52:09+00:00"

# Bucket-guard cutoff for the historical reference block. Mirrors the
# existing kalshi_crypto entry in `web/data.DASHBOARD_RT_CUTOFFS` —
# duplicated here so this module is self-contained.
KALSHI_CRYPTO_BUCKET_GUARD_CUTOFF = "2026-05-16T19:37:00+00:00"

KALSHI_CRYPTO_VOL_V2_VIEW_NAME = "kalshi_crypto_vol_v2_round_trips"


def kalshi_crypto_vol_v2_view_ddl() -> str:
    """CREATE VIEW IF NOT EXISTS statement, exposed for migration scripts.

    Tolerance window on the audit-vs-entry-ts join is 2.0 seconds — the
    minimum observed inter-audit gap on the same ticker is 60s, so the
    window won't cross-bind. The cutoff literal is interpolated from
    KALSHI_CRYPTO_VOL_V2_CUTOFF at view-create time; changing the
    constant requires an explicit DROP + CREATE migration.

    The tolerance is expressed as `ev.ts BETWEEN strftime(...)` rather
    than `ABS(julianday(ev.ts) - julianday(krt.entry_ts)) <= 2.0`, so
    the planner can use `ix_audit_event_ts` to seek the 4-second window
    per krt row. The ABS+julianday form is functionally equivalent but
    blocks index use (function on indexed column), turning each query
    against this view into an O(n_krt × n_audit) scan — measured at
    >90s on prod-scale data. The BETWEEN form lands in <30ms.
    """
    return (
        f"CREATE VIEW IF NOT EXISTS {KALSHI_CRYPTO_VOL_V2_VIEW_NAME} AS\n"
        "SELECT\n"
        "  krt.*,\n"
        "  json_extract(ev.payload_json, '$.vol_v2_classification') AS vol_v2_classification,\n"
        "  json_extract(ev.payload_json, '$.hardcoded_av')          AS hardcoded_av,\n"
        "  json_extract(ev.payload_json, '$.hardcoded_prob_yes')    AS hardcoded_prob_yes,\n"
        "  json_extract(ev.payload_json, '$.hardcoded_edge_pct')    AS hardcoded_edge_pct,\n"
        "  CASE\n"
        f"    WHEN krt.entry_ts >= '{KALSHI_CRYPTO_VOL_V2_CUTOFF}'\n"
        "     AND json_extract(ev.payload_json, '$.vol_v2_classification') IS NOT NULL\n"
        "    THEN 'post'\n"
        "    ELSE 'pre'\n"
        "  END AS vol_v2_era\n"
        "FROM kalshi_round_trips krt\n"
        "LEFT JOIN audit_event ev\n"
        "  ON ev.ts BETWEEN strftime('%Y-%m-%dT%H:%M:%S+00:00', krt.entry_ts, '-2 seconds')\n"
        "               AND strftime('%Y-%m-%dT%H:%M:%S+00:00', krt.entry_ts, '+2 seconds')\n"
        " AND ev.kind = 'kalshi_crypto_evaluated'\n"
        " AND json_extract(ev.payload_json, '$.ticker') = krt.ticker\n"
        "WHERE krt.division = 'kalshi_crypto'"
    )


@dataclass
class VolV2SummaryBlock:
    """One of three stacked summary cards (post / lifetime / post-bucket-guard)."""
    label: str
    n: int
    sum_pnl: float
    avg_pnl: float | None    # None when n == 0
    wr_pct: float | None     # None when n == 0


@dataclass
class VolV2ClassificationRow:
    """One row of the per-classification breakdown table."""
    classification: str       # 'same_fire' | 'new_fire' | 'suppressed_fire' | 'both_skip'
    n: int
    wr_pct: float | None
    sum_pnl: float
    avg_pnl: float | None


@dataclass
class PMVolV2Block:
    """All vol-v2 dashboard metrics for kalshi_crypto, computed live.

    Populated only when the dashboard's selected division is
    'kalshi_crypto'. None on every other division and on the All view.
    """
    post: VolV2SummaryBlock
    lifetime: VolV2SummaryBlock
    post_bucket_guard: VolV2SummaryBlock
    classification: list[VolV2ClassificationRow]
    suppressed_fire_per_day: float | None    # None pre-first-fire
    # Post-cutoff RTs by entry_ts that didn't join the view under ±2s
    # — should stay 0; if it creeps up, the join may be silently
    # dropping real v2 trades again.
    strays_count: int
    cutoff_ts: str = KALSHI_CRYPTO_VOL_V2_CUTOFF


def _run_query(db_url: str, sql: str, args: tuple = ()) -> list[dict]:
    """Execute a SQL query and return a list of dict rows.

    Mirrors the `_query` helper pattern in `web/data.py` exactly —
    uses db.connect as a context manager, fetchall, dict-converts rows.
    """
    with db.connect(db_url) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def _summary_block(db_url: str, label: str, sql: str, args: tuple) -> VolV2SummaryBlock:
    """Run a single-row n/sum_pnl/wr_pct query and return a summary block."""
    rows = _run_query(db_url, sql, args)
    if not rows:
        return VolV2SummaryBlock(label=label, n=0, sum_pnl=0.0, avg_pnl=None, wr_pct=None)
    r = rows[0]
    n = int(r.get("n") or 0)
    sum_pnl = float(r.get("sum_pnl") or 0.0)
    if n == 0:
        return VolV2SummaryBlock(label=label, n=0, sum_pnl=0.0, avg_pnl=None, wr_pct=None)
    return VolV2SummaryBlock(
        label=label,
        n=n,
        sum_pnl=sum_pnl,
        avg_pnl=sum_pnl / n,
        wr_pct=float(r.get("wr_pct") or 0.0),
    )


def _query_post(db_url: str) -> VolV2SummaryBlock:
    """Resolved RTs with vol_v2_era='post' (timestamp + classified, ±2s tolerance)."""
    return _summary_block(
        db_url,
        "Post-vol-v2 (live)",
        f"SELECT COUNT(*) AS n, "
        f"       COALESCE(SUM(realized_pnl), 0.0) AS sum_pnl, "
        f"       AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0 END)*100 AS wr_pct "
        f"FROM {KALSHI_CRYPTO_VOL_V2_VIEW_NAME} "
        f"WHERE resolved_ts IS NOT NULL AND vol_v2_era = 'post'",
        (),
    )


def _query_lifetime(db_url: str) -> VolV2SummaryBlock:
    """All-time resolved kalshi_crypto, no cutoff. The lifetime loss baseline."""
    return _summary_block(
        db_url,
        "Lifetime",
        "SELECT COUNT(*) AS n, "
        "       COALESCE(SUM(realized_pnl), 0.0) AS sum_pnl, "
        "       AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0 END)*100 AS wr_pct "
        "FROM kalshi_round_trips "
        "WHERE division = 'kalshi_crypto' AND resolved_ts IS NOT NULL",
        (),
    )


def _query_post_bucket_guard(db_url: str) -> VolV2SummaryBlock:
    """Bucket-guard cutover to vol-v2 cutover. The favorable pre-v2 window."""
    return _summary_block(
        db_url,
        "Post-bucket-guard window (pre-vol-v2)",
        "SELECT COUNT(*) AS n, "
        "       COALESCE(SUM(realized_pnl), 0.0) AS sum_pnl, "
        "       AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0 END)*100 AS wr_pct "
        "FROM kalshi_round_trips "
        "WHERE division = 'kalshi_crypto' AND resolved_ts IS NOT NULL "
        f"  AND entry_ts >= '{KALSHI_CRYPTO_BUCKET_GUARD_CUTOFF}' "
        f"  AND entry_ts <  '{KALSHI_CRYPTO_VOL_V2_CUTOFF}'",
        (),
    )


def _query_classification(db_url: str) -> list[VolV2ClassificationRow]:
    rows = _run_query(
        db_url,
        f"SELECT vol_v2_classification AS classification, "
        f"       COUNT(*) AS n, "
        f"       COALESCE(SUM(realized_pnl), 0.0) AS sum_pnl, "
        f"       AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0 END)*100 AS wr_pct "
        f"FROM {KALSHI_CRYPTO_VOL_V2_VIEW_NAME} "
        f"WHERE resolved_ts IS NOT NULL AND vol_v2_era = 'post' "
        f"GROUP BY vol_v2_classification "
        f"ORDER BY vol_v2_classification",
        (),
    )
    out: list[VolV2ClassificationRow] = []
    for r in rows:
        n = int(r.get("n") or 0)
        sum_pnl = float(r.get("sum_pnl") or 0.0)
        out.append(VolV2ClassificationRow(
            classification=str(r.get("classification") or ""),
            n=n,
            wr_pct=float(r.get("wr_pct") or 0.0) if n > 0 else None,
            sum_pnl=sum_pnl,
            avg_pnl=(sum_pnl / n) if n > 0 else None,
        ))
    return out


def _query_suppressed_fire_per_day(db_url: str) -> float | None:
    """Rate of vol_v2_classification='suppressed_fire' RTs since cutoff,
    extrapolated to per-day. None when zero hours have elapsed."""
    rows = _run_query(
        db_url,
        f"SELECT COUNT(*) AS n, "
        f"       (julianday('now') - julianday(?)) * 24.0 AS hours_elapsed "
        f"FROM {KALSHI_CRYPTO_VOL_V2_VIEW_NAME} "
        f"WHERE vol_v2_era = 'post' AND vol_v2_classification = 'suppressed_fire'",
        (KALSHI_CRYPTO_VOL_V2_CUTOFF,),
    )
    if not rows:
        return None
    r = rows[0]
    n = int(r.get("n") or 0)
    hours = float(r.get("hours_elapsed") or 0.0)
    if hours <= 0:
        return None
    return n * 24.0 / hours


def _query_strays_count(db_url: str) -> int:
    """Resolved kalshi_crypto RTs with entry_ts >= cutoff whose view row
    has vol_v2_era='pre' (audit didn't join under the ±2s tolerance).
    Should stay 0; non-zero is the join-miss smell surfacing."""
    rows = _run_query(
        db_url,
        f"SELECT COUNT(*) AS n FROM {KALSHI_CRYPTO_VOL_V2_VIEW_NAME} "
        f"WHERE resolved_ts IS NOT NULL "
        f"  AND entry_ts >= ? "
        f"  AND vol_v2_era = 'pre'",
        (KALSHI_CRYPTO_VOL_V2_CUTOFF,),
    )
    if not rows:
        return 0
    return int(rows[0].get("n") or 0)


def query_pm_vol_v2_block(db_url: str) -> PMVolV2Block:
    """Build the kalshi_crypto vol-v2 metric block from prod data."""
    return PMVolV2Block(
        post=_query_post(db_url),
        lifetime=_query_lifetime(db_url),
        post_bucket_guard=_query_post_bucket_guard(db_url),
        classification=_query_classification(db_url),
        suppressed_fire_per_day=_query_suppressed_fire_per_day(db_url),
        strays_count=_query_strays_count(db_url),
    )
