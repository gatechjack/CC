#!/usr/bin/env python3
"""Read-only 4-point verify for the next (latest) bitunix_futures LIVE entry.

Run this once when a new futures entry fires. It checks, per the last N live
entries, the four invariants that the futures FIX-1/3/4 work established:

  1. qty == venue fill      DB bracket_entry_qty vs the actual venue entry fill
                            (needs a GET-only venue pull; see --no-venue).
  2. rejected-leg folds fwd  if a TP leg was rejected, its size must fold into the
                            remaining legs -> uncovered_qty == 0 (no silent gap).
  3. no loss-with-+pnl       a row labelled result='loss' must not carry a positive
                            actual_pnl_dollars (net/gross sign consistency).
  4. SL full-size at entry   a full-size structural stop is attached at entry
                            (position_sl_order_id present, no sl_failed) -> downside
                            never naked.

STRICTLY READ-ONLY. DB access is sqlite `mode=ro`. The optional venue leg is
GET-ONLY: it reuses the sanctioned venue_audit_ro pattern (construct BitunixBroker,
call ONLY get_pending_positions / get_history_trades, monkeypatch every write to
raise). Nothing here places, cancels, or modifies anything.

Data model (verified 2026-08-02):
  paper_trade_record(division='bitunix_futures', execution_mode='live'): order_id,
    ts, side, qty, result, actual_pnl_dollars, extra_json{bracket_entry_qty,
    bracket_placed_qty_by_leg, bracket_uncovered_qty, bracket_position_sl_order_id,
    current_sl, bracket_legs, bracket_degrade_note}
  audit_event(actor='bitunix_futures'): bracket_placed / bracket_tp_leg_failed /
    bracket_position_sl_failed / bracket_coverage_gap_alert  (ts, kind, payload_json)

Usage (on a host with the prod DB; operator runs the venue leg under the service env):
  python scripts/bitunix_futures_next_entry_verify.py            # last 1 entry, venue check on
  python scripts/bitunix_futures_next_entry_verify.py --last 5   # last 5 entries
  python scripts/bitunix_futures_next_entry_verify.py --no-venue # DB-only (runs anywhere)
  python scripts/bitunix_futures_next_entry_verify.py --db data/trading_corp.db --no-venue
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone

DEF_DB = "/home/azureuser/trading_corp/data/trading_corp.db"
DIVISION = "bitunix_futures"
SYMBOL = "BTC/USDT.P"
QTY_TOL = 0.02        # 2% relative tolerance for qty==venue (memory: lot-floor ~6% gap)

GREEN, YEL, RED = "PASS", "WARN", "FAIL"


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _to_ms(iso):
    try:
        return int(datetime.fromisoformat(str(iso)).replace(tzinfo=timezone.utc).timestamp() * 1000)
    except Exception:
        return 0


def load_entries(db: str, last: int, since: str | None):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    where = "division=? AND execution_mode='live'"
    args: list = [DIVISION]
    if since:
        where += " AND ts>=?"
        args.append(since)
    rows = con.execute(
        f"SELECT order_id, ts, side, qty, result, actual_pnl_dollars, extra_json "
        f"FROM paper_trade_record WHERE {where} ORDER BY ts DESC LIMIT ?",
        (*args, last),
    ).fetchall()
    con.close()
    return [dict(r) for r in reversed(rows)]     # chronological


def load_audits(db: str, start_iso: str, end_iso: str | None):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = ("SELECT ts, kind, payload_json FROM audit_event WHERE actor=? AND ts>=?"
         + (" AND ts<?" if end_iso else ""))
    args = [DIVISION, start_iso] + ([end_iso] if end_iso else [])
    rows = con.execute(q, args).fetchall()
    con.close()
    out = []
    for r in rows:
        try:
            out.append((r["kind"], json.loads(r["payload_json"] or "{}")))
        except Exception:
            out.append((r["kind"], {}))
    return out


def check_db(entry: dict, audits: list) -> list[tuple[str, str, str]]:
    """Return [(point, verdict, evidence)] for checks 2, 3, 4 (DB-only)."""
    extra = {}
    try:
        extra = json.loads(entry.get("extra_json") or "{}")
    except Exception:
        pass
    oid = entry["order_id"]
    a_for = [(k, p) for (k, p) in audits if p.get("order_id") in (None, oid) or k.startswith("bracket_")]
    leg_failed = [p for (k, p) in a_for if k == "bracket_tp_leg_failed"]
    sl_failed = [p for (k, p) in a_for if k == "bracket_position_sl_failed"]

    res = []

    # 2 — rejected-leg folds forward
    uncovered = _f(extra.get("bracket_uncovered_qty", 0.0))
    entry_qty = _f(extra.get("bracket_entry_qty", 0.0))
    by_leg = extra.get("bracket_placed_qty_by_leg") or []
    placed = sum(_f(x) for x in by_leg) if isinstance(by_leg, list) else 0.0
    note = extra.get("bracket_degrade_note")
    if uncovered > 0:
        res.append(("2 folds-fwd", YEL,
                    f"uncovered_qty={uncovered:.8f} (>0 = TP coverage gap; downside still SL-covered, "
                    f"missed PROFIT not risk); leg_failed={len(leg_failed)} note={note}"))
    elif leg_failed:
        res.append(("2 folds-fwd", GREEN,
                    f"leg_failed={len(leg_failed)} but uncovered_qty=0 -> folded fwd; "
                    f"placed={placed:.8f}/entry={entry_qty:.8f}"))
    else:
        res.append(("2 folds-fwd", GREEN,
                    f"no leg rejection; placed={placed:.8f}/entry={entry_qty:.8f} uncovered=0"))

    # 3 — no loss-with-+pnl
    result = (entry.get("result") or "").lower()
    pnl = entry.get("actual_pnl_dollars")
    if result == "loss" and pnl is not None and _f(pnl) > 0:
        res.append(("3 loss!=+pnl", RED,
                    f"result=loss BUT actual_pnl_dollars={_f(pnl):+.4f} (>0). Likely the known "
                    f"gross(actual_pnl)/net(classify_result) sign split on tiny 25x trades — confirm."))
    else:
        res.append(("3 loss!=+pnl", GREEN, f"result={result or '-'} pnl={pnl}"))

    # 4 — SL full-size at entry
    sl_id = extra.get("bracket_position_sl_order_id")
    cur_sl = _f(extra.get("current_sl", 0.0))
    if sl_failed:
        res.append(("4 SL full-size", RED,
                    f"bracket_position_sl_failed fired -> SL NOT attached; DOWNSIDE MAY BE NAKED"))
    elif sl_id and cur_sl > 0:
        res.append(("4 SL full-size", GREEN,
                    f"position SL id={str(sl_id)[:12]} @ {cur_sl} (full-size structural stop; "
                    f"venue auto-reduces as TPs fill)"))
    else:
        res.append(("4 SL full-size", YEL,
                    f"SL id={sl_id} current_sl={cur_sl} — SL not confirmed in DB extra; check "
                    f"bracket_placed audit / venue pending SL"))
    return res


async def venue_qty(entries: list, db: str):
    """GET-only venue entry-fill qty per entry, attributed by [ts,next_ts)+side.
    Returns {order_id: venue_entry_qty} or None if creds/env unavailable."""
    sys.path.insert(0, "/home/azureuser/trading_corp")
    try:
        from trading_corp.utils.secrets import load_secrets
        from trading_corp.brokers.bitunix import BitunixBroker
    except Exception as e:
        print(f"  venue: import failed ({e}); run on the prod host. Skipping venue check.")
        return None
    sec = load_secrets()
    if not (getattr(sec, "bitunix_futures_api_key", None) and getattr(sec, "bitunix_futures_api_secret", None)):
        print("  venue: no Bitunix creds (run under the service env). Skipping venue check.")
        return None
    broker = BitunixBroker(api_key=sec.bitunix_futures_api_key, api_secret=sec.bitunix_futures_api_secret)
    for name in ("place_order", "place_tpsl_order", "place_position_tpsl",
                 "place_resting_reduce_only_limit", "cancel_order", "cancel_all_orders",
                 "flash_close_position", "close_all_position", "modify_position_sl",
                 "modify_position_tp_sl_order", "change_position_mode", "change_leverage",
                 "_observe_fill"):
        if hasattr(broker, name):
            def _blocked(*a, _n=name, **k):
                raise RuntimeError(f"read-only verify BLOCKED write method: {_n}")
            setattr(broker, name, _blocked)
    await broker.connect()
    try:
        fills = await broker.get_history_trades(symbol=SYMBOL)
    finally:
        await broker.disconnect()

    def ct(f): return _f(f.get("ctime") or f.get("time") or f.get("ts") or f.get("createTime"))
    def side(f): return str(f.get("side") or f.get("tradeSide") or "").upper()
    def q(f): return _f(f.get("qty") or f.get("tradeQty"))
    ts = [_to_ms(e["ts"]) for e in entries]
    out = {}
    for i, e in enumerate(entries):
        s = ts[i]
        nxt = ts[i + 1] if i + 1 < len(ts) else 10 ** 15
        win_entry = [f for f in fills if s <= ct(f) < nxt and side(f) == "SELL"]  # SELL = short entry
        out[e["order_id"]] = sum(q(f) for f in win_entry)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEF_DB)
    ap.add_argument("--last", type=int, default=1)
    ap.add_argument("--since", default=None, help="ISO date lower bound (optional)")
    ap.add_argument("--no-venue", action="store_true", help="DB-only (skip GET venue pull)")
    args = ap.parse_args()

    entries = load_entries(args.db, args.last, args.since)
    if not entries:
        print("no live bitunix_futures entries found.")
        return 0

    venue = None
    if not args.no_venue:
        import asyncio
        try:
            venue = asyncio.run(venue_qty(entries, args.db))
        except Exception as e:
            print(f"  venue pull error ({e}); continuing DB-only.")

    worst = GREEN
    rank = {GREEN: 0, YEL: 1, RED: 2}
    for e in entries:
        nxt = None
        idx = entries.index(e)
        if idx + 1 < len(entries):
            nxt = entries[idx + 1]["ts"]
        audits = load_audits(args.db, e["ts"], nxt)
        print(f"\n=== entry {e['order_id'][:8]}  {e['ts']}  {e['side']}  qty={_f(e['qty']):.8f} ===")

        rows = check_db(e, audits)

        # 1 — qty == venue fill
        extra = json.loads(e.get("extra_json") or "{}") if e.get("extra_json") else {}
        eq = _f(extra.get("bracket_entry_qty", e.get("qty")))
        if venue is not None and e["order_id"] in venue:
            vq = venue[e["order_id"]]
            if vq <= 0:
                rows.insert(0, ("1 qty==venue", YEL, f"no venue entry fill in window (history depth?) DBqty={eq:.8f}"))
            elif abs(eq - vq) <= max(1e-9, QTY_TOL * eq):
                rows.insert(0, ("1 qty==venue", GREEN, f"DBqty={eq:.8f} venue={vq:.8f} (<= {QTY_TOL:.0%})"))
            else:
                rows.insert(0, ("1 qty==venue", YEL,
                                f"DBqty={eq:.8f} venue={vq:.8f} delta={eq - vq:+.8f} "
                                f"(expected if venue lot-floors; reconcile bracket_entry_qty->fill)"))
        else:
            rows.insert(0, ("1 qty==venue", YEL, f"venue check skipped; DBqty={eq:.8f}"))

        for pt, verdict, ev in rows:
            print(f"  [{verdict}] {pt:16} {ev}")
            worst = verdict if rank[verdict] > rank[worst] else worst

    print(f"\nVERDICT: {worst}  (PASS=all invariants held, WARN=review, FAIL=invariant broken)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
