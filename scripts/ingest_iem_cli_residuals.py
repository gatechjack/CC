"""IEM CLI ground-truth → weather_forecast_residuals ingester.

Per Tier 1 plan §C2. For each verified bet-on station:
  1. Pull IEM CLI daily MaxT/MinT (the official NWS settlement values).
  2. Join to historical kalshi_weather_evaluated audit rows in the same
     DB to recover forecast_temp_f / horizon_hours / coord_source.
  3. Join to weather_nbm_observations to recover NBM percentiles + sigma.
  4. Write one residual row per (station, target_date, kind, source)
     with logic_era tag per the contamination-guard rules.

logic_era assignment (load-bearing — see residual_logic.py):
  - NBM-native rows: 'native_post_fix'
  - Audit rows ts >= 2026-05-22T16:25:00 UTC: 'post_station_fix'
  - Audit rows pre-cutoff: 'pre_station_fix'
  - Safety override: corrected-series (NYC/CHI/HOU) audit rows with
    coord_source != 'yaml_verified' → 'pre_station_fix' regardless of ts.

Default calibration queries filter `WHERE logic_era != 'pre_station_fix'`
to keep wrong-station forecasts out of any per-station baseline.

Build-now-safe: write-only ingestion to a new isolated table. No live
decision path reads weather_forecast_residuals today.

Usage:
    python scripts/ingest_iem_cli_residuals.py --backfill 90
    python scripts/ingest_iem_cli_residuals.py --incremental
    python scripts/ingest_iem_cli_residuals.py --station KNYC --backfill 30
    python scripts/ingest_iem_cli_residuals.py --dry-run

Invocation note: project Python imports require the windows job-object
wrapper. Use `.\\scripts\\run_capped.ps1 python scripts\\ingest_iem_cli_residuals.py [...]`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trading_corp.data.iem_cli_client import CLIDay, IEMCLIClient  # noqa: E402
from trading_corp.data.residual_logic import (  # noqa: E402
    assign_logic_era,
    derive_season,
)
from trading_corp.data.weather_stations import get_registry  # noqa: E402
from trading_corp.persistence.db import init_db, resolve_db_path  # noqa: E402

DEFAULT_DB_URL = "sqlite:///data/trading_corp.db"
log = logging.getLogger("ingest_iem_cli_residuals")

# Maps series prefix → 'daily_max' or 'daily_min'. Derived from the
# settles_what field on each SeriesEntry. Built per-run from registry.


@dataclass(frozen=True)
class ResidualRow:
    """One weather_forecast_residuals row, pre-DB."""
    station_id: str
    target_date: str
    kind: str
    target_iso: str | None
    forecast_temp_f: float
    actual_temp_f: float
    forecast_source: str
    horizon_hours: float
    residual_f: float
    cycle_iso: str  # NOT NULL — see schema
    season: str
    logic_era: str
    icao_source: str = "registry_yaml"


def _series_prefix_from_ticker(ticker: str) -> str | None:
    """Extract the series prefix (e.g. KXHIGHNY) from a ticker like
    KXHIGHNY-26MAY24-T75. Returns None if pattern doesn't match."""
    m = re.match(r"^([A-Z][A-Z0-9]+)-", ticker)
    return m.group(1) if m else None


def _icao_for_series(prefix_to_icao: dict[str, str], series_prefix: str) -> str | None:
    return prefix_to_icao.get(series_prefix)


