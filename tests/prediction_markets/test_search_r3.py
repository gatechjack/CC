"""Stage 4 SEARCH -- RUNG 3 (candidate SELECTION + WRITE). Offline; NO engine, NO network (except the
injected-client refresh test). Proves: the read GATES backfill_complete=1 (partial/orphan whales never
candidates); the write is NO-CLOBBER (never un-pins a promotion, resurrects a removal, or double-writes);
three-bases + active gate + allowlist + N>=50-with-fallback + 30d open-position-proxy recency all hold; and
NO auto-promotion / NO auto-paper (status is always 'candidate').
Spec: reports/prediction_markets/STAGE4_SEARCH_PLAN_2026-08-29.md sec 8A/9A/9D.
"""
import pytest

from trading_corp.prediction_markets import db, search, search_run

NOW = 2_000_000_000
DAY = 86_400
RECENT = NOW - 10 * DAY
OLD = NOW - 100 * DAY


def _db(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    return p


def _whale(conn, w, *, complete=1, name=""):
    conn.execute("INSERT OR IGNORE INTO pm_whale (wallet, user_name, backfill_complete) VALUES (?,?,?)",
                 (w, name, complete))


def _cstat(conn, w, cat, *, n=60, roi=0.2, last_ts=RECENT, win=None):
    conn.execute("INSERT INTO pm_category_stats (wallet, category, n_resolved, roi, win_rate, last_resolved_ts, "
                 "updated_ts) VALUES (?,?,?,?,?,?,?)", (w, cat, n, roi, win, last_ts, NOW))


def _open(conn, w, cat, cid="c1"):
    conn.execute("INSERT OR IGNORE INTO pm_open_position (wallet, condition_id, outcome_index, category) "
                 "VALUES (?,?,0,?)", (w, cid, cat))


def _cand(wallet, category, *, roi=0.2, n=60, thin=False):
    return search.Candidate(wallet=wallet, category=category, roi=roi, n_resolved=n, thin_sample=thin,
                            recent_reason="open_position", rank_in_category=1, user_name="W")


# ═══════════════════════════════ build_wallet_category_stats (the GATED read) ═══════════════════════════════

def test_build_stats_gates_backfill_complete(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        _whale(conn, "0xok", complete=1); _cstat(conn, "0xok", "mlb", n=60, roi=0.2)
        _whale(conn, "0xpart", complete=0); _cstat(conn, "0xpart", "mlb", n=99, roi=0.9)   # partial -> excluded
        _cstat(conn, "0xorphan", "mlb", n=80, roi=0.5)                                       # stats, NO pm_whale row
        rows = search_run.build_wallet_category_stats(conn, ["0xok", "0xpart", "0xorphan"])
    assert {r.wallet for r in rows} == {"0xok"}   # partial + orphan both excluded


def test_build_stats_has_open_position_proxy(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        _whale(conn, "0xa", complete=1); _cstat(conn, "0xa", "mlb"); _open(conn, "0xa", "mlb")
        _whale(conn, "0xb", complete=1); _cstat(conn, "0xb", "mlb")                          # no open position
        rows = {r.wallet: r for r in search_run.build_wallet_category_stats(conn, ["0xa", "0xb"])}
    assert rows["0xa"].has_open_position is True and rows["0xb"].has_open_position is False


def test_build_stats_carries_fields_and_empty(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        _whale(conn, "0xa", complete=1, name="Alice"); _cstat(conn, "0xa", "mlb", n=77, roi=0.33, win=0.6, last_ts=RECENT)
        rows = search_run.build_wallet_category_stats(conn, ["0xa"])
        assert search_run.build_wallet_category_stats(conn, []) == []
    r = rows[0]
    assert r.n_resolved == 77 and r.roi == 0.33 and r.win_rate == 0.6 and r.user_name == "Alice" and r.last_resolved_ts == RECENT


# ═══════════════════════════════ write_candidates (NO-CLOBBER + three-bases) ═══════════════════════════════

def test_write_candidates_writes_candidate_and_roster(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        n = search_run.write_candidates(conn, [_cand("0xw", "mlb")], run_id=7, now_ts=NOW)
        assert n == 1
        wl = conn.execute("SELECT status, active, source, search_run_id FROM pm_watchlist "
                          "WHERE wallet='0xw' AND category='mlb'").fetchone()
        assert wl["status"] == "candidate" and wl["active"] == 1 and wl["source"] == "search" and wl["search_run_id"] == 7
        ro = conn.execute("SELECT source, active FROM pm_roster WHERE wallet='0xw' AND category='mlb'").fetchone()
        assert ro["source"] == "search" and ro["active"] == 1


def test_write_candidates_no_clobber_pinned(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        conn.execute("INSERT INTO pm_watchlist (wallet, category, status, active) VALUES ('0xw','mlb','pinned',1)")
        n = search_run.write_candidates(conn, [_cand("0xw", "mlb")], run_id=7, now_ts=NOW)
        assert n == 0   # NOT written -- a human's promotion is never reverted
        assert conn.execute("SELECT status FROM pm_watchlist WHERE wallet='0xw' AND category='mlb'"
                            ).fetchone()["status"] == "pinned"


def test_write_candidates_no_resurrect_removed(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        conn.execute("INSERT INTO pm_watchlist (wallet, category, status, active) VALUES ('0xw','mlb','candidate',0)")
        n = search_run.write_candidates(conn, [_cand("0xw", "mlb")], run_id=7, now_ts=NOW)
        assert n == 0
        assert conn.execute("SELECT active FROM pm_watchlist WHERE wallet='0xw' AND category='mlb'"
                            ).fetchone()["active"] == 0   # stays removed -- search never resurrects a removal


def test_write_candidates_idempotent_rerun(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        assert search_run.write_candidates(conn, [_cand("0xw", "mlb")], run_id=7, now_ts=NOW) == 1
        assert search_run.write_candidates(conn, [_cand("0xw", "mlb")], run_id=8, now_ts=NOW + 1) == 0


def test_write_candidates_no_clobber_roster_source(tmp_path):
    # a pre-existing pm_roster row (from a prior pin/seed) must NOT have its source flipped to 'search'
    with db.connect(_db(tmp_path)) as conn:
        conn.execute("INSERT INTO pm_roster (wallet, category, source, active) VALUES ('0xw','mlb','seed_precedent',1)")
        n = search_run.write_candidates(conn, [_cand("0xw", "mlb")], run_id=7, now_ts=NOW)
        assert n == 1   # the watchlist candidate IS written (roster pre-existing does not block it)
        assert conn.execute("SELECT source FROM pm_roster WHERE wallet='0xw' AND category='mlb'"
                            ).fetchone()["source"] == "seed_precedent"   # roster source UNTOUCHED (OR IGNORE)
        assert conn.execute("SELECT COUNT(*) FROM pm_watchlist WHERE wallet='0xw' AND status='candidate'"
                            ).fetchone()[0] == 1


def test_write_candidates_touches_only_funnel_and_roster(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        search_run.write_candidates(conn, [_cand("0xw", "mlb")], run_id=7, now_ts=NOW)
        # THREE-BASES: no paper/live/completed base written
        assert conn.execute("SELECT COUNT(*) FROM pm_paper_trade").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_attachment").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM pm_paper_category_stats").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM pm_closed_position").fetchone()[0] == 0


# ═══════════════════════════════ select_and_write_candidates (integration) ═══════════════════════════════

def _run(conn):
    return search_run.open_search_run(conn, started_ts=NOW, leaderboard_category="Sports", leaderboard_limit=250,
                                      min_resolved=50, recency_window_days=30, thin_sample_target=10)


def test_select_and_write_end_to_end_all_qualifiers(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        rid = _run(conn)
        for i in range(12):                                  # 12 complete mlb qualifiers (>=10 -> all, none thin)
            w = "0xq%02d" % i
            _whale(conn, w, complete=1); _cstat(conn, w, "mlb", n=60, roi=0.30 - i * 0.01, last_ts=RECENT)
        res = search_run.select_and_write_candidates(conn, ["0xq%02d" % i for i in range(12)], run_id=rid, now_ts=NOW)
        assert res["n_selected"] == 12 and res["n_written"] == 12
        assert conn.execute("SELECT COUNT(*) FROM pm_watchlist WHERE status='candidate' AND source='search'"
                            ).fetchone()[0] == 12
        assert conn.execute("SELECT n_candidates_written FROM pm_search_run WHERE run_id=?", (rid,)).fetchone()[0] == 12
        assert conn.execute("SELECT COUNT(*) FROM pm_watchlist WHERE status='pinned'").fetchone()[0] == 0  # NO auto-promote


def test_select_and_write_excludes_partial_offcategory_dormant(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        rid = _run(conn)
        _whale(conn, "0xgood", complete=1); _cstat(conn, "0xgood", "mlb", n=60, roi=0.2, last_ts=RECENT); _open(conn, "0xgood", "mlb")
        _whale(conn, "0xpart", complete=0); _cstat(conn, "0xpart", "mlb", n=99, roi=0.9, last_ts=RECENT)  # gated out (partial)
        _whale(conn, "0xcbb", complete=1); _cstat(conn, "0xcbb", "cbb", n=99, roi=0.9, last_ts=RECENT)    # allowlist out
        _whale(conn, "0xdorm", complete=1); _cstat(conn, "0xdorm", "mlb", n=99, roi=0.9, last_ts=OLD)     # recency out
        search_run.select_and_write_candidates(conn, ["0xgood", "0xpart", "0xcbb", "0xdorm"], run_id=rid, now_ts=NOW)
        written = {r["wallet"] for r in conn.execute("SELECT wallet FROM pm_watchlist WHERE status='candidate'")}
    assert written == {"0xgood"}


def test_select_and_write_recency_open_position_proxy(tmp_path):
    # OLD last_resolved_ts but an OPEN position in the category -> recent via the proxy -> written
    with db.connect(_db(tmp_path)) as conn:
        rid = _run(conn)
        _whale(conn, "0xopen", complete=1); _cstat(conn, "0xopen", "mlb", n=60, roi=0.2, last_ts=OLD); _open(conn, "0xopen", "mlb")
        search_run.select_and_write_candidates(conn, ["0xopen"], run_id=rid, now_ts=NOW)
        assert conn.execute("SELECT COUNT(*) FROM pm_watchlist WHERE wallet='0xopen' AND status='candidate'"
                            ).fetchone()[0] == 1


def test_select_and_write_thin_sample_fallback(tmp_path):
    # a category with <10 qualifiers writes its top-10 of the eligible pool (fallback) -- sub-50 rows flagged thin
    with db.connect(_db(tmp_path)) as conn:
        rid = _run(conn)
        _whale(conn, "0xq", complete=1); _cstat(conn, "0xq", "golf", n=60, roi=0.10, last_ts=RECENT)     # qualifier
        for i in range(3):
            w = "0xs%d" % i
            _whale(conn, w, complete=1); _cstat(conn, w, "golf", n=10, roi=0.40 - i * 0.01, last_ts=RECENT)  # sub-50
        res = search_run.select_and_write_candidates(conn, ["0xq", "0xs0", "0xs1", "0xs2"], run_id=rid, now_ts=NOW)
        assert "golf" in res["thin_sample_categories"] and res["n_written"] == 4   # 1 qual + 3 subs, all surface (<10)


def test_select_and_write_rerun_writes_zero_new(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        rid1 = _run(conn)
        _whale(conn, "0xw", complete=1); _cstat(conn, "0xw", "mlb", n=60, roi=0.2, last_ts=RECENT)
        assert search_run.select_and_write_candidates(conn, ["0xw"], run_id=rid1, now_ts=NOW)["n_written"] == 1
        rid2 = search_run.open_search_run(conn, started_ts=NOW + 1, leaderboard_category="Sports", leaderboard_limit=250,
                                          min_resolved=50, recency_window_days=30, thin_sample_target=10)
        r2 = search_run.select_and_write_candidates(conn, ["0xw"], run_id=rid2, now_ts=NOW + 1)
        assert r2["n_selected"] == 1 and r2["n_written"] == 0   # selected again, already written -> 0 new
        assert conn.execute("SELECT n_candidates_written FROM pm_search_run WHERE run_id=?", (rid2,)).fetchone()[0] == 0


def test_select_and_write_roi_null_excluded_no_crash(tmp_path):
    # roi NULL (cost_basis<=0 in pm_category_stats) -> not rankable by the ruled metric -> excluded, no crash
    with db.connect(_db(tmp_path)) as conn:
        rid = _run(conn)
        _whale(conn, "0xn", complete=1); _cstat(conn, "0xn", "mlb", n=60, roi=None, last_ts=RECENT)
        res = search_run.select_and_write_candidates(conn, ["0xn"], run_id=rid, now_ts=NOW)
        assert res["n_written"] == 0
        assert conn.execute("SELECT COUNT(*) FROM pm_watchlist WHERE wallet='0xn'").fetchone()[0] == 0


def test_recency_proxy_is_category_scoped(tmp_path):
    # OLD mlb stats + an open position in a DIFFERENT category (nba) must NOT rescue the mlb row via recency
    with db.connect(_db(tmp_path)) as conn:
        rid = _run(conn)
        _whale(conn, "0xx", complete=1); _cstat(conn, "0xx", "mlb", n=60, roi=0.2, last_ts=OLD); _open(conn, "0xx", "nba")
        search_run.select_and_write_candidates(conn, ["0xx"], run_id=rid, now_ts=NOW)
        assert conn.execute("SELECT COUNT(*) FROM pm_watchlist WHERE wallet='0xx'").fetchone()[0] == 0


def test_select_and_write_above_target_drops_sub_floor(tmp_path):
    # >= target qualifiers -> ALL qualifiers written UNCAPPED (not thin); recent sub-50 rows dropped by the N floor
    with db.connect(_db(tmp_path)) as conn:
        rid = _run(conn)
        for i in range(10):
            w = "0xq%d" % i; _whale(conn, w, complete=1); _cstat(conn, w, "mlb", n=60, roi=0.30 - i * 0.01, last_ts=RECENT)
        for i in range(3):
            w = "0xs%d" % i; _whale(conn, w, complete=1); _cstat(conn, w, "mlb", n=10, roi=0.99, last_ts=RECENT)
        pool = ["0xq%d" % i for i in range(10)] + ["0xs%d" % i for i in range(3)]
        res = search_run.select_and_write_candidates(conn, pool, run_id=rid, now_ts=NOW)
        assert res["n_written"] == 10 and "mlb" not in res["thin_sample_categories"]
        assert res["excluded"][search.EX_BELOW_MIN_RESOLVED] == 3
        written = {r["wallet"] for r in conn.execute("SELECT wallet FROM pm_watchlist WHERE status='candidate'")}
        assert written == {"0xq%d" % i for i in range(10)}


def test_select_and_write_empty_stats_reports_zero_stats_rows(tmp_path):
    # the silent-0 guard: a backfilled pool with NO pm_category_stats (rollup not run) -> n_stats_rows==0 (visible)
    with db.connect(_db(tmp_path)) as conn:
        rid = _run(conn)
        _whale(conn, "0xw", complete=1)   # complete whale, but NO pm_category_stats row (rollup didn't run)
        res = search_run.select_and_write_candidates(conn, ["0xw"], run_id=rid, now_ts=NOW)
        assert res["n_stats_rows"] == 0 and res["n_selected"] == 0 and res["n_written"] == 0


# ═══════════════════════════════ refresh_positions_for (network, isolated) ═══════════════════════════════

class _PClient:
    def __init__(self):
        self.calls = []

    async def fetch_positions(self, wallet, **kw):
        self.calls.append(wallet)
        if wallet == "0xbad":
            raise RuntimeError("simulated positions failure")
        return []   # empty book: refresh_open_positions deletes+inserts nothing (a valid "no open positions")


async def test_refresh_positions_for_isolates_failures(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        c = _PClient()
        res = await search_run.refresh_positions_for(conn, ["0xgood", "0xbad"], client=c, now_ts=NOW)
    assert res["ok"] == 1 and res["failed"] == 1 and res["per_failed"][0]["wallet"] == "0xbad"
    assert set(c.calls) == {"0xgood", "0xbad"}   # both attempted -- one failure did not abort the pass
