"""NBM probabilistic bulletin ingester → trading_corp.db.

Pulls the latest NOMADS NBP cycle (or one named via --cycle), parses
per-station decile + stdev forecasts for every verified bet-on station
in `config/weather_stations.yaml`, and writes rows to
`weather_nbm_observations`.

Build-now-safe: write-only ingestion. No live decision-path read.

First-run mandate (per the Tier 1 plan):
  - Verify each of the verified-series airport ICAOs from the registry
    appears as a separate per-station block in the parsed bulk file.
  - Any missing ICAO → log `nbm_block_missing`, halt for Board review.
    Do NOT silently substitute WFO-office data.

Usage:
    python scripts/ingest_nbm.py                  # latest cycle, all 19 ICAOs, real DB
    python scripts/ingest_nbm.py --dry-run        # no DB writes; print summary
    python scripts/ingest_nbm.py --cycle 2026-05-25T13:00:00+00:00
    python scripts/ingest_nbm.py --db PATH

Invocation note: project Python imports require the windows job-object
wrapper. Use:
    .\\scripts\\run_capped.ps1 python scripts\\ingest_nbm.py [...]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trading_corp.data.nbm_client import (  # noqa: E402
    NBMForecastClient,
    NBMObservation,
    cycle_url,
    latest_cycle_dt,
)
from trading_corp.data.weather_stations import get_registry  # noqa: E402
from trading_corp.persistence.db import init_db, resolve_db_path  # noqa: E402

DEFAULT_DB_URL = "sqlite:///data/trading_corp.db"
log = logging.getLogger("ingest_nbm")


def _parse_cycle_arg(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _target_icaos() -> list[str]:
    """All verified, enabled, bet-on station ICAOs from the registry."""
    reg = get_registry(REPO_ROOT / "config" / "weather_stations.yaml")
    series_rows = reg.list_verified_series()
    icaos = sorted({station.icao for _prefix, _series, station in series_rows})
    return icaos


def _write_observations(
    db_path: Path,
    observations: list[NBMObservation],
    nbm_source: str = "nomads_bulk",
    icao_source: str = "registry_yaml",
    ingest_mode: str = "live_cron",
) -> int:
    """Idempotent UPSERT into weather_nbm_observations. Returns row count written."""
    if not observations:
        return 0
    ingested_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    sql = """
        INSERT INTO weather_nbm_observations (
            station_id, cycle_iso, valid_iso, kind, horizon_hours,
            temp_p10_f, temp_p20_f, temp_p50_f, temp_p70_f, temp_p90_f,
            temp_sigma_f, temp_mean_f,
            nbm_source, icao_source, ingest_mode, ingested_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT (station_id, cycle_iso, valid_iso, kind) DO UPDATE SET
            horizon_hours = excluded.horizon_hours,
            temp_p10_f = excluded.temp_p10_f,
            temp_p20_f = excluded.temp_p20_f,
            temp_p50_f = excluded.temp_p50_f,
            temp_p70_f = excluded.temp_p70_f,
            temp_p90_f = excluded.temp_p90_f,
            temp_sigma_f = excluded.temp_sigma_f,
            temp_mean_f = excluded.temp_mean_f,
            nbm_source = excluded.nbm_source,
            icao_source = excluded.icao_source,
            ingest_mode = excluded.ingest_mode,
            ingested_at = excluded.ingested_at
    """
    rows = [
        (
            o.station_id, o.cycle_iso, o.valid_iso, o.kind, o.horizon_hours,
            o.temp_p10_f, o.temp_p20_f, o.temp_p50_f, o.temp_p70_f, o.temp_p90_f,
            o.temp_sigma_f, o.temp_mean_f,
            nbm_source, icao_source, ingest_mode, ingested_at,
        )
        for o in observations
    ]
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(sql, rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


async def _run(
    cycle_dt: datetime,
    db_path: Path,
    dry_run: bool,
    halt_on_missing: bool,
) -> int:
    target = _target_icaos()
    log.info(
        "NBM ingest: cycle=%s targets=%d stations: %s",
        cycle_dt.isoformat(), len(target), ",".join(target),
    )
    log.info("NBM URL: %s", cycle_url(cycle_dt))

    client = NBMForecastClient()
    try:
        parsed = await client.fetch_cycle(cycle_dt, set(target))
    finally:
        await client.close()

    if parsed is None:
        log.error(
            "NBM ingest: fetch/parse FAILED for cycle %s; no rows written",
            cycle_dt.isoformat(),
        )
        return 2

    found = sorted(parsed.keys())
    missing = sorted(set(target) - set(found))

    log.info("NBM ingest: blocks found  (%d/%d): %s", len(found), len(target), ",".join(found))
    if missing:
        log.warning(
            "NBM ingest: blocks MISSING (%d): %s — see plan §'Per-station extractability'",
            len(missing), ",".join(missing),
        )
        if halt_on_missing:
            log.error(
                "NBM ingest: HALT per first-run mandate (do not silently substitute "
                "WFO-office data). Re-run with --allow-missing if Board has reviewed "
                "and approved a fallback strategy for: %s",
                ",".join(missing),
            )
            return 3

    all_obs: list[NBMObservation] = []
    for icao in found:
        rows = parsed[icao]
        all_obs.extend(rows)
        log.info("  %s: %d observations (9 days × 2 kinds)", icao, len(rows))

    if dry_run:
        log.info(
            "NBM ingest: DRY-RUN — would write %d rows to %s; first 3 rows:",
            len(all_obs), db_path,
        )
        for o in all_obs[:3]:
            log.info("    %s", o)
        return 0

    written = _write_observations(db_path, all_obs)
    log.info("NBM ingest: WROTE %d rows to %s", written, db_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cycle",
        type=_parse_cycle_arg,
        default=None,
        help="NBM cycle ISO-8601 (UTC); defaults to latest published",
    )
    parser.add_argument("--db", default=DEFAULT_DB_URL, help="DB URL or path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse + report; do not write to DB",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help=(
            "after Board review only — do not halt on missing ICAOs; "
            "write rows for present ICAOs and log the missing set"
        ),
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="DEBUG-level logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    cycle_dt = args.cycle if args.cycle is not None else latest_cycle_dt()
    db_path = resolve_db_path(args.db)
    init_db(args.db)

    return asyncio.run(_run(
        cycle_dt=cycle_dt,
        db_path=db_path,
        dry_run=args.dry_run,
        halt_on_missing=not args.allow_missing,
    ))


if __name__ == "__main__":
    sys.exit(main())
