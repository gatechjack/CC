"""WATCHLIST per-row REFRESH button (board task 2026-10-08).

THE ASK: the Prospects table had a per-row Refresh (re-pull the COMPLETED /closed-positions basis on demand) but the
Watchlist did not -- so a promoted, live whale's Analyze basis (pm_closed_position, resolved only) could go stale
(the worked example: 12 stored vs 3593 upstream, 29 days old) with no way to re-pull it. This adds the SAME button to
the Watchlist ACTIONS column, mirroring refresh_one exactly, plus a labelled COMPLETED-basis cell so the result is
visible, and a server-side single-flight guard.

These tests call the REAL handler (TestClient against the real app) and the REAL ingest/rollup pipeline with a FAKE
Polymarket client (no network). Admin operator (the farm actions are admin-gated). PM DB only; offline.

★ NON-TAUTOLOGY NOTE: the acceptance test's "count rose 3 -> 8" is produced by the FAKE UPSTREAM returning 8 rows
(the fixture), NOT by the code under test; the HTML assertion checks the template surfaces the DB value. Neither the
expected count nor the pass condition is read back out of the function being tested.
"""
import asyncio
import types

import pytest
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, farm, stats, ingest
from trading_corp.prediction_markets.db import connect

WALLET = "0x27f738fe203827445690339104aae35b20bc44b0"   # the worked-example cfb whale
CAT = "cfb"


# ── fake upstream /closed-positions ───────────────────────────────────────────────────────────────────────────
def _fake_cp(i: int):
    """One attr-compatible ClosedPositionRow. total_bought>0 => NOT quarantined (compute_row_suspect clause b);
    cur_price>=0.9 => won. Unique condition_id per row so the PK-collision guard never fires."""
    return types.SimpleNamespace(
        proxy_wallet=WALLET, condition_id="cond_%d" % i, slug="s-%d" % i, event_slug="cfb-game-%d" % i,
        title="CFB Game %d" % i, outcome="Yes", outcome_index=0, avg_price=0.5, total_bought=100.0,
        realized_pnl=10.0, cur_price=1.0, end_date="2026-01-01", timestamp=1000 + i)


class FakeClient:
    """Stands in for PolymarketDataAPIClient (async ctx mgr). Returns FakeClient.rows; or raises FakeClient.exc."""
    rows: list = []
    exc: Exception | None = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetch_closed_positions(self, wallet, limit=50, offset=0):
        if FakeClient.exc is not None:
            raise FakeClient.exc
        return list(FakeClient.rows[offset:offset + limit])


def _set_upstream(n: int):
    FakeClient.rows = [_fake_cp(i) for i in range(n)]
    FakeClient.exc = None


def _patch_client_and_derive(monkeypatch):
    """Route the real refresh pipeline through FakeClient, and force every pulled row to category cfb (so tier-2
    categorize -- which would hit the network -- never runs)."""
    monkeypatch.setattr("trading_corp.data.polymarket_data_api_client.PolymarketDataAPIClient", FakeClient)
    monkeypatch.setattr(ingest, "derive_category_from_slug", lambda es, s=None: (CAT, "test"))


# ── db fixture ────────────────────────────────────────────────────────────────────────────────────────────────
def _mk(monkeypatch, tmp_path, *, extra_pins=(), candidate=None):
    """WALLET pinned in cfb (Watchlist). Optional extra pinned wallets + an optional CANDIDATE (prospect)."""
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts) VALUES(?,?,?,1,1,1)",
                     (WALLET, CAT, farm.PINNED))
        for w in extra_pins:
            conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts) VALUES(?,?,?,1,1,1)",
                         (w, CAT, farm.PINNED))
        if candidate is not None:
            conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts) VALUES(?,?,?,1,1,1)",
                         (candidate, CAT, farm.CANDIDATE))
    from trading_corp.prediction_markets.web.app import app
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    cl = TestClient(app)
    cl.headers.update({"Remote-User": "jack"})
    return cl, p


def _seed_completed(p, n, *, wallet=WALLET, now=1000):
    """Seed the COMPLETED basis via the REAL pipeline: n /closed-positions -> upsert -> stamp pm_whale -> rollup."""
    recs = [ingest.cp_to_record(_fake_cp(i), CAT, "test", now) for i in range(n)]
    for r in recs:
        r["wallet"] = wallet
    with connect(p) as conn:
        ingest.upsert_closed_positions(conn, recs)
        ingest._stamp_whale(conn, wallet, now, backfill=False, complete=True, pulled=n, stored=n)
        stats.rollup(conn, now_ts=now)


def _completed_n(p, wallet=WALLET):
    with connect(p) as conn:
        row = conn.execute("SELECT n_resolved FROM pm_category_stats WHERE wallet=? AND category=?",
                           (wallet, CAT)).fetchone()
        return (row["n_resolved"] if row else None)


