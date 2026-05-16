"""Backfill `bitunix_signal_ledger.tf` for rows older than PR 3c (PR 5d).

PR 3c added a `tf` column to the ledger. Pre-existing rows have
`tf=NULL`; the new `score_timeframes` filter would drop them all if it
were applied retroactively. This script reconstructs `tf` from the
matching `webhook_received` audit row's payload (which has
`payload['interval']`) and UPDATEs in place.

Matching strategy:
  - For each ledger row with tf IS NULL, find the nearest-by-ts
    `audit_event` row with kind='webhook_received' and payload.signal
    matching the ledger's signal (case-insensitive).
  - Accept matches within ±MATCH_WINDOW_SECONDS (default 5s) of the
    ledger row's ts. The webhook handler writes the audit BEFORE the
    ledger insert so most matches will be within ~100ms.
  - Extract `payload['interval']` and normalize via
    `_normalize_tf` (the same helper the live observer uses).

Idempotent: run as many times as you want. Already-set `tf` values
are skipped; only NULL rows get touched.

Usage:
    py -m scripts.backfill_signal_ledger_tf \\
        --db-url sqlite:///data/trading_corp.db
    py -m scripts.backfill_signal_ledger_tf --dry-run    # preview only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from trading_corp.agents.divisions.bitunix_futures_observer import (    # noqa: E402
    _normalize_tf,
)
from trading_corp.persistence import db                                  # noqa: E402

MATCH_WINDOW_SECONDS = 5


def _parse_ts(s: str) -> datetime | None:
    """Parse the ISO-format ts strings used in the audit + ledger
    tables. Both ledger.ts and audit_event.ts use the same convention
    (UTC ISO-8601 with seconds precision)."""
    if not s:
        return None
    try:
        # SQLite may have stripped the timezone; treat naive as UTC.
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def backfill(db_url: str, *, dry_run: bool = False) -> dict:
    """Run the backfill. Returns counts dict for reporting."""
    counts = Counter()

    # 1. Pull all ledger rows with NULL tf, oldest first.
    with db.connect(db_url) as conn:
        ledger_rows = conn.execute(
            "SELECT rowid, ts, signal FROM bitunix_signal_ledger "
            "WHERE tf IS NULL ORDER BY ts"
        ).fetchall()
    counts["ledger_null_rows"] = len(ledger_rows)
    if not ledger_rows:
        return dict(counts)

    # 2. Pull every webhook_received audit row in the relevant time
    #    window — index by signal_name → list of (ts, interval).
    earliest = _parse_ts(ledger_rows[0]["ts"])
    latest = _parse_ts(ledger_rows[-1]["ts"])
    if earliest is None or latest is None:
        counts["bad_ts_in_ledger"] = len(ledger_rows)
        return dict(counts)
    pad = timedelta(seconds=MATCH_WINDOW_SECONDS)
    window_start = (earliest - pad).isoformat()
    window_end = (latest + pad).isoformat()

    with db.connect(db_url) as conn:
        audit_rows = conn.execute(
            "SELECT ts, payload_json FROM audit_event "
            "WHERE kind='webhook_received' AND ts >= ? AND ts <= ? "
            "ORDER BY ts",
            (window_start, window_end),
        ).fetchall()

    # signal_name (lowercased) → list of (datetime, interval_raw)
    by_signal: dict[str, list[tuple[datetime, object]]] = {}
    for r in audit_rows:
        try:
            payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
        except Exception:
            continue
        sig = (payload.get("signal") or "").strip().lower()
        if not sig:
            continue
        ts = _parse_ts(r["ts"])
        if ts is None:
            continue
        by_signal.setdefault(sig, []).append((ts, payload.get("interval")))
    counts["audit_rows_indexed"] = sum(len(v) for v in by_signal.values())

    # 3. For each NULL-tf ledger row, find nearest match within window.
    updates: list[tuple[str, int]] = []     # (tf, rowid)
    for r in ledger_rows:
        sig = (r["signal"] or "").strip().lower()
        ledger_ts = _parse_ts(r["ts"])
        if ledger_ts is None or sig not in by_signal:
            counts["unmatched"] += 1
            continue
        # Linear scan — typical signal has <100 audit rows in window.
        best: tuple[datetime, object] | None = None
        best_delta = pad + pad  # something larger than the window
        for (ats, ival) in by_signal[sig]:
            delta = abs(ats - ledger_ts)
            if delta < best_delta:
                best_delta = delta
                best = (ats, ival)
        if best is None or best_delta > pad:
            counts["unmatched"] += 1
            continue
        tf = _normalize_tf(best[1])
        if tf is None:
            counts["matched_but_no_interval"] += 1
            continue
        updates.append((tf, r["rowid"]))
        counts[f"matched_tf_{tf}"] += 1

    counts["matched_total"] = len(updates)

    # 4. UPDATE in batches.
    if updates and not dry_run:
        with db.connect(db_url) as conn:
            conn.executemany(
                "UPDATE bitunix_signal_ledger SET tf = ? WHERE rowid = ?",
                updates,
            )
        counts["updated"] = len(updates)
    elif updates and dry_run:
        counts["would_update"] = len(updates)

    return dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-url",
        default=os.environ.get("TC_DB_URL", "sqlite:///data/trading_corp.db"),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be updated, but don't write.",
    )
    args = parser.parse_args()

    print(f"DB: {args.db_url}")
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'WRITE'}")
    print(f"Match window: ±{MATCH_WINDOW_SECONDS}s")
    print()

    counts = backfill(args.db_url, dry_run=args.dry_run)
    print(json.dumps(counts, indent=2, sort_keys=True))

    if counts.get("ledger_null_rows", 0) == 0:
        print("\nNo NULL-tf rows. Nothing to do.")
        return 0
    matched = counts.get("matched_total", 0)
    null_rows = counts.get("ledger_null_rows", 0)
    print(
        f"\nMatched {matched}/{null_rows} "
        f"({100*matched/max(null_rows,1):.1f}%) of NULL-tf rows."
    )
    if counts.get("unmatched", 0) > 0:
        print(
            f"  {counts['unmatched']} ledger rows had no matching "
            f"webhook_received audit within ±{MATCH_WINDOW_SECONDS}s — "
            "leaving as NULL. (Likely from before audit_event was "
            "written, or a webhook fired without the audit landing.)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
