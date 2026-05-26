#!/usr/bin/env python3
"""One-shot backfill: scrub secret-bearing JSON fields from the
`raw_body_snippet` of every existing `audit_event` row where
`kind = 'webhook_rejected'`.

C-7 security fix — Phase 2 (historical scrub).
Idempotent: rows already containing ***REDACTED*** are left unchanged
and counted as `rows_already_clean`.

Usage:
    python scripts/scrub_webhook_rejected_secrets.py
    python scripts/scrub_webhook_rejected_secrets.py --db data/trading_corp.db
    python scripts/scrub_webhook_rejected_secrets.py --dry-run
    python scripts/scrub_webhook_rejected_secrets.py --dry-run --verbose
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Mirror of _SECRET_FIELDS + redaction regex in trading_corp/web/webhooks.py
# Duplicated here deliberately: this is a one-shot maintenance script that
# operators may run directly on prod without the full virtualenv / project
# import tree.  Removing the import coupling keeps the script truly
# self-contained and prevents any module-not-found crash from masking the
# scrub.
# ---------------------------------------------------------------------------
_SECRET_FIELDS = ("secret", "webhook_secret", "token")

_SCRUB_PATTERN = re.compile(
    r'"(' + "|".join(_SECRET_FIELDS) + r')"\s*:\s*"[^"]*"',
    flags=re.IGNORECASE,
)


def _scrub_snippet(text: str) -> str:
    """Redact secret-bearing JSON string fields inside a plain text snippet.

    No byte-truncation step: snippets stored in the DB were already capped at
    500 bytes by _scrub_secrets_from_body() at write time.  We only regex-sub.
    """
    return _SCRUB_PATTERN.sub(r'"\1": "***REDACTED***"', text)


def run(
    db_path: str,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    """Perform (or simulate) the scrub.  Returns a counts dict."""
    rows_scanned = 0
    rows_changed = 0
    rows_skipped_bad_json = 0
    rows_skipped_no_snippet = 0
    rows_already_clean = 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, payload_json FROM audit_event WHERE kind = 'webhook_rejected'"
        ).fetchall()

        updates: list[tuple[str, int]] = []  # (new_payload_json, id)

        for row in rows:
            rows_scanned += 1
            row_id: int = row["id"]
            raw_payload: str = row["payload_json"] or ""

            # Parse the outer payload dict
            try:
                payload: dict = json.loads(raw_payload)
            except (json.JSONDecodeError, ValueError):
                rows_skipped_bad_json += 1
                if verbose:
                    print(f"  [SKIP bad-json] id={row_id}")
                continue

            if "raw_body_snippet" not in payload:
                rows_skipped_no_snippet += 1
                if verbose:
                    print(f"  [SKIP no-snippet] id={row_id}")
                continue

            original_snippet: str = payload["raw_body_snippet"]
            scrubbed_snippet: str = _scrub_snippet(original_snippet)

            if scrubbed_snippet == original_snippet:
                rows_already_clean += 1
                if verbose:
                    print(f"  [CLEAN] id={row_id}")
                continue

            # Something changed — prepare the update
            payload["raw_body_snippet"] = scrubbed_snippet
            new_payload_json = json.dumps(payload)
            updates.append((new_payload_json, row_id))
            rows_changed += 1

            if verbose:
                before = original_snippet[:120]
                after = scrubbed_snippet[:120]
                print(f"  [SCRUB] id={row_id}")
                print(f"    before: {before!r}")
                print(f"    after:  {after!r}")

        if not dry_run and updates:
            conn.executemany(
                "UPDATE audit_event SET payload_json = ? WHERE id = ?",
                updates,
            )
            conn.commit()
    finally:
        conn.close()

    return {
        "rows_scanned": rows_scanned,
        "rows_changed": rows_changed,
        "rows_skipped_bad_json": rows_skipped_bad_json,
        "rows_skipped_no_snippet": rows_skipped_no_snippet,
        "rows_already_clean": rows_already_clean,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db",
        default="data/trading_corp.db",
        help="Path to the SQLite database file (default: data/trading_corp.db)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Count what would change without writing anything.",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Print id + before/after snippet for each row that would change.",
    )
    args = p.parse_args(argv)

    counts = run(args.db, dry_run=args.dry_run, verbose=args.verbose)

    prefix = "[DRY-RUN] " if args.dry_run else ""
    print(
        f"{prefix}"
        f"rows_scanned={counts['rows_scanned']} "
        f"rows_changed={counts['rows_changed']} "
        f"rows_skipped_bad_json={counts['rows_skipped_bad_json']} "
        f"rows_skipped_no_snippet={counts['rows_skipped_no_snippet']} "
        f"rows_already_clean={counts['rows_already_clean']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
