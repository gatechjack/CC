#!/bin/bash
# One-time data correction: re-tag the 2 closed v2 trades with audit_corrected=true
# and record the reconciler-verified corrected outcome.
#
# Trade #1 (35aa49c9): recorded as loss -1.0R; reconciler shows win +0.838R with TP1+TP2 fills.
# Trade #2 (a467e316): reconciler-verified as correctly recorded (genuine loss).
#
# The original `result` and `actual_r_multiple` columns are preserved for
# historical fidelity. Downstream code that needs the corrected outcome
# should read extra_json.corrected_result / extra_json.corrected_r_multiple
# when extra_json.audit_corrected is true.

set -e
DB=/home/azureuser/trading_corp/data/trading_corp.db

python3 <<'PYEOF'
import json, sqlite3

conn = sqlite3.connect("/home/azureuser/trading_corp/data/trading_corp.db")
conn.row_factory = sqlite3.Row

CORRECTIONS = [
    {
        "order_id": "35aa49c9-bb62-4084-865f-5d839515cd81",
        "patch": {
            "audit_corrected": True,
            "audit_reviewed_ts": "2026-05-20T05:30:00+00:00",
            "audit_review_reason": "silent v2 lifecycle bug — _bitunix_kline_fetcher 200-bar server-cap pagination — see reports/bitunix_audit_integrity_2026-05-20.md",
            "corrected_result": "win",
            "corrected_r_multiple": 0.838,
            "corrected_filled_legs": ["tp1", "tp2"],
            "corrected_current_sl_final": 76269.86667999999,
            "corrected_exit_price": 76269.86667999999,
        },
    },
    {
        "order_id": "a467e316-8889-4969-96d6-466865cb8046",
        "patch": {
            "audit_corrected": True,
            "audit_reviewed_ts": "2026-05-20T05:30:00+00:00",
            "audit_review_reason": "audit-vs-reality reconciler match — recorded outcome verified correct; TP1 missed by $3.97 in price action; genuine SL hit",
            "corrected_result": "loss",
            "corrected_r_multiple": -1.0,
            "corrected_filled_legs": [],
        },
    },
]

for c in CORRECTIONS:
    row = conn.execute(
        "SELECT extra_json FROM paper_trade_record WHERE order_id = ?",
        (c["order_id"],),
    ).fetchone()
    if row is None:
        print(f"NOT FOUND: {c['order_id']}")
        continue
    e = json.loads(row["extra_json"]) if row["extra_json"] else {}
    if e.get("audit_corrected"):
        print(f"ALREADY CORRECTED (idempotent skip): {c['order_id']}")
        continue
    e.update(c["patch"])
    conn.execute(
        "UPDATE paper_trade_record SET extra_json = ? WHERE order_id = ?",
        (json.dumps(e), c["order_id"]),
    )
    print(f"UPDATED: {c['order_id']}")
    print(f"  set: {sorted(c['patch'].keys())}")

conn.commit()

# Verify both rows
print()
print("=== Verification ===")
for c in CORRECTIONS:
    r = conn.execute(
        "SELECT order_id, result, actual_r_multiple, "
        "json_extract(extra_json, '$.audit_corrected') AS audit_corrected, "
        "json_extract(extra_json, '$.corrected_result') AS corrected_result, "
        "json_extract(extra_json, '$.corrected_r_multiple') AS corrected_r "
        "FROM paper_trade_record WHERE order_id = ?",
        (c["order_id"],),
    ).fetchone()
    print(dict(r))

conn.close()
PYEOF

echo "=== DONE ==="
