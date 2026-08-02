"""Shared S3 loader infrastructure.

- Isolated lab DB access (imports lab/labdb.py; prod DB is NEVER touched).
- GET-only HTTP with retry/backoff + optional throttle (no order surface).
- File-based raw-response cache (gitignored) so re-runs don't re-pull.
- Minute-grid coverage + gap detection over the backfill period.
- lab_coverage upsert.

READ-ONLY external I/O. Every network call is an HTTP GET.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# --- paths / lab DB ----------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_LAB_DIR = os.path.join(os.path.dirname(_HERE), "lab")   # research/kalshi_crypto_v2/lab
if _LAB_DIR not in sys.path:
    sys.path.insert(0, _LAB_DIR)
import labdb  # noqa: E402

CACHE_DIR = os.path.join(_HERE, "_rawcache")             # gitignored (*.json under here)

# --- constants ---------------------------------------------------------------
ASSETS = ["BTC", "ETH", "SOL", "XRP"]
MINUTE_MS = 60_000
# Backfill coverage window start: 2026-05-25 00:00:00 UTC (T1 census earliest).
PERIOD_START_MS = int(datetime(2026, 5, 25, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
USER_AGENT = "kcv2-s3-loader"
_CTX = ssl.create_default_context()


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def iso(ts_ms: int | None) -> str:
    if ts_ms is None:
        return "-"
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# --- HTTP (GET only) ---------------------------------------------------------
class GetError(Exception):
    """HTTP GET failure; messages carry no secret values."""


def http_get(url: str, params: dict | None = None, headers: dict | None = None,
             timeout: int = 25, retries: int = 5, backoff: float = 1.6,
             throttle: float = 0.0) -> object:
    """GET url?params -> parsed JSON. Retries on 429/418/5xx/transient with
    exponential backoff. `throttle` sleeps before the call to respect rate caps."""
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    qs = ("?" + urllib.parse.urlencode(clean)) if clean else ""
    full = url + qs
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    last = ""
    for attempt in range(retries):
        if throttle:
            time.sleep(throttle)
        req = urllib.request.Request(full, method="GET", headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            code = e.code
            last = f"HTTP {code} {e.reason}"
            if code in (429, 418) or 500 <= code < 600:
                time.sleep(backoff ** attempt + 0.5)
                continue
            body = e.read(200).decode("utf-8", "replace")
            raise GetError(f"{last}: {body[:180]}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = f"{type(e).__name__}: {str(e)[:120]}"
            time.sleep(backoff ** attempt + 0.5)
            continue
    raise GetError(f"exhausted {retries} retries: {last}")


# --- raw-response cache ------------------------------------------------------
def cache_path(key: str) -> str:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)[:180]
    return os.path.join(CACHE_DIR, safe + ".json")


def cache_get(key: str) -> object | None:
    p = cache_path(key)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    return None


def cache_put(key: str, data: object) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = cache_path(key) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, cache_path(key))


# --- coverage / gaps ---------------------------------------------------------
def minute_coverage(ts_ms_iter, start_ms: int, end_ms: int, step_ms: int = MINUTE_MS) -> dict:
    """Coverage of a fixed `step_ms` grid over [start_ms, end_ms] (default 1min).
    Snaps ts to the grid. Returns rows/min_ts/max_ts/expected/present/missing +
    gap ranges (list of (gap_start_ms, gap_end_ms, missing_buckets)) within [s,e]."""
    present = set()
    mn = mx = None
    for t in ts_ms_iter:
        m = t - (t % step_ms)
        present.add(m)
        mn = m if mn is None else min(mn, m)
        mx = m if mx is None else max(mx, m)
    s = start_ms - (start_ms % step_ms)
    e = end_ms - (end_ms % step_ms)
    expected = (e - s) // step_ms + 1 if e >= s else 0
    present_in = {m for m in present if s <= m <= e}
    missing = expected - len(present_in)
    # gap ranges over the requested grid [s, e]
    gaps = []
    if present_in:
        gap_start = None
        prev_missing_end = None
        t = s
        while t <= e:
            if t not in present_in:
                if gap_start is None:
                    gap_start = t
                prev_missing_end = t
            else:
                if gap_start is not None:
                    gaps.append((gap_start, prev_missing_end,
                                 (prev_missing_end - gap_start) // step_ms + 1))
                    gap_start = None
            t += step_ms
        if gap_start is not None:
            gaps.append((gap_start, prev_missing_end,
                         (prev_missing_end - gap_start) // step_ms + 1))
    return {"rows": len(present), "min_ts": mn, "max_ts": mx,
            "expected": expected, "present": len(present_in), "missing": missing,
            "gap_frac": (missing / expected) if expected else 1.0, "gaps": gaps}


INTERVAL_MS = {"1min": 60_000, "5min": 300_000, "15min": 900_000,
               "30min": 1_800_000, "1hour": 3_600_000}


def write_coverage(conn, source: str, asset: str, rows: int, min_ts: int | None,
                   max_ts: int | None, gap_count: int, note: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO lab_coverage(source,asset,rows,min_ts,max_ts,gap_count,note)"
        " VALUES(?,?,?,?,?,?,?)", (source, asset, rows, min_ts, max_ts, gap_count, note))
    conn.commit()


def connect():
    labdb.migrate()          # idempotent DDL
    conn = labdb.connect()
    conn.execute("PRAGMA busy_timeout=30000")   # tolerate concurrent reader/writer
    return conn
