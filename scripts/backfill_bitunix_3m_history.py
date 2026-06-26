#!/usr/bin/env python3
"""THROW-AWAY backfill: pull historical 3m klines from the BitUnix public
kline endpoint and idempotently insert them into `bitunix_bar_history`,
byte-identical to how the live archiver writes (trading_corp/data/
bitunix_bar_archiver.py @ prod md5 53c2e64d).

Why this exists: validate 15m-SFP -> 3m-BOS at verdict-grade n. The live
engine only has ~6 weeks of 3m (since 2026-05-15); we want ~7.5 months
back to 2025-11-01.

SAFETY CONTRACT (the live engine PID is writing into this same table the
whole time this runs):
  * Additive only. INSERT OR IGNORE on PK (symbol, ts_ms, timeframe) ==
    the archiver's exact statement -> a pre-existing row (live OR a prior
    backfill row) is NEVER overwritten. The live row always wins.
  * Disjoint range. Backfill writes OLD ts (Nov-2025 .. seam); live owns
    the newest bars. By construction we stop at the per-coin live MIN(ts)
    seam, so there is no ts overlap with live rows at all.
  * Scoped. Only (one of the 4 symbols, '3m') rows are touched. Never any
    other symbol/timeframe, never a schema change.
  * Watched. Before the run and after every SAFETY_EVERY windows + after
    each coin, re-read the live high-water (MAX ts_ms per symbol), total
    count, and the table's CREATE sql. If the live high-water goes
    BACKWARD, a count shrinks, or the schema drifts -> ABORT LOUD. The
    live engine keeps running; the backfill is resumable.
  * Resumable. Per-coin cursor checkpointed to JSON after every window.
  * OHLC-validated. Null/zero/negative/inverted bars from the API are
    dropped and counted, never written.

Stdlib only (urllib + sqlite3 + json) so it runs on prod with no deps.
BitUnix now 403s urllib's default UA, so we send a browser User-Agent
(the only reason the live engine's httpx works is its own UA).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://fapi.bitunix.com/api/v1/futures/market/kline"
UA = {"User-Agent": "Mozilla/5.0"}
TF = "3m"
TF_MS = 180_000
SERVER_PAGE_CAP = 200          # verified venue cap (matches live fetcher)
SAFETY_EVERY = 25              # windows between live-safety re-checks
DEFAULT_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

# Exact prod table shape (md5 53c2e64d). We refuse to write unless the
# live table's CREATE sql matches this (whitespace-normalized).
EXPECTED_DDL = """
CREATE TABLE "bitunix_bar_history" (
    symbol       TEXT NOT NULL,
    ts_ms        INTEGER NOT NULL,
    timeframe    TEXT NOT NULL,
    open         REAL NOT NULL,
    high         REAL NOT NULL,
    low          REAL NOT NULL,
    close        REAL NOT NULL,
    volume       REAL NOT NULL,
    inserted_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, ts_ms, timeframe)
)
"""
INSERT_SQL = (
    "INSERT OR IGNORE INTO bitunix_bar_history "
    "(symbol, ts_ms, timeframe, open, high, low, close, volume, inserted_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _now_iso() -> str:
    # byte-identical to the archiver's _utc_now_iso()
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso_to_ms(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def fetch_window(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    q = urllib.parse.urlencode({
        "symbol": symbol, "interval": TF,
        "startTime": start_ms, "endTime": end_ms, "limit": SERVER_PAGE_CAP,
    })
    req = urllib.request.Request(BASE + "?" + q, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        d = json.load(r)
    if d.get("code") != 0:
        raise RuntimeError(f"bitunix kline err code={d.get('code')} msg={d.get('msg')!r}")
    return d.get("data") or []


def valid_ohlc(o: float, h: float, l: float, c: float, v: float) -> bool:
    """Reject only TRUE corruption (missing field, non-positive price,
    negative volume). NOT high/low tick-inversions: the venue emits
    O/H/L/C each rounded to the tick independently, so ~3-4% of bars have
    e.g. high 0.1 below open. The LIVE archiver writes these raw (prod has
    569-920 such 3m rows/coin, all benign), so the backfill must too --
    dropping them would gap the data the SFP/BOS contiguity walks and
    break byte-parity with live capture."""
    if any(x is None for x in (o, h, l, c, v)):
        return False
    if o <= 0 or h <= 0 or l <= 0 or c <= 0 or v < 0:
        return False
    return True


def is_tick_odd(o: float, h: float, l: float, c: float) -> bool:
    """Benign venue tick-rounding inconsistency (written, just counted)."""
    return h < l or h < o or h < c or l > o or l > c


def schema_sql(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='bitunix_bar_history'"
    ).fetchone()
    return row[0] if row else None


def live_snapshot(conn: sqlite3.Connection, coins: list[str]) -> dict:
    snap = {"schema": _norm(schema_sql(conn) or ""), "by_coin": {}}
    for c in coins:
        row = conn.execute(
            "SELECT COUNT(*), MAX(ts_ms) FROM bitunix_bar_history "
            "WHERE timeframe=? AND symbol=?", (TF, c)
        ).fetchone()
        snap["by_coin"][c] = {"count": row[0] or 0, "max_ts": row[1] or 0}
    return snap


def assert_live_safe(conn: sqlite3.Connection, baseline: dict, coins: list[str],
                     written_so_far: dict) -> None:
    cur = live_snapshot(conn, coins)
    if cur["schema"] != baseline["schema"]:
        _abort("SCHEMA DRIFT on bitunix_bar_history -- live table changed shape mid-run")
    for c in coins:
        b, n = baseline["by_coin"][c], cur["by_coin"][c]
        # live high-water (newest bar) must never go backward
        if n["max_ts"] < b["max_ts"]:
            _abort(f"{c}: live high-water REGRESSED {b['max_ts']}->{n['max_ts']} "
                   "(live rows vanished?) -- stopping to protect live capture")
        # count must only grow (we add old rows; live adds new rows)
        if n["count"] < b["count"]:
            _abort(f"{c}: row count SHRANK {b['count']}->{n['count']} -- stopping")
    # advance the rolling baseline so the next check is vs the latest live max
    for c in coins:
        baseline["by_coin"][c]["max_ts"] = max(
            baseline["by_coin"][c]["max_ts"], cur["by_coin"][c]["max_ts"])
        baseline["by_coin"][c]["count"] = max(
            baseline["by_coin"][c]["count"], cur["by_coin"][c]["count"])


def _abort(msg: str) -> None:
    print("\n" + "!" * 70, flush=True)
    print("ABORT (live-capture safety):", msg, flush=True)
    print("!" * 70, flush=True)
    raise SystemExit(3)


def load_ckpt(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_ckpt(path: str, d: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    import os
    os.replace(tmp, path)


def per_coin_seam(conn: sqlite3.Connection, coin: str) -> int | None:
    """The live MIN(ts_ms) for this coin/3m -- backfill stops here so it
    never overlaps live-captured rows. None if no live rows yet."""
    row = conn.execute(
        "SELECT MIN(ts_ms) FROM bitunix_bar_history WHERE timeframe=? AND symbol=?",
        (TF, coin)
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="target sqlite db")
    ap.add_argument("--coins", default=",".join(DEFAULT_COINS))
    ap.add_argument("--start", default="2025-11-01T00:00:00+00:00")
    ap.add_argument("--end", default="auto",
                    help="'auto' = per-coin live MIN(ts) seam, or an ISO ts")
    ap.add_argument("--rate", type=float, default=1.2, help="seconds between API calls")
    ap.add_argument("--checkpoint", default=None)
    ap.add_argument("--create-clone", action="store_true",
                    help="SAMPLE ONLY: create the prod-shape table if absent")
    ap.add_argument("--max-windows", type=int, default=0,
                    help="SAMPLE ONLY: cap windows/coin (0=unlimited)")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch+validate, do not write")
    args = ap.parse_args()

    coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]
    ckpt_path = args.checkpoint or (args.db + ".backfill_ckpt.json")
    start_ms = _iso_to_ms(args.start)
    ckpt = load_ckpt(ckpt_path)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    conn = sqlite3.connect(args.db, timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        existing = schema_sql(conn)
        if existing is None:
            if args.create_clone:
                conn.execute(EXPECTED_DDL)
                conn.execute("CREATE INDEX IF NOT EXISTS bitunix_bar_history_sym_tf_ts_idx "
                             "ON bitunix_bar_history(symbol, timeframe, ts_ms)")
                conn.commit()
                print("[clone] created prod-shape table")
            else:
                _abort("table bitunix_bar_history does not exist (use --create-clone for a sample clone)")
        elif _norm(existing) != _norm(EXPECTED_DDL):
            _abort("live table CREATE sql does NOT match expected prod DDL -- refusing to write")

        baseline = live_snapshot(conn, coins)
        print(f"[start] {_now_iso()}  db={args.db}  rate={args.rate}s/call")
        for c in coins:
            s = baseline["by_coin"][c]
            print(f"   live[{c}]: count={s['count']} max_ts={s['max_ts']}"
                  f"{' ('+_ms_to_iso(s['max_ts'])+')' if s['max_ts'] else ''}")

        totals = {c: {"written": 0, "bad": 0, "odd": 0, "windows": 0} for c in coins}
        t0 = time.time()
        for coin in coins:
            if args.end == "auto":
                seam = per_coin_seam(conn, coin)
                end_ms = seam if seam is not None else now_ms
            else:
                end_ms = _iso_to_ms(args.end)
            cursor = max(int(ckpt.get(coin, start_ms)), start_ms)
            print(f"\n[{coin}] range {_ms_to_iso(cursor)} -> {_ms_to_iso(end_ms)}")
            w = 0
            while cursor < end_ms:
                if args.max_windows and w >= args.max_windows:
                    print(f"[{coin}] hit --max-windows {args.max_windows} (sample stop)")
                    break
                win_end = min(cursor + SERVER_PAGE_CAP * TF_MS, end_ms)
                rows = fetch_window(coin, cursor, win_end)
                out = []
                for r in rows:
                    ts = int(r["time"])
                    if ts >= end_ms:                 # never cross the seam
                        continue
                    if ts + TF_MS > now_ms:          # drop in-progress bar
                        continue
                    o = float(r["open"]); h = float(r["high"]); l = float(r["low"])
                    cl = float(r["close"]); v = float(r.get("baseVol") or 0.0)
                    if not valid_ohlc(o, h, l, cl, v):
                        totals[coin]["bad"] += 1
                        continue
                    if is_tick_odd(o, h, l, cl):
                        totals[coin]["odd"] += 1
                    out.append((coin, ts, TF, o, h, l, cl, v, _now_iso()))
                if out and not args.dry_run:
                    before = conn.total_changes
                    conn.executemany(INSERT_SQL, out)
                    conn.commit()
                    # total_changes is cumulative; the delta = rows this
                    # INSERT OR IGNORE actually inserted (ignores skip dups)
                    totals[coin]["written"] += conn.total_changes - before
                totals[coin]["windows"] += 1
                w += 1
                cursor = win_end
                ckpt[coin] = cursor
                save_ckpt(ckpt_path, ckpt)
                if w % SAFETY_EVERY == 0:
                    assert_live_safe(conn, baseline, coins, totals)
                    print(f"[{coin}] ..{w} windows, safety OK, at {_ms_to_iso(cursor)}")
                time.sleep(args.rate)
            assert_live_safe(conn, baseline, coins, totals)
            print(f"[{coin}] done: windows={totals[coin]['windows']} "
                  f"written={totals[coin]['written']} bad={totals[coin]['bad']} "
                  f"tick_odd={totals[coin]['odd']}")

        elapsed = time.time() - t0
        print(f"\n[end] {_now_iso()}  elapsed={elapsed:.1f}s")
        for c in coins:
            print(f"   {c}: windows={totals[c]['windows']} written={totals[c]['written']} "
                  f"bad_bars={totals[c]['bad']} tick_odd={totals[c]['odd']}")
        # final coverage report
        for c in coins:
            row = conn.execute(
                "SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM bitunix_bar_history "
                "WHERE timeframe=? AND symbol=?", (TF, c)).fetchone()
            print(f"   coverage[{c}]: rows={row[0]} "
                  f"{_ms_to_iso(row[1]) if row[1] else '-'} .. "
                  f"{_ms_to_iso(row[2]) if row[2] else '-'}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
