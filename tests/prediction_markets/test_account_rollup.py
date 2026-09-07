"""Per-account / per-sub-division realized-P&L aggregation (multi-account foundation, 2026-09-01). Offline, PM DB
only, NO pytest fixtures -> runnable standalone (`python test_account_rollup.py`) and under pytest.

Proves the REALIZED-ONLY basis (R2 ruling): realized P&L sums ONLY terminal closes (is_exit=1), NEVER an entry
row's value -- the R4 BASIS discipline (seed a wrong-source value that must NOT appear). Also: win/loss counting
(won NULL = neither), open exposure at cost from the journal, and cross-sub-division + cross-account aggregation.
"""
import os
import tempfile

from trading_corp.prediction_markets import db, subdivision

_ORDER = {   # a filled entry, field-shaped like the real journal; overridden per row
    "account_id": "kalshi_jack", "category": "mlb",
    "wallet": "0x16bb9951a36fce71e2ef57890b786145e0ba8492",
    "condition_id": "0xcid", "outcome_index": 1, "signal_id": "sig", "client_order_id": "coid",
    "ticker": "KXMLBGAME-26AUG311840SDCIN-SD", "order_side": "bid", "outcome_leg": "yes",
    "is_exit": 0, "submitted_count": 5, "submitted_price": 0.60, "time_in_force": "immediate_or_cancel",
    "outcome_status": "filled", "broker_order_id": "bo", "fill_count": 5.0, "fill_price": 0.60,
    "remaining_count": None, "fee": 0.0, "error_detail": None, "dry_run": 0,
    "submitted_ts": 1788200000, "response_ts": 1788200000,
    "close_source": None, "realized_pnl": None, "won": None, "settled_ts": None,
}


def _ins(conn, **kw):
    row = dict(_ORDER)
    row.update(kw)
    cols = ", ".join(row)
    conn.execute("INSERT INTO pm_subdivision_order (%s) VALUES (%s)" % (cols, ", ".join(["?"] * len(row))),
                 tuple(row.values()))


def _seed(conn):
    conn.execute("INSERT INTO pm_account (account_id, venue, secret_ref, label, active, created_ts) "
                 "VALUES ('kalshi_jack','kalshi','KALSHI','Jack (KALSHI)',1,1787000000)")
    conn.execute("INSERT INTO pm_account (account_id, venue, secret_ref, label, active, created_ts) "
                 "VALUES ('karen','kalshi','KALSHI_KAREN','Karen',1,1787000000)")
    for acct, cat in (("kalshi_jack", "mlb"), ("kalshi_jack", "nba"), ("karen", "mlb")):
        conn.execute("INSERT INTO pm_subdivision (account_id, category, label, market_types, sizing_mode, "
                     "fixed_stake_usd, active, created_ts) VALUES (?,?,?,?,?,?,1,1787000000)",
                     (acct, cat, "%s %s" % (acct, cat), "moneyline", "contracts", 0.01))


def _db():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "pm.db")
    os.environ["PM_DB_PATH"] = p
    db.init_db(p)
    return p


def test_realized_sums_only_closes_not_entries():
    p = _db()
    with db.connect(p) as conn:
        _seed(conn)
        # an OPEN entry carrying a BOGUS realized_pnl (wrong-source value) -- must be IGNORED (is_exit=0)
        _ins(conn, is_exit=0, ticker="KXMLBGAME-26AUG311840SDCIN-SD", realized_pnl=999.0, fill_price=0.60)
        # a real WIN close (is_exit=1): settled at 1.0/ct, realized +2.01, won=1
        _ins(conn, is_exit=1, close_source="settlement", ticker="KXMLBGAME-26AUG311840SDCIN-SD",
             fill_price=1.0, realized_pnl=2.0080, won=1)
        # a real LOSS close: settled 0.0, realized -2.14, won=0
        _ins(conn, is_exit=1, close_source="settlement", ticker="KXMLBGAME-26AUG311840SDCIN-CIN",
             outcome_index=0, fill_price=0.0, realized_pnl=-2.1425, won=0)
        # a VOID close: won NULL -> counts in n_closed but neither win nor loss
        _ins(conn, is_exit=1, close_source="settlement_void", ticker="KXMLBGAME-26AUG311840SDCIN-XX",
             fill_price=0.0, realized_pnl=0.0, won=None)
        s = subdivision.subdivision_pnl(conn, "kalshi_jack", "mlb")
    assert abs(s["realized_pnl"] - (2.0080 - 2.1425)) < 1e-9, s["realized_pnl"]  # 999 entry EXCLUDED
    assert s["wins"] == 1 and s["losses"] == 1          # the void is neither
    assert s["n_closed"] == 3                            # win + loss + void


