"""Historical NBM probabilistic backfill from AWS S3 noaa-nbm-grib2-pds.

Iterates dates [start, end], fetches one NBM cycle per date (default 13z),
parses the per-station NBP text bulletin via the same parser used by
scripts/ingest_nbm.py (NBMForecastClient.parse_bulletin), extracts
per-airport blocks for the 19 registry-verified ICAOs, and writes rows
to weather_nbm_observations with ingest_mode='historical_backfill'.

Per Tier 1 plan (2026-05-25 archive-probe + Board decision):
  - Practical lower bound: 2021-01-15 (v4.0 era; TXNSD/TXNP* verified).
  - All 19 settlement ICAOs present as per-station blocks in every
    sampled historical date — same keying as live NOMADS.
  - Discard raw bulletins after parse (no /tmp accumulation); reproducible
    from S3 if ever needed.
  - Tag rows with ingest_mode='historical_backfill' to separate from
    'live_cron' rows produced by scripts/ingest_nbm.py.
  - Halt-on-missing-ICAO mandate carries over from C1: do not silently
    substitute or skip; log + stop.

Write-only to weather_nbm_observations. No live decision-path consumption.
No service restart. Read-only against S3.

Usage:
    python scripts/backfill_nbm_historical.py --start 2021-01-15
    python scripts/backfill_nbm_historical.py --start 2021-01-15 --end 2022-01-01
    python scripts/backfill_nbm_historical.py --start 2025-01-01 --cycle 13
    python scripts/backfill_nbm_historical.py --dry-run --start 2025-12-25
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trading_corp.data.nbm_client import (  # noqa: E402
    NBMObservation,
    parse_bulletin,
)
from trading_corp.data.weather_stations import get_registry  # noqa: E402
from trading_corp.persistence.db import init_db, resolve_db_path  # noqa: E402

DEFAULT_DB_URL = "sqlite:///data/trading_corp.db"

S3_BASE = (
    "https://noaa-nbm-grib2-pds.s3.amazonaws.com/"
    "blend.{date}/{hh}/text/blend_nbptx.t{hh}z"
)
USER_AGENT = "trading-corp-weather-arb-backfill (+https://trading.jacksumner.com)"
REQUEST_TIMEOUT_SEC = 180.0  # generous: 26-34 MB files

# Per 2026-05-25 archive probe: v4.0 era starts ~Sep 2020 but the
# bulletin layout / TXNSD presence is verified-clean from 2021-01-15.
DEFAULT_START = date(2021, 1, 15)

log = logging.getLogger("backfill_nbm_historical")


def _target_icaos() -> list[str]:
    reg = get_registry(REPO_ROOT / "config" / "weather_stations.yaml")
    icaos = sorted({s.icao for _p, _se, s in reg.list_verified_series()})
    return icaos


def _s3_url(d: date, hh: int) -> str:
    return S3_BASE.format(date=d.strftime("%Y%m%d"), hh=f"{hh:02d}")


def _cycle_dt(d: date, hh: int) -> datetime:
    return datetime(d.year, d.month, d.day, hh, 0, 0, tzinfo=timezone.utc)


def _write_rows(
    db_path: Path,
    observations: list[NBMObservation],
    ingest_mode: str = "historical_backfill",
    nbm_source: str = "nomads_s3_archive",
    icao_source: str = "registry_yaml",
) -> int:
    """UPSERT into weather_nbm_observations. Mirrors scripts/ingest_nbm.py
    but writes ingest_mode='historical_backfill' + nbm_source='nomads_s3_archive'."""
    if not observations:
        return 0
    ingested_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    sql = """
        INSERT INTO weather_nbm_observations (
            station_id, cycle_iso, valid_iso, kind, horizon_hours,
            temp_p10_f, temp_p20_f, temp_p50_f, temp_p70_f, temp_p90_f,
            temp_sigma_f, temp_mean_f,
            nbm_source, icao_source, ingest_mode, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    params = [
        (o.station_id, o.cycle_iso, o.valid_iso, o.kind, o.horizon_hours,
         o.temp_p10_f, o.temp_p20_f, o.temp_p50_f, o.temp_p70_f, o.temp_p90_f,
         o.temp_sigma_f, o.temp_mean_f,
         nbm_source, icao_source, ingest_mode, ingested_at)
        for o in observations
    ]
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(sql, params)
        conn.commit()
        return len(params)
    finally:
        conn.close()


def _fetch_one_cycle(
    client: httpx.Client,
    d: date,
    hh: int,
    target_set: set[str],
) -> tuple[list[NBMObservation], list[str], str] | None:
    """Fetch one historical NBP bulletin at one cycle; parse target ICAOs.

    Returns (observations, missing_icaos, status_msg) on success;
    None on hard transient failure (network error). 404 returns
    ([], all_targets, 'http_404'). Field-incomplete returns (obs, missing,
    'field_missing') so the caller can decide fallback vs halt.
    """
    url = _s3_url(d, hh)
    try:
        r = client.get(url)
    except Exception:
        return None  # network failure; let outer loop retry
    if r.status_code == 404:
        return ([], sorted(target_set), "http_404")
    if r.status_code != 200:
        log.warning("  %s %02dz: HTTP %d (%s)", d, hh, r.status_code, r.reason_phrase)
        return None
    text = r.text
    cycle_dt = _cycle_dt(d, hh)
    try:
        parsed = parse_bulletin(text, cycle_dt, target_set)
    except Exception as e:
        log.warning("  %s %02dz: parse failed: %s", d, hh, e)
        return None
    obs: list[NBMObservation] = []
    for icao in target_set:
        if icao in parsed:
            obs.extend(parsed[icao])
    missing = sorted(target_set - set(parsed.keys()))
    status = "ok" if not missing else "field_missing"
    return (obs, missing, status)


def _fetch_one_date_with_fallback(
    client: httpx.Client,
    d: date,
    preferred_cycle: int,
    fallback_cycles: list[int],
    target_set: set[str],
) -> tuple[list[NBMObservation], list[str], str, int] | None:
    """Try preferred cycle first; if 404 or field-missing, try fallbacks in
    order. Returns (observations, missing_icaos, status, cycle_used) or None
    on hard transient failure across all cycles.

    Per Tier 1 plan 2026-05-25: cycle fallback handles upstream-corrupted
    bulletins (verified case: 2021-04-24 13z file was 4.5MB short and
    contained no TXN* rows; 01z/07z/19z that day were clean).
    """
    cycles_to_try = [preferred_cycle] + [c for c in fallback_cycles
                                          if c != preferred_cycle]
    res_by_cycle: list[tuple[int, str, list[str]]] = []
    last_obs: list[NBMObservation] = []
    last_missing: list[str] = []
    for hh in cycles_to_try:
        res = _fetch_one_cycle(client, d, hh, target_set)
        if res is None:
            res_by_cycle.append((hh, "transient_fail", []))
            continue
        obs, missing, status = res
        res_by_cycle.append((hh, status, missing))
        if status == "ok":
            return (obs, missing, status, hh)
        last_obs, last_missing = obs, missing
    # No cycle returned a clean parse. If ANY cycle was 404 on all → propagate that.
    statuses = {st for _, st, _ in res_by_cycle}
    if statuses == {"http_404"}:
        return ([], sorted(target_set), "http_404_all_cycles", preferred_cycle)
    # At least one had field_missing — that's the failure mode the user cares about.
    log.warning("  %s: cycle fallback tried %s; all failed completeness",
                d, [f"{hh}z={st}" for hh, st, _ in res_by_cycle])
    return (last_obs, last_missing, "field_missing_all_cycles", preferred_cycle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end", type=date.fromisoformat, default=None,
                        help="default = today UTC")
    parser.add_argument("--cycle", type=int, default=13,
                        choices=[1, 7, 13, 19],
                        help="NBM cycle hour (default 13z)")
    parser.add_argument("--db", default=DEFAULT_DB_URL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help=("after Board review only — log + skip dates with any missing ICAO "
              "instead of halting. Default is halt-on-first-missing-ICAO."),
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    start = args.start
    end = args.end or datetime.now(timezone.utc).date()
    if start > end:
        log.error("start %s > end %s", start, end)
        return 2

    target = _target_icaos()
    target_set = set(target)
    log.info(
        "backfill: %s → %s (cycle %02dz), 19 targets: %s",
        start, end, args.cycle, ",".join(target),
    )

    db_path = resolve_db_path(args.db)
    init_db(args.db)

    n_dates = (end - start).days + 1
    log.info("dates: %d; mode: %s",
             n_dates, "dry-run" if args.dry_run else "WRITE")

    total_obs = 0
    total_written = 0
    n_ok = 0
    n_ok_fallback = 0
    n_404 = 0
    n_missing_halt = 0
    n_other_err = 0
    fallback_dates: list[tuple[str, int]] = []  # (date, cycle_used)
    t0 = time.time()

    fallback_cycles = [c for c in (19, 7, 1) if c != args.cycle]

    with httpx.Client(
        timeout=REQUEST_TIMEOUT_SEC,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
    ) as client:
        cur = start
        i = 0
        while cur <= end:
            i += 1
            res = _fetch_one_date_with_fallback(
                client, cur, args.cycle, fallback_cycles, target_set,
            )
            if res is None:
                # transient failure; retry once
                time.sleep(2.0)
                res = _fetch_one_date_with_fallback(
                    client, cur, args.cycle, fallback_cycles, target_set,
                )
            if res is None:
                log.warning("  %s: HARD FAIL on retry; skipping", cur)
                n_other_err += 1
                cur += timedelta(days=1)
                continue
            observations, missing, status, cycle_used = res
            if status == "http_404_all_cycles":
                log.info("  %s: HTTP 404 across all cycles; skip", cur)
                n_404 += 1
                cur += timedelta(days=1)
                continue
            if status == "field_missing_all_cycles":
                if args.allow_missing:
                    log.warning("  %s: missing %d/19 ICAOs across ALL cycles: %s "
                                "— allow-missing, recording present",
                                cur, len(missing), ",".join(missing))
                else:
                    log.error(
                        "  %s: missing %d/19 ICAOs across ALL cycles: %s "
                        "— HALT. Re-run with --allow-missing after Board review.",
                        cur, len(missing), ",".join(missing),
                    )
                    n_missing_halt += 1
                    return 3
            if cycle_used != args.cycle:
                fallback_dates.append((cur.isoformat(), cycle_used))
                n_ok_fallback += 1
                log.info("  %s: primary cycle %02dz failed; fallback %02dz OK",
                         cur, args.cycle, cycle_used)
            if not args.dry_run:
                wrote = _write_rows(db_path, observations,
                                    ingest_mode="historical_backfill",
                                    nbm_source="nomads_s3_archive",
                                    icao_source="registry_yaml")
                total_written += wrote
            total_obs += len(observations)
            n_ok += 1
            if i % 50 == 0 or i == n_dates:
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta = (n_dates - i) / rate if rate > 0 else 0
                log.info("  progress: %d/%d (ok=%d ok_fb=%d 404=%d err=%d halt=%d) "
                         "elapsed=%.0fs ETA=%.0fs total_obs=%d",
                         i, n_dates, n_ok, n_ok_fallback, n_404, n_other_err,
                         n_missing_halt, elapsed, eta, total_obs)
            cur += timedelta(days=1)

    elapsed = time.time() - t0
    log.info("=== DONE ===")
    log.info("dates processed: %d in %.0fs (%.1f/s)", n_dates, elapsed,
             n_dates / elapsed if elapsed else 0)
    log.info("  ok=%d (of which fallback=%d)  http_404=%d  hard_err=%d  halt=%d",
             n_ok, n_ok_fallback, n_404, n_other_err, n_missing_halt)
    if fallback_dates:
        log.info("fallback-cycle dates (%d): %s",
                 len(fallback_dates),
                 ", ".join(f"{d}({hh:02d}z)" for d, hh in fallback_dates[:20]))
        if len(fallback_dates) > 20:
            log.info("  ... +%d more", len(fallback_dates) - 20)
    log.info("total observations parsed: %d", total_obs)
    if not args.dry_run:
        log.info("total rows written (UPSERT): %d", total_written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
