"""Stage 4 SEARCH -- RUNG 2 (discovery + on-demand first-sight backfill + run record). Offline.

Injected fake client + clock + no-op sleep -> NO network, NO real DB path, NO engine. Covers Ruling 1
(known-complete whale read from DB not re-pulled; never-seen/partial backfilled once; refresh always pulls)
and the adversarial target: a partial/failed backfill is VISIBLE, marked NOT-complete (excluded from
ranking), never half-ranked -- and is re-attempted on the next run until complete, then skipped.
Spec: reports/prediction_markets/STAGE4_SEARCH_PLAN_2026-08-29.md sec 8A/9A/9C/9D.
"""
import ast
import inspect
import json
from types import SimpleNamespace

import pytest

from trading_corp.prediction_markets import db, search_run

NOW = 1_700_000_000


async def _no_events(slug, **kw):
    raise AssertionError("tier-2 fetch_events must NOT be called (all fixture slugs are tier-1 mlb)")


async def _nosleep(_delay):
    pass


def _cp(wallet, i, *, ts=NOW):
    """A ClosedPositionRow-shaped fixture (attr access, as ingest.cp_to_record reads). mlb-* slug -> tier-1."""
    return SimpleNamespace(
        proxy_wallet=wallet, condition_id="c%d" % i, slug="mlb-g%d" % i, event_slug="mlb-g%d" % i,
        title="MLB %d" % i, outcome="Yes", outcome_index=0, avg_price=0.5, total_bought=100.0,
        realized_pnl=10.0, cur_price=1.0, end_date="2026-01-01", timestamp=ts)


class FakeClient:
    def __init__(self, *, leaderboard=(), closed=None, raise_for=(), raise_at_offset=None):
        self.leaderboard = list(leaderboard)                              # [(wallet, name)]
        self.closed = {k.lower(): list(v) for k, v in (closed or {}).items()}
        self.raise_for = {w.lower() for w in raise_for}                   # raises on EVERY offset
        self.raise_at_offset = {k.lower(): v for k, v in (raise_at_offset or {}).items()}  # raises at offset >= v
        self.pulled = []                                                  # wallets whose /closed-positions was hit

    async def fetch_leaderboard(self, *, category, limit):
        return [SimpleNamespace(proxy_wallet=w, user_name=n) for w, n in self.leaderboard]

    async def fetch_closed_positions(self, wallet, *, limit=50, offset=0):
        w = wallet.lower()
        if offset == 0:
            self.pulled.append(w)
        if w in self.raise_for:
            raise RuntimeError("simulated pull failure for %s" % w)
        if w in self.raise_at_offset and offset >= self.raise_at_offset[w]:
            raise RuntimeError("simulated mid-pagination failure for %s at offset %d" % (w, offset))
        return self.closed.get(w, [])[offset:offset + limit]


