"""One-shot diagnostic: probe kalshi broker against past-expiration stuck
tickers + a known-recent freshly-resolved ticker as positive control.

Used to debug whether kalshi_llm_arbitrage's stuck-pending backlog is
caused by (a) bad ticker format / delisted markets, (b) broker in stub
mode, or (c) `get_market_resolution` semantics misclassifying settled
markets as pending.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys

sys.path.insert(0, "/home/azureuser/trading_corp")

from trading_corp.brokers.kalshi import KalshiBroker
from trading_corp.utils.secrets import load_secrets


async def main():
    conn = sqlite3.connect("/home/azureuser/trading_corp/data/trading_corp.db")
    stuck = conn.execute("""
        SELECT
          json_extract(a.payload_json,'$.ticker') AS ticker,
          json_extract(a.payload_json,'$.expires_at') AS expires_at,
          json_extract(a.payload_json,'$.category') AS category,
          datetime(a.ts) AS placed
        FROM audit_event a
        LEFT JOIN kalshi_round_trips r ON r.order_id = json_extract(a.payload_json, '$.order_id')
        WHERE a.actor = 'kalshi_llm_arbitrage' AND a.kind = 'would_have_placed'
          AND COALESCE(json_extract(a.payload_json,'$.side'),'buy') = 'buy'
          AND r.order_id IS NULL
          AND json_extract(a.payload_json,'$.expires_at') IS NOT NULL
          AND json_extract(a.payload_json,'$.expires_at') < datetime('now','-1 day')
        ORDER BY json_extract(a.payload_json,'$.expires_at') ASC
        LIMIT 5;
    """).fetchall()
    # Positive controls: a recently-resolved kalshi_round_trip + a recent
    # kalshi_llm row that DID resolve.
    pos = conn.execute("""
        SELECT ticker, market_result, resolved_ts
        FROM kalshi_round_trips
        WHERE division IN ('kalshi_llm_arbitrage','kalshi_crypto')
          AND market_result IN ('yes','no')
        ORDER BY resolved_ts DESC LIMIT 3;
    """).fetchall()
    conn.close()

    secrets = load_secrets()
    print(f"secrets.kalshi_api_key_id is None? {secrets.kalshi_api_key_id is None}")
    print(f"secrets.kalshi_private_key_pem is None? {secrets.kalshi_private_key_pem is None}")
    broker = KalshiBroker(
        api_key_id=secrets.kalshi_api_key_id,
        private_key_pem=secrets.kalshi_private_key_pem,
        demo=False,
    )
    print(f"broker._stub = {broker._stub}")
    try:
        await broker.connect()
        print(f"broker.connect() succeeded; _connected={broker._connected}")
    except Exception as e:
        print(f"broker.connect() failed: {type(e).__name__}: {e}")
        return

    print()
    print("=== POSITIVE CONTROLS (known-resolved kalshi_round_trip tickers) ===")
    for ticker, market_result, resolved_ts in pos:
        try:
            res = await broker.get_market_resolution(ticker)
            print(
                f"  {ticker:50s} kalshi_round_trip says result={market_result!r} resolved_ts={resolved_ts}; "
                f"broker says status={res.get('status')!r} result={res.get('result')!r}"
            )
        except Exception as e:
            print(f"  {ticker:50s} ERROR: {type(e).__name__}: {e}")

    print()
    print("=== STUCK PAST-EXPIRATION TICKERS ===")
    for ticker, expires_at, category, placed in stuck:
        try:
            res = await broker.get_market_resolution(ticker)
            print(
                f"  {ticker:50s} placed={placed} exp={expires_at} cat={category!r} → "
                f"status={res.get('status')!r} result={res.get('result')!r}"
            )
        except Exception as e:
            print(f"  {ticker:50s} ERROR: {type(e).__name__}: {e}")

    # Also try the raw pykalshi call for one stuck ticker to see WHY get_market fails.
    if stuck:
        sample_ticker = stuck[0][0]
        print()
        print(f"=== RAW pykalshi get_market({sample_ticker!r}) ===")
        try:
            m = await broker._client.get_market(sample_ticker)
            print(f"  raw market object: {m!r}")
            print(f"  raw market.result: {getattr(m, 'result', '<MISSING>')!r}")
            print(f"  raw market.status: {getattr(m, 'status', '<MISSING>')!r}")
            print(f"  raw market.close_time: {getattr(m, 'close_time', '<MISSING>')!r}")
        except Exception as e:
            print(f"  pykalshi.get_market raised: {type(e).__name__}: {e}")

    try:
        await broker.close()
    except Exception:
        pass


asyncio.run(main())
