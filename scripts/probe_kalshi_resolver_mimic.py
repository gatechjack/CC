"""Mimic the resolver's exact scan: pull the 50 oldest LLM unresolved rows
(by audit_event ts ASC, side='buy'), call broker.get_market_resolution on
each, tally the status distribution.

If this probe reports 50 resolved but the running resolver loop logs
50 pending, that's a smoking gun for a broker-init or live-state bug.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, "/home/azureuser/trading_corp")

from trading_corp.brokers.kalshi import KalshiBroker
from trading_corp.utils.secrets import load_secrets


async def main():
    conn = sqlite3.connect("/home/azureuser/trading_corp/data/trading_corp.db")
    rows = conn.execute("""
        SELECT
          a.ts AS ts,
          json_extract(a.payload_json,'$.order_id') AS order_id,
          json_extract(a.payload_json,'$.ticker') AS ticker,
          json_extract(a.payload_json,'$.expires_at') AS expires_at,
          json_extract(a.payload_json,'$.category') AS category,
          json_extract(a.payload_json,'$.qty') AS qty,
          json_extract(a.payload_json,'$.limit_price') AS limit_price,
          json_extract(a.payload_json,'$.outcome') AS outcome
        FROM audit_event a
        LEFT JOIN kalshi_round_trips r ON r.order_id = json_extract(a.payload_json, '$.order_id')
        WHERE a.actor = 'kalshi_llm_arbitrage' AND a.kind = 'would_have_placed'
          AND COALESCE(json_extract(a.payload_json,'$.side'),'buy') = 'buy'
          AND r.order_id IS NULL
          AND json_extract(a.payload_json,'$.order_id') NOT IN (
                SELECT entry_order_id FROM kalshi_round_trips WHERE entry_order_id IS NOT NULL
              )
        ORDER BY a.ts ASC LIMIT 50;
    """).fetchall()
    conn.close()

    secrets = load_secrets()
    broker = KalshiBroker(
        api_key_id=secrets.kalshi_api_key_id,
        private_key_pem=secrets.kalshi_private_key_pem,
        demo=False,
    )
    await broker.connect()
    print(f"broker._stub={broker._stub} _connected={broker._connected}")
    print(f"Probing {len(rows)} oldest LLM unresolved rows (by ts ASC)...")
    print()

    status_counts = Counter()
    pending_samples = []
    resolved_samples = []
    not_found_samples = []
    failed_compute_count = 0

    for ts, order_id, ticker, expires_at, category, qty, limit_price, outcome in rows:
        try:
            res = await broker.get_market_resolution(ticker)
        except Exception as e:
            status_counts["EXCEPTION"] += 1
            print(f"  ERR {ticker}: {type(e).__name__}: {e}")
            continue
        status = res.get("status")
        result = res.get("result")
        status_counts[status] += 1
        # Mimic the _compute_round_trip_row filter to see if rows would be dropped
        qty_f = float(qty or 0.0)
        price_f = float(limit_price or 0.0)
        side = (outcome or "").lower()
        compute_ok = (
            side in ("yes", "no") and qty_f > 0 and 0 < price_f < 1.0
        )
        if status == "resolved" and not compute_ok:
            failed_compute_count += 1
        sample_row = (ticker, ts, expires_at, category, status, result, qty_f, price_f, side, compute_ok)
        if status == "resolved" and len(resolved_samples) < 5:
            resolved_samples.append(sample_row)
        elif status == "pending" and len(pending_samples) < 5:
            pending_samples.append(sample_row)
        elif status == "not_found" and len(not_found_samples) < 5:
            not_found_samples.append(sample_row)

    print(f"STATUS DISTRIBUTION across {len(rows)} oldest LLM rows:")
    for status, n in sorted(status_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {status:15s} {n}")
    print(f"failed_compute_count (resolved but would be dropped by _compute filter): {failed_compute_count}")
    print()
    if resolved_samples:
        print("RESOLVED samples (would be inserted to kalshi_round_trips):")
        for r in resolved_samples:
            print(f"  {r}")
    if pending_samples:
        print("PENDING samples:")
        for r in pending_samples:
            print(f"  {r}")
    if not_found_samples:
        print("NOT_FOUND samples:")
        for r in not_found_samples:
            print(f"  {r}")

    try:
        await broker.close()
    except Exception:
        pass


asyncio.run(main())
