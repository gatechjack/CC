"""Stage 2, phase 2 -- per-category CONTENT (Watchlist paper rows + Prospects completed/ranked rows + the
pinned-whale PAPER detail). Offline; FastAPI TestClient over a seeded schema-9 PM DB.

★ THE BASIS SEPARATION IS THE POINT. The substitution bug -- the pinned list silently borrowing the
completed-trade rollup -- caused this rebuild. Phase 2 is where the two bases meet on one page. So the core
test (`test_basis_watchlist_paper_prospects_completed`) seeds ONE pair where the paper source and the completed
source DISAGREE and asserts the DISPLAYED VALUE: the Watchlist row shows the PAPER number, the Prospects row
shows the COMPLETED number, and neither list leaks the other's pair. Not counts -- the actual rendered numbers.

Vocabulary (F-3): the screen renders 'Watchlist' / 'Prospects'; the code words 'pinned' / 'candidate' do NOT leak.
"""
import re

from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db

NOW = 1_700_000_000
# distinct 42-char wallets (0x + 40 hex) so the truncated whale-label + full-wallet href are unambiguous
WP = "0x" + "a" * 38 + "01"   # pinned whale
WC = "0x" + "b" * 38 + "02"   # candidate whale


def _client(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    db.init_db(p)
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app), p


def _whale(conn, wallet, name=None, backfill=1):
    conn.execute("INSERT INTO pm_whale (wallet, user_name, first_seen_ts, last_refresh_ts, backfill_complete) "
                 "VALUES (?,?,?,?,?)", (wallet, name, NOW, NOW, backfill))


def _pin(conn, wallet, category, *, status="pinned", active=1, last_polled_ts=NOW):
    conn.execute("INSERT INTO pm_roster (wallet, category, active, last_polled_ts, added_ts) VALUES (?,?,1,?,?)",
                 (wallet, category, last_polled_ts, NOW))
    conn.execute("INSERT INTO pm_watchlist (wallet, category, status, active, added_ts) VALUES (?,?,?,?,?)",
                 (wallet, category, status, active, NOW))


def _cstats(conn, wallet, category, *, n_resolved=None, roi=None, win_rate=None, net=None, awp=None,
            n_cids=None, two_sided=None):
    """pm_category_stats (COMPLETED basis). Only set columns are inserted (mig-004 caveat cols are NOT NULL
    DEFAULT 0, so an unset one takes its default rather than an explicit NULL)."""
    cols = {"n_resolved": n_resolved, "roi": roi, "win_rate": win_rate, "net_realized_pnl": net,
            "avg_win_price": awp, "n_condition_ids": n_cids, "two_sided_pct": two_sided}
    names, vals = ["wallet", "category", "updated_ts"], [wallet, category, NOW]
    for c, v in cols.items():
        if v is not None:
            names.append(c); vals.append(v)
    conn.execute("INSERT INTO pm_category_stats (%s) VALUES (%s)"
                 % (", ".join(names), ", ".join(["?"] * len(vals))), vals)


def _pstats(conn, wallet, category, *, n_closed=0, wins=0, losses=0, win_rate=None, roi=None,
            net_paper_pnl=None, cost_basis=None, n_open=0, n_stale=0, n_void=0):
    """pm_paper_category_stats (PAPER basis)."""
    conn.execute(
        "INSERT OR REPLACE INTO pm_paper_category_stats "
        "(wallet, category, n_closed, wins, losses, win_rate, net_paper_pnl, cost_basis, roi, "
        " avg_entry_price, n_open, n_stale, n_void, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,NULL,?,?,?,?)",
        (wallet, category, n_closed, wins, losses, win_rate, net_paper_pnl, cost_basis, roi,
         n_open, n_stale, n_void, NOW))


def _paper_open(conn, wallet, category, cid, n=1):
    """N OPEN pm_paper_trade rows -- the LIVE open count the Watchlist 'open' column reads (R6)."""
    for i in range(n):
        conn.execute("INSERT INTO pm_paper_trade (wallet, category, condition_id, outcome_index, "
                     "entry_observed_ts, opened_ts, status) VALUES (?,?,?,?,?,?, 'open')",
                     (wallet, category, "%s%d" % (cid, i), 0, NOW + i, NOW + i))


