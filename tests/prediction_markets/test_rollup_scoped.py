"""Scoped rollup -- the refresh-button availability fix (2026-10-10).

stats.rollup(wallet=X) must write pm_category_stats + pm_category_onesided_stats rows VALUE-IDENTICAL to a
FULL run for X, and leave EVERY other wallet's rows byte-for-byte untouched.

★ NON-TAUTOLOGICAL: the ORACLE is the FULL-table rollup (a genuinely DIFFERENT SQL path -- it scans every
wallet), the subject is the SCOPED path (it scans one). If scoping dropped or mis-bound a WHERE, the two
diverge and these fail. The expected values are produced by the independent full path, never hand-copied
from the scoped path. Offline, PM DB only -> runs standalone and under pytest.
"""
import os
import tempfile

from trading_corp.prediction_markets import db, stats

_CP = {   # a scoreable (pnl_suspect=0) closed position; overridden per row
    "slug": "mlb-nyy-bos-2026-08-01", "event_slug": "mlb-nyy-bos", "title": "Yankees vs Red Sox",
    "category": "mlb", "category_source": "slug_prefix", "outcome": "Yes", "outcome_index": 0,
    "avg_price": 0.5, "total_bought": 100.0, "cost_basis": 50.0, "realized_pnl": 10.0, "cur_price": 1.0,
    "won": 1, "pnl_suspect": 0, "pnl_anomaly": 0, "resolved_ts": 1788000000,
}


def _cp(conn, wallet, condition_id, **kw):
    row = dict(_CP)
    row.update(kw)
    row["wallet"] = wallet
    row["condition_id"] = condition_id
    cols = ", ".join(row)
    conn.execute("INSERT INTO pm_closed_position (%s) VALUES (%s)" % (cols, ", ".join(["?"] * len(row))),
                 tuple(row.values()))


def _seed(conn):
    # wA: 3 wins + 2 losses, DISTINCT condition_ids (one-sided), its OWN distinct numbers
    _cp(conn, "wA", "cidA1", won=1, realized_pnl=40.0, cost_basis=60.0, total_bought=100.0, avg_price=0.60)
    _cp(conn, "wA", "cidA2", won=1, realized_pnl=20.0, cost_basis=30.0, avg_price=0.30)
    _cp(conn, "wA", "cidA3", won=1, realized_pnl=15.0, cost_basis=25.0, avg_price=0.25)
    _cp(conn, "wA", "cidA4", won=0, realized_pnl=-30.0, cost_basis=30.0, avg_price=0.30)
    _cp(conn, "wA", "cidA5", won=0, realized_pnl=-10.0, cost_basis=10.0, avg_price=0.10)
    # wB: a DIFFERENT shape (1 win, 3 losses) -> any cross-wallet contamination would move wA's numbers
    _cp(conn, "wB", "cidB1", won=1, realized_pnl=8.0, cost_basis=20.0, avg_price=0.20)
    _cp(conn, "wB", "cidB2", won=0, realized_pnl=-20.0, cost_basis=20.0, avg_price=0.20)
    _cp(conn, "wB", "cidB3", won=0, realized_pnl=-15.0, cost_basis=15.0, avg_price=0.15)
    _cp(conn, "wB", "cidB4", won=0, realized_pnl=-5.0, cost_basis=5.0, avg_price=0.05)
    conn.commit()


def _db():
    p = os.path.join(tempfile.mkdtemp(), "pm.db")
    db.init_db(p)
    return p


def _rows(conn, table, wallet):
    cur = conn.execute("SELECT * FROM %s WHERE wallet=? ORDER BY category" % table, (wallet,))
    names = [c[0] for c in cur.description]
    return {r[names.index("category")]: dict(zip(names, r)) for r in cur.fetchall()}


def test_scoped_values_identical_to_full_and_others_never_written():
    pf, ps = _db(), _db()
    with db.connect(pf) as cf, db.connect(ps) as cs:
        _seed(cf); _seed(cs)
        stats.rollup(cf, now_ts=1700)                  # ORACLE: full-table (scans all wallets)
        stats.rollup(cs, now_ts=1700, wallet="wA")     # SUBJECT: scoped
        assert _rows(cs, "pm_category_stats", "wA") == _rows(cf, "pm_category_stats", "wA") != {}
        assert _rows(cs, "pm_category_onesided_stats", "wA") == _rows(cf, "pm_category_onesided_stats", "wA") != {}
        # the scoped run NEVER wrote wB (fresh db) -> no row; the full db has it (proves scope did not touch others)
        assert _rows(cs, "pm_category_stats", "wB") == {}
        assert _rows(cf, "pm_category_stats", "wB") != {}


def test_scoped_leaves_existing_other_wallet_rows_untouched():
    p = _db()
    with db.connect(p) as conn:
        _seed(conn)
        stats.rollup(conn, now_ts=1700)                # full: wA and wB rows now both present
        wB_before = _rows(conn, "pm_category_stats", "wB")
        assert wB_before and wB_before["mlb"]["updated_ts"] == 1700
        conn.execute("UPDATE pm_closed_position SET realized_pnl = realized_pnl + 1 WHERE wallet='wA'")
        conn.commit()
        stats.rollup(conn, now_ts=1800, wallet="wA")   # scoped run at a NEW now_ts
        wB_after = _rows(conn, "pm_category_stats", "wB")
        assert wB_after == wB_before                   # byte-identical incl updated_ts=1700 -> scope never touched wB
        wA_scoped = _rows(conn, "pm_category_stats", "wA")
    # wA now equals a fresh FULL oracle on the SAME (mutated) data
    po = _db()
    with db.connect(po) as co:
        _seed(co)
        co.execute("UPDATE pm_closed_position SET realized_pnl = realized_pnl + 1 WHERE wallet='wA'")
        co.commit()
        stats.rollup(co, now_ts=1800)
        assert wA_scoped == _rows(co, "pm_category_stats", "wA")


def test_scoped_zero_rows_writes_nothing_and_preserves_prior():
    p = _db()
    with db.connect(p) as conn:
        _seed(conn)
        stats.rollup(conn, now_ts=1700)                # wB row exists
        wB_before = _rows(conn, "pm_category_stats", "wB")
        n = stats.rollup(conn, now_ts=1800, wallet="wNONE")   # a wallet with NO closed rows
        assert n == 0                                  # nothing written (INSERT OR REPLACE never deletes)
        assert _rows(conn, "pm_category_stats", "wB") == wB_before   # prior rows preserved -- same as a full run
        assert _rows(conn, "pm_category_stats", "wNONE") == {}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for f in fns:
        f(); print("PASS", f.__name__)
    print("ALL %d PASS" % len(fns))