def _db(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    return p


# ═══════════════════════════════════ discovery ═══════════════════════════════════

async def test_discover_dedup_lowercase_order():
    c = FakeClient(leaderboard=[("0xAAA", "Alice"), ("0xbbb", "Bob"), ("0xAAA", "dupe")])
    got = await search_run.discover_wallets(c, category="Sports", limit=10)
    assert got == [("0xaaa", "Alice"), ("0xbbb", "Bob")]     # deduped, lowercased, rank order


# ═══════════════════════════════ first-sight backfill (Ruling 1) ═══════════════════════════════

async def test_ensure_backfilled_skips_complete_whale(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        conn.execute("INSERT INTO pm_whale (wallet, backfill_complete) VALUES ('0xdone', 1)")
        c = FakeClient(closed={"0xdone": [_cp("0xdone", 1)]})
        res = await search_run.ensure_backfilled(conn, "0xdone", client=c, now_ts=NOW,
                                                 fetch_events=_no_events, sleep=_nosleep)
        assert res["action"] == "skipped_complete"
        assert "0xdone" not in c.pulled                       # NOT pulled -- read from DB
        assert conn.execute("SELECT COUNT(*) FROM pm_closed_position WHERE wallet='0xdone'").fetchone()[0] == 0


async def test_ensure_backfilled_first_sight_never_seen(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        c = FakeClient(closed={"0xnew": [_cp("0xnew", i) for i in range(30)]})   # 30<50 -> short page -> complete
        res = await search_run.ensure_backfilled(conn, "0xnew", client=c, now_ts=NOW,
                                                 fetch_events=_no_events, sleep=_nosleep)
        assert res["action"] == "backfilled" and res["verdict"] == "complete"
        assert "0xnew" in c.pulled
        assert conn.execute("SELECT COUNT(*) FROM pm_closed_position WHERE wallet='0xnew'").fetchone()[0] == 30
        assert conn.execute("SELECT backfill_complete FROM pm_whale WHERE wallet='0xnew'").fetchone()[0] == 1


async def test_ensure_backfilled_retries_prior_partial(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        conn.execute("INSERT INTO pm_whale (wallet, backfill_complete) VALUES ('0xpart', 0)")  # prior partial
        c = FakeClient(closed={"0xpart": [_cp("0xpart", i) for i in range(30)]})
        res = await search_run.ensure_backfilled(conn, "0xpart", client=c, now_ts=NOW,
                                                 fetch_events=_no_events, sleep=_nosleep)
        assert res["action"] == "backfilled"                  # NOT skipped -- an incomplete whale is (re)attempted
        assert "0xpart" in c.pulled


async def test_refresh_one_pulls_even_when_complete_and_stamps_last_refresh(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        conn.execute("INSERT INTO pm_whale (wallet, backfill_complete, last_refresh_ts) VALUES ('0xdone',1,111)")
        c = FakeClient(closed={"0xdone": [_cp("0xdone", i) for i in range(5)]})
        res = await search_run.refresh_one(conn, "0xdone", client=c, now_ts=NOW,
                                           fetch_events=_no_events, sleep=_nosleep)
        assert res["action"] == "refreshed" and "0xdone" in c.pulled          # refresh IGNORES backfill_complete
        assert conn.execute("SELECT last_refresh_ts FROM pm_whale WHERE wallet='0xdone'").fetchone()[0] == NOW


async def test_refresh_one_on_never_seen_creates_row(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        c = FakeClient(closed={"0xfresh": [_cp("0xfresh", i) for i in range(5)]})
        res = await search_run.refresh_one(conn, "0xfresh", client=c, now_ts=NOW,
                                           fetch_events=_no_events, sleep=_nosleep)
        assert res["action"] == "refreshed" and "0xfresh" in c.pulled
        row = conn.execute("SELECT backfill_complete, last_refresh_ts FROM pm_whale WHERE wallet='0xfresh'").fetchone()
        assert row is not None and row["backfill_complete"] == 1 and row["last_refresh_ts"] == NOW
        assert conn.execute("SELECT COUNT(*) FROM pm_closed_position WHERE wallet='0xfresh'").fetchone()[0] == 5


async def test_refresh_cap_truncate_downgrades_complete_to_partial(tmp_path):
    # a refresh that CAP-truncates is a real truncation -> downgrade complete->partial (safe: benches from
    # ranking until a complete pull). This is the ONLY direction the flag moves on a refresh; never 0->1 on bad data.
    with db.connect(_db(tmp_path)) as conn:
        conn.execute("INSERT INTO pm_whale (wallet, backfill_complete) VALUES ('0xbig', 1)")
        c = FakeClient(closed={"0xbig": [_cp("0xbig", i) for i in range(100)]})
        res = await search_run.refresh_one(conn, "0xbig", client=c, now_ts=NOW, cap=100,
                                           fetch_events=_no_events, sleep=_nosleep)
        assert res["verdict"] == "partial"
        assert conn.execute("SELECT backfill_complete FROM pm_whale WHERE wallet='0xbig'").fetchone()[0] == 0


async def test_refresh_raise_keeps_complete_and_prior_rows(tmp_path):
    # a refresh whose pull RAISES must NOT bench a good whale: prior complete state + rows are untouched.
    with db.connect(_db(tmp_path)) as conn:
        for i in range(3):
            conn.execute("INSERT INTO pm_closed_position (wallet, condition_id, outcome_index, resolved_ts) "
                         "VALUES ('0xgood', ?, 0, ?)", ("old%d" % i, NOW))
        conn.execute("INSERT INTO pm_whale (wallet, backfill_complete, last_refresh_ts) VALUES ('0xgood', 1, 500)")
        c = FakeClient(raise_for=["0xgood"])
        with pytest.raises(Exception):
            await search_run.refresh_one(conn, "0xgood", client=c, now_ts=NOW,
                                         fetch_events=_no_events, sleep=_nosleep)
        row = conn.execute("SELECT backfill_complete, last_refresh_ts FROM pm_whale WHERE wallet='0xgood'").fetchone()
        assert row["backfill_complete"] == 1 and row["last_refresh_ts"] == 500   # UNCHANGED
        assert conn.execute("SELECT COUNT(*) FROM pm_closed_position WHERE wallet='0xgood'").fetchone()[0] == 3


# ═══════════════════════════════════ run_search orchestration ═══════════════════════════════════

async def test_run_search_backfills_new_skips_complete_records_run(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        conn.execute("INSERT INTO pm_whale (wallet, backfill_complete) VALUES ('0xdone', 1)")
        c = FakeClient(leaderboard=[("0xdone", "D"), ("0xnew", "N")],
                       closed={"0xdone": [_cp("0xdone", 1)], "0xnew": [_cp("0xnew", i) for i in range(20)]})
        res = await search_run.run_search(conn, client=c, clock=lambda: NOW,
                                          fetch_events=_no_events, sleep=_nosleep)
        assert res["status"] == "ok" and res["n_discovered"] == 2
        assert res["skipped_complete"] == 1 and res["backfilled_complete"] == 1 and res["n_backfilled"] == 1
        assert "0xdone" not in c.pulled and "0xnew" in c.pulled
        row = conn.execute("SELECT status, n_discovered, n_backfilled, n_candidates_written, finished_ts "
                           "FROM pm_search_run WHERE run_id=?", (res["run_id"],)).fetchone()
        assert row["status"] == "ok" and row["n_discovered"] == 2 and row["n_backfilled"] == 1
        assert row["n_candidates_written"] == 0 and row["finished_ts"] == NOW   # R2 writes no candidates


async def test_run_search_isolates_failed_wallet(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        c = FakeClient(leaderboard=[("0xok", "O"), ("0xbad", "B")],
                       closed={"0xok": [_cp("0xok", i) for i in range(10)]}, raise_for=["0xbad"])
        res = await search_run.run_search(conn, client=c, clock=lambda: NOW,
                                          fetch_events=_no_events, sleep=_nosleep)
        assert res["status"] == "ok"                          # a single wallet failure never aborts the run
        assert res["backfilled_complete"] == 1 and res["failed"] == 1
        bad = conn.execute("SELECT backfill_complete FROM pm_whale WHERE wallet='0xbad'").fetchone()
        assert bad is None or bad["backfill_complete"] != 1   # failed whale is NOT complete -> excluded from ranking
        summ = json.loads(conn.execute("SELECT summary FROM pm_search_run WHERE run_id=?",
                                       (res["run_id"],)).fetchone()["summary"])
        assert summ["failed"] and summ["failed"][0]["wallet"] == "0xbad"   # VISIBLE, not silent


async def test_run_search_partial_visible_and_not_complete(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        c = FakeClient(leaderboard=[("0xpart", "P")], closed={"0xpart": [_cp("0xpart", i) for i in range(100)]})
        res = await search_run.run_search(conn, client=c, clock=lambda: NOW, cap=100,   # cap truncates -> partial
                                          fetch_events=_no_events, sleep=_nosleep)
        assert res["backfilled_partial"] == 1 and res["backfilled_complete"] == 0
        assert conn.execute("SELECT backfill_complete FROM pm_whale WHERE wallet='0xpart'").fetchone()[0] == 0
        summ = json.loads(conn.execute("SELECT summary FROM pm_search_run WHERE run_id=?",
                                       (res["run_id"],)).fetchone()["summary"])
        assert "0xpart" in summ["partial_wallets"]


async def test_run_search_discovery_failure_is_error_but_run_recorded(tmp_path):
    class BoomClient:
        async def fetch_leaderboard(self, *, category, limit):
            raise RuntimeError("leaderboard down")
    with db.connect(_db(tmp_path)) as conn:
        res = await search_run.run_search(conn, client=BoomClient(), clock=lambda: NOW,
                                          fetch_events=_no_events, sleep=_nosleep)
        assert res["status"] == "error" and res["n_discovered"] == 0
        row = conn.execute("SELECT status, finished_ts FROM pm_search_run WHERE run_id=?",
                           (res["run_id"],)).fetchone()
        assert row["status"] == "error" and row["finished_ts"] == NOW   # recorded, not left dangling


async def test_partial_retries_next_run_completes_then_skips(tmp_path):
    """The full Ruling-1 lifecycle: partial -> retried (not skipped) -> complete -> then skipped forever."""
    with db.connect(_db(tmp_path)) as conn:
        rows = [_cp("0xw", i) for i in range(100)]
        # run 1: cap truncates -> partial (backfill_complete=0)
        r1 = await search_run.run_search(conn, client=FakeClient(leaderboard=[("0xw", "W")], closed={"0xw": rows}),
                                         clock=lambda: NOW, cap=100, fetch_events=_no_events, sleep=_nosleep)
        assert r1["backfilled_partial"] == 1
        assert conn.execute("SELECT backfill_complete FROM pm_whale WHERE wallet='0xw'").fetchone()[0] == 0
        # run 2: incomplete -> RE-ATTEMPTED (not skipped); cap high enough -> completes
        c2 = FakeClient(leaderboard=[("0xw", "W")], closed={"0xw": rows})
        r2 = await search_run.run_search(conn, client=c2, clock=lambda: NOW, cap=8000,
                                         fetch_events=_no_events, sleep=_nosleep)
        assert "0xw" in c2.pulled and r2["backfilled_complete"] == 1 and r2["skipped_complete"] == 0
        assert conn.execute("SELECT backfill_complete FROM pm_whale WHERE wallet='0xw'").fetchone()[0] == 1
        # run 3: complete -> SKIPPED (Ruling 1: never auto-re-pull a complete whale)
        c3 = FakeClient(leaderboard=[("0xw", "W")], closed={"0xw": rows})
        r3 = await search_run.run_search(conn, client=c3, clock=lambda: NOW, cap=8000,
                                         fetch_events=_no_events, sleep=_nosleep)
        assert "0xw" not in c3.pulled and r3["skipped_complete"] == 1 and r3["n_backfilled"] == 0


async def test_mid_pagination_raise_stamps_nothing(tmp_path):
    # pages 0,50 succeed; offset 100 exhausts retries -> _pull_closed raises -> the accumulated 100 rows are
    # DISCARDED (nothing upserted, nothing stamped). The whale is left with ZERO rows and NO pm_whale row.
    with db.connect(_db(tmp_path)) as conn:
        c = FakeClient(leaderboard=[("0xmid", "M")], closed={"0xmid": [_cp("0xmid", i) for i in range(200)]},
                       raise_at_offset={"0xmid": 100})
        res = await search_run.run_search(conn, client=c, clock=lambda: NOW, cap=8000,
                                          fetch_events=_no_events, sleep=_nosleep)
        assert res["failed"] == 1 and res["status"] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM pm_closed_position WHERE wallet='0xmid'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM pm_whale WHERE wallet='0xmid'").fetchone()[0] == 0


async def test_partial_relisted_while_still_partial(tmp_path):
    # a whale that stays partial across runs is RE-ATTEMPTED (not skipped) and RE-LISTED each time (visible).
    with db.connect(_db(tmp_path)) as conn:
        rows = [_cp("0xp", i) for i in range(100)]
        r1 = await search_run.run_search(conn, client=FakeClient(leaderboard=[("0xp", "P")], closed={"0xp": rows}),
                                         clock=lambda: NOW, cap=100, fetch_events=_no_events, sleep=_nosleep)
        r2 = await search_run.run_search(conn, client=FakeClient(leaderboard=[("0xp", "P")], closed={"0xp": rows}),
                                         clock=lambda: NOW, cap=100, fetch_events=_no_events, sleep=_nosleep)
        assert r1["backfilled_partial"] == 1 and r2["backfilled_partial"] == 1 and r2["skipped_complete"] == 0
        s2 = json.loads(conn.execute("SELECT summary FROM pm_search_run WHERE run_id=?",
                                     (r2["run_id"],)).fetchone()["summary"])
        assert "0xp" in s2["partial_wallets"]


def test_open_close_search_run_fields(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        rid = search_run.open_search_run(conn, started_ts=NOW, leaderboard_category="Sports",
                                         leaderboard_limit=250, min_resolved=50, recency_window_days=30,
                                         thin_sample_target=10)
        assert isinstance(rid, int) and rid > 0
        row = conn.execute("SELECT * FROM pm_search_run WHERE run_id=?", (rid,)).fetchone()
        assert row["status"] == "running" and row["min_resolved"] == 50
        assert row["recency_window_days"] == 30 and row["thin_sample_target"] == 10 and row["finished_ts"] is None
        search_run.close_search_run(conn, rid, finished_ts=NOW + 5, n_discovered=7, n_backfilled=3,
                                    status="ok", summary="{}")
        row2 = conn.execute("SELECT finished_ts, n_discovered, n_backfilled, n_candidates_written, status "
                            "FROM pm_search_run WHERE run_id=?", (rid,)).fetchone()
        assert row2["finished_ts"] == NOW + 5 and row2["n_discovered"] == 7 and row2["n_backfilled"] == 3
        assert row2["n_candidates_written"] == 0 and row2["status"] == "ok"


# ═══════════════════════════════════ R7 independence ═══════════════════════════════════

def test_search_run_does_not_import_order_path():
    forbidden = {"execution", "arm", "live_driver", "kalshi_live", "brokers", "kalshi", "boot_reconcile"}
    tree = ast.parse(inspect.getsource(search_run))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.update(a.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.update(node.module.split("."))
            for a in node.names:
                imported.add(a.name)
    assert not (imported & forbidden), sorted(imported & forbidden)
