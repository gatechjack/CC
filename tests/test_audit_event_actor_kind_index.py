"""A / de-block — the `ix_audit_event_actor_kind` index that closes the
resolver + dedup SCAN freeze family (the on-loop audit_event scans A's risk.py
removal did NOT touch).

Proves: init_db creates the index, it's idempotent, the existing ts-index is
preserved, and the four kept hot-path queries flip SCAN -> SEARCH using it.
The index is additive — it changes plan/speed only, never query RESULTS (the
existing `test_count_open_entries_*` tests exercise the dedup's results and
stay green under the new schema).
"""
from trading_corp.persistence import db


def test_actor_kind_index_created_and_ts_index_preserved(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'idx.db'}"
    db.init_db(db_url)
    with db.connect(db_url) as conn:
        idx = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='audit_event'"
            )
        }
    assert "ix_audit_event_actor_kind" in idx      # the new index
    assert "ix_audit_event_ts" in idx              # existing index untouched


def test_init_db_idempotent(tmp_path):
    """CREATE INDEX IF NOT EXISTS — running init_db twice is a no-op, no error,
    no duplicate index, never touches rows."""
    db_url = f"sqlite:///{tmp_path / 'idx2.db'}"
    db.init_db(db_url)
    db.init_db(db_url)
    with db.connect(db_url) as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM sqlite_master "
            "WHERE type='index' AND name='ix_audit_event_actor_kind'"
        ).fetchone()["c"]
    assert n == 1


# The four KEPT on-loop audit_event readers (real query shapes). Each filters
# `actor = ? AND kind = / IN (...)` — the prefix the (actor,kind) index serves.
_QUERIES = {
    "reconciler": (
        "SELECT payload_json FROM audit_event "
        "WHERE actor=? AND kind IN (?, ?) ORDER BY id DESC LIMIT 1",
        ("bitunix_position_reconciler", "position_state_reconciled",
         "position_state_divergence_detected"),
    ),
    "count_open_entries": (
        "SELECT json_extract(payload_json,'$.condition_id') AS cid, COUNT(*) "
        "FROM audit_event "
        "WHERE actor='polymarket_arbitrage' AND kind='would_have_placed' "
        "AND json_extract(payload_json,'$.condition_id') IN ('0xAAA') "
        "AND json_extract(payload_json,'$.order_id') NOT IN ("
        "  SELECT order_id FROM polymarket_round_trips "
        "  WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage') "
        "GROUP BY cid",
        (),
    ),
    "polymarket_resolver": (
        "SELECT a.ts, a.payload_json FROM audit_event a "
        "LEFT JOIN polymarket_round_trips r "
        "  ON r.order_id = json_extract(a.payload_json,'$.order_id') "
        "WHERE a.actor='polymarket_arbitrage' AND a.kind='would_have_placed' "
        "  AND r.order_id IS NULL LIMIT 50",
        (),
    ),
    "kalshi_resolver": (
        "SELECT a.ts, a.payload_json FROM audit_event a "
        "LEFT JOIN kalshi_round_trips r "
        "  ON r.order_id = json_extract(a.payload_json,'$.order_id') "
        "WHERE a.actor='kalshi_llm_arbitrage' AND a.kind='would_have_placed' "
        "  AND r.order_id IS NULL LIMIT 50",
        (),
    ),
}


def test_hot_path_queries_use_the_index(tmp_path):
    """SCAN -> SEARCH proof: every kept on-loop reader's plan uses the new index
    (so none full-scans audit_event on the event loop)."""
    db_url = f"sqlite:///{tmp_path / 'idx3.db'}"
    db.init_db(db_url)
    with db.connect(db_url) as conn:
        for name, (sql, params) in _QUERIES.items():
            plan = " | ".join(
                str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params)
            )
            assert "ix_audit_event_actor_kind" in plan, f"{name} not using index: {plan}"
            # the audit_event side must be a SEARCH, not a full SCAN
            assert "SCAN audit_event" not in plan, f"{name} still SCANs audit_event: {plan}"
