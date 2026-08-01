"""One-shot backfill: book settled post-epoch kalshi_llm_arbitrage
`would_have_placed` audit rows as Option-A round-trips in
`kalshi_round_trips`, using the resolver's tested logic so order_ids match
and the live resolver later IGNOREs them (INSERT OR IGNORE on order_id).

Default mode is DRY-RUN (prints candidates + resolutions + computed pnl,
writes nothing).  Pass --apply to actually insert.

Usage:
    KEY_VAULT_URI=<uri> /home/azureuser/trading_corp/venv/bin/python \\
        scripts/backfill_kalshi_llm_settled.py [--apply]

Expected today (2026-08-01): 3 distinct tickers settle —
  CPI  (result no,  won 1) x1 emission
  SARB (result no,  won 1) x7 emissions
  BoK  (result yes, won 0) x2 emissions
= 10 Option-A rows (one per emission, per the resolver's behaviour).

Idempotent: `_insert_round_trip` uses INSERT OR IGNORE keyed on order_id,
so re-running after --apply is safe.

Do NOT hardcode the 10 tickers — let `broker.get_market_resolution` decide.
Only emissions whose ticker resolves to status 'resolved' or 'void' are
booked; pending/not_found are skipped (dry-run prints them too).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict

sys.path.insert(0, "/home/azureuser/trading_corp")

from trading_corp.utils.secrets import load_secrets
from trading_corp.brokers.kalshi import KalshiBroker
from trading_corp.agents.kalshi_resolver import (
    _compute_round_trip_row,
    _insert_round_trip,
)
from trading_corp.persistence import db as _db

# Epoch consistent with DASHBOARD_RT_CUTOFFS['kalshi_llm_arbitrage'] and the
# epoch clause in _fetch_unresolved_orders for 'kalshi_llm_arbitrage'.
_LLM_EPOCH = "2026-07-07T16:40:00+00:00"
_LLM_ACTOR = "kalshi_llm_arbitrage"


def _fetch_unresolved_llm(db_url: str) -> list[dict]:
    """Mirror _fetch_unresolved_orders's WHERE clause for kalshi_llm_arbitrage.

    Returns post-epoch 'would_have_placed' BUY rows with no round-trip yet
    and not already linked as an entry leg.  The resolver's per-actor
    LIMIT/ordering is intentionally omitted here — we want the FULL set for
    a one-shot backfill review, not just the top-50.
    """
    with _db.connect(db_url) as conn:
        cur = conn.execute(
            "SELECT a.ts AS ts, a.actor AS actor, a.payload_json "
            "FROM audit_event a "
            "LEFT JOIN kalshi_round_trips r "
            "  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            "WHERE a.actor = ? "
            "  AND a.kind IN ('would_have_placed', 'kalshi_copy_placed_live') "
            "  AND COALESCE(json_extract(a.payload_json, '$.side'), 'buy') = 'buy' "
            "  AND a.ts >= ? "
            "  AND r.order_id IS NULL "
            "  AND json_extract(a.payload_json, '$.order_id') NOT IN ("
            "        SELECT entry_order_id FROM kalshi_round_trips "
            "        WHERE entry_order_id IS NOT NULL"
            "      ) "
            "ORDER BY a.ts ASC",
            (_LLM_ACTOR, _LLM_EPOCH),
        )
        rows: list[dict] = []
        for r in cur.fetchall():
            try:
                p = json.loads(r["payload_json"])
                p["_ts"] = r["ts"]
                p["_actor"] = r["actor"]
                rows.append(p)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
    return rows


async def main(db_url: str, *, apply: bool) -> None:
    secrets = load_secrets()
    broker = KalshiBroker(
        api_key_id=secrets.kalshi_api_key_id,
        private_key_pem=secrets.kalshi_private_key_pem,
        demo=False,
    )
    await broker.connect()
    print(f"broker: stub={broker._stub} connected={broker._connected}")

    candidates = _fetch_unresolved_llm(db_url)
    print(f"\nFound {len(candidates)} unresolved post-epoch kalshi_llm_arbitrage "
          f"BUY rows (no round-trip yet).\n")

    would_book: list[dict] = []
    skipped_pending: list[str] = []
    skipped_bad: list[str] = []

    for row in candidates:
        ticker = row.get("ticker") or ""
        order_id = row.get("order_id") or ""
        res = await broker.get_market_resolution(ticker)
        status = res.get("status", "")
        result = res.get("result")

        if status not in ("resolved", "void"):
            skipped_pending.append(f"  SKIP [{status:>12}] {ticker!r:45s}  order_id={order_id!r}")
            continue

        rt = _compute_round_trip_row(row, res)
        if rt is None:
            skipped_bad.append(f"  SKIP [bad_payload] {ticker!r:45s}  order_id={order_id!r}")
            continue

        sign = "+" if rt["realized_pnl"] >= 0 else ""
        won_str = "WIN " if rt["won"] else "LOSS"
        print(
            f"  BOOK [{status:>8} / {result or 'void':>3}] {won_str} "
            f"pnl={sign}{rt['realized_pnl']:.4f}  ticker={ticker!r}  order_id={order_id!r}"
        )
        would_book.append(rt)

    if skipped_pending:
        print(f"\n--- Skipped (not yet settled / not_found): {len(skipped_pending)} ---")
        for s in skipped_pending:
            print(s)
    if skipped_bad:
        print(f"\n--- Skipped (bad payload / uncomputable): {len(skipped_bad)} ---")
        for s in skipped_bad:
            print(s)

    # Summary grouped by ticker.
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for rt in would_book:
        by_ticker[rt["ticker"]].append(rt)

    print(f"\n{'='*60}")
    print(f"DRY-RUN summary — would book {len(would_book)} rows "
          f"across {len(by_ticker)} distinct tickers:")
    for ticker, rts in sorted(by_ticker.items()):
        ex = rts[0]
        total_pnl = sum(r["realized_pnl"] for r in rts)
        print(
            f"  {ticker:50s}  market_result={ex.get('market_result','?'):>4}  "
            f"n_emissions={len(rts)}  total_pnl={total_pnl:+.4f}"
        )

    if not apply:
        print(f"\n[DRY-RUN] Nothing written. Pass --apply to insert.")
    else:
        inserted = 0
        ignored = 0
        for rt in would_book:
            ok = _insert_round_trip(db_url, rt)
            if ok:
                inserted += 1
            else:
                ignored += 1
        print(f"\n[APPLY] Inserted {inserted} rows, ignored {ignored} (already present).")

    try:
        await broker.close()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually insert rows (default: dry-run only).",
    )
    args = parser.parse_args()

    import os
    db_url = os.environ.get("DATABASE_URL", "sqlite:////home/azureuser/trading_corp/data/trading_corp.db")
    asyncio.run(main(db_url, apply=args.apply))
