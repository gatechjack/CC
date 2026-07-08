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
CAUSE_A = "resume_write_failed_or_registry_race"   # recorded reject, resume never wrote back
CAUSE_B = "wait_cancelled_by_restart"              # no decision recorded
CAUSE_APPROVED_STRANDED = "resume_write_failed_approved_stranded"  # recorded approve/modify, never executed
ACTOR = "pmcc_reconciler"


def _disposition(dec):
    """Per-row (terminal_status, cause, board_audit_kind) from the recorded decision.
    reject / no-decision -> board_rejected; a recorded approve|modify that never executed
    (system-stranded) -> cancelled ('cancelled' is closer to truthful than 'rejected' for
    that specific case, per Board Option 2 on 2026-07-08). Uniform board_rejected was
    steer #1's intent for mechanism simplicity; the one-row deviation earns a more honest
    audit trail."""
    if dec is None:
        return ("board_rejected", CAUSE_B, "board_rejected")
    d = (dec.get("decision") or "").lower()
    if d == "reject":
        return ("board_rejected", CAUSE_A, "board_rejected")
    return ("cancelled", CAUSE_APPROVED_STRANDED, "cancelled")


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
            term, cause, audit_kind = _disposition(dec)
            classified.append((r, term, cause, audit_kind, dec))

        n = len(classified)
        by_term = {}
        for _, term, _, _, _ in classified:
            by_term[term] = by_term.get(term, 0) + 1
        print(f"[backfill] strategy={STRATEGY} status=risk_approved ts<{before}")
        print(f"[backfill] would touch {n} row(s): "
              + " / ".join(f"{v} -> {k}" for k, v in sorted(by_term.items())))
        for r, term, cause, _, dec in classified:
            rec = f" recorded={dec.get('decision')}/{dec.get('source')}" if dec else " recorded=none"
            print(f"    {r['id'][:8]} {r['ts']} {r['symbol']:<6} {r['side']:<4} x{r['qty']:g} "
                  f"-> {term} cause={cause}{rec}")

        if not args.commit:
            print("[backfill] DRY-RUN — no changes written. Re-run with --commit to apply.")
            return 0

        touched = []
        for r, term, cause, audit_kind, dec in classified:
            reason = f"{BACKFILL_MARKER} (cause={cause}) - approval never resumed"
            cur = conn.execute(
                "UPDATE proposed_order SET status=?, board_reason=? "
                "WHERE id=? AND strategy=? AND status='risk_approved'",
                (term, reason, r["id"], STRATEGY),
            )
            if cur.rowcount != 1:
                print(f"    SKIP {r['id'][:8]} - not risk_approved anymore (idempotent)")
                continue
            ts_now = iso(now_utc())
            recorded_decision = f"{dec.get('decision')}_{dec.get('source')}" if dec else "none"
            conn.execute(
                "INSERT INTO audit_event(ts, actor, kind, payload_json) VALUES(?,?,?,?)",
                (ts_now, "board", audit_kind,
                 json.dumps({"order_id": r["id"], "reason": reason, "recovered_by": KIND_BACKFILL})),
            )
            conn.execute(
                "INSERT INTO audit_event(ts, actor, kind, payload_json) VALUES(?,?,?,?)",
                (ts_now, ACTOR, KIND_BACKFILL,
                 json.dumps({"order_id": r["id"], "strategy": STRATEGY, "division": STRATEGY,
                             "cause": cause, "reason": reason, "row_ts": r["ts"],
                             "terminal_status": term, "recovered_ts": ts_now,
                             "recorded_decision": recorded_decision,
                             "recorded_source": (dec or {}).get("source")})),
            )
            touched.append(r["id"])

        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(touched) + ("\n" if touched else ""))
        print(f"[backfill] COMMITTED {len(touched)} row(s) -> {TERMINAL}. Touched ids -> {args.out}")
        print(
            f"[backfill] inverse (rollback): UPDATE proposed_order SET status='risk_approved', "
            f"board_reason=NULL WHERE strategy='{STRATEGY}' AND status IN ('board_rejected','cancelled') "
            f"AND board_reason LIKE '{BACKFILL_MARKER}%';"
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
