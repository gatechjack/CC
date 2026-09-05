"""pm_web SEARCH button -- the Farm-League discovery control (2026-09-05). Offline FastAPI TestClient.

Proves the button the way it will actually be attacked:
  - ADMIN-ONLY at the boundary: a non-admin / no-identity POST is refused SERVER-SIDE (403), not by a hidden
    button -- the same M4 gate as promote/refresh/attach (Search spends ~1900 Polymarket calls, Refresh at scale).
  - ★ SINGLE-FLIGHT tested by POSTing DIRECTLY while a run is marked running (Jack's explicit ask), NOT by
    checking the button is disabled: the second POST launches NOTHING and no second run row appears.
  - the sweep is a DETACHED subprocess: _spawn_search is stubbed so no process is launched; we assert it is
    invoked once, with the acquired run_id, on a fresh acquire -- and is NOT invoked on a refused one.
  - a launch FAILURE releases the lock (row -> error), so a failed spawn never strands 'running'.
  - FEEDBACK both halves: the running fragment self-polls (learn it FINISHED); done/idle omit the poll (stop).

Runs on the box at Gate-A (fastapi/jinja2 present).
"""
import trading_corp.prediction_markets.web.app as appmod
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, search_run

_KNOBS = dict(leaderboard_category="Sports", leaderboard_limit=250,
              min_resolved=50, recency_window_days=30, thin_sample_target=10)


def _client(monkeypatch, tmp_path, admins="jack"):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    db.init_db(p)
    if admins is None:
        monkeypatch.delenv("PM_ADMIN_IDENTITIES", raising=False)
    else:
        monkeypatch.setenv("PM_ADMIN_IDENTITIES", admins)
    return TestClient(appmod.app, raise_server_exceptions=False), p


def _stub_spawn(monkeypatch):
    """Replace the detached-subprocess launch with a recorder so no process is spawned in a test."""
    calls = []
    monkeypatch.setattr(appmod, "_spawn_search",
                        lambda run_id, *, category, db_path: calls.append(
                            {"run_id": run_id, "category": category, "db_path": db_path}))
    return calls


def _running_count(p):
    with db.connect(p) as conn:
        return conn.execute("SELECT COUNT(*) FROM pm_search_run WHERE status='running'").fetchone()[0]


# ── ADMIN GATE (boundary, not the hidden button) ──────────────────────────────────────────────────────────────
def test_search_post_as_nonadmin_forbidden_launches_nothing(monkeypatch, tmp_path):
    cl, p = _client(monkeypatch, tmp_path)
    calls = _stub_spawn(monkeypatch)
    r = cl.post("/farm/search", headers={"Remote-User": "karen"})
    assert r.status_code == 403                       # ★ proven by a POST, server-side
    assert calls == [] and _running_count(p) == 0     # nothing acquired, nothing launched


def test_search_post_no_identity_forbidden(monkeypatch, tmp_path):
    cl, p = _client(monkeypatch, tmp_path)
    _stub_spawn(monkeypatch)
    assert cl.post("/farm/search").status_code == 403          # fail-closed: no identity -> refused
    assert _running_count(p) == 0


# ── ADMIN ALLOWED: acquires the lock + launches the detached sweep ──────────────────────────────────────────────
def test_search_post_as_admin_acquires_and_launches(monkeypatch, tmp_path):
    cl, p = _client(monkeypatch, tmp_path)
    calls = _stub_spawn(monkeypatch)
    r = cl.post("/farm/search", headers={"Remote-User": "jack", "HX-Request": "true"})
    assert r.status_code == 200 and "Search underway" in r.text          # the immediate 'underway' ack
    assert len(calls) == 1                                                # launched exactly once
    with db.connect(p) as conn:
        row = conn.execute("SELECT run_id, status, params_json FROM pm_search_run").fetchone()
    assert row["status"] == "running" and calls[0]["run_id"] == row["run_id"]
    assert calls[0]["category"] == "Sports"
    assert search_run._row_params(row)["launcher"] == "ui"


