#!/usr/bin/env python3
"""One-shot recovery: replay rows from the file-based audit fallback into audit_event.

When LoggerAgent.log_event() exhausts all DB-lock retries it appends a JSON line
to `data/audit_event_write_failed.jsonl` (resolved relative to the DB file).
Run this script to drain that file back into the database.

Idempotency: a row is skipped if audit_event already has an entry with the
same (ts, actor, kind, payload_json).  Hash is a content comparison — since
audit_event has no natural unique key, this guards against double-replay.

Drain semantics: after all lines in the JSONL are processed (whether inserted
or skipped as duplicates), the file is renamed to
`audit_event_write_failed.jsonl.replayed-<iso_ts>` so the history is preserved
but the live fallback slot is clear.  If the file is partially processed (e.g.
an exception mid-run), non-replayed lines remain in the original file so a
re-run picks them up.

Usage:
    python scripts/replay_audit_event_write_failed.py
    python scripts/replay_audit_event_write_failed.py --db sqlite:///data/trading_corp.db
    python scripts/replay_audit_event_write_failed.py --dry-run
    python scripts/replay_audit_event_write_failed.py --file /path/to/custom.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trading_corp.persistence import db as _db


def _fallback_path(db_url: str) -> Path:
    return _db.resolve_db_path(db_url).parent / "audit_event_write_failed.jsonl"


def _row_exists(conn, ts: str, actor: str, kind: str, payload_json: str) -> bool:
    """Return True if audit_event already has this (ts, actor, kind, payload_json)."""
    row = conn.execute(
        "SELECT id FROM audit_event "
        "WHERE ts = ? AND actor = ? AND kind = ? AND payload_json = ? "
        "LIMIT 1",
        (ts, actor, kind, payload_json),
    ).fetchone()
    return row is not None


def replay(
    db_url: str = "sqlite:///data/trading_corp.db",
    file_path: Path | None = None,
    *,
    dry_run: bool = False,
) -> dict:
    """Drain the fallback JSONL into audit_event.

    Returns:
        dict with keys: scanned, inserted, skipped_existing
    """
    _db.init_db(db_url)

    if file_path is None:
        file_path = _fallback_path(db_url)

    if not file_path.exists():
        print(f"Fallback file not found: {file_path} — nothing to replay.")
        return {"scanned": 0, "inserted": 0, "skipped_existing": 0}

    lines = file_path.read_text(encoding="utf-8").splitlines()
    parsed: list[tuple[int, dict]] = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"  WARN: line {i + 1} is not valid JSON ({exc}); skipping.")
            continue
        parsed.append((i, entry))

    scanned = len(parsed)
    inserted = 0
    skipped_existing = 0

    # Indices of successfully-handled lines (inserted or confirmed-duplicate).
    # We only drain them if all succeed — partial failure leaves the file intact
    # for a re-run.
    replayed_indices: list[int] = []

    with _db.connect(db_url) as conn:
        for line_idx, entry in parsed:
            ts = entry.get("ts", "")
            actor = entry.get("actor", "")
            kind = entry.get("kind", "")
            payload = entry.get("payload", {})
            payload_json = json.dumps(payload, separators=(",", ":"), default=str)

            if _row_exists(conn, ts, actor, kind, payload_json):
                skipped_existing += 1
                replayed_indices.append(line_idx)
                continue

            if dry_run:
                # Count as "would insert" but don't touch the DB.
                replayed_indices.append(line_idx)
                continue

            conn.execute(
                "INSERT INTO audit_event(ts, actor, kind, payload_json) VALUES(?,?,?,?)",
                (ts, actor, kind, payload_json),
            )
            inserted += 1
            replayed_indices.append(line_idx)

    # Determine which original lines were NOT replayed
    all_nonempty_indices = {i for i, _ in parsed}
    not_replayed = all_nonempty_indices - set(replayed_indices)

    if not dry_run:
        if not not_replayed:
            # All lines handled — rename the file so the fallback slot is clear.
            ts_suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            archive_path = file_path.with_suffix(f".jsonl.replayed-{ts_suffix}")
            file_path.rename(archive_path)
        else:
            # Partial success — rewrite the file with only the un-replayed lines.
            remaining_lines = [lines[i] for i in sorted(not_replayed)]
            file_path.write_text("\n".join(remaining_lines) + "\n", encoding="utf-8")

    return {"scanned": scanned, "inserted": inserted, "skipped_existing": skipped_existing}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="sqlite:///data/trading_corp.db",
                   help="DB URL (default: sqlite:///data/trading_corp.db)")
    p.add_argument("--file", default=None,
                   help="Path to the JSONL fallback file (default: derived from --db)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be inserted without writing")
    args = p.parse_args(argv)

    file_path = Path(args.file) if args.file else None
    counts = replay(args.db, file_path, dry_run=args.dry_run)
    mode = "DRY-RUN" if args.dry_run else "WROTE"
    print(
        f"{mode}: scanned={counts['scanned']} inserted={counts['inserted']} "
        f"skipped_existing={counts['skipped_existing']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
