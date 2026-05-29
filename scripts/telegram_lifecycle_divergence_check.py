"""Telegram lifecycle divergence monitor.

Compares bitunix_futures lifecycle resolutions (paper_trade_record rows
where result IS NOT NULL) against confirmed Telegram close-out deliveries
(telegram_notification_success audit rows with path LIKE 'lifecycle_close_out%').

If resolutions > deliveries, writes a telegram_lifecycle_divergence_detected
audit row so the drift is visible in the dashboard.

Usage:
    py scripts/telegram_lifecycle_divergence_check.py [--db URL] [--hours N]

Default DB: TC_DB_URL env or sqlite:///data/trading_corp.db
Default window: last 24 hours.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from trading_corp.persistence import db as _db  # noqa: E402


def run_check(db_url: str, hours: int = 24) -> dict:
    """Run the divergence check and return a summary dict.

    Side-effect: writes a telegram_lifecycle_divergence_detected audit row
    if divergence > 0.
    """
    window_start = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat(timespec="seconds")

    with _db.connect(db_url) as conn:
        # A: bitunix_futures lifecycle resolutions in the window
        row_a = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM paper_trade_record
            WHERE division = 'bitunix_futures'
              AND result IS NOT NULL
              AND result_ts >= ?
            """,
            (window_start,),
        ).fetchone()
        n_resolutions = row_a["n"] if row_a else 0

        # B: telegram_notification_success rows for close-out paths in the window
        row_b = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM audit_event
            WHERE kind = 'telegram_notification_success'
              AND ts >= ?
              AND json_extract(payload_json, '$.path') LIKE 'lifecycle_close_out%'
            """,
            (window_start,),
        ).fetchone()
        n_success_close_out = row_b["n"] if row_b else 0

    divergence = n_resolutions - n_success_close_out

    summary = {
        "window_hours": hours,
        "window_start": window_start,
        "n_resolutions": n_resolutions,
        "n_success_close_out": n_success_close_out,
        "divergence": divergence,
    }

    if divergence > 0:
        try:
            with _db.connect(db_url) as conn:
                conn.execute(
                    "INSERT INTO audit_event (ts, actor, kind, payload_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "telegram_divergence_monitor",
                        "telegram_lifecycle_divergence_detected",
                        json.dumps(summary, default=str),
                    ),
                )
        except Exception as exc:
            print(f"[WARN] Could not write divergence audit row: {exc}")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Telegram lifecycle divergence monitor"
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("TC_DB_URL", "sqlite:///data/trading_corp.db"),
        help="SQLite DB URL (default: TC_DB_URL env or sqlite:///data/trading_corp.db)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Window in hours to check (default: 24)",
    )
    args = parser.parse_args()

    summary = run_check(db_url=args.db, hours=args.hours)

    divergence = summary["divergence"]
    print(
        f"Window: last {summary['window_hours']}h  |  "
        f"Resolutions: {summary['n_resolutions']}  |  "
        f"Delivered close-outs: {summary['n_success_close_out']}  |  "
        f"Divergence: {divergence}"
    )
    if divergence > 0:
        print(
            f"[ALERT] {divergence} resolution(s) have no confirmed Telegram delivery. "
            f"telegram_lifecycle_divergence_detected audit row written."
        )
    else:
        print("[OK] No divergence detected.")


if __name__ == "__main__":
    main()
