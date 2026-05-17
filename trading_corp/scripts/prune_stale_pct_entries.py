"""Polymarket Copy Trader (PCT) stale-entry pruner.

Background
----------
PCT mirrors whale BUY trades from Polymarket. Apify polls whale wallets
on a ~10-minute cadence; fast whale auto-settles (winners that resolve
in seconds) frequently complete BEFORE our next poll, so we mirror the
BUY but never see the SELL — the result is a `would_have_placed BUY`
audit row with no matching round-trip and no `resolves_at` field on the
payload (so the natural resolver-ordering fix can't help).

These rows accumulate at ~70/day. Without periodic pruning, dashboard
"Open" tile counts grow indefinitely with junk that will never resolve.
A one-shot DELETE on 2026-05-16 03:29 UTC removed 1,745 rows; this
script automates the same predicate as a nightly cron.

Predicate (matches the 2026-05-16 03:29 UTC one-shot exactly)
------------------------------------------------------------
DELETE candidates are audit_event rows where ALL of:
  - actor = 'polymarket_copy_trader'
  - kind  = 'would_have_placed'
  - payload.side = 'buy' (default 'buy' when absent)
  - ts < now() - cutoff_hours        (default 24)
  - payload.order_id NOT IN polymarket_round_trips.order_id
  - payload.order_id NOT IN polymarket_round_trips.entry_order_id

Sell-side rows are preserved (they're exits — never the source of stale
BUYs). Rows tied to ANY round-trip via either column are preserved
(they ARE resolved, just maybe via the paired column).

Safety
------
* `--dry-run` is the DEFAULT. `--apply` is required to actually DELETE.
* `--max-rows` caps the single-run delete count (default 5000) so a
  bug in the predicate can't take the whole table.
* Writes a `pct_stale_prune` audit_event for every run (dry-run too)
  with the candidate count and applied flag, so the cron's behavior
  is itself auditable.

Usage
-----
    python -m trading_corp.scripts.prune_stale_pct_entries \
        --apply --cutoff-hours 24 --max-rows 5000

    # Dry-run preview (default):
    python -m trading_corp.scripts.prune_stale_pct_entries
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from trading_corp.persistence import db as _db

log = logging.getLogger(__name__)

DEFAULT_CUTOFF_HOURS = 24
DEFAULT_MAX_ROWS = 5000
ACTOR = "polymarket_copy_trader"
KIND = "would_have_placed"
AUDIT_KIND = "pct_stale_prune"

# Inline-SQL safe: cutoff_hours and max_rows are bound parameters; the
# only constants in the SQL itself are hardcoded actor/kind strings.
# Indented LIMIT works on this SQLite version (3.46+) — the test suite
# exercises it.
_PREDICATE_WHERE = """
    WHERE actor = ?
      AND kind  = ?
      AND COALESCE(json_extract(payload_json, '$.side'), 'buy') = 'buy'
      AND ts < datetime('now', ?)
      AND COALESCE(json_extract(payload_json, '$.order_id'), '') NOT IN
          (SELECT order_id FROM polymarket_round_trips
            WHERE order_id IS NOT NULL)
      AND COALESCE(json_extract(payload_json, '$.order_id'), '') NOT IN
          (SELECT entry_order_id FROM polymarket_round_trips
            WHERE entry_order_id IS NOT NULL)