# ── 1. the button renders on every watchlist row, nowhere it shouldn't ──────────────────────────────────────────
def test_refresh_button_on_every_watchlist_row(monkeypatch, tmp_path):
    other = "0x00000000000000000000000000000000000000ab"
    cl, _p = _mk(monkeypatch, tmp_path, extra_pins=[other])
    html = cl.get("/farm/%s" % CAT).text
    # one Watchlist refresh form per pinned whale, carrying ?from=watchlist and targeting the watchlist fragment
    assert html.count("/farm/%s/refresh/%s?from=watchlist" % (CAT, WALLET)) >= 1
    assert html.count("/farm/%s/refresh/%s?from=watchlist" % (CAT, other)) >= 1
    assert 'hx-target="#pm-watchlist-rows"' in html
    assert '<div id="pm-watchlist-rows">' in html          # the swap target wrapper exists
    # the completed-basis column + cell render (labelled, distinct from the paper columns)
    assert ">completed<" in html and 'drill=scoreable' in html
    # the Prospects button is UNCHANGED: still targets its own fragment and does NOT carry ?from=watchlist
    assert 'hx-target="#pm-prospects-rows"' in html


# ── 2. ★ ACCEPTANCE: a refresh raises the stored resolved count, and the new count is visible on screen ──────────
def test_refresh_raises_resolved_count_and_shows_it(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _patch_client_and_derive(monkeypatch)
    _seed_completed(p, 3)                                   # stale basis: 3 resolved
    n0 = _completed_n(p)
    assert n0 == 3
    html0 = cl.get("/farm/%s" % CAT).text
    assert ("/whale/%s/%s?drill=scoreable" % (WALLET, CAT)) in html0 and ">3</a>" in html0
    # upstream now has 8 (a SUPERSET of the 3) -> the on-demand refresh must bring them in
    _set_upstream(8)
    r = cl.post("/farm/%s/refresh/%s?from=watchlist" % (CAT, WALLET), headers={"HX-Request": "true"})
    assert r.status_code == 200
    n1 = _completed_n(p)
    assert n1 == 8 and n1 > n0                              # stored count ROSE (real ingest+rollup, fake upstream)
    assert ">8</a>" in r.text                               # the NEW count is on screen (re-rendered fragment)
    assert "resolved position(s) now" in r.text            # the success notice states what happened


# ── 3. ★ a failed refresh (429 / timeout) leaves stored values unchanged and shows a visible error ──────────────
@pytest.mark.parametrize("exc", [Exception("429 Too Many Requests"), asyncio.TimeoutError("read timeout")])
def test_failed_refresh_errs_safe(monkeypatch, tmp_path, exc):
    cl, p = _mk(monkeypatch, tmp_path)
    _patch_client_and_derive(monkeypatch)
    _seed_completed(p, 5)
    n0 = _completed_n(p)
    assert n0 == 5

    async def _raise(*a, **k):
        raise exc
    monkeypatch.setattr(ingest, "_pull_closed", _raise)    # the pull propagates -> _refresh_whale catches -> 'failed'
    r = cl.post("/farm/%s/refresh/%s?from=watchlist" % (CAT, WALLET), headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert _completed_n(p) == n0                            # UNCHANGED: a raised pull leaves the whale intact
    assert "FAILED" in r.text and "UNCHANGED" in r.text    # the visible error


# ── 4. double-POST does NOT double-call the external API (server-side guard, not the UI disable) ─────────────────
def test_double_post_single_flight(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    from trading_corp.prediction_markets.web import app as appmod
    calls = {"n": 0}

    async def _fake_rw(wallet, now_ts):
        calls["n"] += 1
        return "complete"
    monkeypatch.setattr(appmod, "_refresh_whale", _fake_rw)

    appmod._refresh_inflight.add(WALLET.lower())            # a refresh is already in flight for this wallet
    try:
        r = cl.post("/farm/%s/refresh/%s?from=watchlist" % (CAT, WALLET), headers={"HX-Request": "true"})
    finally:
        appmod._refresh_inflight.discard(WALLET.lower())
    assert r.status_code == 200
    assert calls["n"] == 0                                  # the second pull was NOT started (server-side)
    assert "already running" in r.text.lower()             # the inflight notice
    # a normal refresh DOES call it and leaves the guard set CLEAN (add/discard in finally)
    r2 = cl.post("/farm/%s/refresh/%s?from=watchlist" % (CAT, WALLET), headers={"HX-Request": "true"})
    assert r2.status_code == 200 and calls["n"] == 1
    assert WALLET.lower() not in appmod._refresh_inflight


# ── 5. the PAPER columns + tables are untouched by a refresh (the basis boundary) ───────────────────────────────
def test_paper_tables_untouched_by_refresh(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _patch_client_and_derive(monkeypatch)
    with connect(p) as conn:
        conn.execute("INSERT INTO pm_paper_trade(wallet,category,condition_id,outcome_index,entry_observed_ts,status,opened_ts) "
                     "VALUES(?,?,?,0,1,'open',1)", (WALLET, CAT, "0xpaperA"))
        conn.execute("INSERT INTO pm_paper_trade(wallet,category,condition_id,outcome_index,entry_observed_ts,status,opened_ts) "
                     "VALUES(?,?,?,0,1,'open',1)", (WALLET, CAT, "0xpaperB"))

    def _snap():
        with connect(p) as conn:
            pt = conn.execute("SELECT * FROM pm_paper_trade ORDER BY condition_id").fetchall()
            pcs = conn.execute("SELECT * FROM pm_paper_category_stats").fetchall()
            return [tuple(r) for r in pt], [tuple(r) for r in pcs]
    before = _snap()
    _set_upstream(6)
    r = cl.post("/farm/%s/refresh/%s?from=watchlist" % (CAT, WALLET), headers={"HX-Request": "true"})
    assert r.status_code == 200
    after = _snap()
    assert before == after                                 # paper trades + paper rollup BYTE-identical


# ── 6. the Prospects Refresh still behaves exactly as before (no shared-handler regression) ─────────────────────
def test_prospects_refresh_unchanged(monkeypatch, tmp_path):
    cand = "0x11111111111111111111111111111111111111cd"
    cl, p = _mk(monkeypatch, tmp_path, candidate=cand)
    from trading_corp.prediction_markets.web import app as appmod

    async def _fake_rw(wallet, now_ts):
        return "complete"
    monkeypatch.setattr(appmod, "_refresh_whale", _fake_rw)
    # NO ?from -> the handler must still render the PROSPECTS fragment (the legacy path), not the watchlist one
    r = cl.post("/farm/%s/refresh/%s" % (CAT, cand), headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "SCREEN ONLY" in r.text                          # the Prospects-only loss caveat => prospects fragment
    assert 'hx-target="#pm-watchlist-rows"' not in r.text   # it is NOT the watchlist fragment
    # and WITH ?from=watchlist -> the watchlist fragment (routing proven both ways)
    r2 = cl.post("/farm/%s/refresh/%s?from=watchlist" % (CAT, WALLET), headers={"HX-Request": "true"})
    assert r2.status_code == 200 and 'hx-target="#pm-watchlist-rows"' in r2.text


# ── 7. nothing the refresh writes is read by the live roster query or any gate (assert, don't assume) ───────────
def test_refresh_does_not_move_live_roster(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _patch_client_and_derive(monkeypatch)
    from trading_corp.prediction_markets import driver_roster
    with connect(p) as conn:
        conn.execute("INSERT INTO pm_account(account_id,venue,label,active,created_ts,secret_ref) "
                     "VALUES('kalshi_jack','kalshi','Jack',1,1,'kv/ref')")
        conn.execute("INSERT INTO pm_subdivision(account_id,category,label,sizing_mode,fixed_stake_usd,active,created_ts) "
                     "VALUES('kalshi_jack',?, 'Jack CFB','fixed',5.0,1,1)", (CAT,))
        conn.execute("INSERT INTO pm_subdivision_attachment(account_id,category,wallet,active,added_ts) "
                     "VALUES('kalshi_jack',?,?,1,1)", (CAT, WALLET))
        before = driver_roster.active_driver_subdivisions(conn)
    assert before and before[0]["account_id"] == "kalshi_jack"   # roster is non-empty (a real baseline)
    _set_upstream(7)
    assert cl.post("/farm/%s/refresh/%s?from=watchlist" % (CAT, WALLET),
                   headers={"HX-Request": "true"}).status_code == 200
    with connect(p) as conn:
        after = driver_roster.active_driver_subdivisions(conn)
    assert before == after                                  # a refresh moves NOTHING the engine roster reads


def test_engine_source_reads_none_of_the_refresh_tables():
    """Static guard (the Phase 0 grep, frozen as a test): the engine roster + gate source must reference NONE of the
    tables the refresh writes. If a future edit makes the engine read one, this fails before it can ship."""
    import os
    from trading_corp.prediction_markets import driver_roster, execution
    written = ["pm_closed_position", "pm_whale", "pm_category_stats",
               "pm_category_onesided_stats", "pm_score_snapshot", "pm_whale_score"]
    for mod in (driver_roster, execution):
        src = open(mod.__file__, encoding="utf-8").read()
        for t in written:
            assert t not in src, "%s reads %s -- refresh is no longer live-safe" % (os.path.basename(mod.__file__), t)
