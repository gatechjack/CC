"""Stage 4 R4 -- the SORTABLE PROSPECTS SCREEN. Server-side behavior via the FastAPI TestClient (the JS column
sort is progressive enhancement, not unit-testable here -- we assert its hooks are present). Offline, PM DB only.

Proves: the F-1 loss caveat is VISIBLE; win% is non-sortable with the caveat on the header; THIN-SAMPLE flags a
sub-floor candidate; per-whale LAST-UPDATED renders; default order is cost-ROI desc; the sort JS + sortable
headers are wired; the REFRESH route is POST-only (no GET mutates), 303 for a browser + an htmx partial for htmx;
and Promote is reachable against a real candidate WITHOUT auto-paper-trading.
"""
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, farm

NOW = 1_700_000_000
CAND = "0xcandidatewhaleaaaaaaaaaaaaaaaaaaaaaaaa"


def _mk(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    db.init_db(p)
    with db.connect(p) as conn:
        # a PINNED pair so 'mlb' is an active tile (else /farm/mlb 404s)
        conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts) "
                     "VALUES('0xpinnedwhale','mlb',?,1,1,1)", (farm.PINNED,))
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app), p


def _add_cand(p, wallet, *, roi=0.2, n=60, last_ts=NOW, complete=1, name="Cand"):
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts,source) "
                     "VALUES(?, 'mlb', ?, 1,1,1,'search')", (wallet, farm.CANDIDATE))
        conn.execute("INSERT OR REPLACE INTO pm_whale(wallet,user_name,backfill_complete,last_refresh_ts) "
                     "VALUES(?,?,?,?)", (wallet, name, complete, last_ts))
        conn.execute("INSERT INTO pm_category_stats(wallet,category,n_resolved,roi,win_rate,net_realized_pnl,updated_ts) "
                     "VALUES(?, 'mlb', ?, ?, 0.55, 100.0, 1)", (wallet, n, roi))


def _status(p, wallet):
    with db.connect(p) as conn:
        r = conn.execute("SELECT status FROM pm_watchlist WHERE wallet=? AND category='mlb'", (wallet,)).fetchone()
    return r["status"] if r else None


# ─────────────────────────────── render: caveat / forms / last-updated / sort hooks ───────────────────────────────

def test_prospects_render_caveat_forms_lastupdated_sorthooks(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND)
    html = cl.get("/farm/mlb").text
    assert 'data-caveat="loss-omission"' in html and 'SCREEN' in html          # F-1 caveat VISIBLE (not buried)
    assert ('action="/farm/mlb/promote/%s"' % CAND) in html                    # Promote reachable
    assert ('action="/farm/mlb/refresh/%s"' % CAND) in html                    # Refresh wired
    assert 'last&nbsp;updated' in html                                         # per-whale last-updated column
    assert '/static/pm_sort.js' in html and 'pm-sortable-table' in html        # sort JS + sortable table
    assert 'pm-sortable' in html                                               # sortable headers present


def test_winpct_non_sortable_with_caveat_on_header(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND)
    html = cl.get("/farm/mlb").text
    assert 'pm-th-caveat' in html and 'optimistic' in html and 'pm-nosort' in html   # caveat ON the header
    assert 'pm-sort-default' in html   # cost-ROI is the default sort column


def test_thin_sample_flag_visible_for_sub_floor_candidate(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND, n=12)                # < 50 floor -> came via the fallback -> THIN
    html = cl.get("/farm/mlb").text
    assert 'pm-badge-thin' in html and '>THIN<' in html


def test_no_thin_flag_for_full_sample_qualifier(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND, n=60)               # >= 50 -> full-sample qualifier
    html = cl.get("/farm/mlb").text
    assert 'pm-badge-thin' not in html


def test_default_order_cost_roi_desc(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, "0xhiroi", roi=0.50, n=60)
    _add_cand(p, "0xloroi", roi=0.05, n=60)
    html = cl.get("/farm/mlb").text
    assert html.index("0xhiroi") < html.index("0xloroi")   # higher cost-ROI loads first (the default view)


