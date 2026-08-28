"""Stage 2, phase 1 -- navigation skeleton (Dashboard shell -> Farm-League tiles -> per-category page).
Offline; FastAPI TestClient over a seeded schema-9 PM DB. Encodes the phase-1 bar:

  - the three NEW routes resolve (/dashboard, /farm, /farm/{category});
  - the legacy /, /scoreboard, /farm still resolve (built ALONGSIDE, nothing removed/rewritten);
  - the category TILES are driven by the ACTIVE funnel (never a hardcoded 15) -- a deactivated category
    yields NO tile;
  - the per-category page KNOWS its category (heading/breadcrumb);
  - a DEACTIVATED (or unknown / nonexistent) category is NOT reachable by URL -> 404 (the specific test
    Part B asked for);
  - the Watchlist(paper) and Prospects(completed) regions read SEPARATE bases -- a BASIS-style check where a
    cross-wire (both regions reading one base) would make the two counts equal and FAIL the test;
  - F-3 vocabulary: the screen renders 'Watchlist'/'Prospects'; the code words 'pinned'/'candidate' do NOT leak.

Spec: reports/prediction_markets/PM_REQUIREMENTS.md (three lists / three bases; F-3 vocabulary);
PM_REBUILD_PLAN Stage 2 (phase 1 = navigation skeleton).
"""
import re

from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db

NOW = 1_700_000_000


def _pin(conn, wallet, category, *, status="pinned", active=1):
    """Minimal pm_watchlist row (schema 9). `active` is explicit so a DEACTIVATED pair (Stage-0 removal) can
    be seeded. Everything the farm readers need is LEFT-joined, so a bare watchlist row is enough."""
    conn.execute(
        "INSERT INTO pm_watchlist (wallet, category, status, active, added_ts) VALUES (?,?,?,?,?)",
        (wallet, category, status, active, NOW),
    )


def _client(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)          # connect() reads the DB path from env, per request
    db.init_db(p)                                # fresh, fully migrated (schema 9)
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app), p


# ── routes resolve (new + legacy alongside) ──────────────────────────────────────────────────────