def _paper_trade(conn, wallet, category, cid, *, status="open", title=None, slug=None, realized_pnl=None,
                 won=None, size_basis=None, cost_basis=None, entry=None):
    conn.execute(
        "INSERT INTO pm_paper_trade (wallet, category, condition_id, outcome_index, title, slug, status, "
        "size_basis, cost_basis, entry_price_avg_at_observation, realized_pnl, won, entry_observed_ts, "
        "opened_ts) VALUES (?,?,?,0,?,?,?,?,?,?,?,?,?,?)",
        (wallet, category, cid, title, slug, status, size_basis, cost_basis, entry, realized_pnl, won,
         NOW, NOW))


def _closed(conn, wallet, category, cid, *, slug=None, title=None, won=1):
    """A pm_closed_position row (COMPLETED lane) -- used to prove the PAPER detail does NOT read it."""
    conn.execute("INSERT INTO pm_closed_position (wallet, condition_id, slug, title, category, outcome_index, "
                 "won, pnl_suspect, ingested_ts, updated_ts, resolved_ts) VALUES (?,?,?,?,?,0,?,0,?,?,?)",
                 (wallet, cid, slug, title, category, won, NOW, NOW, NOW))


def _regions(body):
    """Split the category page into the (watchlist-region, prospects-region) halves at the prospects div."""
    wl, _, pr = body.partition('id="pm-region-prospects"')
    return wl, pr


# ── THE BASIS TEST (the whole point of this phase) ────────────────────────────────────────────────

