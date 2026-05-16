"""One-shot prod patcher: fix dashboard tiles+tab labels showing LIMIT
values instead of true counts. Idempotent.

Three anchored edits in `web/data.py`:
  1. Insert `_query_pm_resolved_stats` after `_query_pm_pending_count`.
  2. Replace `_pm_summary` signature/body to accept `resolved_stats` kwarg.
  3. Replace `build_pm_dashboard`'s asyncio.gather + summary call.

Three anchored edits in `web/templates/partials/pm_dashboard_body.html`:
  4. Open tab label uses summary.n_pending instead of `open_trades | length`.
  5. History tab label uses summary.n_resolved instead of `round_trips | length`.
  6. Open-tab header + History "All" filter use summary counts.
"""
from __future__ import annotations

import pathlib
import sys

DATA_PY = pathlib.Path("/home/azureuser/trading_corp/trading_corp/web/data.py")
TPL = pathlib.Path(
    "/home/azureuser/trading_corp/trading_corp/web/templates/partials/pm_dashboard_body.html"
)


# ── data.py edit 1 — insert _query_pm_resolved_stats ──────────────────

# Anchor on the END of _query_pm_pending_count's return statement and the
# definition of _pm_equity_at. We insert the new function between them.
DATA_OLD_INSERT = """    total += int(rows[0].get("n") or 0)

    return total


def _pm_equity_at(curve: list[PMEquityPoint], at_or_before: datetime) -> float | None:"""

DATA_NEW_INSERT = """    total += int(rows[0].get("n") or 0)

    return total


def _query_pm_resolved_stats(
    db_url: str, division_slugs: list[str],
) -> dict:
    \"\"\"Aggregate stats over ALL resolved round-trips (no LIMIT), cross-venue.

    Returns {n_resolved, n_wins, n_voids, total_realized_pnl}. Used by the
    dashboard summary tiles so they reflect true totals rather than the
    capped list size — `_query_pm_round_trips` truncates at history_limit,
    so `len(round_trips)` was silently capped at 100 (or whichever limit).
    \"\"\"
    out = {"n_resolved": 0, "n_wins": 0, "n_voids": 0, "total_realized_pnl": 0.0}
    if not division_slugs:
        return out

    poly_slugs = [s for s in division_slugs if s.startswith(_POLYMARKET_PREFIX)]
    if poly_slugs:
        poly_ph = ",".join("?" for _ in poly_slugs)
        # polymarket_round_trips has no `market_result` column — voids
        # would surface via extra_json if at all (rare on polymarket).
        # Approximate void as realized_pnl=0 AND won=0, which matches the
        # PMRoundTrip-derivation rule used in `_query_pm_round_trips`.
        rows = _query(
            db_url,
            f"SELECT COUNT(*) AS n_resolved, "
            f"       COALESCE(SUM(won), 0) AS n_wins, "
            f"       COALESCE(SUM(CASE WHEN won = 0 AND realized_pnl = 0.0 THEN 1 ELSE 0 END), 0) AS n_voids, "
            f"       COALESCE(SUM(realized_pnl), 0.0) AS total_pnl "
            f"FROM polymarket_round_trips "
            f"WHERE COALESCE(division, 'polymarket_arbitrage') IN ({poly_ph})",
            tuple(poly_slugs),
        )
        if rows:
            out["n_resolved"] += int(rows[0].get("n_resolved") or 0)
            out["n_wins"] += int(rows[0].get("n_wins") or 0)
            out["n_voids"] += int(rows[0].get("n_voids") or 0)
            out["total_realized_pnl"] += float(rows[0].get("total_pnl") or 0.0)

    kalshi_slugs = [s for s in division_slugs if s.startswith(_KALSHI_PREFIX)]
    if kalshi_slugs:
        kalshi_ph = ",".join("?" for _ in kalshi_slugs)
        rows = _query(
            db_url,
            f"SELECT COUNT(*) AS n_resolved, "
            f"       COALESCE(SUM(won), 0) AS n_wins, "
            f"       COALESCE(SUM(CASE WHEN COALESCE(market_result,'') = 'void' THEN 1 ELSE 0 END), 0) AS n_voids, "
            f"       COALESCE(SUM(realized_pnl), 0.0) AS total_pnl "
            f"FROM kalshi_round_trips "
            f"WHERE division IN ({kalshi_ph})",
            tuple(kalshi_slugs),
        )
        if rows:
            out["n_resolved"] += int(rows[0].get("n_resolved") or 0)
            out["n_wins"] += int(rows[0].get("n_wins") or 0)
            out["n_voids"] += int(rows[0].get("n_voids") or 0)
            out["total_realized_pnl"] += float(rows[0].get("total_pnl") or 0.0)

    return out


def _pm_equity_at(curve: list[PMEquityPoint], at_or_before: datetime) -> float | None:"""


