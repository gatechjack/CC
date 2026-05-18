"""Merge tmp/prod_alerts/slice_*.json files into a single filtered cache.

az vm run-command stdout is capped at ~4KB, so the prod-alert pull is
paginated into 6-hour slices. This script:
  - reads each slice file's stdout
  - parses pipe-separated rows (ts|actor|signal|symbol|price|tf)
  - applies pink_box + smoke_test + empty-signal filters at ingest
  - deduplicates by (ts, actor, signal)
  - writes the merged cache to data/historical_alerts/

Usage:
    python scripts/merge_prod_alert_slices.py \
        --slice-dir tmp/prod_alerts \
        --start 2026-04-30 --end 2026-05-18
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / "data" / "historical_alerts"

FILTER_SIGNALS = {
    "smoke_test_lowercase_d",
    "smoke_test_post_deploy_2",
    "pink_box_bull",
    "pink_box_bear",
}


def _extract_stdout(slice_text: str) -> str:
    try:
        d = json.loads(slice_text)
        msg = d["value"][0]["message"]
        if "[stdout]" not in msg:
            return ""
        return msg.split("[stdout]", 1)[1].split("[stderr]", 1)[0].strip()
    except Exception:
        return ""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--slice-dir", default="tmp/prod_alerts")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--out", default=None)
    args = p.parse_args()
    slice_dir = Path(args.slice_dir)
    start = datetime.fromisoformat(args.start + "T00:00:00+00:00")
    end = datetime.fromisoformat(args.end + "T00:00:00+00:00")

    seen: set[tuple[str, str, str]] = set()
    rows: list[dict] = []
    filtered: dict[str, int] = {}
    truncation_warnings: list[str] = []

    slice_files = sorted(slice_dir.glob("slice_*.json"))
    print(f"Reading {len(slice_files)} slice files...")
    for sf in slice_files:
        body = _extract_stdout(sf.read_text(encoding="utf-8"))
        if not body:
            continue
        # Detect mid-row truncation: first line should start with an ISO ts
        first_line = body.split("\n", 1)[0]
        if first_line and not (len(first_line) >= 4 and first_line[:4].isdigit()):
            truncation_warnings.append(f"{sf.name}: first chars {first_line[:30]!r}")
        for line in body.split("\n"):
            if not line.strip():
                continue
            parts = line.split("|", 5)
            if len(parts) < 3:
                continue
            ts, actor, signal = parts[0], parts[1], parts[2]
            symbol = parts[3] if len(parts) > 3 else None
            price = parts[4] if len(parts) > 4 else None
            interval_raw = parts[5] if len(parts) > 5 else None
            # TradingView interval → confluence-config tf string
            tf_map = {
                "3": "3m", "5": "5m", "15": "15m", "30": "30m",
                "60": "1h", "240": "4h", "D": "1d",
            }
            tf = tf_map.get((interval_raw or "").strip()) if interval_raw else None
            # Skip truncated leading row (no proper ISO ts)
            if not (len(ts) >= 4 and ts[:4].isdigit()):
                continue
            try:
                ts_dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if not (start <= ts_dt < end):
                continue
            signal_l = (signal or "").lower()
            if not signal_l:
                filtered["empty_signal"] = filtered.get("empty_signal", 0) + 1
                continue
            if signal_l in FILTER_SIGNALS:
                filtered[signal_l] = filtered.get(signal_l, 0) + 1
                continue
            key = (ts, actor, signal_l)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "ts": ts, "actor": actor, "signal": signal_l,
                "symbol": symbol or None,
                "price": float(price) if price and price not in ("", "None") else None,
                "tf": tf or None,
            })
    rows.sort(key=lambda r: r["ts"])

    if truncation_warnings:
        print(f"  WARN: {len(truncation_warnings)} slices may be truncated:")
        for w in truncation_warnings[:5]:
            print(f"    {w}")
        if len(truncation_warnings) > 5:
            print(f"    ... +{len(truncation_warnings)-5} more")

    print(f"Merged: {len(rows)} unique alerts kept")
    print("Filtered breakdown:")
    for k, n in sorted(filtered.items()):
        print(f"  {k}: {n}")

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
