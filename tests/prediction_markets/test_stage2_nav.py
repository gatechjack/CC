"""Stage 2, phase 1 -- navigation skeleton (Dashboard shell -> Farm-League tiles -> per-category page).
Offline; FastAPI TestClient over a seeded schema-9 PM DB. Encodes the phase-1 bar:

  - the three NEW routes resolve (/dashboard, /farm, /farm/{category});
  - the legacy /, /scoreboard, /farm still resolve (built ALONGSIDE, nothing removed/rewritten);
  - the category TILES are the ruled 16-category ALLOWLIST (Jack 2026-08-30, the tile-vanish fix): a category
    EXISTS by allowlist membership, NOT by having pinned whales -- so an empty watchlist still renders its tile,
    while a NON-allowlist category (cbb/fifwc/nascar/unknown) yields NO tile and 404s;
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
    # M2 R1: / is the ACCOUNTS OVERVIEW; Farm League stays a nav peer; the flat "Live sub-divisions" nav item is
    # superseded by per-account -> sub-divisions. No pm_account seeded here (schema 9) -> honest-empty accounts.
    assert "Farm League" in body and 'href="/farm"' in body
    assert ">Accounts</a>" in body                 # the new top-of-hierarchy
    assert "No accounts yet" in body               # no pm_account seeded -> honest-empty (never an error)


# (phase 3) The legacy alongside-resolves test was removed: the flat scoreboard + farm pages are RETIRED.
# Their retirement (/, /farm now serve the hierarchy; /scoreboard + the temp paths 404) is asserted in
# test_stage2_phase3.py.


# ── tiles are the ruled ALLOWLIST (Jack 2026-08-30) -- NOT driven by pinned rows ───────────────────

def test_farm_league_tiles_are_the_allowlist(tmp_path, monkeypatch):
    """DELIBERATE REVERSAL (Jack 2026-08-30, the tile-vanish defect). Pre-fix the tiles were driven by ACTIVE
    PINNED rows -- so emptying a category's watchlist vanished its tile AND 404'd its page, STRANDING its
    prospects (the exact bug: promote 3 into ATP, demote them all, ATP disappears). The RULE now: a category
    EXISTS iff it is in the 16-category allowlist. So ALL 16 render as tiles regardless of pinned data (an empty
    watchlist is legitimate); a NON-allowlist category never renders, even with an active pinned whale."""
    from trading_corp.prediction_markets import search
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _pin(conn, "0xa", "mlb")                             # a category WITH a pinned whale
        _pin(conn, "0xc", "ufc", status="candidate")         # a category with ONLY a candidate (empty watchlist)
        _pin(conn, "0xd", "cbb", active=0)                   # NON-allowlist + deactivated -> no tile
        _pin(conn, "0xe", "unknown")                         # NON-allowlist WITH an active pin -> STILL no tile
        _pin(conn, "0xf", "nascar")                          # NON-allowlist -> no tile
        # nhl / wnba / epl / ... have NO rows at all, yet must STILL render (allowlist membership, not data)
    r = client.get("/farm")
    assert r.status_code == 200
    body = r.text
    # EVERY allowlist category renders a tile -- incl. ones with no pinned, only-candidate, or NO rows at all
    for c in search.CATEGORY_ALLOWLIST:
        assert ('href="/farm/%s"' % c) in body, "allowlist category %s must render a tile" % c
    # non-allowlist categories NEVER render (deactivated by omission), regardless of any pm_watchlist row
    for c in ("cbb", "unknown", "nascar", "fifwc"):
        assert ('href="/farm/%s"' % c) not in body
    # header count = the FULL allowlist (16), not the pinned-driven number
    assert "<strong>16</strong>" in body
    # category NAMES render UPPERCASE for display; the URL in the href stays lowercase
    assert 'pm-tile-name">MLB<' in body


def test_empty_watchlist_category_renders_tile_and_page(tmp_path, monkeypatch):
    """THE defect made explicit (Jack 2026-08-30): a category with PROSPECTS and an EMPTY WATCHLIST must still
    render its tile AND its page -- Watchlist honest-empty, Prospects populated -- so the prospects are never
    stranded behind a missing tile. Models ATP after every pinned whale was demoted: 0 pinned, candidates intact.
    A naive 'tiles render' test would pass while this case broke, so the empty-watchlist case is asserted head-on."""
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        # atp: one candidate with completed stats (ranks into Prospects) + ZERO pinned atp (empty Watchlist)
        _pin(conn, "0xcand", "atp", status="candidate", active=1)
        conn.execute("INSERT INTO pm_category_stats (wallet, category, n_resolved, roi, win_rate, updated_ts) "
                     "VALUES (?,?,?,?,?,?)", ("0xcand", "atp", 60, 0.20, 0.55, NOW))
        conn.execute("INSERT INTO pm_whale (wallet, backfill_complete) VALUES ('0xcand', 1)")   # completeness gate
    # the tile renders on the league page (allowlist), despite 0 pinned atp
    assert 'href="/farm/atp"' in client.get("/farm").text
    # the page renders (was a 404 pre-fix) ...
    r = client.get("/farm/atp")
    assert r.status_code == 200
    body = r.text
    # ... Watchlist honest-empty (0), Prospects populated (>=1) -- the two sections read separate bases and
    # Prospects does not depend on the watchlist at all.
    wl = re.search(r'id="pm-region-watchlist"\s+data-basis="([^"]+)"\s+data-count="(\d+)"', body)
    pr = re.search(r'id="pm-region-prospects"\s+data-basis="([^"]+)"\s+data-count="(\d+)"', body)
    assert wl and pr, "both regions must render with an explicit basis + count"
    assert wl.group(1) == "paper" and wl.group(2) == "0"       # empty watchlist -> honest-empty, NOT a missing page
    assert pr.group(1) == "completed" and int(pr.group(2)) >= 1  # prospects reachable


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
    """The Stage 2 phase 1 URL guard still holds under the allowlist rule: a NON-allowlist category is unreachable
    by URL -> 404, even WITH an active pinned whale (existence is allowlist membership, not pinned rows; Jack
    2026-08-30). cbb / fifwc / unknown stay unreachable by typing the path."""
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _pin(conn, "0xa", "mlb")                         # allowlist -> reachable
        _pin(conn, "0xd", "cbb", active=1)               # NON-allowlist WITH an active pinned whale -> STILL 404
    assert client.get("/farm/cbb").status_code == 404    # not in the allowlist, active pin notwithstanding
    assert client.get("/farm/fifwc").status_code == 404  # deactivated by omission
    assert client.get("/farm/unknown").status_code == 404
    assert client.get("/farm/banana").status_code == 404  # nonexistent -> not a fabricated page
    # and an allowlist category IS reachable (case-insensitive on the path segment)
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
        # completeness gate (2026-08-29): query_scoreboard now ranks ONLY backfill_complete=1 whales
        conn.execute("INSERT INTO pm_whale (wallet, backfill_complete) VALUES ('0xc1', 1)")
    r = client.get("/farm/mlb")
    assert r.status_code == 200
    body = r.text
    wl = re.search(r'id="pm-region-watchlist"\s+data-basis="([^"]+)"\s+data-count="(\d+)"', body)
    pr = re.search(r'id="pm-region-prospects"\s+data-basis="([^"]+)"\s+data-count="(\d+)"', body)
    assert wl and pr, "both regions must render with an explicit basis + count"
    assert wl.group(1) == "paper" and wl.group(2) == "2"       # paper basis, 2 pinned pairs
    assert pr.group(1) == "completed" and pr.group(2) == "1"   # completed basis, 1 candidate pair
    assert wl.group(2) != pr.group(2)                          # distinct -> not one shared data path