# ── data.py edit 2 — _pm_summary signature + body ─────────────────────

DATA_OLD_SUMMARY = """def _pm_summary(
    round_trips: list[PMRoundTrip],
    equity_curve: list[PMEquityPoint],
    pending_count: int,
) -> PMSummary:
    \"\"\"Compute the summary cards. Returns zeros/Nones cleanly when there's
    no data so the template doesn't have to guard.\"\"\"
    n_wins = sum(1 for rt in round_trips if rt.won == 1)
    n_resolved = len(round_trips)
    n_voids = sum(1 for rt in round_trips if rt.market_result == "void")
    n_losses = n_resolved - n_wins - n_voids
    decisive = n_wins + n_losses
    win_rate = (100.0 * n_wins / decisive) if decisive > 0 else None
    total_pnl = sum(rt.realized_pnl for rt in round_trips)"""

DATA_NEW_SUMMARY = """def _pm_summary(
    round_trips: list[PMRoundTrip],
    equity_curve: list[PMEquityPoint],
    pending_count: int,
    resolved_stats: dict | None = None,
) -> PMSummary:
    \"\"\"Compute the summary cards. Returns zeros/Nones cleanly when there's
    no data so the template doesn't have to guard.

    `resolved_stats` is the output of `_query_pm_resolved_stats` (true
    aggregates over ALL resolved round-trips, no LIMIT). When provided,
    n_resolved / n_wins / n_voids / total_realized_pnl come from there.
    When None (legacy callers), fall back to counting from the round_trips
    list — but be aware the list is truncated by `history_limit` and
    callers that care about correct tile counts MUST pass resolved_stats.
    \"\"\"
    if resolved_stats is not None:
        n_resolved = int(resolved_stats.get("n_resolved", 0))
        n_wins = int(resolved_stats.get("n_wins", 0))
        n_voids = int(resolved_stats.get("n_voids", 0))
        total_pnl = float(resolved_stats.get("total_realized_pnl", 0.0))
    else:
        n_wins = sum(1 for rt in round_trips if rt.won == 1)
        n_resolved = len(round_trips)
        n_voids = sum(1 for rt in round_trips if rt.market_result == "void")
        total_pnl = sum(rt.realized_pnl for rt in round_trips)
    n_losses = n_resolved - n_wins - n_voids
    decisive = n_wins + n_losses
    win_rate = (100.0 * n_wins / decisive) if decisive > 0 else None"""


# ── data.py edit 3 — build_pm_dashboard asyncio.gather + summary call ─

DATA_OLD_GATHER = """    round_trips, equity_curve, open_trades, whales, kalshi_watch_only = await asyncio.gather(
        asyncio.to_thread(_query_pm_round_trips, db_url, target_slugs, history_limit),
        asyncio.to_thread(_query_pm_equity_curve, db_url, target_slugs, equity_curve_days),
        asyncio.to_thread(_query_pm_open_trades, db_url, target_slugs, 200),
        asyncio.to_thread(_query_pm_whales, db_url, target_slugs),
        asyncio.to_thread(_query_kalshi_watch_only_rows, db_url, target_slugs),
    )

    # Pending count = len(open_trades). One source of truth — no separate
    # count query that could go out of sync with the list.
    summary = _pm_summary(round_trips, equity_curve, len(open_trades))"""