def test_search_post_non_htmx_redirects(monkeypatch, tmp_path):
    cl, p = _client(monkeypatch, tmp_path)
    _stub_spawn(monkeypatch)
    r = cl.post("/farm/search", headers={"Remote-User": "jack"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/farm"      # JS-off PRG back to /farm


# ── ★ SINGLE-FLIGHT: a DIRECT second POST while running launches nothing ────────────────────────────────────────
def test_second_direct_post_while_running_launches_nothing(monkeypatch, tmp_path):
    cl, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:                                           # a live run already in flight
        first = search_run.acquire_search_lock(conn, now_ts=int(_now()), launcher="ui", **_KNOBS)
    assert first["acquired"] is True
    calls = _stub_spawn(monkeypatch)
    r = cl.post("/farm/search", headers={"Remote-User": "jack", "HX-Request": "true"})   # direct POST, bypasses UI
    assert r.status_code == 200 and "Search underway" in r.text           # shows the in-flight run, not a new one
    assert calls == []                                                    # ★ nothing launched -- the guard refused
    assert _running_count(p) == 1                                         # still exactly one run


# ── LAUNCH FAILURE releases the lock (never stranded 'running') ─────────────────────────────────────────────────
def test_launch_failure_releases_the_lock(monkeypatch, tmp_path):
    cl, p = _client(monkeypatch, tmp_path)

    def _boom(run_id, *, category, db_path):
        raise OSError("cannot fork")
    monkeypatch.setattr(appmod, "_spawn_search", _boom)
    r = cl.post("/farm/search", headers={"Remote-User": "jack", "HX-Request": "true"})
    assert r.status_code == 200                                           # the request still renders a status panel
    with db.connect(p) as conn:
        row = conn.execute("SELECT status, summary FROM pm_search_run").fetchone()
    assert row["status"] == "error" and "failed to launch" in (row["summary"] or "")
    assert _running_count(p) == 0                                         # NOT stranded running
    # and a fresh run can be started afterwards (the reclaim path is clear)
    calls = _stub_spawn(monkeypatch)
    cl.post("/farm/search", headers={"Remote-User": "jack", "HX-Request": "true"})
    assert len(calls) == 1


# ── STATUS endpoint + feedback both halves ──────────────────────────────────────────────────────────────────────
def test_status_endpoint_idle_then_running(monkeypatch, tmp_path):
    cl, p = _client(monkeypatch, tmp_path)
    idle = cl.get("/farm/search/status", headers={"Remote-User": "jack"})
    assert idle.status_code == 200 and "No search has run yet" in idle.text
    assert 'hx-get="/farm/search/status"' not in idle.text                # idle does NOT poll
    with db.connect(p) as conn:
        search_run.acquire_search_lock(conn, now_ts=int(_now()), launcher="ui", **_KNOBS)
    running = cl.get("/farm/search/status", headers={"Remote-User": "jack"}).text
    assert "Search underway" in running
    assert 'hx-get="/farm/search/status"' in running                      # ★ running self-polls -> learn it FINISHED


def test_done_status_reports_count_and_stops_polling(monkeypatch, tmp_path):
    cl, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        rid = search_run.acquire_search_lock(conn, now_ts=int(_now()), launcher="ui", **_KNOBS)["run_id"]
        search_run.close_search_run(conn, rid, finished_ts=int(_now()), n_discovered=50, n_backfilled=47,
                                    status="ok", summary="{}", n_candidates_written=134)
    html = cl.get("/farm/search/status", headers={"Remote-User": "jack"}).text
    assert "134" in html and "finished" in html
    assert 'hx-get="/farm/search/status"' not in html                     # polling STOPS once done


# ── the panel is admin-only (UI hint atop the server gate) ──────────────────────────────────────────────────────
def test_farm_page_shows_panel_to_admin_only(monkeypatch, tmp_path):
    cl, p = _client(monkeypatch, tmp_path)
    admin_html = cl.get("/farm", headers={"Remote-User": "jack"}).text
    assert "Prospect discovery" in admin_html and "Run Search" in admin_html
    assert "may briefly compete with live copying" in admin_html          # the honest warning is on the page
    karen_html = cl.get("/farm", headers={"Remote-User": "karen"}).text
    assert "Prospect discovery" not in karen_html and "Run Search" not in karen_html


def _now():
    # a fixed clock for the tests that pre-seed rows; the app itself uses time.time().
    import time as _t
    return _t.time()
