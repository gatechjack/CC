"""MACE Division PnL Snapshotter — forward-only daily curve (Part C, Source C).

Snapshot semantics and timing are Q4 knobs pending Jack's ruling
(see reports/mace/PNL_DECISIONS_2026-09-04.md for the open questions).

Recommended defaults implemented:
  - Snapshot time  : 16:15 ET post-close (after manage-loop closes, before
                     after-hours noise).  Knob: snapshot_hour_et / snapshot_min_et.
  - Realized source: cumulative SUM of mace_rung.realized_pnl for ALL closed
                     rungs (gross, as stored).  Q1 not final.
  - Open unrealized: SUM over open/closing rungs of (credit_actual - mark)*100*contracts
                     using the latest mace_rung_live.mark joined on rung_id;
                     None-marks contribute 0 (honest: unavailable mark = 0 MTM).
  - Curve source   : realized_to_date only (Q2 default); open_unrealized is stored
                     separately for optionality.
  - Forward-only   : pre-build history is NOT reconstructed.  The realized-window
                     stats in mace_view (_realized_pnl_windows) DO cover pre-build
                     closed rungs since they sum realized_pnl directly.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone

from trading_corp.utils.time import now_et, now_utc

log = logging.getLogger(__name__)


def compute_snapshot(conn: sqlite3.Connection) -> dict:
    """Compute the current PnL snapshot from the DB (read-only SELECTs).

    Returns a dict with keys:
      snap_date        : today's ET date as YYYY-MM-DD
      realized_to_date : SUM of realized_pnl over ALL status='closed' rungs (float)
      open_unrealized  : SUM over open/closing rungs of (credit_actual - mark)*100*contracts
                         using the latest mace_rung_live.mark joined by rung_id.
                         None-marks contribute 0; if ALL open rungs lack a mark the
                         value may be 0.0 (honest).
      open_rungs       : count of open/closing rungs (int)
      ts               : now_utc() ISO string
    """
    snap_date = now_et().date().isoformat()
    ts = now_utc().isoformat(timespec="seconds")

    # Realized: sum of all closed rung realized_pnl
    realized_to_date = 0.0
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM mace_rung "
            "WHERE status='closed' AND realized_pnl IS NOT NULL"
        ).fetchone()
        if row:
            realized_to_date = float(row[0] or 0.0)
    except Exception:  # noqa: BLE001
        log.warning("pnl_snapshot: realized_to_date query failed", exc_info=True)

    # Open unrealized: join open/closing rungs with their latest marks
    open_rungs = 0
    open_unrealized = 0.0
    try:
        rows = conn.execute(
            "SELECT r.rung_id, r.credit_actual, r.contracts, l.mark "
            "FROM mace_rung r "
            "LEFT JOIN mace_rung_live l ON l.rung_id = r.rung_id "
            "WHERE r.status IN ('open', 'closing')"
        ).fetchall()
        open_rungs = len(rows)
        for row in rows:
            credit = row[1]
            contracts = row[2] or 1
            mark = row[3]
            if credit is not None and mark is not None:
                try:
                    open_unrealized += (float(credit) - float(mark)) * 100.0 * int(contracts)
                except (TypeError, ValueError):
                    pass
            # None mark contributes 0 (honest — unavailable mark = 0 MTM)
    except Exception:  # noqa: BLE001
        log.warning("pnl_snapshot: open_unrealized query failed", exc_info=True)

    return {
        "snap_date": snap_date,
        "realized_to_date": realized_to_date,
        "open_unrealized": open_unrealized,
        "open_rungs": open_rungs,
        "ts": ts,
    }


def write_daily_snapshot(conn: sqlite3.Connection) -> dict:
    """Compute + INSERT OR REPLACE into mace_pnl_snapshot (keyed on snap_date).

    Idempotent re-runs (INSERT OR REPLACE).  Returns the snapshot dict on
    success, or {} on any failure (fail-safe — never raises).
    """
    try:
        snap = compute_snapshot(conn)
        conn.execute(
            "INSERT OR REPLACE INTO mace_pnl_snapshot "
            "(snap_date, realized_to_date, open_unrealized, open_rungs, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                snap["snap_date"],
                snap["realized_to_date"],
                snap["open_unrealized"],
                snap["open_rungs"],
                snap["ts"],
            ),
        )
        conn.commit()
        log.info(
            "pnl_snapshot: wrote snap_date=%s realized=%.2f open_rungs=%d",
            snap["snap_date"],
            snap["realized_to_date"],
            snap["open_rungs"],
        )
        return snap
    except Exception:  # noqa: BLE001
        log.exception("pnl_snapshot: write_daily_snapshot failed (fail-safe)")
        return {}


async def pnl_snapshot_loop(
    store_conn_factory,
    snapshot_hour_et: int = 16,
    snapshot_min_et: int = 15,
    poll_sec: int = 300,
) -> None:
    """Forward-only daily PnL snapshotter loop.

    Snapshot semantics (Q4 knob, pending Jack's ruling):
      - One row/day written at snapshot_hour_et:snapshot_min_et ET (default 16:15).
      - INSERT OR REPLACE on snap_date, so re-runs within the same day are
        idempotent (the last write wins).
      - Pre-build history is NOT reconstructed; the curve starts at deploy time.

    Loop behaviour:
      - Computes the next fire datetime = today's snapshot_hour:snapshot_min ET
        (or tomorrow's if we're already past it today).
      - Sleeps in bounded chunks (<= poll_sec seconds) until that time, re-checking
        on each wake so a clock adjustment or restart is tolerated.
      - On/after fire time for a date not yet snapshotted this session, calls
        write_daily_snapshot and records the last-snapshotted date.
      - Fully fail-safe: any error is logged and the loop sleeps then continues.
      - Never raises.

    Args:
        store_conn_factory : zero-arg callable returning a sqlite3.Connection.
        snapshot_hour_et   : hour (ET) to fire the snapshot (default 16).
        snapshot_min_et    : minute (ET) to fire the snapshot (default 15).
        poll_sec           : max sleep chunk between wake-ups (default 300 = 5 min).
    """
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")

    last_snapped_date: str | None = None

    while True:
        try:
            now = now_et()
            today_fire = now.replace(
                hour=snapshot_hour_et,
                minute=snapshot_min_et,
                second=0,
                microsecond=0,
            )
            if now >= today_fire:
                # Already past today's fire time — target tomorrow
                from datetime import timedelta
                next_fire = today_fire + timedelta(days=1)
            else:
                next_fire = today_fire

            wait_sec = (next_fire - now).total_seconds()
            if wait_sec > 0:
                # Sleep in bounded chunks so we can re-check after a clock adjustment
                chunk = min(float(poll_sec), wait_sec)
                await asyncio.sleep(chunk)
                continue

            # At or past fire time — check if we've already snapped this date
            snap_date = now_et().date().isoformat()
            if last_snapped_date == snap_date:
                # Already snapped today; sleep poll_sec and re-evaluate
                await asyncio.sleep(float(poll_sec))
                continue

            # Fire the snapshot
            try:
                conn = store_conn_factory()
                result = write_daily_snapshot(conn)
                if result:
                    last_snapped_date = result.get("snap_date") or snap_date
                    log.info("pnl_snapshot_loop: snapshot fired for %s", last_snapped_date)
                else:
                    log.warning("pnl_snapshot_loop: write returned empty (fail-safe)")
                    # Don't record last_snapped_date so we retry next poll
            except Exception:  # noqa: BLE001
                log.exception("pnl_snapshot_loop: conn/write failed (fail-safe)")

            await asyncio.sleep(float(poll_sec))

        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("pnl_snapshot_loop: unexpected error (fail-safe, sleeping)")
            await asyncio.sleep(float(poll_sec))