def test_basis_watchlist_paper_prospects_completed(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _whale(conn, WP, "PinnedWhale"); _whale(conn, WC, "CandWhale")
        # PINNED pair: paper says 40% / +25% ROI; completed says 89% / +99% ROI (the WRONG source for pinned)
        _pin(conn, WP, "mlb", status="pinned")
        _pstats(conn, WP, "mlb", n_closed=5, wins=2, losses=3, win_rate=0.40, roi=0.25,
                net_paper_pnl=50.0, cost_basis=200.0, n_open=2)
        _cstats(conn, WP, "mlb", n_resolved=50, win_rate=0.89, roi=0.99, net=900.0)  # must NOT surface on pinned
        # CANDIDATE pair: completed 60% / +10% ROI (the ONLY basis a prospect has)
        _pin(conn, WC, "mlb", status="candidate")
        _cstats(conn, WC, "mlb", n_resolved=30, win_rate=0.60, roi=0.10, net=120.0)
    r = client.get("/farm/mlb")
    assert r.status_code == 200
    wl, pr = _regions(r.text)
    # WATCHLIST shows the PAPER number, never the completed one
    assert ('href="/watchlist/%s/mlb"' % WP) in wl        # pinned whale -> PAPER detail
    assert "40%" in wl and "89%" not in wl                # paper win%, NOT completed win%
    assert "+25.0%" in wl                                 # paper ROI
    assert WC not in wl                                   # a candidate never appears in the Watchlist
    # PROSPECTS shows the COMPLETED number, and never the pinned pair
    assert ('href="/whale/%s/mlb"' % WC) in pr            # prospect -> COMPLETED detail
    assert "60%" in pr                                    # completed win% for the candidate
    assert WP not in pr                                   # a pinned pair never appears in Prospects


# ── open/closed honesty (R6): real open count + honest-empty performance ──────────────────────────

def test_open_count_shown_performance_honest_empty(tmp_path, monkeypatch):
    """14 open / 0 closed: the open count is real and first-class; performance is honest-empty, NOT a fake 0%."""
    client, p = _client(monkeypatch, tmp_path)
    W = "0x" + "c" * 38 + "03"
    with db.connect(p) as conn:
        _whale(conn, W, "AllOpen")
        _pin(conn, W, "nba", status="pinned")
        _paper_open(conn, W, "nba", "0xopen", n=14)      # 14 live OPEN paper trades, no pcs row (0 closed)
    r = client.get("/farm/nba")
    assert r.status_code == 200
    wl, _ = _regions(r.text)
    assert ">14</a>" in wl                                # the live open count is displayed (R6)
    assert "0%" not in wl                                 # NO fabricated win rate -- honest-empty, not zero
    assert ("href=\"/watchlist/%s/nba\"" % W) in wl       # links to its paper detail


# ── n_stale beside n_resolved (survivorship must be visible) ──────────────────────────────────────

def test_n_stale_visible_on_watchlist(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    W = "0x" + "d" * 38 + "04"
    with db.connect(p) as conn:
        _whale(conn, W, "HasStale")
        _pin(conn, W, "ufc", status="pinned")
        _pstats(conn, W, "ufc", n_closed=5, wins=3, losses=2, win_rate=0.6, roi=0.2, n_stale=3, n_open=1)
    r = client.get("/farm/ufc")
    wl, _ = _regions(r.text)
    assert 'pm-badge-anom' in wl and '>3</span>' in wl    # 3 stale exits rendered as a visible badge


# ── actions: Analyze WIRED; Demote / Promote / Promote-to-watchlist WIRED (Stage 3 R6, no longer disabled) ──

def test_actions_analyze_and_r6_farm_actions_wired(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _whale(conn, WP, "P"); _whale(conn, WC, "C")
        _pin(conn, WP, "mlb", status="pinned"); _pstats(conn, WP, "mlb", n_closed=1, n_open=1)
        _pin(conn, WC, "mlb", status="candidate"); _cstats(conn, WC, "mlb", n_resolved=30, roi=0.1, win_rate=0.6)
    body = client.get("/farm/mlb").text
    wl, pr = _regions(body)
    # Analyze is WIRED (HTMX POST to the existing analyze route), never disabled
    assert ('hx-post="/farm/analyze/%s/mlb"' % WP) in wl
    # R6: Demote is now a WIRED POST form; the old disabled buttons are gone
    assert ('action="/farm/mlb/demote/%s"' % WP) in wl
    assert 'disabled title="Demote' not in wl and 'disabled title="Promote' not in wl
    # R6: Promote-to-live -- no live sub-division seeded here -> the honest inert note renders (never "broken")
    assert "no live" in wl.lower()
    # R6: Prospects Promote-to-watchlist is now a WIRED POST form (no longer disabled)
    assert ('action="/farm/mlb/promote/%s"' % WC) in pr
    assert 'disabled title="Promote to Watchlist' not in pr


# ── Prospects: honest-empty today, active gate holds, ranked-candidates only ──────────────────────

def test_prospects_empty_when_no_candidates(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    W = "0x" + "e" * 38 + "05"
    with db.connect(p) as conn:
        _whale(conn, W); _pin(conn, W, "mlb", status="pinned"); _pstats(conn, W, "mlb", n_open=1)
    body = client.get("/farm/mlb").text
    _, pr = _regions(body)
    assert "No prospects yet" in pr                       # honest-empty, not fabricated rows
    assert 'data-empty-nosearch="1"' in pr


def test_prospects_active_gate_excludes_deactivated_candidate(tmp_path, monkeypatch):
    """The ranker must not surface a DEACTIVATED (active=0) candidate -- the active gate holds in the prospects
    section (query_scoreboard gates active=1 AND the candidate set is active-gated)."""
    client, p = _client(monkeypatch, tmp_path)
    PIN = "0x" + "f" * 38 + "06"
    LIVE = "0x" + "1" * 38 + "07"    # active candidate
    DEAD = "0x" + "2" * 38 + "08"    # deactivated candidate
    with db.connect(p) as conn:
        _whale(conn, PIN); _whale(conn, LIVE); _whale(conn, DEAD)
        _pin(conn, PIN, "mlb", status="pinned"); _pstats(conn, PIN, "mlb", n_open=1)
        _pin(conn, LIVE, "mlb", status="candidate", active=1)
        _cstats(conn, LIVE, "mlb", n_resolved=30, roi=0.1, win_rate=0.6)
        _pin(conn, DEAD, "mlb", status="candidate", active=0)
        _cstats(conn, DEAD, "mlb", n_resolved=30, roi=0.2, win_rate=0.7)
    _, pr = _regions(client.get("/farm/mlb").text)
    assert LIVE in pr                                     # the active candidate ranks and shows
    assert DEAD not in pr                                 # the deactivated candidate shows NOWHERE


def test_prospects_inner_gate_composes_with_outer_candidate_filter(tmp_path, monkeypatch):
    """DEFENSE-IN-DEPTH / COMPOSITION: the active gate lives INSIDE query_scoreboard (the ranker); the candidate
    scoping is a loader-level filter OUTSIDE it. A DEACTIVATED (active=0) pair must be dropped by the INNER gate,
    so even if the OUTER candidate filter admitted its wallet (worst case), the pair can NEVER reach Prospects.
    The Prospects list is EMPTY today, so this defect would stay invisible until Search populates candidates in
    Stage 4 -- hence the explicit test now."""
    from trading_corp.prediction_markets import stats
    client, p = _client(monkeypatch, tmp_path)
    DEAD = "0x" + "9" * 38 + "10"
    with db.connect(p) as conn:
        _whale(conn, DEAD)
        # a DEACTIVATED candidate that otherwise looks highly rankable (well above the ranker floor)
        _pin(conn, DEAD, "mlb", status="candidate", active=0)
        _cstats(conn, DEAD, "mlb", n_resolved=99, roi=0.5, win_rate=0.9)
        board = stats.query_scoreboard(conn, category="mlb")
    # (1) the INNER gate (query_scoreboard's WHERE active<>0) drops the deactivated pair from the ranked board
    assert not any(r["wallet"] == DEAD for r in board), "inner active gate must exclude the deactivated pair"
    # (2) COMPOSITION: even a permissive/buggy OUTER filter that ADMITS this wallet cannot resurface it -- the
    #     loader selects `[r for r in board if r.wallet in cand]` FROM `board`, which the inner gate already
    #     emptied of the deactivated pair. So it never reaches Prospects, no matter what the outer filter does.
    outer_admits_the_dead_wallet = {DEAD}
    prospects = [r for r in board if r["wallet"] in outer_admits_the_dead_wallet]
    assert prospects == [], "deactivated pair must not reach Prospects even if the outer filter admits its wallet"


# ── the pinned-whale PAPER detail (paper basis, NOT completed) ────────────────────────────────────

def test_paper_detail_shows_paper_trades_not_completed(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    W = "0x" + "3" * 38 + "09"
    with db.connect(p) as conn:
        _whale(conn, W, "PaperWhale")
        _pin(conn, W, "mlb", status="pinned")
        _pstats(conn, W, "mlb", n_closed=1, wins=1, win_rate=1.0, roi=0.5, n_open=1)
        _paper_trade(conn, W, "mlb", "0xpap", status="open", title="PAPER-MARKET-XYZ", slug="paper-slug-xyz")
        _closed(conn, W, "mlb", "0xcls", slug="closed-slug-abc", title="COMPLETED-MARKET-ABC")  # must NOT show
    r = client.get("/watchlist/%s/mlb" % W)
    assert r.status_code == 200
    body = r.text
    assert "Watchlist &middot; paper basis" in body      # the basis is stated on the page
    assert "paper-slug-xyz" in body                       # a PAPER trade renders
    assert "closed-slug-abc" not in body                  # a COMPLETED position does NOT (basis separation)


def test_paper_detail_honest_empty_for_unknown(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    body = client.get("/watchlist/0x000000000000000000000000000000000000dead/mlb").text
    assert "no paper trades for this pair yet" in body    # honest-empty, never a fabrication


# ── vocabulary (F-3): screen words only; 'pinned' / 'candidate' never leak ─────────────────────────

def test_vocab_no_internal_words_leak(tmp_path, monkeypatch):
    client, p = _client(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _whale(conn, WP); _whale(conn, WC)
        _pin(conn, WP, "mlb", status="pinned"); _pstats(conn, WP, "mlb", n_closed=1, n_open=1)
        _pin(conn, WC, "mlb", status="candidate"); _cstats(conn, WC, "mlb", n_resolved=30, roi=0.1, win_rate=0.6)
    cat = client.get("/farm/mlb").text
    assert "Watchlist" in cat and "Prospects" in cat                 # screen words present on the category page
    for body in (cat, client.get("/watchlist/%s/mlb" % WP).text):
        assert "pinned" not in body and "candidate" not in body      # internal code words never leak


# (phase 3) The legacy alongside-resolves test was removed: /scoreboard + the temporary /dashboard,/farm-league
# are RETIRED (404); /, /farm now serve the hierarchy. Retirement is asserted in test_stage2_phase3.py.
