"""Farm league category page -- LIVE-WHALE STATE + PROMOTE GUARD (board task 2026-09-11).

THE DEFECT this proves fixed: the Watchlist row used to show "Promote > <account>" for EVERY account regardless
of whether that account was already copying the whale live -- so Jack could not tell which whales were live, and a
second click re-promoted. Now, for each live account (pm_subdivision_attachment active=1) the row shows a
non-clickable "<account> Live Whale" status badge (lit lamp + attachment age) IN PLACE OF Promote; Promote stays
only for the account(s) the whale is NOT live on; the Promote route refuses a repeat attach with a 409 and never
inserts a second attachment row.

Offline, PM DB only, admin operator (the farm actions are admin-gated; the DENY is proven in test_m4_gates.py).
"""
import re

from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, farm

WALLET = "0x16bb9951a36fce71e2ef57890b786145e0ba8492"
J_ATTACH = "/live/kalshi_jack/mlb/attach/%s" % WALLET
K_ATTACH = "/live/kalshi_karen/mlb/attach/%s" % WALLET


def _mk(monkeypatch, tmp_path):
    """WALLET pinned in mlb; TWO active accounts (jack + karen) so the row offers two promote targets. karen's
    mlb sub-division is auto-created on her first attach (promote_to_live ruling 1) -- deliberately NOT seeded."""
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts) VALUES(?,?,?,1,1,1)",
                     (WALLET, "mlb", farm.PINNED))
        conn.execute("INSERT INTO pm_paper_trade(wallet,category,condition_id,outcome_index,entry_observed_ts,status,opened_ts) "
                     "VALUES(?,?,?,0,1,'open',1)", (WALLET, "mlb", "0xpa"))
        conn.execute("INSERT INTO pm_account(account_id,venue,label,active,created_ts) VALUES('kalshi_jack','kalshi','Jack (KALSHI)',1,1)")
        conn.execute("INSERT INTO pm_account(account_id,venue,label,active,created_ts) VALUES('kalshi_karen','kalshi','Karen',1,1)")
        conn.execute("INSERT INTO pm_subdivision(account_id,category,label,sizing_mode,fixed_stake_usd,active,created_ts) "
                     "VALUES('kalshi_jack','mlb','Jack MLB','fixed',5.0,1,1)")
    from trading_corp.prediction_markets.web.app import app
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    cl = TestClient(app); cl.headers.update({"Remote-User": "jack"})
    return cl, p


def _attach_count(p, wallet=WALLET):
    with db.connect(p) as conn:
        return conn.execute("SELECT COUNT(*) FROM pm_subdivision_attachment WHERE active=1 AND wallet=?",
                            (wallet,)).fetchone()[0]


def _row_count(p):
    """Total attachment ROWS for WALLET (active or not) -- the guard must never INSERT a second one."""
    with db.connect(p) as conn:
        return conn.execute("SELECT COUNT(*) FROM pm_subdivision_attachment WHERE wallet=?", (WALLET,)).fetchone()[0]


# ── the row's LIVE-vs-PROMOTE state ──────────────────────────────────────────────────────────────────────────

def test_no_attachment_shows_both_promotes_no_badge(monkeypatch, tmp_path):
    """Baseline: the whale is live on NEITHER account -> a Promote for each, and NO live badge (nothing to lie about)."""
    cl, _p = _mk(monkeypatch, tmp_path)
    html = cl.get("/farm/mlb").text
    assert ('action="%s"' % J_ATTACH) in html            # Promote > Jack present
    assert ('action="%s"' % K_ATTACH) in html            # Promote > Karen present
    assert 'class="fl-live"' not in html                 # no live badge
    assert "Live Whale" not in html


