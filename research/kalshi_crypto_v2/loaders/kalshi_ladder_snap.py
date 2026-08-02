"""Fork 2: Kalshi hourly-ladder SNAPSHOT loader -> lab_kalshi_ladder_snap.

Per SAMPLED hourly event (default 1 event / 24h / asset), capture ALL strike
markets' yes bid/ask/mean at window OPEN (one candlesticks call per strike,
tiny window). Enough for S5 Breeden-Litzenberger density + market-implied p.

COST NOTE: an open-time price requires 1 call per strike (~150-190 strikes /
event). A full every-hour pull would be ~785k calls (= the full 1m pull). Event
sampling is what keeps this to hours; strikes are NOT truncated (density tails).
Resumable: events already snapped are skipped. Full 1m ladder pull stays off.

Usage:
  python .../kalshi_ladder_snap.py [--every-hours 24] [--series KXBTC] [--dry-run]
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _kalshi_auth import KalshiAuthError, KalshiRest  # noqa: E402

os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")
LADDERS = [("KXBTC", "BTC"), ("KXETH", "ETH"), ("KXSOLE", "SOL"), ("KXXRP", "XRP")]
THROTTLE = 0.16


def epoch(iso: str | None):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) if iso else None


def _num(m, *keys):
    for k in keys:
        v = m.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def sampled_events(rest, series, every_hours):
    """Return [(event_ticker, ref_ts)] sampled ~1 per `every_hours`."""
    events = rest.paginated("/events", "events",
                            {"series_ticker": series, "status": "settled", "limit": 200},
                            max_pages=200)
    start_s, now_s = common.PERIOD_START_MS // 1000, common.now_ms() // 1000
    out, seen = [], set()
    for e in events:
        etk = e.get("event_ticker")
        # ref = event's strike-close hour; derive from strike_date or close via a market later.
        ref = epoch(e.get("strike_date") or e.get("close_time"))
        if ref is None or ref < start_s or ref > now_s:   # clip to backfill period
            continue
        bucket = (ref - start_s) // 3600 // every_hours
        if bucket in seen:
            continue
        seen.add(bucket)
        out.append((etk, ref))
    return out


def snap_event(rest, conn, asset, series, etk) -> int:
    mk = rest.get("/markets", {"event_ticker": etk, "limit": 1000})
    markets = mk.get("markets", []) or []
    if not markets:
        return 0
    open_ts = min((epoch(m.get("open_time")) for m in markets if m.get("open_time")), default=None)
    recs = []
    for m in markets:
        tkr = m.get("ticker")
        ot = epoch(m.get("open_time")) or open_ts
        if ot is None:
            continue
        time.sleep(THROTTLE)
        try:
            resp = rest.get(f"/series/{series}/markets/{tkr}/candlesticks",
                            {"period_interval": 1, "start_ts": ot - 60, "end_ts": ot + 240})
        except KalshiAuthError:
            continue
        arr = resp.get("candlesticks", []) or []
        if not arr:
            continue
        c = min(arr, key=lambda x: abs((x.get("end_period_ts") or 0) - ot))
        yb, ya, pr = c.get("yes_bid") or {}, c.get("yes_ask") or {}, c.get("price") or {}
        recs.append((asset, series, etk, tkr, _num(m, "floor_strike"), _num(m, "cap_strike"),
                     open_ts, c.get("end_period_ts"),
                     _num(yb, "close_dollars", "close"), _num(ya, "close_dollars", "close"),
                     _num(pr, "mean_dollars", "mean"), _num(c, "volume_fp", "volume"),
                     _num(c, "open_interest_fp", "open_interest"),
                     m.get("result"), _num(m, "settlement_value_dollars", "expiration_value")))
    if recs:
        conn.executemany(
            "INSERT OR REPLACE INTO lab_kalshi_ladder_snap(asset,series,event_ticker,"
            "market_ticker,floor_strike,cap_strike,ref_ts,snap_ts,yes_bid,yes_ask,"
            "price_mean,volume,open_interest,result,settlement_value)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", recs)
        conn.commit()
    return len(recs)


def main() -> int:
    every = int(sys.argv[sys.argv.index("--every-hours") + 1]) if "--every-hours" in sys.argv else 24
    only = sys.argv[sys.argv.index("--series") + 1] if "--series" in sys.argv else None
    dry = "--dry-run" in sys.argv
    rest = KalshiRest()
    conn = None if dry else common.connect()   # dry-run is network-only (no DB write/lock)
    print(f"ladder snapshots  every_hours={every}  dry_run={dry}")
    try:
        for series, asset in LADDERS:
            if only and series != only:
                continue
            evs = sampled_events(rest, series, every)
            done = set() if dry else {r[0] for r in conn.execute(
                "SELECT DISTINCT event_ticker FROM lab_kalshi_ladder_snap WHERE series=?", (series,))}
            todo = [e for e in evs if e[0] not in done]
            print(f"  {series}: {len(evs)} sampled events ({len(done)} already snapped, "
                  f"{len(todo)} to do)", flush=True)
            if dry:
                continue
            total = 0
            for i, (etk, _ref) in enumerate(todo):
                total += snap_event(rest, conn, asset, series, etk)
                if (i + 1) % 20 == 0:
                    print(f"    {series}: {i+1}/{len(todo)} events, {total} strike-snaps", flush=True)
            print(f"  {series} DONE: {total} strike-snaps over {len(todo)} events", flush=True)
    finally:
        if conn is not None:
            conn.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KalshiAuthError as e:
        print(f"STOP creds: {e}")
        raise SystemExit(2)
