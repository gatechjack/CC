"""Pull prod webhook_received alerts via az vm run-command (SSH-blocked path).

az vm run-command caps stdout at ~4KB, so we paginate one day at a time.
Output shape matches the existing `data/historical_alerts/cache_alerts_*.json`
cache so the backtest harness can load it. Filters pink_box_*, smoke_test_*,
and empty-signal rows at ingest per v1.1 hybrid backtest spec.

Usage:
    python scripts/fetch_prod_alerts_az.py --start 2026-04-30 --end 2026-05-18
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / "data" / "historical_alerts"

FILTER_SIGNALS = {
    "smoke_test_lowercase_d",
    "smoke_test_post_deploy_2",
    "pink_box_bull",
    "pink_box_bear",
}


def _az_bin() -> str:
    return shutil.which("az") or shutil.which("az.cmd") or "az.cmd"


def _az_query(sql: str, retries: int = 3, sleep_s: float = 5.0) -> str:
    cmd = [
        _az_bin(), "vm", "run-command", "invoke",
        "-g", "rg-shared-prod", "-n", "tc-prod-vm",
        "--command-id", "RunShellScript",
        "--scripts",
        f'sqlite3 -separator "|" /home/azureuser/trading_corp/data/trading_corp.db "{sql}"',
    ]
    last_err = ""
    for attempt in range(retries):
        r = subprocess.run(cmd, capture_output=True, text=True, shell=False)
        combined = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            data = json.loads(r.stdout)
            msg = data["value"][0]["message"]
            if "[stdout]" not in msg:
                raise RuntimeError(f"unexpected az output: {msg[:200]}")
            return msg.split("[stdout]", 1)[1].split("[stderr]", 1)[0].strip()
        last_err = combined
        if "Conflict" in combined or "in progress" in combined:
            time.sleep(sleep_s)
            continue
        break
    raise RuntimeError(f"az query failed after {retries} retries: {last_err[:300]}")


def fetch(start: datetime, end: datetime) -> list[dict]:
    print(f"Querying prod day-by-day for {start.date()} -> {end.date()}...")
    rows: list[dict] = []
    filtered_counts: dict[str, int] = {}
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=1), end)
        sql = (
            "SELECT ts, actor, "
            "json_extract(payload_json, '$.signal') AS signal, "
            "json_extract(payload_json, '$.symbol') AS symbol, "
            "json_extract(payload_json, '$.price') AS price, "
            "json_extract(payload_json, '$.tf') AS tf "
            "FROM audit_event "
            "WHERE actor IN ('lord_otter','market_cypher') "
            "AND kind='webhook_received' "
            f"AND ts >= '{cur.isoformat()}' "
            f"AND ts <  '{nxt.isoformat()}' "
            "ORDER BY ts"
        )
        out = _az_query(sql)
        day_kept = 0
        day_dropped = 0
        for line in out.split("\n"):
            if not line.strip():
                continue
            parts = line.split("|", 5)
            if len(parts) < 3:
                continue
            ts, actor, signal = parts[0], parts[1], parts[2]
            symbol = parts[3] if len(parts) > 3 else None
            price = parts[4] if len(parts) > 4 else None
            tf = parts[5] if len(parts) > 5 else None
            signal_l = (signal or "").lower()
            if not signal_l:
                filtered_counts["empty_signal"] = filtered_counts.get("empty_signal", 0) + 1
                day_dropped += 1
                continue
            if signal_l in FILTER_SIGNALS:
                filtered_counts[signal_l] = filtered_counts.get(signal_l, 0) + 1
                day_dropped += 1
                continue
            rows.append({
                "ts": ts,
                "actor": actor,
                "signal": signal_l,
                "symbol": symbol or None,
                "price": float(price) if price and price not in ("", "None") else None,
                "tf": tf or None,
            })
            day_kept += 1
        print(f"  {cur.date()}: kept={day_kept}, dropped={day_dropped}")
        cur = nxt
    print(f"TOTAL kept: {len(rows)}; filtered:")
    for k, n in sorted(filtered_counts.items()):
        print(f"  {k}: {n}")
    return rows


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--out", default=None)
    args = p.parse_args()
    start = datetime.fromisoformat(args.start + "T00:00:00+00:00")
    end = datetime.fromisoformat(args.end + "T00:00:00+00:00")
    rows = fetch(start, end)
    if args.out is None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        out = (
            CACHE_DIR / f"cache_alerts_prod_filtered_"
            f"{start.date().isoformat().replace('-','')}_"
            f"{end.date().isoformat().replace('-','')}.json"
        )
    else:
        out = Path(args.out)
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"wrote {out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