def test_attached_one_account_badge_replaces_only_that_promote(monkeypatch, tmp_path):
    """Attach on jack only -> "Jack Live Whale" badge (with lamp + age) REPLACES the jack Promote; the karen
    Promote is untouched (still a button, because she is not live on this whale)."""
    cl, p = _mk(monkeypatch, tmp_path)
    assert cl.post(J_ATTACH, follow_redirects=False).status_code == 303
    html = cl.get("/farm/mlb").text
    assert 'class="fl-live"' in html and "Jack Live Whale" in html      # badge shown for jack
    assert 'class="fl-lamp"' in html                                    # the lit lamp
    assert re.search(r'class="fl-age">[^<]*ago', html)                  # attachment age rendered ("Ns ago" etc.)
    assert ('action="%s"' % J_ATTACH) not in html                      # jack Promote GONE (badge instead)
    assert ('action="%s"' % K_ATTACH) in html                          # karen Promote still there
    assert "Karen Live Whale" not in html                              # karen is not live -> no karen badge


def test_attached_both_accounts_two_badges_no_promote(monkeypatch, tmp_path):
    """Live on BOTH -> both badges, and NOT A SINGLE Promote-to-live for this whale (nothing left to promote)."""
    cl, p = _mk(monkeypatch, tmp_path)
    assert cl.post(J_ATTACH, follow_redirects=False).status_code == 303
    assert cl.post(K_ATTACH, follow_redirects=False).status_code == 303   # auto-creates karen/mlb + attaches
    assert _attach_count(p) == 2
    html = cl.get("/farm/mlb").text
    assert "Jack Live Whale" in html and "Karen Live Whale" in html
    assert ('action="%s"' % J_ATTACH) not in html
    assert ('action="%s"' % K_ATTACH) not in html
    assert html.count('class="fl-live"') == 2                           # exactly the two badges


# ── the server-side PROMOTE guard (backstops a stale page / direct POST) ──────────────────────────────────────

def test_second_promote_refused_409_and_no_new_row(monkeypatch, tmp_path):
    """A repeat attach is REFUSED 409 (loud, not a silent re-redirect) and inserts NO second row (PK + UPSERT)."""
    cl, p = _mk(monkeypatch, tmp_path)
    r1 = cl.post(J_ATTACH, follow_redirects=False)
    assert r1.status_code == 303 and _attach_count(p) == 1 and _row_count(p) == 1
    r2 = cl.post(J_ATTACH, follow_redirects=False)                       # second click / stale page
    assert r2.status_code == 409
    assert "already" in r2.text.lower() and "kalshi_jack" in r2.text
    assert _attach_count(p) == 1 and _row_count(p) == 1                  # STILL one attachment, no duplicate inserted


def test_demote_refused_409_names_attachment_and_cli(monkeypatch, tmp_path):
    """Demote of a live-attached whale REFUSES with a 409 that SAYS WHY (names the live attachment + the CLI
    detach), never a silent 303; the whale stays pinned (live is a subset of pinned)."""
    cl, p = _mk(monkeypatch, tmp_path)
    cl.post(J_ATTACH, follow_redirects=False)
    r = cl.post("/farm/mlb/demote/%s" % WALLET, follow_redirects=False)
    assert r.status_code == 409
    assert "kalshi_jack/mlb" in r.text and "detach" in r.text.lower()
    with db.connect(p) as conn:
        assert conn.execute("SELECT status FROM pm_watchlist WHERE wallet=? AND category='mlb'",
                            (WALLET,)).fetchone()["status"] == farm.PINNED


# ── the shipped CSS carries the badge + one-line layout (guards against the stylesheet being dropped) ──────────

def test_pm_css_ships_badge_and_oneline_layout():
    import os
    from trading_corp.prediction_markets.web import app as appmod
    css = open(os.path.join(appmod._STATIC_DIR, "pm.css"), encoding="utf-8").read()
    assert ".fl-live{" in css and ".fl-live .fl-lamp{" in css            # the badge + its lamp
    assert re.search(r"\.pm-actioncell\{[^}]*display:flex", css)         # action cell = one wrapping row
