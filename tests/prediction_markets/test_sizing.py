"""Per-sub-division contract sizing from the UI (2026-09-12): the bounds gate (R2), the reader (R4), the
owner/admin-gated writer + audit (R1/R4), the R3 confirm estimate incl. the no-fills case, the full R1 authz
matrix on the routes, and the page render before/after a change. NEVER posts against prod -- tmp DBs only (R5)."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, sizing

NOW = 1789200000
ACC = "kalshi_karen"       # owned by 'karen'; admin = 'jack'


def _raw(p):
    """A raw autocommit connection with the Row factory -- db.connect is a @contextmanager, so a persistent test
    connection is opened directly with the same pragmas."""
    c = sqlite3.connect(p, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _conn(p):
    db.init_db(p)
    return _raw(p)


def _seed(conn, *, account=ACC, owner="karen", category="mlb", mode="contracts", contracts=5):
    conn.execute("INSERT INTO pm_account (account_id,venue,secret_ref,label,active,created_ts,owner_identity) "
                 "VALUES (?,?,?,?,1,?,?)", (account, "kalshi", "K", account, NOW, owner))
    conn.execute("INSERT INTO pm_subdivision (account_id,category,label,active,created_ts,sizing_mode,contracts) "
                 "VALUES (?,?,?,1,?,?,?)", (account, category, account + " " + category.upper(), NOW, mode, contracts))
    conn.commit()


# ── R2 bounds ────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("val,ok", [(0, False), (1, True), (5, True), (50, True), (51, False),
                                    (2.5, False), ("abc", False), (None, False), ("10", True)])
def test_bounds(val, ok):
    if ok:
        assert sizing.validate_contracts(val) == int(float(val))
    else:
        with pytest.raises(sizing.SizingError):
            sizing.validate_contracts(val)


def test_bounds_constants_are_a_ui_ruling():
    assert (sizing.CONTRACTS_MIN, sizing.CONTRACTS_MAX) == (1, 50)


# ── reader (R4) ──────────────────────────────────────────────────────────────────────────────────
def test_read_sizing(tmp_path):
    conn = _conn(str(tmp_path / "pm.db")); _seed(conn)
    s = sizing.read_sizing(conn, ACC, "mlb", now_ts=NOW)
    assert s["contracts"] == 5 and s["sizing_mode"] == "contracts" and s["editable"] is True
    assert s["min"] == 1 and s["max"] == 50 and s["last_change"] is None and s["set_by"] is None
    assert sizing.read_sizing(conn, ACC, "nfl", now_ts=NOW) is None          # no such sub-division


def test_read_sizing_non_contracts_not_editable(tmp_path):
    conn = _conn(str(tmp_path / "pm.db")); _seed(conn, mode="fixed")
    s = sizing.read_sizing(conn, ACC, "mlb", now_ts=NOW)
    assert s["editable"] is False and s["sizing_mode"] == "fixed"


# ── writer + audit (R1 data write / R4 audit) ──────────────────────────────────────────────────────
def test_set_contracts_writes_and_audits(tmp_path):
    conn = _conn(str(tmp_path / "pm.db")); _seed(conn)
    r = sizing.set_contracts(conn, ACC, "mlb", 1, "karen", NOW)
    assert r == {"ok": True, "changed": True, "old": 5, "new": 1, "account_id": ACC, "category": "mlb"}
    # the engine's per-cycle read (pm_subdivision.contracts) now returns 1
    assert conn.execute("SELECT contracts FROM pm_subdivision WHERE account_id=? AND category='mlb'", (ACC,)).fetchone()[0] == 1
    assert sizing.last_change(conn, ACC, "mlb") == {"old": 5, "new": 1, "by": "karen", "ts": NOW}   # who/from/to/when


def test_set_contracts_idempotent_noop(tmp_path):
    conn = _conn(str(tmp_path / "pm.db")); _seed(conn, contracts=5)
    assert sizing.set_contracts(conn, ACC, "mlb", 5, "karen", NOW)["changed"] is False
    assert sizing.recent_changes(conn, ACC, "mlb") == []                     # no audit row for a no-op


def test_set_contracts_refuses_non_contracts_mode(tmp_path):
    conn = _conn(str(tmp_path / "pm.db")); _seed(conn, mode="fixed")
    with pytest.raises(sizing.SizingError):
        sizing.set_contracts(conn, ACC, "mlb", 3, "jack", NOW)
    assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_sizing_audit").fetchone()[0] == 0


def test_set_contracts_bounds_enforced(tmp_path):
    conn = _conn(str(tmp_path / "pm.db")); _seed(conn)
    for bad in (0, 51):
        with pytest.raises(sizing.SizingError):
            sizing.set_contracts(conn, ACC, "mlb", bad, "jack", NOW)


def test_recent_changes_last_5(tmp_path):
    conn = _conn(str(tmp_path / "pm.db")); _seed(conn)
    for i, n in enumerate([4, 3, 2, 6, 1, 7], 1):           # 6 changes; expect the last 5 newest-first
        sizing.set_contracts(conn, ACC, "mlb", n, "jack", NOW + i)
    changes = sizing.recent_changes(conn, ACC, "mlb", limit=5)
    assert len(changes) == 5 and [c["new"] for c in changes] == [7, 1, 6, 2, 3]


# ── R3 estimate ────────────────────────────────────────────────────────────────────────────────────
def test_estimate_no_fills(tmp_path):
    conn = _conn(str(tmp_path / "pm.db")); _seed(conn)
    assert sizing.estimate_cost(conn, ACC, "mlb", 3) == (None, None)         # -> "no fills yet to estimate from"


def test_estimate_from_fills(tmp_path):
    conn = _conn(str(tmp_path / "pm.db")); _seed(conn)
    for fp in (0.40, 0.60):
        conn.execute("INSERT INTO pm_subdivision_order (account_id,category,ticker,outcome_leg,is_exit,dry_run,"
                     "outcome_status,fill_count,fill_price,response_ts) VALUES (?,?,?,?,0,0,'filled',1,?,?)",
                     (ACC, "mlb", "KX-1", "yes", fp, NOW))
    conn.commit()
    avg, est = sizing.estimate_cost(conn, ACC, "mlb", 3)
    assert avg == pytest.approx(0.50) and est == pytest.approx(1.50)


# ── R1 authz matrix on the routes ──────────────────────────────────────────────────────────────────
def _client(monkeypatch, tmp_path, **seed):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    conn = _conn(p); _seed(conn, **seed); conn.close()
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def _hdr(ident):
    return {"Remote-User": ident} if ident else {}


# (identity, new_n, expected POST status). current=5. owner=karen, admin=jack.
@pytest.mark.parametrize("ident,new_n,code", [
    ("karen", 1, 303),    # owner lowers  -> OK
    ("karen", 10, 403),   # owner raises  -> forbidden (admin only)
    ("jack", 1, 303),     # admin lowers  -> OK
    ("jack", 10, 303),    # admin raises  -> OK
    (None, 1, 403),       # no identity   -> forbidden
    ("bob", 1, 403),      # not owner/admin -> forbidden
])
def test_authz_matrix_post(monkeypatch, tmp_path, ident, new_n, code):
    cl = _client(monkeypatch, tmp_path)
    r = cl.post("/live/%s/mlb/sizing/%d" % (ACC, new_n), headers=_hdr(ident), follow_redirects=False)
    assert r.status_code == code


def test_authz_matrix_get_confirm(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    assert cl.get("/live/%s/mlb/sizing?n=1" % ACC, headers=_hdr("karen")).status_code == 200      # owner lower -> confirm
    assert cl.get("/live/%s/mlb/sizing?n=10" % ACC, headers=_hdr("karen"), follow_redirects=False).status_code == 403  # owner raise
    assert cl.get("/live/%s/mlb/sizing?n=10" % ACC, headers=_hdr("jack")).status_code == 200       # admin raise -> confirm


@pytest.mark.parametrize("n,code", [(0, 400), (51, 400), (1, 200), (50, 200)])
def test_bounds_on_route(monkeypatch, tmp_path, n, code):
    cl = _client(monkeypatch, tmp_path)
    # use admin so bounds are the only gate in play (a raise is allowed for admin)
    assert cl.get("/live/%s/mlb/sizing?n=%d" % (ACC, n), headers=_hdr("jack"), follow_redirects=False).status_code == code


def test_non_contracts_mode_conflict(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path, mode="fixed")
    assert cl.get("/live/%s/mlb/sizing?n=1" % ACC, headers=_hdr("jack"), follow_redirects=False).status_code == 409
    assert cl.post("/live/%s/mlb/sizing/1" % ACC, headers=_hdr("jack"), follow_redirects=False).status_code == 409


def test_post_applies_and_audits_via_route(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    r = cl.post("/live/%s/mlb/sizing/1" % ACC, headers=_hdr("karen"), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/live/%s/mlb" % ACC
    # verify the write + audit landed
    import os
    conn = _raw(os.environ["PM_DB_PATH"])
    assert conn.execute("SELECT contracts FROM pm_subdivision WHERE account_id=? AND category='mlb'", (ACC,)).fetchone()[0] == 1
    a = conn.execute("SELECT old_contracts,new_contracts,changed_by FROM pm_subdivision_sizing_audit").fetchone()
    assert tuple(a) == (5, 1, "karen")
    conn.close()


# ── page render before/after ────────────────────────────────────────────────────────────────────────
def test_page_render_before_and_after(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    before = cl.get("/live/%s/mlb" % ACC, headers=_hdr("jack")).text
    assert "Sizing" in before and "5 contracts / copy" in before          # current value shown
    cl.post("/live/%s/mlb/sizing/1" % ACC, headers=_hdr("jack"), follow_redirects=False)
    after = cl.get("/live/%s/mlb" % ACC, headers=_hdr("jack")).text
    assert "1 contract / copy" in after and "set by jack" in after        # new value + audit line
