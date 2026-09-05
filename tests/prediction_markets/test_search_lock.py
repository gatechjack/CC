"""Stage 4 SEARCH -- the SINGLE-FLIGHT GUARD + the bucket fail-loud (2026-09-05). Offline.

The guard is what makes a UI Search button safe: nothing refuses a second run today, and two concurrent
~92-min sweeps double the Polymarket API load against the same prod IP the armed engine polls. These tests
prove the guard the way it will actually be attacked -- by starting a SECOND run while one is marked running
(the direct-POST bypass), not by checking a button is disabled -- and prove the heartbeat-vs-fixed-ceiling
staleness property (a genuine long run is never falsely reclaimed; a crashed one is reclaimed within the window).

Spec: reports/prediction_markets/FARM_SEARCH_BUTTON_2026-09-05.md.
"""
import pytest

from trading_corp.prediction_markets import db, search_run

NOW = 1_700_000_000
DAY = 86_400
KNOBS = dict(leaderboard_category="Sports", leaderboard_limit=250,
             min_resolved=50, recency_window_days=30, thin_sample_target=10)


def _db(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    return p


def _acquire(conn, now, launcher="ui"):
    return search_run.acquire_search_lock(conn, now_ts=now, launcher=launcher, **KNOBS)


def _count_running(conn):
    return conn.execute("SELECT COUNT(*) FROM pm_search_run WHERE status='running'").fetchone()[0]


# ═══════════════════════════════ single-flight (the boundary) ═══════════════════════════════

def test_first_acquire_succeeds_and_inserts_one_running_row(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        res = _acquire(conn, NOW)
        assert res["acquired"] is True and res["run_id"] == 1
        assert _count_running(conn) == 1
        lock = search_run.running_lock(conn, now_ts=NOW)
        assert lock is not None and lock["run_id"] == 1


def test_second_acquire_while_running_is_refused_no_new_row(tmp_path):
    """The DIRECT-POST bypass: a second run started while one is genuinely in flight is refused -- and NO
    second row is written (the guard, not the hidden button, is what refuses)."""
    with db.connect(_db(tmp_path)) as conn:
        first = _acquire(conn, NOW)
        second = _acquire(conn, NOW + 5)             # a moment later, heartbeat still fresh
        assert second == {"acquired": False, "run_id": first["run_id"], "reason": "already_running"}
        assert _count_running(conn) == 1             # still exactly one; the second did not insert


def test_second_connection_sees_committed_lock_and_is_refused(tmp_path):
    """Two SEQUENTIAL requests each use their own connection. Because acquire commits the running row inside
    BEGIN IMMEDIATE before returning, a fresh connection's acquire sees it and refuses -- the realistic
    two-tab / double-submit race resolves to one live run."""
    p = _db(tmp_path)
    with db.connect(p) as c1:
        assert _acquire(c1, NOW)["acquired"] is True
    with db.connect(p) as c2:
        assert _acquire(c2, NOW + 3)["acquired"] is False
        assert _count_running(c2) == 1


def test_closed_run_releases_the_lock(tmp_path):
    """A finished run (status='ok') does not hold the lock -- the next acquire succeeds."""
    with db.connect(_db(tmp_path)) as conn:
        rid = _acquire(conn, NOW)["run_id"]
        search_run.close_search_run(conn, rid, finished_ts=NOW + 100, n_discovered=50, n_backfilled=47,
                                    status="ok", summary="{}", n_candidates_written=134)
        again = _acquire(conn, NOW + 200)
        assert again["acquired"] is True and again["run_id"] != rid


# ═══════════════════════════════ heartbeat-based staleness ═══════════════════════════════

def test_stale_lock_is_reclaimed_not_permanent(tmp_path):
    """A crashed sweep strands a 'running' row (close_search_run's finally never ran). After the stale window
    it is reclaimable: running_lock reads None, and a new acquire succeeds while the old row is marked errored."""
    with db.connect(_db(tmp_path)) as conn:
        old = _acquire(conn, NOW)["run_id"]
        later = NOW + search_run.SEARCH_STALE_SEC + 1          # heartbeat now expired
        assert search_run.running_lock(conn, now_ts=later) is None
        res = _acquire(conn, later)
        assert res["acquired"] is True and res["run_id"] != old
        reclaimed = conn.execute("SELECT status, summary FROM pm_search_run WHERE run_id=?", (old,)).fetchone()
        assert reclaimed["status"] == "error" and "reclaimed" in (reclaimed["summary"] or "")
        assert _count_running(conn) == 1                       # only the fresh lock is running


def test_heartbeat_keeps_a_long_run_alive_past_the_window(tmp_path):
    """★ The reason for a heartbeat and not a fixed ceiling: a genuine run that OUTLASTS the stale window is
    NEVER falsely reclaimed, because it keeps heartbeating. Here the run started 40 min ago (> 30-min window)
    but heartbeated 20 min ago -> still LIVE -> a second acquire is refused. A fixed ceiling would have wrongly
    permitted the second run -- exactly the concurrency the guard exists to prevent."""
    with db.connect(_db(tmp_path)) as conn:
        rid = _acquire(conn, NOW)["run_id"]
        hb = NOW + 20 * 60
        search_run.heartbeat_search_run(conn, rid, now_ts=hb)
        far = NOW + 40 * 60                                    # 40 min after start (> 30-min window)...
        assert far - NOW > search_run.SEARCH_STALE_SEC         # ...confirm we are past the fixed-ceiling point
        assert far - hb < search_run.SEARCH_STALE_SEC          # ...but only 20 min since the last heartbeat
        assert search_run.running_lock(conn, now_ts=far) is not None   # still live
        assert _acquire(conn, far)["acquired"] is False                # so a second run is refused
        assert _count_running(conn) == 1


def test_acquire_records_knobs_and_launcher(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        rid = _acquire(conn, NOW, launcher="cli")["run_id"]
        row = conn.execute(
            "SELECT leaderboard_category, leaderboard_limit, min_resolved, recency_window_days, "
            "thin_sample_target, params_json FROM pm_search_run WHERE run_id=?", (rid,)).fetchone()
        assert row["leaderboard_category"] == "Sports" and row["min_resolved"] == 50
        params = search_run._row_params(row)
        assert params["launcher"] == "cli" and params["heartbeat_ts"] == NOW


# ═══════════════════════════════ status for the UI feedback poll ═══════════════════════════════

def test_latest_status_idle_running_done_error_stale(tmp_path):
    p = _db(tmp_path)
    with db.connect(p) as conn:
        assert search_run.latest_search_status(conn, now_ts=NOW)["state"] == "idle"

        rid = _acquire(conn, NOW)["run_id"]
        st = search_run.latest_search_status(conn, now_ts=NOW + 10)
        assert st["state"] == "running" and st["run_id"] == rid

        # a running row whose heartbeat expired reads 'stale' (crashed), never 'running' forever
        assert search_run.latest_search_status(
            conn, now_ts=NOW + search_run.SEARCH_STALE_SEC + 1)["state"] == "stale"

        search_run.close_search_run(conn, rid, finished_ts=NOW + 100, n_discovered=50, n_backfilled=47,
                                    status="ok", summary="{}", n_candidates_written=134)
        done = search_run.latest_search_status(conn, now_ts=NOW + 200)
        assert done["state"] == "done" and done["n_candidates_written"] == 134

        rid2 = _acquire(conn, NOW + 300)["run_id"]
        search_run.close_search_run(conn, rid2, finished_ts=NOW + 400, n_discovered=0, n_backfilled=0,
                                    status="error", summary="boom")
        assert search_run.latest_search_status(conn, now_ts=NOW + 500)["state"] == "error"


# ═══════════════════════════════ bucket fail-loud (the --category decoy) ═══════════════════════════════

def test_valid_and_global_buckets_pass():
    pytest.importorskip("trading_corp.data.polymarket_data_api_client")   # pulls httpx; box-scratch has it
    search_run.assert_valid_bucket("Sports")     # a real bucket -> ok
    search_run.assert_valid_bucket(None)         # global leaderboard -> ok


def test_fine_category_is_rejected_loudly():
    pytest.importorskip("trading_corp.data.polymarket_data_api_client")
    with pytest.raises(ValueError) as ei:
        search_run.assert_valid_bucket("mlb")
    msg = str(ei.value)
    assert "mlb" in msg and "Sports" in msg and "discovers nothing" in msg


async def test_discover_wallets_rejects_fine_category_before_any_network():
    pytest.importorskip("trading_corp.data.polymarket_data_api_client")

    class _NoCallClient:
        async def fetch_leaderboard(self, *, category, limit):
            raise AssertionError("fetch_leaderboard must NOT be reached for a fine category")

    with pytest.raises(ValueError):
        await search_run.discover_wallets(_NoCallClient(), category="ufc", limit=10)