def _build_residual_from_audit(
    *,
    audit_ts: str,
    payload: dict,
    cli_high: int | None,
    cli_low: int | None,
    prefix_to_icao: dict[str, str],
    prefix_to_kind: dict[str, str],
) -> ResidualRow | None:
    """Build one residual row from a single kalshi_weather_evaluated audit row.

    The audit row carries the forecast_temp_f the strategy USED for its bet
    decision; the IEM CLI value is the actual settlement temp.
    """
    ticker = payload.get("ticker")
    if not isinstance(ticker, str):
        return None
    series_prefix = _series_prefix_from_ticker(ticker)
    if series_prefix is None:
        return None
    icao = _icao_for_series(prefix_to_icao, series_prefix)
    if icao is None:
        return None
    kind = prefix_to_kind.get(series_prefix)
    if kind not in ("daily_max", "daily_min"):
        return None

    forecast = payload.get("forecast_temp_f")
    target_iso = payload.get("target_iso")
    horizon = payload.get("horizon_hours")
    if forecast is None or target_iso is None or horizon is None:
        return None

    # The forecast in the audit row is the strategy's blended forecast.
    forecast_source = "nws_blend"

    try:
        target_dt = datetime.fromisoformat(target_iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    target_date_obj = target_dt.date()
    target_date_str = target_date_obj.isoformat()

    actual = cli_high if kind == "daily_max" else cli_low
    if actual is None:
        return None

    residual = float(actual) - float(forecast)
    coord_source = payload.get("coord_source")
    logic_era = assign_logic_era(
        forecast_source=forecast_source,
        audit_ts_iso=audit_ts,
        audit_coord_source=coord_source if isinstance(coord_source, str) else None,
        series_prefix=series_prefix,
    )
    season = derive_season(target_date_obj)

    return ResidualRow(
        station_id=icao,
        target_date=target_date_str,
        kind=kind,
        target_iso=target_iso,
        forecast_temp_f=float(forecast),
        actual_temp_f=float(actual),
        forecast_source=forecast_source,
        horizon_hours=float(horizon),
        residual_f=residual,
        cycle_iso=audit_ts,  # NOT NULL: use audit ts as the synthetic cycle id
        season=season,
        logic_era=logic_era,
    )


def _build_residuals_from_nbm(
    conn: sqlite3.Connection,
    icao: str,
    cli_by_date: dict[date, CLIDay],
) -> list[ResidualRow]:
    """For each NBM observation row joinable to a CLI actual, emit a residual.

    Two rows per (cycle, valid_date, kind): one for `nbm_p50` and one for
    `nbm_mean`. Both share the same actual_temp_f from CLI; they differ
    in their forecast_temp_f.
    """
    rows: list[ResidualRow] = []
    for r in conn.execute(
        "SELECT cycle_iso, valid_iso, kind, horizon_hours, temp_p50_f, "
        "       temp_mean_f "
        "FROM weather_nbm_observations WHERE station_id=?",
        (icao,),
    ):
        cycle_iso, valid_iso, kind, horizon, p50, mean_f = r
        try:
            valid_dt = datetime.fromisoformat(valid_iso.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        target_date_obj = valid_dt.date()
        cli = cli_by_date.get(target_date_obj)
        if cli is None:
            continue
        actual = cli.high_f if kind == "daily_max" else cli.low_f
        target_date_str = target_date_obj.isoformat()
        season = derive_season(target_date_obj)
        for source_name, fcst in (("nbm_p50", p50), ("nbm_mean", mean_f)):
            logic_era = assign_logic_era(
                forecast_source=source_name,
                audit_ts_iso=None,
                audit_coord_source=None,
                series_prefix=None,
            )
            rows.append(ResidualRow(
                station_id=icao,
                target_date=target_date_str,
                kind=kind,
                target_iso=valid_iso,
                forecast_temp_f=float(fcst),
                actual_temp_f=float(actual),
                forecast_source=source_name,
                horizon_hours=float(horizon),
                residual_f=float(actual) - float(fcst),
                cycle_iso=cycle_iso,
                season=season,
                logic_era=logic_era,
            ))
    return rows


def _write_residuals(db_path: Path, rows: list[ResidualRow]) -> int:
    if not rows:
        return 0
    ingested_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    sql = """
        INSERT INTO weather_forecast_residuals (
            station_id, target_date, kind, target_iso, forecast_temp_f,
            actual_temp_f, forecast_source, horizon_hours, residual_f,
            cycle_iso, season, logic_era, icao_source, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (station_id, target_date, kind, forecast_source, cycle_iso)
        DO UPDATE SET
            target_iso = excluded.target_iso,
            forecast_temp_f = excluded.forecast_temp_f,
            actual_temp_f = excluded.actual_temp_f,
            horizon_hours = excluded.horizon_hours,
            residual_f = excluded.residual_f,
            season = excluded.season,
            logic_era = excluded.logic_era,
            icao_source = excluded.icao_source,
            ingested_at = excluded.ingested_at
    """
    params = [
        (r.station_id, r.target_date, r.kind, r.target_iso, r.forecast_temp_f,
         r.actual_temp_f, r.forecast_source, r.horizon_hours, r.residual_f,
         r.cycle_iso, r.season, r.logic_era, r.icao_source, ingested_at)
        for r in rows
    ]
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(sql, params)
        conn.commit()
        return len(params)
    finally:
        conn.close()


async def _backfill_one_station(
    iem: IEMCLIClient,
    db_path: Path,
    icao: str,
    series_prefixes_for_icao: list[str],
    prefix_to_icao: dict[str, str],
    prefix_to_kind: dict[str, str],
    start: date,
    end: date,
) -> list[ResidualRow]:
    """Pull CLI window for one ICAO; produce residuals for both audit and NBM joins."""
    cli_days = await iem.fetch_window(icao, start, end)
    if cli_days is None:
        log.warning("IEM fetch failed for %s; skipping", icao)
        return []
    cli_by_date: dict[date, CLIDay] = {d.valid_date: d for d in cli_days}
    log.info("  %s: pulled %d IEM CLI days [%s..%s]", icao, len(cli_days), start, end)

    rows: list[ResidualRow] = []

    # Audit-derived residuals
    conn = sqlite3.connect(db_path)
    try:
        # Filter by ticker prefix to limit work per station
        prefix_clauses = " OR ".join(
            "json_extract(payload_json,'$.ticker') LIKE ?" for _ in series_prefixes_for_icao
        )
        prefix_params = [f"{p}%" for p in series_prefixes_for_icao]
        if not prefix_clauses:
            audit_count = 0
        else:
            query = (
                "SELECT ts, payload_json FROM audit_event "
                "WHERE actor='kalshi_weather_arb' AND kind='kalshi_weather_evaluated' "
                "AND ts >= ? AND ts < ? "
                f"AND ({prefix_clauses})"
            )
            start_iso = f"{start.isoformat()}T00:00:00"
            end_iso = f"{(end + timedelta(days=1)).isoformat()}T00:00:00"
            audit_count = 0
            for ts, payload_json in conn.execute(
                query, [start_iso, end_iso, *prefix_params]
            ):
                try:
                    payload = json.loads(payload_json)
                except (TypeError, ValueError):
                    continue
                ticker = payload.get("ticker")
                if not isinstance(ticker, str):
                    continue
                # Find CLI actual for the target date
                target_iso = payload.get("target_iso")
                if target_iso is None:
                    continue
                try:
                    target_dt = datetime.fromisoformat(target_iso.replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    continue
                cli = cli_by_date.get(target_dt.date())
                if cli is None:
                    continue
                row = _build_residual_from_audit(
                    audit_ts=ts,
                    payload=payload,
                    cli_high=cli.high_f,
                    cli_low=cli.low_f,
                    prefix_to_icao=prefix_to_icao,
                    prefix_to_kind=prefix_to_kind,
                )
                if row is not None:
                    rows.append(row)
                    audit_count += 1
    finally:
        conn.close()
    log.info("  %s: %d audit-derived residual rows", icao, audit_count)

    # NBM-native residuals (cli × nbm)
    conn = sqlite3.connect(db_path)
    try:
        nbm_rows = _build_residuals_from_nbm(conn, icao, cli_by_date)
    finally:
        conn.close()
    rows.extend(nbm_rows)
    log.info("  %s: %d NBM-derived residual rows", icao, len(nbm_rows))

    return rows


async def _run(
    db_path: Path,
    start: date,
    end: date,
    target_stations: list[str] | None,
    dry_run: bool,
) -> int:
    reg = get_registry(REPO_ROOT / "config" / "weather_stations.yaml")
    series_rows = reg.list_verified_series()
    prefix_to_icao: dict[str, str] = {p: s.icao for p, _se, s in series_rows}
    prefix_to_kind: dict[str, str] = {
        p: ("daily_max" if se.settles_what == "daily_max_temp" else "daily_min")
        for p, se, _s in series_rows
    }
    icao_to_prefixes: dict[str, list[str]] = {}
    for p, _se, s in series_rows:
        icao_to_prefixes.setdefault(s.icao, []).append(p)

    icaos = sorted(icao_to_prefixes.keys())
    if target_stations:
        icaos = [i for i in icaos if i in set(target_stations)]
    log.info("IEM CLI ingest: window=[%s..%s] stations=%d", start, end, len(icaos))

    iem = IEMCLIClient()
    all_rows: list[ResidualRow] = []
    try:
        for icao in icaos:
            rows = await _backfill_one_station(
                iem, db_path, icao,
                icao_to_prefixes[icao],
                prefix_to_icao, prefix_to_kind,
                start, end,
            )
            all_rows.extend(rows)
    finally:
        await iem.close()

    log.info("IEM CLI ingest: total residual rows produced: %d", len(all_rows))

    if dry_run:
        # Report logic_era distribution from the pre-write rows
        dist: dict[str, int] = {}
        for r in all_rows:
            dist[r.logic_era] = dist.get(r.logic_era, 0) + 1
        log.info("DRY-RUN logic_era distribution: %s", dist)
        if all_rows:
            log.info("First 2 rows: %s", all_rows[:2])
        return 0

    written = _write_residuals(db_path, all_rows)
    log.info("IEM CLI ingest: WROTE %d rows to %s", written, db_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=DEFAULT_DB_URL)
    parser.add_argument("--backfill", type=int, default=None,
                        help="days back from today (e.g. 90)")
    parser.add_argument("--start", type=date.fromisoformat, default=None,
                        help="explicit start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=date.fromisoformat, default=None,
                        help="explicit end date (YYYY-MM-DD), default=today UTC")
    parser.add_argument("--incremental", action="store_true",
                        help="use 7d window ending today (typical daily run)")
    parser.add_argument("--station", action="append", default=None,
                        help="limit to specific ICAO(s); repeatable")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    today_utc = datetime.now(timezone.utc).date()
    end = args.end or today_utc
    if args.start:
        start = args.start
    elif args.incremental:
        start = end - timedelta(days=7)
    elif args.backfill:
        start = end - timedelta(days=args.backfill)
    else:
        # Default: 90-day backfill on first run.
        start = end - timedelta(days=90)

    db_path = resolve_db_path(args.db)
    init_db(args.db)

    return asyncio.run(_run(
        db_path=db_path,
        start=start,
        end=end,
        target_stations=args.station,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    sys.exit(main())