def test_open_exposure_from_journal_at_cost():
    p = _db()
    with db.connect(p) as conn:
        _seed(conn)
        # two open entries on one ticker (a 2-whale-style stack), no close -> held 10ct at cost
        _ins(conn, is_exit=0, fill_count=5.0, fill_price=0.60, ticker="KXMLBGAME-26AUG311840SDCIN-SD")
        _ins(conn, is_exit=0, fill_count=5.0, fill_price=0.50, ticker="KXMLBGAME-26AUG311840SDCIN-SD",
             wallet="0xother")
        s = subdivision.subdivision_pnl(conn, "kalshi_jack", "mlb")
    assert s["n_open"] == 1 and s["open_contracts"] == 10.0
    assert abs(s["open_cost_usd"] - (5 * 0.60 + 5 * 0.50)) < 1e-9
    assert s["realized_pnl"] == 0.0 and s["n_closed"] == 0


def test_account_pnl_aggregates_across_subdivisions():
    p = _db()
    with db.connect(p) as conn:
        _seed(conn)
        _ins(conn, category="mlb", is_exit=1, close_source="settlement", fill_price=1.0, realized_pnl=3.0, won=1)
        _ins(conn, category="nba", is_exit=1, close_source="settlement", fill_price=0.0, realized_pnl=-1.0, won=0,
             ticker="KXNBAGAME-26AUG31LALBOS-LAL")
        agg = subdivision.account_pnl(conn, "kalshi_jack")
    assert agg["n_subdivisions"] == 2                    # mlb + nba (karen's mlb is a different account)
    assert abs(agg["realized_pnl"] - 2.0) < 1e-9
    assert agg["wins"] == 1 and agg["losses"] == 1
    cats = {b["category"]: b for b in agg["subdivisions"]}
    assert abs(cats["mlb"]["realized_pnl"] - 3.0) < 1e-9 and abs(cats["nba"]["realized_pnl"] + 1.0) < 1e-9


def test_accounts_overview_lists_each_account():
    p = _db()
    with db.connect(p) as conn:
        _seed(conn)
        _ins(conn, account_id="kalshi_jack", category="mlb", is_exit=1, close_source="settlement",
             fill_price=1.0, realized_pnl=5.0, won=1)
        ov = subdivision.accounts_overview(conn)
    by = {a["account_id"]: a for a in ov}
    assert set(by) == {"kalshi_jack", "karen"}
    assert abs(by["kalshi_jack"]["realized_pnl"] - 5.0) < 1e-9
    assert by["karen"]["realized_pnl"] == 0.0            # Karen has no PM orders (attach-no) -> honest zero
    assert by["kalshi_jack"]["account_label"] == "Jack (KALSHI)"


def test_empty_pre_migration_010_is_zeroed_not_error():
    # subdivision_pnl on a fresh DB with the order table present but no rows -> zeroes, never a raise
    p = _db()
    with db.connect(p) as conn:
        s = subdivision.subdivision_pnl(conn, "kalshi_jack", "mlb")
    assert s["realized_pnl"] == 0.0 and s["n_open"] == 0 and s["n_closed"] == 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print("ALL %d PASS" % len(fns))
