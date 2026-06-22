#!/usr/bin/env python3
"""D1 backfill — correct the PnL/fee MAGNITUDE on records that were double-booked
by the pre-D1 netted-close auto-book (booked on the full netted close qty instead
of the record's own share).

  *** DRY-RUN BY DEFAULT — operator-gated, separate from the code deploy. ***
  *** DISTINCT from the P2 label-only record-correction (7d1a78dc/e1758fc9). ***

This corrects MAGNITUDE only (actual_pnl_dollars, exit_fee_usd, net_realized_usd,
actual_r_multiple). It scales the stored economics by the record's qty share of
the netted close — the exact inverse of the over-booking:

    q_close    = <full netted close qty the OLD booking used>   (audit "qty")
    closed_qty = min(record_qty, q_close)
    ratio      = closed_qty / q_close
    corrected_pnl      = old_pnl      * ratio
    corrected_exit_fee = old_exit_fee * ratio
    corrected_net      = corrected_pnl - entry_fee - corrected_exit_fee

It does NOT relabel result/exit_kind (that is P2's domain). If the corrected net
SIGN differs from the stored result, it FLAGS the record and leaves the label
untouched for the operator to decide.

Safety:
  - DRY-RUN unless --apply. Dry-run never opens the DB for writing.
  - SKIPS (no write) any record it cannot safely correct: no auto-book audit,
    already-D1-era booking (audit carries 'netted_close_qty'), ratio == 1
    (no over-attribution), or already backfilled (extra.d1_backfill_corrected).
  - --apply wraps all writes in ONE transaction and stamps an audit_event +
    idempotency marker (re-run is a no-op).

Usage:
  DRY-RUN (default, read-only):
    python d1_backfill_double_booked.py --db /home/azureuser/trading_corp/data/trading_corp.db
  APPLY (operator-gated; only after reviewing the dry-run):
    python d1_backfill_double_booked.py --db <path> --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone

DEFAULT_DB = "/home/azureuser/trading_corp/data/trading_corp.db"
DEFAULT_ORDER_IDS = ["125b6f9e", "81f5427a"]
AUTO_BOOK_KIND = "auto_book_server_side_close"
BACKFILL_ACTOR = "d1_backfill_double_booked"
BACKFILL_KIND = "d1_backfill_pnl_correction"
EPS = 1e-12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _find_record(conn, oid):
    """Match by exact order_id or by prefix (the field cases are short prefixes)."""
    row = conn.execute(
        "SELECT order_id, side, qty, entry_reference_price, actual_pnl_dollars, "
        "actual_r_multiple, result, extra_json FROM paper_trade_record "
        "WHERE order_id = ?", (oid,)).fetchone()
    if row is not None:
        return row
    return conn.execute(
        "SELECT order_id, side, qty, entry_reference_price, actual_pnl_dollars, "
        "actual_r_multiple, result, extra_json FROM paper_trade_record "
        "WHERE order_id LIKE ? ORDER BY ts DESC LIMIT 1", (oid + "%",)).fetchone()


def _autobook_payload(conn, order_id):
    """The most recent auto-book audit for this order_id (the OLD booking)."""
    rows = conn.execute(
        "SELECT payload_json FROM audit_event WHERE kind = ? ORDER BY id DESC",
        (AUTO_BOOK_KIND,)).fetchall()
    for r in rows:
        try:
            p = json.loads(r["payload_json"])
        except (TypeError, ValueError):
            continue
        if str(p.get("order_id", "")).startswith(order_id) or \
                p.get("order_id") == order_id:
            return p
    return None


def _approx_result(net):
    return "win" if net > 0 else "loss" if net < 0 else "scratch"


def assess(conn, oid):
    """Return a dict describing the proposed correction (or a skip reason)."""
    rec = _find_record(conn, oid)
    if rec is None:
        return {"oid": oid, "skip": "no paper_trade_record found"}
    full_oid = rec["order_id"]
    try:
        extra = json.loads(rec["extra_json"]) if rec["extra_json"] else {}
    except (TypeError, ValueError):
        extra = {}
    if extra.get("d1_backfill_corrected"):
        return {"oid": full_oid, "skip": "already backfilled (idempotent)"}

    audit = _autobook_payload(conn, oid)
    if audit is None:
        return {"oid": full_oid, "skip": "no auto_book_server_side_close audit"}
    if "netted_close_qty" in audit:
        return {"oid": full_oid,
                "skip": "D1-era booking (already qty-attributed) — no backfill"}

    q_close = _f(audit.get("qty"))            # OLD code wrote fqty (= full close)
    record_qty = _f(rec["qty"])
    if q_close <= 0 or record_qty <= 0:
        return {"oid": full_oid, "skip": f"bad qty (q_close={q_close} rec={record_qty})"}
    closed_qty = min(record_qty, q_close)
    ratio = closed_qty / q_close
    if abs(ratio - 1.0) <= 1e-9:
        return {"oid": full_oid,
                "skip": f"ratio==1 (record_qty {record_qty} >= close {q_close}); "
                        "no over-attribution"}

    old_pnl = _f(rec["actual_pnl_dollars"])
    old_exit_fee = _f(extra.get("exit_fee_usd"))
    entry_fee = _f(extra.get("entry_fee_usd"))
    mdr = _f(extra.get("max_dollar_risk"))

    corr_pnl = old_pnl * ratio
    corr_exit_fee = old_exit_fee * ratio
    corr_net = corr_pnl - entry_fee - corr_exit_fee
    corr_r = (corr_pnl / mdr) if mdr else None

    old_net = _f(extra.get("net_realized_usd"), old_pnl - entry_fee - old_exit_fee)
    sign_flip = (rec["result"] is not None
                 and _approx_result(corr_net) != _approx_result(old_net))

    return {
        "oid": full_oid, "side": rec["side"], "result": rec["result"],
        "record_qty": record_qty, "q_close": q_close, "closed_qty": closed_qty,
        "ratio": ratio,
        "old_pnl": old_pnl, "corr_pnl": corr_pnl,
        "old_exit_fee": old_exit_fee, "corr_exit_fee": corr_exit_fee,
        "old_net": old_net, "corr_net": corr_net, "corr_r": corr_r,
        "entry_fee": entry_fee, "sign_flip": sign_flip,
    }


def apply_one(conn, p, now):
    extra_patch = (
        "json_set(extra_json, "
        "'$.exit_fee_usd', ?, '$.net_realized_usd', ?, "
        "'$.d1_backfill_corrected', json('true'), "
        "'$.d1_backfill_ts', ?, '$.d1_backfill_ratio', ?, "
        "'$.d1_backfill_prev_pnl', ?, '$.d1_backfill_prev_exit_fee', ?)")
    conn.execute(
        f"UPDATE paper_trade_record SET actual_pnl_dollars = ?, "
        f"actual_r_multiple = ?, extra_json = {extra_patch} "
        f"WHERE order_id = ? AND "
        f"json_extract(extra_json, '$.d1_backfill_corrected') IS NULL",
        (p["corr_pnl"], p["corr_r"], p["corr_exit_fee"], p["corr_net"],
         now, p["ratio"], p["old_pnl"], p["old_exit_fee"], p["oid"]),
    )
    conn.execute(
        "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?,?,?,?)",
        (now, BACKFILL_ACTOR, BACKFILL_KIND, json.dumps({
            "order_id": p["oid"], "ratio": p["ratio"],
            "record_qty": p["record_qty"], "netted_close_qty": p["q_close"],
            "closed_qty": p["closed_qty"],
            "prev_pnl": p["old_pnl"], "corrected_pnl": p["corr_pnl"],
            "prev_exit_fee": p["old_exit_fee"], "corrected_exit_fee": p["corr_exit_fee"],
            "prev_net": p["old_net"], "corrected_net": p["corr_net"],
            "result_unchanged": p["result"], "sign_flip_flagged": p["sign_flip"],
        })),
    )


def main():
    ap = argparse.ArgumentParser(description="D1 PnL double-booking backfill (dry-run by default)")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--order-ids", default=",".join(DEFAULT_ORDER_IDS),
                    help="comma-separated order_id (prefixes ok)")
    ap.add_argument("--apply", action="store_true",
                    help="WRITE the corrections (default: dry-run, read-only)")
    args = ap.parse_args()
    oids = [s.strip() for s in args.order_ids.split(",") if s.strip()]

    mode = "APPLY (writing)" if args.apply else "DRY-RUN (read-only)"
    print(f"=== D1 backfill — {mode} ===")
    print(f"db={args.db}")
    print(f"order_ids={oids}\n")

    # dry-run opens read-only; apply opens read-write.
    if args.apply:
        conn = sqlite3.connect(args.db)
    else:
        conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    plans, skips = [], []
    for oid in oids:
        a = assess(conn, oid)
        (skips if "skip" in a else plans).append(a)

    for s in skips:
        print(f"SKIP  {s['oid']}: {s['skip']}")
    if skips:
        print()

    sum_old, sum_corr = 0.0, 0.0
    for p in plans:
        sum_old += p["old_pnl"]
        sum_corr += p["corr_pnl"]
        print(f"CORRECT {p['oid']}  side={p['side']}  result={p['result']}")
        print(f"   record_qty={p['record_qty']:.10g}  netted_close={p['q_close']:.10g}"
              f"  closed={p['closed_qty']:.10g}  ratio={p['ratio']:.6f}")
        print(f"   pnl      {p['old_pnl']:+.6f} -> {p['corr_pnl']:+.6f}")
        print(f"   exit_fee {p['old_exit_fee']:.6f} -> {p['corr_exit_fee']:.6f}")
        print(f"   net      {p['old_net']:+.6f} -> {p['corr_net']:+.6f}"
              f"   r={p['corr_r'] if p['corr_r'] is None else round(p['corr_r'],4)}")
        if p["sign_flip"]:
            print("   ** SIGN-FLIP: corrected net sign differs from stored result — "
                  "label NOT changed (operator/P2 decision) **")
        print()

    if plans:
        print(f"-- stacked PnL: sum {sum_old:+.6f} -> {sum_corr:+.6f} "
              f"(over-book removed: {sum_old - sum_corr:+.6f}) --\n")

    if not args.apply:
        print("DRY-RUN only — no rows written. Re-run with --apply to commit "
              "(after operator review).")
        conn.close()
        return

    if not plans:
        print("APPLY: nothing to correct.")
        conn.close()
        return

    now = _now()
    try:
        with conn:  # transaction
            for p in plans:
                apply_one(conn, p, now)
        print(f"APPLIED {len(plans)} correction(s) in one transaction @ {now}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
