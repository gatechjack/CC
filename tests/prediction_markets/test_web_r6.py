"""Stage 3 R6 -- the three farm-action POST routes (pm_web's FIRST mutating routes besides Analyze).

Proves via the FastAPI TestClient: the buttons stop being disabled (forms render + wired), the routes mutate
correctly with a 303 PRG, a GET NEVER mutates (no crawler/prefetch demote), a double-submit is idempotent, a
promote-to-live to a nonexistent sub-division is a safe no-op, and the /live pages stay READ-ONLY (detach is a
CLI action). Offline, PM DB only.
"""
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, farm

WALLET = "0x16bb9951a36fce71e2ef57890b786145e0ba8492"


def _mk(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts) VALUES(?,?,?,1,1,1)",
                     (WALLET, "mlb", farm.PINNED))
        conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts) VALUES('0xcand','mlb',?,1,1,1)",
                     (farm.CANDIDATE,))
        conn.execute("INSERT INTO pm_paper_trade(wallet,category,condition_id,outcome_index,entry_observed_ts,status,opened_ts) "
                     "VALUES(?,?,?,0,1,'open',1)", (WALLET, "mlb", "0xpa"))
        conn.execute("INSERT INTO pm_account(account_id,venue,label,active,created_ts) VALUES('kalshi_jack','kalshi','Jack (KALSHI)',1,1)")
        conn.execute("INSERT INTO pm_subdivision(account_id,category,label,sizing_mode,fixed_stake_usd,active,created_ts) "
                     "VALUES('kalshi_jack','mlb','Jack MLB','fixed',5.0,1,1)")
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app), p


def _status(p, wallet, category):
    with db.connect(p) as conn:
        r = conn.execute("SELECT status FROM pm_watchlist WHERE wallet=? AND category=?", (wallet, category)).fetchone()
    return r["status"] if r else None


def _attach_count(p):
    with db.connect(p) as conn:
        return conn.execute("SELECT COUNT(*) FROM pm_subdivision_attachment WHERE active=1").fetchone()[0]


def test_watchlist_buttons_render_wired_not_disabled(monkeypatch, tmp_path):
    cl, _p = _mk(monkeypatch, tmp_path)
    html = cl.get("/farm/mlb").text
    assert ('action="/farm/mlb/demote/%s"' % WALLET) in html            # Demote form wired
    assert ('action="/live/kalshi_jack/mlb/attach/%s"' % WALLET) in html  # Promote-to-live form wired (category-joined target)
    assert 'disabled title="Demote to Prospect' not in html             # the old disabled buttons are gone
    assert 'disabled title="Promote to a live sub-division' not in html


def test_promote_watchlist_route_flips_status_with_303(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    r = cl.post("/farm/mlb/promote/0xcand", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/farm/mlb"
    assert _status(p, "0xcand", "mlb") == farm.PINNED


def test_demote_route_303_and_preserves_paper(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        paper_before = conn.execute("SELECT COUNT(*) FROM pm_paper_trade").fetchone()[0]
    r = cl.post("/farm/mlb/demote/%s" % WALLET, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/farm/mlb"
    assert _status(p, WALLET, "mlb") == farm.CANDIDATE
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_paper_trade").fetchone()[0] == paper_before   # F-5: paper preserved


def test_promote_to_live_route_attaches_303_and_idempotent(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    r = cl.post("/live/kalshi_jack/mlb/attach/%s" % WALLET, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/live/kalshi_jack/mlb"
    assert _attach_count(p) == 1
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_order").fetchone()[0] == 0    # attachment, NEVER an order
    cl.post("/live/kalshi_jack/mlb/attach/%s" % WALLET, follow_redirects=False)                # double-submit
    assert _attach_count(p) == 1                                                               # still ONE attachment (idempotent)
    # the sub-division page now shows the attached whale (read-only) -- assert the WHALE renders, not just the heading
    page = cl.get("/live/kalshi_jack/mlb").text
    assert "Copies these whales" in page and ("/watchlist/%s/mlb" % WALLET) in page            # the attachment flows to the UI


def test_get_never_mutates(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    cl.get("/farm/mlb"); cl.get("/live/kalshi_jack/mlb"); cl.get("/")
    assert _status(p, WALLET, "mlb") == farm.PINNED and _status(p, "0xcand", "mlb") == farm.CANDIDATE
    assert _attach_count(p) == 0
    # a GET on a mutating path is Method Not Allowed -> a crawler/prefetch CANNOT promote/demote/attach
    assert cl.get("/farm/mlb/promote/0xcand").status_code == 405
    assert cl.get("/farm/mlb/demote/%s" % WALLET).status_code == 405
    assert cl.get("/live/kalshi_jack/mlb/attach/%s" % WALLET).status_code == 405


def test_promote_to_live_wrong_category_is_safe_no_autocreate(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    # WALLET is pinned in mlb, not nba -> the category-join refuses; auto-create must NOT fire (no orphan sub-division)
    r = cl.post("/live/kalshi_jack/nba/attach/%s" % WALLET, follow_redirects=False)
    assert r.status_code == 303                                                         # no crash
    assert _attach_count(p) == 0                                                        # nothing attached
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_subdivision WHERE account_id='kalshi_jack' AND category='nba'").fetchone()[0] == 0


def test_demote_route_refuses_when_live_attached(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    cl.post("/live/kalshi_jack/mlb/attach/%s" % WALLET, follow_redirects=False)         # WALLET now pinned AND live
    r = cl.post("/farm/mlb/demote/%s" % WALLET, follow_redirects=False)
    assert r.status_code == 303                                                         # 303 (safe no-op), never a crash
    assert _status(p, WALLET, "mlb") == farm.PINNED                                     # REFUSED: still pinned+live (live subset of pinned)


def test_live_pages_stay_read_only(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    cl.post("/live/kalshi_jack/mlb/attach/%s" % WALLET, follow_redirects=False)         # attach so the list renders
    for path in ("/live", "/live/kalshi_jack/mlb"):
        html = cl.get(path).text.lower()
        for token in ("<form", "hx-post", "place order", "/order", "disarm", 'type="submit"'):
            assert token not in html, (path, token)                                     # detach is CLI -> /live has no form
    assert cl.post("/live").status_code == 405                                          # still no POST on the list
