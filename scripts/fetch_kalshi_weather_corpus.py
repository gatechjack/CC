"""Pull kalshi_weather replay corpus from prod via az vm run-command.

`az vm run-command` caps stdout at ~4KB, so we paginate by day with
`hex(payload_json)` server-side encoding to safely transport embedded JSON.
If a day-window's output is truncated (malformed-row detection), sub-paginate
to 6h, then 1h.

Output (one JSON object per line):
    tmp/kw_corpus_whp.jsonl  — 636 would_have_placed rows (full payload + ts)
    tmp/kw_corpus_rt.jsonl   — 556 kalshi_round_trips rows

Usage:
    python scripts/fetch_kalshi_weather_corpus.py

No imports from trading_corp/; runs unwrapped per CLAUDE.md (stdlib only +
subprocess to call az CLI).
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TMP_DIR = REPO_ROOT / "tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)

WINDOW_START = datetime(2026, 5, 15, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 5, 23, tzinfo=timezone.utc)

RT_COLS = [
    "order_id", "ticker", "event_ticker", "event_title", "category",
    "strategy", "division", "arb_type", "outcome_bet", "qty", "entry_price",
    "notional", "entry_ts", "resolved_ts", "market_result", "won",
    "realized_pnl", "roi_pct", "implied_at_entry",
]  # `extra_json` fetched separately as hex; appended after the tail


def _az_bin() -> str:
    return shutil.which("az") or shutil.which("az.cmd") or "az.cmd"


def _az_query(sql: str, retries: int = 12, sleep_s: float = 10.0) -> str:
    # Base64-encode the SQL so `<` and `>` operators don't trip Windows CMD
    # redirection when az.cmd dispatches to its internal shell.
    sql_b64 = base64.b64encode(sql.encode("utf-8")).decode("ascii")
    script = (
        f"echo {sql_b64} | base64 -d | "
        f"sqlite3 /home/azureuser/trading_corp/data/trading_corp.db"
    )
    cmd = [
        _az_bin(), "vm", "run-command", "invoke",
        "-g", "rg-shared-prod", "-n", "tc-prod-vm",
        "--command-id", "RunShellScript",
        "--scripts", script,
    ]
    last_err = ""
    for attempt in range(retries):
        r = subprocess.run(cmd, capture_output=True, text=True, shell=False)
        combined = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            try:
                data = json.loads(r.stdout)
                msg = data["value"][0]["message"]
            except Exception as e:
                raise RuntimeError(f"az output parse failed: {e}; stdout[:200]={r.stdout[:200]}")
            if "[stdout]" not in msg:
                raise RuntimeError(f"unexpected az output: {msg[:200]}")
            return msg.split("[stdout]", 1)[1].split("[stderr]", 1)[0].strip()
        last_err = combined
        if "Conflict" in combined or "in progress" in combined:
            time.sleep(sleep_s)
            continue
        break
    raise RuntimeError(f"az query failed after {retries} retries: {last_err[:300]}")


def _count(table: str, where: str) -> int:
    sql = f"SELECT COUNT(*) FROM {table} WHERE {where}"
    out = _az_query(sql)
    return int(out.strip())


def _fetch_whp_window(start: datetime, end: datetime) -> list[dict] | None:
    """Fetch WHP rows in [start, end). Returns None on suspected truncation."""
    sql = (
        "SELECT ts || '|' || hex(payload_json) "
        "FROM audit_event "
        "WHERE actor='kalshi_weather_arb' AND kind='would_have_placed' "
        f"AND ts >= '{start.isoformat()}' AND ts < '{end.isoformat()}' "
        "ORDER BY ts"
    )
    out = _az_query(sql)
    rows: list[dict] = []
    for line in out.split("\n"):
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            return None
        ts, hex_payload = line.split("|", 1)
        # hex string must be even-length & all hex chars
        if len(hex_payload) % 2 != 0 or not all(c in "0123456789abcdefABCDEF" for c in hex_payload):
            return None
        try:
            payload = json.loads(bytes.fromhex(hex_payload).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return None
        payload["ts"] = ts
        payload["actor"] = "kalshi_weather_arb"
        rows.append(payload)
    return rows


def fetch_whp(start: datetime, end: datetime) -> list[dict]:
    rows: list[dict] = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=1), end)
        # expected per window
        expected = _count(
            "audit_event",
            f"actor='kalshi_weather_arb' AND kind='would_have_placed' "
            f"AND ts >= '{cur.isoformat()}' AND ts < '{nxt.isoformat()}'"
        )
        fetched = _fetch_whp_window(cur, nxt)
        if fetched is None or len(fetched) != expected:
            print(f"  day {cur.date()}: expected {expected}, got {len(fetched) if fetched else 'malformed'} — sub-paginating to 6h")
            sub_cur = cur
            while sub_cur < nxt:
                sub_end = min(sub_cur + timedelta(hours=6), nxt)
                sub_expected = _count(
                    "audit_event",
                    f"actor='kalshi_weather_arb' AND kind='would_have_placed' "
                    f"AND ts >= '{sub_cur.isoformat()}' AND ts < '{sub_end.isoformat()}'"
                )
                sub_fetched = _fetch_whp_window(sub_cur, sub_end)
                if sub_fetched is None or len(sub_fetched) != sub_expected:
                    print(f"    6h {sub_cur.isoformat()}: expected {sub_expected}, got {len(sub_fetched) if sub_fetched else 'malformed'} — sub-paginating to 1h")
                    h_cur = sub_cur
                    while h_cur < sub_end:
                        h_end = min(h_cur + timedelta(hours=1), sub_end)
                        h_fetched = _fetch_whp_window(h_cur, h_end)
                        if h_fetched is None:
                            raise RuntimeError(f"truncation even at 1h: {h_cur.isoformat()}")
                        rows.extend(h_fetched)
                        h_cur = h_end
                else:
                    rows.extend(sub_fetched)
                sub_cur = sub_end
        else:
            rows.extend(fetched)
        print(f"  {cur.date()}: cumulative {len(rows)} WHP rows")
        cur = nxt
    return rows


def _fetch_rt_window(start: datetime, end: datetime) -> list[dict] | None:
    cols_sql = ", ".join(RT_COLS) + ", hex(extra_json)"
    sql = (
        f"SELECT {cols_sql} "
        "FROM kalshi_round_trips "
        "WHERE strategy='kalshi_weather_arb' "
        f"AND resolved_ts >= '{start.isoformat()}' "
        f"AND resolved_ts <  '{end.isoformat()}' "
        "ORDER BY resolved_ts"
    )
    out = _az_query(sql)
    rows: list[dict] = []
    for line in out.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < len(RT_COLS) + 1:
            return None
        # Last field is extra_json hex; everything before is the regular cols
        head = parts[:len(RT_COLS)]
        extra_hex = parts[len(RT_COLS)]  # exactly one trailing field
        row = dict(zip(RT_COLS, head))
        if extra_hex and all(c in "0123456789abcdefABCDEF" for c in extra_hex) and len(extra_hex) % 2 == 0:
            try:
                row["extra_json"] = json.loads(bytes.fromhex(extra_hex).decode("utf-8"))
            except (ValueError, json.JSONDecodeError):
                row["extra_json"] = None
                row["extra_json_hex_raw"] = extra_hex
        else:
            row["extra_json"] = None
        rows.append(row)
    return rows


def fetch_rt(start: datetime, end: datetime) -> list[dict]:
    rows: list[dict] = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=1), end)
        expected = _count(
            "kalshi_round_trips",
            f"strategy='kalshi_weather_arb' "
            f"AND resolved_ts >= '{cur.isoformat()}' "
            f"AND resolved_ts <  '{nxt.isoformat()}'"
        )
        fetched = _fetch_rt_window(cur, nxt)
        if fetched is None or len(fetched) != expected:
            print(f"  day {cur.date()}: expected {expected}, got {len(fetched) if fetched else 'malformed'} — sub-paginating to 6h")
            sub_cur = cur
            while sub_cur < nxt:
                sub_end = min(sub_cur + timedelta(hours=6), nxt)
                sub_expected = _count(
                    "kalshi_round_trips",
                    f"strategy='kalshi_weather_arb' "
                    f"AND resolved_ts >= '{sub_cur.isoformat()}' "
                    f"AND resolved_ts <  '{sub_end.isoformat()}'"
                )
                sub_fetched = _fetch_rt_window(sub_cur, sub_end)
                if sub_fetched is None or len(sub_fetched) != sub_expected:
                    raise RuntimeError(f"RT truncation at 6h: {sub_cur.isoformat()}")
                rows.extend(sub_fetched)
                sub_cur = sub_end
        else:
            rows.extend(fetched)
        print(f"  {cur.date()}: cumulative {len(rows)} RT rows")
        cur = nxt
    return rows


def main() -> int:
    print(f"Fetching WHP rows for {WINDOW_START.date()} -> {WINDOW_END.date()}...")
    whp_rows = fetch_whp(WINDOW_START, WINDOW_END)
    print(f"\nFetching RT rows for {WINDOW_START.date()} -> {WINDOW_END.date()}...")
    rt_rows = fetch_rt(WINDOW_START, WINDOW_END)

    whp_path = TMP_DIR / "kw_corpus_whp.jsonl"
    rt_path = TMP_DIR / "kw_corpus_rt.jsonl"
    with whp_path.open("w", encoding="utf-8") as f:
        for r in whp_rows:
            f.write(json.dumps(r) + "\n")
    with rt_path.open("w", encoding="utf-8") as f:
        for r in rt_rows:
            f.write(json.dumps(r) + "\n")

    print(f"\nWrote {len(whp_rows)} WHP rows -> {whp_path}")
    print(f"Wrote {len(rt_rows)} RT rows -> {rt_path}")

    # Final validation against expected (per prior session's prod probe)
    expected_whp, expected_rt = 636, 556
    ok = True
    if len(whp_rows) != expected_whp:
        print(f"WARN: expected {expected_whp} WHP rows, got {len(whp_rows)}")
        ok = False
    if len(rt_rows) != expected_rt:
        print(f"WARN: expected {expected_rt} RT rows, got {len(rt_rows)}")
        ok = False
    if whp_rows:
        s = json.dumps(whp_rows[0])
        print(f"\nFirst WHP row sample (len={len(s)}): {s[:400]}...")
    if rt_rows:
        s = json.dumps(rt_rows[0])
        print(f"First RT row sample (len={len(s)}): {s[:400]}...")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
