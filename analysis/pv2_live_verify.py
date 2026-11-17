"""pv2_live_verify.py -- READ-ONLY live Data API v2 verification, box scratch only.

Run:  cd /home/azureuser/pv2_scratch && PYTHONPATH=. /home/azureuser/trading_corp/venv/bin/python pv2_live_verify.py

What it does (and does NOT do):
  - Public unauthenticated GETs to data-api.polymarket.com/v2 via the NEW client in this
    scratch tree. Throttled (max_concurrent=2 + explicit sleeps). Declared budget ~<=90
    calls; a PolymarketRateLimitError aborts with partial results.
  - Reads prediction_markets.db STRICTLY mode=ro (stored v1-era rows + attachments).
  - NO Kalshi calls, NO order path, NO arm reads, NO writes anywhere, NO engine touch.
  - The full arm-gated evaluate (Kalshi matching + gates) is NOT driven here: it needs the
    engine's authenticated Kalshi context/service env, which scratch does not have and we
    do not improvise. The matcher layer is proven offline by the test suite instead.

Parts:
  A. per-wallet FULL BOOK via fetch_positions_book (complete? pages? n?)
  B. CLOSED value-parity: v2 rows vs stored v1-era pm_closed_position rows, joined on
     (wallet, condition_id, outcome_index): avg_price / total_bought(<-total_size) /
     realized_pnl / cur_price / outcome / resolved_ts delta. Includes the partial-sale
     signal: any |v2.total_size - stored.total_bought| mismatch is listed.
  C. OPEN partial-sale identity: rows with realized_pnl != 0 must still satisfy
     pnl == current_value - initial_value (unrealized only).
  D. activity sanity: one wallet, 1 page: ts in seconds range, BUY/SELL mix.
  E. SIGNAL-SIDE DRY-RUN: per (account, category): entry signals the live loop would
     produce from these books via positions_to_entry_signals (counts + samples).
  F. leaderboard: category=Sports, 1 page of 5 (v2 category param works).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time

from trading_corp.data.polymarket_data_api_client import (
    PolymarketDataAPIClient, PolymarketRateLimitError,
)
from trading_corp.prediction_markets import live_driver as ld

PM_DB = "/home/azureuser/trading_corp/data/prediction_markets.db"
ITF_WHALE = "0x746295855be6f49fe2da2accd69ea34b3cf23ac3"
PARITY_WALLETS_MAX = 3
CLOSED_PAGES_PER_WALLET = 4      # x50 = up to 200 closed rows per parity wallet
SLEEP = 0.5


def ro(path):
    c = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    c.row_factory = sqlite3.Row
    return c


def attachments():
    with ro(PM_DB) as c:
        rows = c.execute(
            "SELECT account_id, category, wallet FROM pm_subdivision_attachment "
            "WHERE active=1 ORDER BY account_id, category, wallet").fetchall()
    return [(r["account_id"], r["category"], (r["wallet"] or "").lower()) for r in rows]


def stored_closed(wallet):
    with ro(PM_DB) as c:
        rows = c.execute(
            "SELECT condition_id, outcome_index, outcome, avg_price, total_bought, "
            "realized_pnl, cur_price, resolved_ts FROM pm_closed_position WHERE wallet=?",
            (wallet,)).fetchall()
    return {(r["condition_id"], int(r["outcome_index"] or 0)): r for r in rows}


def near(a, b, tol):
    try:
        return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))
    except (TypeError, ValueError):
        return False


async def main():
    att = attachments()
    wallets = sorted({w for _a, _c, w in att if w})
    if ITF_WHALE not in wallets:
        wallets.append(ITF_WHALE)
    print("attached (account,category) pairs=%d distinct_wallets=%d" % (len(att), len(wallets)))
    budget = {"calls": 0}
    books = {}
    summary = {"book": [], "parity": {}, "open_partial": [], "activity": {},
               "signals": {}, "leaderboard": {}, "errors": []}

    async with PolymarketDataAPIClient(max_concurrent=2) as client:
        # -- A. full books -------------------------------------------------------------
        for w in wallets:
            try:
                book = await client.fetch_positions_book(w)
                budget["calls"] += book.pages
                books[w] = book
                summary["book"].append(
                    {"wallet": w[:10], "complete": book.complete, "pages": book.pages, "n": book.n})
            except PolymarketRateLimitError as e:
                summary["errors"].append("RATE LIMITED at book %s: %s" % (w[:10], e))
                print(json.dumps(summary, indent=1, default=str)); return
            except Exception as e:  # noqa: BLE001 -- report and continue (per-wallet isolation)
                summary["errors"].append("book %s: %r" % (w[:10], e))
            await asyncio.sleep(SLEEP)

        # -- B. closed value-parity ----------------------------------------------------
        parity_wallets = []
        with ro(PM_DB) as c:
            for r in c.execute(
                    "SELECT wallet, COUNT(*) n FROM pm_closed_position GROUP BY wallet "
                    "ORDER BY n DESC").fetchall():
                if (r["wallet"] or "").lower() in wallets:
                    parity_wallets.append((r["wallet"].lower(), r["n"]))
                if len(parity_wallets) >= PARITY_WALLETS_MAX:
                    break
        for w, n_stored in parity_wallets:
            stored = stored_closed(w)
            got = []
            for page in range(CLOSED_PAGES_PER_WALLET):
                try:
                    rows = await client.fetch_closed_positions(w, limit=50, offset=page * 50)
                except PolymarketRateLimitError as e:
                    summary["errors"].append("RATE LIMITED at closed %s: %s" % (w[:10], e)); break
                budget["calls"] += 1
                got.extend(rows)
                if len(rows) < 50:
                    break
                await asyncio.sleep(SLEEP)
            matched = eq = 0
            mism = []
            ts_deltas = []
            for cp in got:
                key = (cp.condition_id, cp.outcome_index)
                st = stored.get(key)
                if st is None:
                    continue
                matched += 1
                ok = (near(cp.avg_price, st["avg_price"], 1e-3)
                      and near(cp.total_bought, st["total_bought"], 1e-3)
                      and near(cp.realized_pnl, st["realized_pnl"], 1e-3)
                      and near(cp.cur_price, st["cur_price"], 1e-2)
                      and (cp.outcome or "") == (st["outcome"] or ""))
                if ok:
                    eq += 1
                elif len(mism) < 5:
                    mism.append({"cid": cp.condition_id[:14], "v2_tb": cp.total_bought,
                                 "st_tb": st["total_bought"], "v2_rp": cp.realized_pnl,
                                 "st_rp": st["realized_pnl"], "v2_avg": cp.avg_price,
                                 "st_avg": st["avg_price"], "v2_out": cp.outcome,
                                 "st_out": st["outcome"]})
                try:
                    ts_deltas.append(int(cp.timestamp) - int(st["resolved_ts"] or 0))
                except (TypeError, ValueError):
                    pass
            ts_deltas.sort()
            summary["parity"][w[:10]] = {
                "stored_rows": n_stored, "v2_rows_pulled": len(got), "joined": matched,
                "value_equal": eq, "mismatches": mism,
                "resolved_ts_delta_median": (ts_deltas[len(ts_deltas) // 2] if ts_deltas else None),
                "resolved_ts_delta_max_abs": (max(abs(d) for d in ts_deltas) if ts_deltas else None)}

        # -- C. open partial-sale identity --------------------------------------------
        for w, book in books.items():
            for p in book.rows:
                rp = p.extra.get("realized_pnl")
                try:
                    rp = float(rp) if rp is not None else 0.0
                except (TypeError, ValueError):
                    rp = 0.0
                if abs(rp) > 0.01:
                    ident = abs(p.pnl - (p.current_value - p.initial_value)) <= 0.05
                    summary["open_partial"].append(
                        {"wallet": w[:10], "cid": p.condition_id[:14], "realized": rp,
                         "unrealized_identity_holds": ident})

        # -- D. activity sanity --------------------------------------------------------
        try:
            acts = await client.fetch_activity(wallets[0], limit=100)
            budget["calls"] += 1
            secs_ok = all(1_500_000_000 < a.timestamp < 2_200_000_000 for a in acts if a.timestamp)
            summary["activity"] = {
                "wallet": wallets[0][:10], "rows": len(acts),
                "timestamps_in_seconds_range": secs_ok,
                "sides": sorted({a.side for a in acts}),
                "types": sorted({a.type for a in acts})}
        except Exception as e:  # noqa: BLE001
            summary["errors"].append("activity: %r" % (e,))

        # -- E. signal-side dry-run per (account, category) ---------------------------
        for acct, cat, w in att:
            book = books.get(w)
            if book is None or not book.complete:
                continue
            sigs = ld.positions_to_entry_signals(book.rows, w)
            k = "%s/%s" % (acct, cat)
            cur = summary["signals"].setdefault(k, {"n": 0, "sample": []})
            cur["n"] += len(sigs)
            for s in sigs[:2]:
                if len(cur["sample"]) < 3:
                    cur["sample"].append("%s|%s|idx%d" % (s.slug, s.outcome, s.outcome_index))

        # -- F. leaderboard ------------------------------------------------------------
        try:
            lead = await client.fetch_leaderboard(category="Sports", limit=5)
            budget["calls"] += 1
            summary["leaderboard"] = {"rows": len(lead),
                                      "first": (lead[0].proxy_wallet[:10] if lead else None),
                                      "vol_positive": all(e.vol >= 0 for e in lead)}
        except Exception as e:  # noqa: BLE001
            summary["errors"].append("leaderboard: %r" % (e,))

    summary["call_budget_used"] = budget["calls"]
    nonzero = {k: v for k, v in summary["signals"].items() if v["n"] > 0}
    summary["signals_nonzero"] = nonzero
    summary["signals_total_pairs"] = len(summary["signals"])
    summary["signals"] = {k: summary["signals"][k] for k in list(summary["signals"])[:8]}
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    t0 = time.time()
    asyncio.run(main())
    print("done in %.0fs" % (time.time() - t0))