def test_dashboard_route_resolves(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _pin(conn, "0xaaa", "mlb"); _pin(conn, "0xbbb", "nba")
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "Farm League" in body
    assert 'href="/farm"' in body                 # the menu option links into the hierarchy
    # Live sub-divisions are P3 -> present but DISABLED, honest (no fake data)
    assert "Live sub-divisions" in body and "coming in P3" in body
    assert "pm-menu-card-disabled" in body
    # data-driven category count = 2 (the two seeded active categories), not a hardcoded number
    assert 'pm-region-count">2<' in body


# (phase 3) The legacy alongside-resolves test was removed: the flat scoreboard + farm pages are RETIRED.
# Their retirement (/, /farm now serve the hierarchy; /scoreboard + the temp paths 404) is asserted in
# test_stage2_phase3.py.


# ── tiles are driven by the ACTIVE data, never a hardcoded list ───────────────────────────────────

def test_farm_league_tiles_are_data_driven(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _pin(conn, "0xa", "mlb"); _pin(conn, "0xb", "nba"); _pin(conn, "0xc", "ufc")
        _pin(conn, "0xd", "cbb", active=0)               # DEACTIVATED -> no tile
        _pin(conn, "0xe", "unknown", active=0)           # DEACTIVATED -> no tile
    r = client.get("/farm")
    assert r.status_code == 200
    body = r.text
    # exactly the 3 ACTIVE categories render as tiles ...
    assert 'href="/farm/mlb"' in body
    assert 'href="/farm/nba"' in body
    assert 'href="/farm/ufc"' in body
    # ... and the deactivated ones are ABSENT (a hardcoded 15 would silently show them)
    assert 'href="/farm/cbb"' not in body
    assert 'href="/farm/unknown"' not in body
    # header count reflects the active set (3), not a hardcoded number
    assert "<strong>3</strong>" in body
    # category NAMES render UPPERCASE for display; the URL in the href stays lowercase
    assert 'pm-tile-name">MLB<' in body
    assert 'pm-tile-name">NBA<' in body


# ── the per-category page knows its category ──────────────────────────────────────────────────────

def test_category_page_knows_its_category(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _pin(conn, "0xa", "mlb")
    r = client.get("/farm/mlb")
    assert r.status_code == 200
    body = r.text
    assert '<h1 class="pm-h1">MLB</h1>' in body               # heading IS the category, UPPERCASE
    assert 'href="/farm">Farm League</a>' in body      # breadcrumb back up the hierarchy
    # consistent casing: the category NAME renders UPPERCASE in every DISPLAY context (h1 / breadcrumb / title).
    # The lowercase slug appears ONLY inside URL paths (/watchlist/.../mlb, /whale/.../mlb) now that phase 2
    # renders rows -- assert every lowercase 'mlb' is a URL path segment (preceded by '/'), never display text.
    assert all(body[m.start() - 1] == '/' for m in re.finditer('mlb', body))
    # F-3: screen words present; internal code words NOT leaked to the UI
    assert "Watchlist" in body and "Prospects" in body
    assert "pinned" not in body and "candidate" not in body


# ── a deactivated / unknown / nonexistent category does NOT render a page (THE specific test) ──────

def test_deactivated_category_not_reachable_by_url(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _pin(conn, "0xa", "mlb")                         # active
        _pin(conn, "0xd", "cbb", active=0)               # DEACTIVATED (Stage-0 removed)
    # a deactivated category MUST NOT render a page, even by typing its URL
    assert client.get("/farm/cbb").status_code == 404
    # an unknown / nonexistent category also 404s (not a fabricated page)
    assert client.get("/farm/banana").status_code == 404
    # and the active one IS reachable (case-insensitive on the path segment)
    assert client.get("/farm/mlb").status_code == 200
    assert client.get("/farm/MLB").status_code == 200


# ── the two regions read SEPARATE bases (BASIS test) ──────────────────────────────────────────────

def test_watchlist_and_prospects_read_separate_bases(tmp_path, monkeypatch):
    """Seed DIFFERENT counts on the two bases in ONE category. Watchlist(pinned/paper)=2,
    Prospects(candidate/completed)=1. If the two regions shared a data path (a cross-wire), the counts would
    be equal -- this asserts they differ AND that each region declares its own basis.
    (Phase-2 note: Prospects are now RANKED via query_scoreboard, so the candidate needs completed stats
    above the ranker floor to surface -- a bare candidate row would rank as nothing.)"""
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _pin(conn, "0xw1", "mlb", status="pinned")       # Watchlist -> paper basis
        _pin(conn, "0xw2", "mlb", status="pinned")       # Watchlist -> paper basis
        _pin(conn, "0xc1", "mlb", status="candidate")    # Prospects -> completed basis
        conn.execute("INSERT INTO pm_category_stats (wallet, category, n_resolved, roi, win_rate, updated_ts) "
                     "VALUES (?,?,?,?,?,?)", ("0xc1", "mlb", 20, 0.10, 0.55, NOW))
    r = client.get("/farm/mlb")
    assert r.status_code == 200
    body = r.text
    wl = re.search(r'id="pm-region-watchlist"\s+data-basis="([^"]+)"\s+data-count="(\d+)"', body)
    pr = re.search(r'id="pm-region-prospects"\s+data-basis="([^"]+)"\s+data-count="(\d+)"', body)
    assert wl and pr, "both regions must render with an explicit basis + count"
    assert wl.group(1) == "paper" and wl.group(2) == "2"       # paper basis, 2 pinned pairs
    assert pr.group(1) == "completed" and pr.group(2) == "1"   # completed basis, 1 candidate pair
    assert wl.group(2) != pr.group(2)                          # distinct -> not one shared data path