DATA_NEW_GATHER = """    round_trips, equity_curve, open_trades, whales, kalshi_watch_only, pending_count, resolved_stats = await asyncio.gather(
        asyncio.to_thread(_query_pm_round_trips, db_url, target_slugs, history_limit),
        asyncio.to_thread(_query_pm_equity_curve, db_url, target_slugs, equity_curve_days),
        asyncio.to_thread(_query_pm_open_trades, db_url, target_slugs, 200),
        asyncio.to_thread(_query_pm_whales, db_url, target_slugs),
        asyncio.to_thread(_query_kalshi_watch_only_rows, db_url, target_slugs),
        asyncio.to_thread(_query_pm_pending_count, db_url, target_slugs),
        asyncio.to_thread(_query_pm_resolved_stats, db_url, target_slugs),
    )

    # Tiles must show TRUE totals, not list lengths. open_trades/round_trips
    # are LIMIT-capped (200/history_limit); deriving n_pending/n_resolved
    # from `len()` silently truncated the tiles at the limit values.
    summary = _pm_summary(round_trips, equity_curve, pending_count, resolved_stats)"""


# ── template edits ────────────────────────────────────────────────────

TPL_OLD_TABS = """    Open ({{ view.open_trades | length }})
  </button>
  <button data-pm-tab=\"history\"
          class=\"pm-tab-btn px-4 py-2 text-sm font-mono uppercase tracking-wider
                 border-b-2 border-transparent text-muted hover:text-mono\">
    History ({{ view.round_trips | length }})
  </button>"""

TPL_NEW_TABS = """    Open ({{ view.summary.n_pending }})
  </button>
  <button data-pm-tab=\"history\"
          class=\"pm-tab-btn px-4 py-2 text-sm font-mono uppercase tracking-wider
                 border-b-2 border-transparent text-muted hover:text-mono\">
    History ({{ view.summary.n_resolved }})
  </button>"""

TPL_OLD_OPEN_HDR = """        {{ view.open_trades | length }} awaiting market settle · click row to expand"""

TPL_NEW_OPEN_HDR = """        {% if view.summary.n_pending > (view.open_trades | length) -%}
          showing {{ view.open_trades | length }} of {{ view.summary.n_pending }} awaiting market settle · click row to expand
        {%- else -%}
          {{ view.summary.n_pending }} awaiting market settle · click row to expand
        {%- endif %}"""

TPL_OLD_ALL_FILTER = """                data-filter=\"all\">All ({{ view.round_trips | length }})</button>"""

TPL_NEW_ALL_FILTER = """                data-filter=\"all\">All ({{ view.summary.n_resolved }})</button>"""


def _apply(path: pathlib.Path, old: str, new: str, label: str) -> bool:
    """Idempotent anchored replace. Returns True on patch applied."""
    src = path.read_text(encoding="utf-8")
    if new in src and old not in src:
        print(f"  [{label}] already patched — skip")
        return True
    if old not in src:
        print(f"  [{label}] ANCHOR NOT FOUND — abort", file=sys.stderr)
        return False
    path.write_text(src.replace(old, new, 1), encoding="utf-8")
    print(f"  [{label}] patched")
    return True


def main() -> int:
    print(f"== patching {DATA_PY} ==")
    ok = True
    ok &= _apply(DATA_PY, DATA_OLD_INSERT, DATA_NEW_INSERT, "insert _query_pm_resolved_stats")
    ok &= _apply(DATA_PY, DATA_OLD_SUMMARY, DATA_NEW_SUMMARY, "_pm_summary signature/body")
    ok &= _apply(DATA_PY, DATA_OLD_GATHER, DATA_NEW_GATHER, "build_pm_dashboard gather+summary")
    print(f"== patching {TPL} ==")
    ok &= _apply(TPL, TPL_OLD_TABS, TPL_NEW_TABS, "tab labels Open/History")
    ok &= _apply(TPL, TPL_OLD_OPEN_HDR, TPL_NEW_OPEN_HDR, "Open tab header")
    ok &= _apply(TPL, TPL_OLD_ALL_FILTER, TPL_NEW_ALL_FILTER, "History 'All' filter button")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
