#!/usr/bin/env python3
"""One-shot backfill: advance the pre-existing orphaned robinhood_pmcc `risk_approved` rows
to `board_rejected` (STEP 3 of the PMCC lifecycle-leak fix).

SELECT-first (dry-run is the DEFAULT); UPDATE only with --commit (operator-authorized).
Idempotent (guarded on status='risk_approved' — re-running touches 0 rows). Per-row audit:
`board/board_rejected` (parity with the healthy graph path + the live reconciler) +
`pmcc_reconciler/pmcc_orphan_backfilled` with a per-row cause: A=`resume_write_failed_or_registry_race`
when a `board_decision_received` audit exists for the id, else B=`wait_cancelled_by_restart`.

Reversible: --commit writes the touched ids to --out; the inverse is
  UPDATE proposed_order SET status='risk_approved', board_reason=NULL
  WHERE strategy='robinhood_pmcc' AND status='board_rejected'
    AND board_reason LIKE 'orphan backfill 2026-07-08%';

PARITY (not reuse) with trading_corp/agents/pmcc_approval_reconciler.expire_pmcc_approval:
same terminal status + board_reason shape + audit kinds. Kept as standalone, reviewable SQL so
the operator can read exactly what runs before authorizing --commit. (A test asserts the
constants match the module.)

Deploy order: run AFTER the lifecycle fix is live + verified. The fix stops NEW orphans; this
cleans the pre-existing residue. Boot-recovery (Fix B) will already have cleared the
thread-carrying (B) orphans on the first restart, so the dry-run typically shows the
decision-recorded (A) remainder. Scoped strictly to robinhood_pmcc. See reports/2026-07-08_pmcc_*.md.

Usage:
  # dry-run (SELECT only, no changes):
  python backfill_pmcc_risk_approved.py --db-url sqlite:////home/azureuser/trading_corp/data/trading_corp.db
  # authorized apply:
  python backfill_pmcc_risk_approved.py --db-url <same> --commit --out backfilled_ids.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta

from trading_corp.persistence import db
from trading_corp.utils.time import iso, now_utc

STRATEGY = "robinhood_pmcc"
TERMINAL = "board_rejected"
GRACE_MIN = 90
BACKFILL_MARKER = "orphan backfill 2026-07-08"
KIND_BACKFILL = "pmcc_orphan_backfilled"
CAUSE_A = "resume_write_failed_or_registry_race"   # a board_decision_received audit exists
CAUSE_B = "wait_cancelled_by_restart"              # no decision recorded
ACTOR = "pmcc_reconciler"


def _decision_detail(conn, order_id):
    row = conn.execute(
        "SELECT payload_json FROM audit_event WHERE actor='hitl' "
        "AND kind='board_decision_received' AND json_extract(payload_json,'$.order_id')=? "
        "ORDER BY ts DESC LIMIT 1", (order_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Backfill orphaned robinhood_pmcc risk_approved rows -> board_rejected",
    )
    ap.add_argument("--db-url", required=True, help="sqlite:///... URL or path to the trading_corp DB")
    ap.add_argument("--commit", action="store_true", help="APPLY the UPDATE (default: dry-run SELECT only)")
    ap.add_argument("--before", default=None, help="ISO ts upper bound (default: now - 90min)")
    ap.add_argument("--out", default="backfilled_ids.txt", help="file for touched ids (commit mode)")
    args = ap.parse_args()

    now = now_utc()
    before = args.before or iso(now - timedelta(minutes=GRACE_MIN))

    with db.connect(args.db_url) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, ts, symbol, side, qty, status FROM proposed_order "
            "WHERE strategy=? AND status='risk_approved' AND ts < ? ORDER BY ts",
            (STRATEGY, before),
        ).fetchall()]

        classified = []
        for r in rows:
            dec = _decision_detail(conn, r["id"])
            cause = CAUSE_A if dec is not None else CAUSE_B
            classified.append((r, cause, dec))

        n = len(classified)
        n_a = sum(1 for _, c, _ in classified if c == CAUSE_A)
        print(f"[backfill] strategy={STRATEGY} status=risk_approved ts<{before}")
        print(f"[backfill] would touch {n} row(s): {n_a} cause=A(decision-recorded) / {n - n_a} cause=B(no-decision)")
        for r, cause, dec in classified:
            rec = f" recorded={dec.get('decision')}/{dec.get('source')}" if dec else ""
            print(f"    {r['id'][:8]} {r['ts']} {r['symbol']:<6} {r['side']:<4} x{r['qty']:g} cause={cause}{rec}")

        odd = [r for r, c, d in classified if d and (d.get("decision") or "").lower() != "reject"]
        if odd:
            print(
                f"[backfill] [WARN] {len(odd)} row(s) have a recorded decision that is NOT 'reject'; "
                f"they will still be set {TERMINAL} per Board steer #1: {[r['id'][:8] for r in odd]}"
            )

        if not args.commit:
            print("[backfill] DRY-RUN — no changes written. Re-run with --commit to apply.")
            return 0

        touched = []
        for r, cause, dec in classified:
            reason = f"{BACKFILL_MARKER} (cause={cause}) - approval never resumed"
            cur = conn.execute(
                "UPDATE proposed_order SET status=?, board_reason=? "
                "WHERE id=? AND strategy=? AND status='risk_approved'",
                (TERMINAL, reason, r["id"], STRATEGY),
            )
            if cur.rowcount != 1:
                print(f"    SKIP {r['id'][:8]} - not risk_approved anymore (idempotent)")
                continue
            ts_now = iso(now_utc())
            conn.execute(
                "INSERT INTO audit_event(ts, actor, kind, payload_json) VALUES(?,?,?,?)",
                (ts_now, "board", "board_rejected",
                 json.dumps({"order_id": r["id"], "reason": reason, "recovered_by": KIND_BACKFILL})),
            )
            conn.execute(
                "INSERT INTO audit_event(ts, actor, kind, payload_json) VALUES(?,?,?,?)",
                (ts_now, ACTOR, KIND_BACKFILL,
                 json.dumps({"order_id": r["id"], "strategy": STRATEGY, "division": STRATEGY,
                             "cause": cause, "reason": reason, "row_ts": r["ts"],
                             "recovered_ts": ts_now,
                             "recorded_decision": (dec or {}).get("decision"),
                             "recorded_source": (dec or {}).get("source")})),
            )
            touched.append(r["id"])

        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(touched) + ("\n" if touched else ""))
        print(f"[backfill] COMMITTED {len(touched)} row(s) -> {TERMINAL}. Touched ids -> {args.out}")
        print(
            f"[backfill] inverse (rollback): UPDATE proposed_order SET status='risk_approved', "
            f"board_reason=NULL WHERE strategy='{STRATEGY}' AND status='{TERMINAL}' "
            f"AND board_reason LIKE '{BACKFILL_MARKER}%';"
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
