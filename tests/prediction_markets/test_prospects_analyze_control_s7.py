"""Prospects Analyze control (2026-09-02): the omission caveat's "Analyze" is now an actual UNGATED Analyze BUTTON on
un-analyzed prospect rows, so "analyze -> see the omission -> decide on promotion" is doable from the list. Guards:
(1) the control is present on un-analyzed rows and is a real hx-post to the analyze route; (2) an ANALYZED row shows
the FIGURE and NO re-run invite (cost-safety: the button only where a spend is legitimate); (3) NO bulk / analyze-all
control; (4) the control is UNGATED (Karen judges too, R3) while Promote/Refresh stay in the action cell untouched.
Offline, PM DB only. (The OOB row-update + route-ungated live in test_analyze_route_s5.py, which has the fake client.)
"""
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, farm

NOW = 1_700_000_000
CAND = "0xcandidatewhaleaaaaaaaaaaaaaaaaaaaaaaaa"


def _mk(monkeypatch, tmp_path, identity="jack"):
    p = str(tmp_path / "pm.db"); monkeypatch.setenv("PM_DB_PATH", p); db.init_db(p)
    with db.connect(p) as c:
        c.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts) "
                  "VALUES('0xpinnedwhale','mlb',?,1,1,1)", (farm.PINNED,))
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    from trading_corp.prediction_markets.web.app import app
    cl = TestClient(app); cl.headers.update({"Remote-User": identity})
    return cl, p


def _add_cand(p, wallet, *, roi=0.2, n=60, name="Cand"):
    with db.connect(p) as c:
        c.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts,source) "
                  "VALUES(?, 'mlb', ?, 1,1,1,'search')", (wallet, farm.CANDIDATE))
        c.execute("INSERT OR REPLACE INTO pm_whale(wallet,user_name,backfill_complete,last_refresh_ts) "
                  "VALUES(?,?,1,?)", (wallet, name, NOW))
        c.execute("INSERT INTO pm_category_stats(wallet,category,n_resolved,roi,win_rate,net_realized_pnl,updated_ts) "
                  "VALUES(?, 'mlb', ?, ?, 0.90, 100.0, 1)", (wallet, n, roi))


def _ground(p, wallet, *, omission, coverage, a_only, hw=5, hl=52):
    with db.connect(p) as c:
        c.execute("INSERT OR REPLACE INTO pm_loss_grounding_cache(wallet,category,honest_wins,honest_losses,"
                  "a_only_losses,loss_omission_pct,coverage_pct,activity_truncated,n_activity_held_resolved,"
                  "completeness,grounded_ts) VALUES(?,'mlb',?,?,?,?,?,0,?,?,?)",
                  (wallet, hw, hl, a_only, omission, coverage, (a_only or 0) + hw + hl, "complete(x)", NOW))
        c.commit()


def test_unanalyzed_row_has_a_real_analyze_button(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND)                                                  # no grounding row -> un-analyzed
    html = cl.get("/farm/mlb").text
    assert ('hx-post="/farm/analyze/%s/mlb"' % CAND) in html            # the caveat IS a real Analyze control
    assert 'hx-target="#pm-analyze-panel"' in html                     # swaps the result into the page panel
    assert "pm-omit-analyze" in html and "hx-disabled-elt" in html      # clickable + double-click/double-spend guard
    assert "Analyze</span>" in html                                     # the CTA word


def test_analyzed_row_shows_figure_and_no_rerun_invite(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND)
    _ground(p, CAND, omission=0.94, coverage=0.99, a_only=47)           # already analyzed
    html = cl.get("/farm/mlb").text
    assert "94%&nbsp;losses" in html                                    # the figure shows
    assert ('hx-post="/farm/analyze/%s/mlb"' % CAND) not in html        # NO re-run invite on an analyzed row
    assert "pm-omit-analyze" not in html                               # the button is gone -> "already analyzed"


def test_analyze_control_is_ungated_for_non_admin(monkeypatch, tmp_path):
    # Karen (non-admin) is a promotion judge too (R3): she must see + reach the Analyze control on un-analyzed rows.
    cl, p = _mk(monkeypatch, tmp_path, identity="karen")
    _add_cand(p, CAND)
    r = cl.get("/farm/mlb")
    assert r.status_code == 200
    assert ('hx-post="/farm/analyze/%s/mlb"' % CAND) in r.text          # rendered for a non-admin -> not gated


def test_no_bulk_analyze_all_control(monkeypatch, tmp_path):
    # cost-safety: a per-row control is fine (each a legitimate first-analyze), but NO single-click that spends N times.
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, "0xaaaa1", name="A"); _add_cand(p, "0xbbbb2", name="B")
    low = cl.get("/farm/mlb").text.lower()
    assert "analyze all" not in low and "analyze-all" not in low and "analyzeall" not in low


def test_promote_and_refresh_still_present_in_action_cell(monkeypatch, tmp_path):
    # the fix must not disturb the (admin-gated, server-side) Promote/Refresh actions.
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND)
    html = cl.get("/farm/mlb").text
    assert ('action="/farm/mlb/promote/%s"' % CAND) in html
    assert ('action="/farm/mlb/refresh/%s"' % CAND) in html
