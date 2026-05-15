"""Backfill K3 round-trip exit prices using market resolution.

Pre-2026-05-14 K3 paired exits recorded `exit_price=$0` for every settled
market because broker.quote() returns 0 on resolved markets. This script
walks every `kalshi_round_trips` row for division='kalshi_copy_trading',
calls `KalshiBroker.get_market_resolution(ticker)`, and updates the row
with the correct exit_price + realized_pnl + won flag.

Idempotent: re-runs are safe. Rows that resolve to the same exit_price
on re-query produce identical UPDATEs.

Run on prod where the KalshiBroker has live credentials.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time

DB = "/home/azureuser/trading_corp/data/trading_corp.db"


async def main(dry_run: bool = False) -> None:
    sys.path.insert(0, "/home/azureuser/trading_corp")
    from trading_corp.brokers.kalshi import KalshiBroker
    from trading_corp.utils import secrets as _secrets

    secrets = _secrets.load_secrets()
    broker = KalshiBroker(
        api_key_id=secrets.kalshi_api_key_id,
        private_key_pem=secrets.kalshi_private_key_pem,
    )
    await broker.connect()

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    rows = list(db.execute("""
      SELECT id, order_id, ticker, outcome_bet, qty, entry_price, won,
             realized_pnl, extra_json
      FROM kalshi_round_trips
      WHERE division='kalshi_copy_trading'
        AND entry_price > 0
      ORDER BY entry_ts ASC
    """))

    print(f"Backfilling {len(rows)} K3 round-trips...")

    stats = {"resolved_win": 0, "resolved_loss": 0, "void": 0,
             "pending_skip": 0, "not_found_skip": 0, "no_change": 0, "errors": 0}
    delta_pnl = 0.0

    for i, r in enumerate(rows, 1):
        ticker = r["ticker"]
        try:
            res = await broker.get_market_resolution(ticker)
        except Exception as e:
            stats["errors"] += 1
            if i <= 10 or i % 25 == 0:
                print(f"  [{i:3}/{len(rows)}] {ticker[:30]:30} ERR {e}")
            continue

        status = res.get("status")
        winner = res.get("result")

        if status == "pending":
            stats["pending_skip"] += 1
            continue
        if status == "not_found":
            stats["not_found_skip"] += 1
            continue

        if status == "void":
            new_exit = float(r["entry_price"])
            new_pnl = 0.0
            new_won = None
            stats["void"] += 1
        elif status == "resolved":
            new_exit = 1.0 if winner == r["outcome_bet"] else 0.0
            new_pnl = float(r["qty"]) * (new_exit - float(r["entry_price"]))
            new_won = 1 if new_pnl > 0 else 0
            if new_won:
                stats["resolved_win"] += 1
            else:
                stats["resolved_loss"] += 1
        else:
            stats["errors"] += 1
            continue

        # Update extra_json's exit_price marker
        extra = json.loads(r["extra_json"] or "{}")
        prev_exit = extra.get("exit_price")
        extra["exit_price"] = new_exit
        extra["whale_exit_price"] = new_exit
        extra["backfill_source"] = "market_resolution"
        extra["backfill_ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        old_pnl = float(r["realized_pnl"] or 0.0)
        if abs(new_pnl - old_pnl) < 1e-6:
            stats["no_change"] += 1
        else:
            delta_pnl += (new_pnl - old_pnl)

        if not dry_run:
            db.execute("""
              UPDATE kalshi_round_trips
              SET realized_pnl = ?, won = ?,
                  market_result = ?, extra_json = ?
              WHERE id = ?
            """, (new_pnl, new_won, winner or status, json.dumps(extra), r["id"]))

        if i <= 5 or i % 25 == 0:
            wstr = "WIN" if new_won == 1 else ("LOSS" if new_won == 0 else "VOID")
            print(f"  [{i:3}/{len(rows)}] {ticker[:30]:30} bet={r['outcome_bet']:4} "
                  f"entry=${r['entry_price']:.3f} -> exit=${new_exit:.3f} "
                  f"old_pnl=${old_pnl:.2f} new_pnl=${new_pnl:.2f} {wstr}")

        # Gentle rate limit
        if i % 10 == 0:
            await asyncio.sleep(0.5)

    if not dry_run:
        db.commit()
    db.close()
    await broker.disconnect()

    print()
    print("=== BACKFILL SUMMARY ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"  total_delta_realized_pnl: ${delta_pnl:+.2f}")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    print(f"mode: {'DRY-RUN' if dry else 'WRITE'}")
    asyncio.run(main(dry_run=dry))
