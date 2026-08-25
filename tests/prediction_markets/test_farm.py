"""CP3b-1 farm-league READ-ONLY queries + page. Offline; tmp DB only. Covers the THREE-STATE ZERO
(never_polled / polled_none_open / polled_has_open -- must stay DISTINCT, never one '0'), the
4751346/nfl quarantine-zero edge (n_resolved=0 but n_excluded>0 -> a reason, not 'no activity'), the
'unknown' category rendered honestly, DATA-DRIVEN tabs (not a hardcoded 4), and the Candidates
'no search has run yet' honest-empty. Builds against the shipped five-status pm_paper_trade.

Spec: reports/prediction_markets/P2_PLAN.md (farm league); CP3b-1 rulings 2026-08-25.
"""
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, farm

NOW = 1_700_000_000


def _whale(conn, wallet, name=None, backfill=1):
    conn.execute("INSERT INTO pm_whale (wallet, user_name, first_seen_ts, backfill_complete) VALUES (?,?,?,?)",
                 (wallet, name, NOW, backfill))


def _cstats(conn, wallet, category, *, n_resolved=None, n_excluded=None, roi=None, awp=None, net=None,
            win_rate=None, n_cids=None, two_sided=None):
    # Migration-004 caveat columns (two_sided_pct, n_condition_ids, ...) are NOT NULL DEFAULT 0. Insert ONLY
    # the values we actually set, so an unset one falls back to its DEFAULT instead of an explicit NULL (which
    # NOT NULL rejects). single_game_pct stays unset -> NULL (nullable; NULL-for-Fed by design).
    cols = {"n_resolved": n_resolved, "n_excluded": n_excluded, "roi": roi, "avg_win_price": awp,
            "net_realized_pnl": net, "win_rate": win_rate, "n_condition_ids": n_cids, "two_sided_pct": two_sided}
    names, vals = ["wallet", "category", "updated_ts"], [wallet, category, NOW]
    for col, v in cols.items():
        if v is not None:
            names.append(col); vals.append(v)
    conn.execute("INSERT INTO pm_category_stats (%s) VALUES (%s)"
                 % (", ".join(names), ", ".join(["?"] * len(vals))), vals)


def _pin(conn, wallet, category, *, last_polled_ts=None, status="pinned"):
    conn.execute("INSERT INTO pm_roster (wallet, category, active, last_polled_ts, added_ts) VALUES (?,?,1,?,?)",
                 (wallet, category, last_polled_ts, NOW))
    conn.execute("INSERT INTO pm_watchlist (wallet, category, status, added_ts) VALUES (?,?,?,?)",
                 (wallet, category, status, NOW))


def _paper_open(conn, wallet, category, cid, n=1):
    for i in range(n):
        conn.execute("INSERT INTO pm_paper_trade (wallet, category, condition_id, outcome_index, "
                     "entry_observed_ts, opened_ts) VALUES (?,?,?,?,?,?)",
                     (wallet, category, "%s%d" % (cid, i), 0, NOW + i, NOW + i))


