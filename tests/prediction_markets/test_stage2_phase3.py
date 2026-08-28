"""Stage 2, phase 3 -- REPOINT AND RETIRE. The Farm-League hierarchy now serves the GOOD URLs; the flat
scoreboard/farm pages AND the temporary /dashboard,/farm-league paths are GONE. Offline; FastAPI TestClient.

The proof INVERTS from phases 1-2: this phase DESTROYS the legacy pages, so / and /farm must CHANGE (serve the
new pages), the old + temporary paths must 404 (no permanent aliases), and the per-category content survives the
move. Base consolidation: ONE shell (pm_shell.html) -- the whale detail pages moved onto it and pm_base.html is
retired. Rendering every page type here is ALSO the dangling-reference check: a template still extending/including
a deleted file would 500, not 200/404.
"""
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db

NOW = 1_700_000_000


def _client(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    db.init_db(p)
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app), p


def _whale(conn, wallet, name=None, backfill=1):
    conn.execute("INSERT INTO pm_whale (wallet, user_name, first_seen_ts, last_refresh_ts, backfill_complete) "
                 "VALUES (?,?,?,?,?)", (wallet, name, NOW, NOW, backfill))


def _pin(conn, wallet, category, *, status="pinned", active=1):
    conn.execute("INSERT INTO pm_roster (wallet, category, active, last_polled_ts, added_ts) VALUES (?,?,1,?,?)",
                 (wallet, category, NOW, NOW))
    conn.execute("INSERT INTO pm_watchlist (wallet, category, status, active, added_ts) VALUES (?,?,?,?,?)",
                 (wallet, category, status, active, NOW))


# ── the good URLs now serve the hierarchy (they CHANGED -- byte-identical would mean the repoint didn't take) ──

def test_root_serves_dashboard(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _pin(conn, "0xa", "mlb"); _pin(conn, "0xb", "nba")
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "pm-menu-card" in body                          # the DASHBOARD menu (not the retired scoreboard table)
    assert "Live sub-divisions" in body and "coming in P3" in body
    assert 'href="/farm"' in body                          # the Farm League menu option links to the tiles
    assert 'pm-region-count">2<' in body                   # data-driven category count (2 seeded)


def test_farm_serves_tile_grid(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _pin(conn, "0xa", "mlb"); _pin(conn, "0xb", "ufc")
    r = client.get("/farm")
    assert r.status_code == 200
    body = r.text
    assert 'class="pm-tilegrid"' in body                   # the TILE GRID (not the retired flat farm)
    assert 'href="/farm/mlb"' in body and 'href="/farm/ufc"' in body
    assert "Kalshi-copyable categories" in body


def test_farm_category_serves_filled_page(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _pin(conn, "0xa", "mlb")
    r = client.get("/farm/mlb")
    assert r.status_code == 200
    body = r.text
    assert 'id="pm-region-watchlist"' in body and 'id="pm-region-prospects"' in body
    assert '<h1 class="pm-h1">MLB</h1>' in body             # the per-category page moved intact


# ── the deactivated-category URL guard STILL holds at the NEW path ────────────────────────────────

def test_deactivated_category_404_at_new_path(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _pin(conn, "0xa", "mlb")                            # active
        _pin(conn, "0xd", "cbb", active=0)                  # deactivated
    assert client.get("/farm/cbb").status_code == 404       # a deactivated category is NOT reachable at /farm/{cat}
    assert client.get("/farm/banana").status_code == 404    # nor an unknown one
    assert client.get("/farm/mlb").status_code == 200       # the active one is


# ── the old + TEMPORARY paths are GONE (retire + NO aliases) ──────────────────────────────────────

def test_retired_and_temporary_paths_404(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _pin(conn, "0xa", "mlb")
    # RETIRED legacy pages (F-4: the scoreboard PAGE goes; the query_scoreboard FUNCTION lives on as the ranker)
    assert client.get("/scoreboard").status_code == 404
    assert client.get("/farm/list").status_code == 404      # the flat-farm HTMX fragment
    # REMOVED temporary paths -- no permanent aliases (the checklist forbids them)
    assert client.get("/dashboard").status_code == 404
    assert client.get("/farm-league").status_code == 404
    assert client.get("/farm-league/mlb").status_code == 404
    # /healthz keeps working
    assert client.get("/healthz").status_code == 200


# ── base consolidation: ONE shell -- whale pages moved onto pm_shell; pm_base retired ─────────────

def test_whale_pages_render_under_one_shell(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _whale(conn, "0xw", "W")
    for path in ("/whale/0xw", "/whale/0xw/mlb"):
        r = client.get(path)
        assert r.status_code == 200                          # renders => no dangling `extends pm_base.html` (else 500)
        body = r.text
        # the pm_shell nav (Dashboard / Farm League / Live sub-divisions) proves ONE shell, not the old pm_base nav
        assert ">Dashboard</a>" in body and 'href="/farm"' in body and "Live sub-divisions" in body


# ── every internal link points at the hierarchy, never the temp/legacy paths ──────────────────────

def test_no_temp_or_legacy_links_anywhere(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _whale(conn, "0xa"); _pin(conn, "0xa", "mlb")
    for path in ("/", "/farm", "/farm/mlb", "/watchlist/0xa/mlb", "/whale/0xa"):
        body = client.get(path).text
        assert "/dashboard" not in body                      # the temp dashboard path is gone from every link
        assert "/farm-league" not in body                    # the temp farm-league path is gone from every link


# ── vocabulary still clean ────────────────────────────────────────────────────────────────────────

def test_vocab_still_clean(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _whale(conn, "0xa"); _pin(conn, "0xa", "mlb")
    for path in ("/", "/farm", "/farm/mlb", "/watchlist/0xa/mlb"):
        body = client.get(path).text
        assert "pinned" not in body and "candidate" not in body


# ── preserved from the retired scoreboard-render suite: the freshness band the Prospects section uses ──
# (test_scoreboard_render.py rendered the now-deleted scoreboard PAGE; its page tests are obsolete. This one
# pure-function test is basis-relevant -- the Prospects section stamps the same refresh band -- so it survives.)

def test_refresh_band_state_thresholds():
    from trading_corp.prediction_markets import stats
    assert stats.refresh_band_state(NOW - 2 * 86400, NOW)["band"] == "green"    # 2d
    assert stats.refresh_band_state(NOW - 10 * 86400, NOW)["band"] == "amber"   # 10d
    assert stats.refresh_band_state(NOW - 20 * 86400, NOW)["band"] == "red"     # 20d
    none_band = stats.refresh_band_state(None, NOW)
    assert none_band["band"] == "red" and none_band["note"]                     # missing => red + honest note