"""


def _count_candidates(conn, cutoff_modifier: str) -> int:
    sql = "SELECT COUNT(*) AS n FROM audit_event" + _PREDICATE_WHERE
    row = conn.execute(sql, (ACTOR, KIND, cutoff_modifier)).fetchone()
    return int(row["n"]) if row else 0


def _delete_candidates(conn, cutoff_modifier: str, max_rows: int) -> int:
    """Delete up to max_rows matching rows. Returns rows affected.

    Uses a subquery on rowid so the LIMIT is enforceable inside DELETE
    (SQLite's DELETE doesn't accept LIMIT directly without a build flag).
    """
    sql = (
        "DELETE FROM audit_event WHERE rowid IN ("
        "  SELECT rowid FROM audit_event"
        + _PREDICATE_WHERE
        + "  LIMIT ?"
        + ")"
    )
    cur = conn.execute(sql, (ACTOR, KIND, cutoff_modifier, max_rows))
    return cur.rowcount or 0


def _write_audit(
    conn,
    *,
    n_candidates: int,
    n_deleted: int,
    apply: bool,
    cutoff_hours: int,
    max_rows: int,
) -> None:
    payload = {
        "actor_pruned": ACTOR,
        "kind_pruned": KIND,
        "cutoff_hours": cutoff_hours,
        "max_rows": max_rows,
        "candidates": n_candidates,
        "deleted": n_deleted,
        "apply": apply,
        "dry_run": not apply,
        # Tag on division so the dashboard activity-rail can pick this up
        # via the existing per-division match — see CLAUDE.md §1.
        "division": "polymarket_copy_trading",
        "strategy": "pct_stale_pruner",
    }
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO audit_event (ts, actor, kind, payload_json) "
        "VALUES (?, ?, ?, ?)",
        (ts, "pct_stale_pruner", AUDIT_KIND, json.dumps(payload)),
    )


def prune(
    *,
    db_url: str,
    apply: bool = False,
    cutoff_hours: int = DEFAULT_CUTOFF_HOURS,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> dict[str, Any]:
    """Prune stale PCT pending audit rows.

    Returns a dict with `candidates`, `deleted`, `apply`, `cutoff_hours`.
    """
    if cutoff_hours < 1:
        raise ValueError("cutoff_hours must be >= 1")
    if max_rows < 1:
        raise ValueError("max_rows must be >= 1")

    cutoff_modifier = f"-{int(cutoff_hours)} hours"

    with _db.connect(db_url) as conn:
        n_candidates = _count_candidates(conn, cutoff_modifier)
        n_deleted = 0
        if apply and n_candidates > 0:
            n_deleted = _delete_candidates(conn, cutoff_modifier, max_rows)
        _write_audit(
            conn,
            n_candidates=n_candidates,
            n_deleted=n_deleted,
            apply=apply,
            cutoff_hours=cutoff_hours,
            max_rows=max_rows,
        )

    return {
        "candidates": n_candidates,
        "deleted": n_deleted,
        "apply": apply,
        "cutoff_hours": cutoff_hours,
        "max_rows": max_rows,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="prune_stale_pct_entries",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--apply", action="store_true",
        help="Actually DELETE matching rows. Default is dry-run (count-only).",
    )
    p.add_argument(
        "--cutoff-hours", type=int, default=DEFAULT_CUTOFF_HOURS,
        help=f"Rows older than this are candidates (default {DEFAULT_CUTOFF_HOURS}).",
    )
    p.add_argument(
        "--max-rows", type=int, default=DEFAULT_MAX_ROWS,
        help=f"Cap delete count per run (default {DEFAULT_MAX_ROWS}).",
    )
    p.add_argument(
        "--db-url", default=None,
        help="SQLite DB URL. Defaults to secrets.db_url (KV-loaded).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)

    db_url = args.db_url
    if db_url is None:
        # Lazy import so unit tests can pass --db-url without touching KV.
        from trading_corp.utils.secrets import load_secrets
        secrets = load_secrets()
        db_url = secrets.db_url

    result = prune(
        db_url=db_url,
        apply=args.apply,
        cutoff_hours=args.cutoff_hours,
        max_rows=args.max_rows,
    )

    mode = "APPLY" if args.apply else "DRY-RUN"
    log.info(
        "[%s] candidates=%d deleted=%d cutoff_hours=%d max_rows=%d",
        mode, result["candidates"], result["deleted"],
        result["cutoff_hours"], result["max_rows"],
    )
    return 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