def test_partial_whale_absent_from_prospects(monkeypatch, tmp_path):
    # the completeness gate holds on the page too: a partial-backfill candidate never renders
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND, complete=0)
    html = cl.get("/farm/mlb").text
    assert CAND not in html


# ─────────────────────────────── the REFRESH route (POST-only, 303 / htmx partial) ───────────────────────────────

def test_refresh_route_post_303_and_no_get(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND)
    import trading_corp.prediction_markets.web.app as appmod
    seen = {}

    async def _fake(wallet, now_ts):
        seen["wallet"] = wallet

    monkeypatch.setattr(appmod, "_refresh_whale", _fake)   # avoid the network; test the route contract
    r = cl.post("/farm/mlb/refresh/%s" % CAND, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/farm/mlb"
    assert seen["wallet"] == CAND
    assert cl.get("/farm/mlb/refresh/%s" % CAND).status_code == 405   # GET NEVER mutates (POST-only)


def test_refresh_route_htmx_returns_prospects_partial(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND)
    import trading_corp.prediction_markets.web.app as appmod

    async def _fake(wallet, now_ts):
        return None

    monkeypatch.setattr(appmod, "_refresh_whale", _fake)
    r = cl.post("/farm/mlb/refresh/%s" % CAND, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert 'data-caveat="loss-omission"' in r.text        # the prospects FRAGMENT (caveat + table)
    assert "<html" not in r.text.lower()                  # a partial, not the full shell


class _FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def test_refresh_partial_drops_whale_and_notices(monkeypatch, tmp_path):
    """END-TO-END safety chain at the web layer (the review's gap): a refresh that comes back PARTIAL flips
    backfill_complete 1->0 -> the completeness gate drops the whale from the RE-RENDERED partial, and a NOTICE
    explains the drop (never a silent vanish, never ranked on partial data)."""
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND, n=60, complete=1)                          # starts complete -> in prospects
    # simulate a truncated refresh: mark the whale partial, no network
    import trading_corp.prediction_markets.search_run as sr

    async def _fake_refresh(conn, wallet, *, client, now_ts, **kw):
        conn.execute("UPDATE pm_whale SET backfill_complete=0 WHERE wallet=?", (wallet,))
        return {"verdict": "partial", "action": "refreshed"}

    monkeypatch.setattr(sr, "refresh_one", _fake_refresh)
    monkeypatch.setattr("trading_corp.data.polymarket_data_api_client.PolymarketDataAPIClient", _FakeClient)
    r = cl.post("/farm/mlb/refresh/%s" % CAND, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert CAND not in r.text                                     # partial whale DROPPED from the re-render
    assert 'data-refresh-notice="1"' in r.text and "INCOMPLETE" in r.text   # and EXPLAINED (not a silent vanish)


def test_roi_none_orders_last_no_crash(monkeypatch, tmp_path):
    # a candidate with NULL roi must sort LAST in the default cost-ROI-desc order, without crashing the render
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, "0xhasroi", roi=0.2, n=60)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts,source) "
                     "VALUES('0xnoroi','mlb',?,1,1,1,'search')", (farm.CANDIDATE,))
        conn.execute("INSERT INTO pm_whale(wallet,user_name,backfill_complete) VALUES('0xnoroi','NoRoi',1)")
        conn.execute("INSERT INTO pm_category_stats(wallet,category,n_resolved,roi,updated_ts) "
                     "VALUES('0xnoroi','mlb',60,NULL,1)")
    html = cl.get("/farm/mlb").text
    assert html.index("0xhasroi") < html.index("0xnoroi")   # None roi sorts last


# ─────────────────────────────── Promote reachable, NO auto-paper ───────────────────────────────

def test_promote_candidate_reachable_no_auto_paper(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND)
    with db.connect(p) as conn:
        paper_before = conn.execute("SELECT COUNT(*) FROM pm_paper_trade").fetchone()[0]
    r = cl.post("/farm/mlb/promote/%s" % CAND, follow_redirects=False)
    assert r.status_code == 303 and _status(p, CAND) == farm.PINNED     # candidate -> pinned (reachable, works)
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_paper_trade").fetchone()[0] == paper_before   # NO auto-paper