def _seed(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        # State 3 -- polled, HAS 2 open
        _whale(conn, "0xhasopen", "HasOpen")
        _cstats(conn, "0xhasopen", "mlb", n_resolved=50, roi=0.10, awp=0.60, net=100, win_rate=0.5, n_cids=40, two_sided=0.1)
        _pin(conn, "0xhasopen", "mlb", last_polled_ts=NOW)
        _paper_open(conn, "0xhasopen", "mlb", "0xc", n=2)
        # State 2 -- polled, NOTHING open
        _whale(conn, "0xnoneopen", "NoneOpen")
        _cstats(conn, "0xnoneopen", "mlb", n_resolved=20, roi=-0.05, awp=0.80, net=-10, win_rate=0.4, n_cids=15, two_sided=0.2)
        _pin(conn, "0xnoneopen", "mlb", last_polled_ts=NOW)
        # State 1 -- NEVER polled (last_polled_ts IS NULL)
        _whale(conn, "0xnever", "Never")
        _cstats(conn, "0xnever", "ufc", n_resolved=5, roi=0.20, awp=0.50, net=5, win_rate=0.6, n_cids=5)
        _pin(conn, "0xnever", "ufc", last_polled_ts=None)
        # Quarantine-zero edge -- n_resolved=0 but n_excluded>0 (the 4751346/nfl case)
        _whale(conn, "0xquar", "Quar")
        _cstats(conn, "0xquar", "nfl", n_resolved=0, n_excluded=7)
        _pin(conn, "0xquar", "nfl", last_polled_ts=NOW)
        # 'unknown' category -- pinned + paper-traded, rendered honestly (never hidden)
        _whale(conn, "0xunk", "Unk")
        _cstats(conn, "0xunk", "unknown", n_resolved=1444, roi=0.01, awp=0.90, net=50, win_rate=0.5, n_cids=1000, two_sided=0.3)
        _pin(conn, "0xunk", "unknown", last_polled_ts=NOW)
        conn.commit()
    return p


# ---------- poll_state (the three-state zero -- pure) ----------
def test_poll_state_three_distinct():
    assert farm.poll_state(None, 0) == farm.POLL_NEVER          # NULL ts -> never OBSERVED
    assert farm.poll_state(None, 5) == farm.POLL_NEVER          # NULL ts wins even if a stray count exists
    assert farm.poll_state(NOW, 0) == farm.POLL_NONE_OPEN       # observed, nothing open
    assert farm.poll_state(NOW, 3) == farm.POLL_HAS_OPEN        # observed, n open
    # the three tokens are genuinely different (the whole point)
    assert len({farm.POLL_NEVER, farm.POLL_NONE_OPEN, farm.POLL_HAS_OPEN}) == 3


# ---------- data-driven tabs ----------
def test_farm_categories_data_driven(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        cats = farm.farm_categories(conn, farm.PINNED)
    assert cats == ["mlb", "nfl", "ufc", "unknown"]            # sorted; NOT a hardcoded MLB/UFC/NBA/Fed


# ---------- every pinned pair displayed, never filtered/ranked out ----------
def test_all_pinned_displayed_no_min_resolved_filter(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        rows = farm.farm_rows(conn, status=farm.PINNED)
    assert len(rows) == 5                                      # incl n_resolved=5 (below DEFAULT_MIN_RESOLVED=10) and n_resolved=0 (quarantined)
    by = {(r["wallet"], r["category"]): r for r in rows}
    assert by[("0xnever", "ufc")]["n_resolved"] == 5           # NOT dropped by a min-resolved gate
    assert by[("0xquar", "nfl")]["n_resolved"] == 0            # the quarantine-zero pair still shows


def test_three_state_on_real_rows(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        rows = {(r["wallet"], r["category"]): r for r in farm.farm_rows(conn, status=farm.PINNED)}
    assert rows[("0xhasopen", "mlb")]["poll_state"] == farm.POLL_HAS_OPEN
    assert rows[("0xhasopen", "mlb")]["n_open"] == 2
    assert rows[("0xnoneopen", "mlb")]["poll_state"] == farm.POLL_NONE_OPEN
    assert rows[("0xnoneopen", "mlb")]["n_open"] == 0
    assert rows[("0xnever", "ufc")]["poll_state"] == farm.POLL_NEVER
    assert rows[("0xnever", "ufc")]["last_polled_ts"] is None


def test_quarantine_zero_edge(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        q = {(r["wallet"], r["category"]): r for r in farm.farm_rows(conn, status=farm.PINNED)}[("0xquar", "nfl")]
    assert q["n_resolved"] == 0 and q["n_excluded"] == 7
    assert q["all_quarantined"] is True                       # renders '0 + quarantined(7)', not a bare 0


def test_category_filter_keeps_all_in_category(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        mlb = farm.farm_rows(conn, status=farm.PINNED, category="mlb")
        unknown = farm.farm_rows(conn, status=farm.PINNED, category="unknown")
    assert {r["wallet"] for r in mlb} == {"0xhasopen", "0xnoneopen"}
    assert len(unknown) == 1 and unknown[0]["category"] == "unknown"   # 'unknown' is a real, filterable tab


def test_farm_summary_counts(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        s = farm.farm_summary(conn)
    assert s["n_pinned"] == 5
    assert s["states"] == {farm.POLL_NEVER: 1, farm.POLL_NONE_OPEN: 3, farm.POLL_HAS_OPEN: 1}
    assert s["n_quarantined_pairs"] == 1 and s["n_unknown_pairs"] == 1
    assert s["n_candidates"] == 0                             # no search has run


def test_candidates_empty_until_search(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        assert farm.farm_rows(conn, status=farm.CANDIDATE) == []


# ---------- the page (TestClient) ----------
def _client(tmp_path, monkeypatch):
    p = _seed(tmp_path)
    monkeypatch.setenv("PM_DB_PATH", p)
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def test_farm_page_200_and_three_states_rendered(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch).get("/farm")
    assert r.status_code == 200
    html = r.text
    # all three poll-state classes present -> the three-state zero is distinct in the DOM, not one '0'
    assert "pm-poll-never" in html and "pm-poll-empty" in html and "pm-poll-open" in html
    assert "never polled" in html                             # never-polled stated in WORDS, not a bare 0/dash
    # data-driven tabs
    for c in ("mlb", "nfl", "ufc", "unknown"):
        assert ">%s<" % c in html
    # unknown rendered honestly (tier-1 miss badge), NOT hidden
    assert "pm-badge-unknown" in html
    # quarantine-zero reason shown, not a bare 0 (badge uses &nbsp; so 'quarantined (N)' does not wrap)
    assert "pm-badge-quar" in html and "quarantined&nbsp;(7)" in html


def test_farm_candidates_no_search_message(tmp_path, monkeypatch):
    html = _client(tmp_path, monkeypatch).get("/farm").text
    assert 'data-empty="no-search"' in html
    assert "No search has run yet" in html
    assert "no candidates found" not in html.lower()          # the FALSE statement must NOT appear


def test_farm_list_partial_200(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch).get("/farm/list?category=mlb")
    assert r.status_code == 200
    assert "0xhasopen"[:10] in r.text and "0xnoneopen"[:10] in r.text
