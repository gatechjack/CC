"""Tests for trading_corp.prediction_markets.paper -- the /positions paper poller (CP3a).

Offline: no network, no engine, no live DB. The fake client returns REAL PositionRow objects built via
PositionRow.from_api(raw), so `.extra` (outcomeIndex / redeemable / curPrice / endDate) is populated
exactly as the live client parses it -- the poller reads those from .extra, so a naive fixture would
hide a bug.
"""
import sqlite3

from trading_corp.prediction_markets import db, paper
from trading_corp.prediction_markets.category import derive_category_from_slug
from trading_corp.data.polymarket_data_api_client import PositionRow

WALLET = "0xwhale"
MLB_SLUG = "mlb-team-a-team-b-2026-09-01"
UFC_SLUG = "ufc-fighter-a-fighter-b-2026-09-01"
MLB_CAT = derive_category_from_slug(MLB_SLUG)[0]   # 'mlb'
UFC_CAT = derive_category_from_slug(UFC_SLUG)[0]   # 'ufc'
NOW = 1_700_000_000


def _raw(cond, *, oi=0, size=100.0, avg=0.40, cur=0.50, redeemable=False, slug=MLB_SLUG, end="2026-09-01"):
    """A raw /positions API row (the shape PositionRow.from_api consumes)."""
    return {"proxyWallet": WALLET, "conditionId": cond, "asset": "a%s" % oi, "size": size,
            "avgPrice": avg, "initialValue": size * avg, "currentValue": size * cur, "cashPnl": 0.0,
            "title": "T", "outcome": "Yes", "slug": slug, "eventSlug": slug,
            "outcomeIndex": oi, "redeemable": redeemable, "curPrice": cur, "endDate": end}


class _Client:
    """Fake data-api client; its /positions snapshot is a mutable list of raw API dicts."""
    def __init__(self, raws):
        self.raws = list(raws)

    async def fetch_positions(self, wallet):
        return [PositionRow.from_api(r) for r in self.raws]


def _db(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    return p


def _pin(conn, wallet, category):
    conn.execute("INSERT OR IGNORE INTO pm_watchlist (wallet, category, status) VALUES (?, ?, 'pinned')",
                 (wallet, category))


async def test_captures_genuinely_open_in_pinned_category(tmp_path):
    """OR-filter excludes resolved-unredeemed; category filter excludes off-category; two-sided legs both
    land; entry columns are observation-provenance and cost_basis = size_basis * entry_price."""
    p = _db(tmp_path)
    client = _Client([
        _raw("0xc1", oi=0, size=100, avg=0.40, cur=0.55),                     # MLB open -> captured
        _raw("0xc1", oi=1, size=50, avg=0.60, cur=0.45),                      # MLB open, other leg -> captured
        _raw("0xc2", oi=0, size=200, avg=0.30, cur=1.0, redeemable=True),     # MLB resolved-unredeemed -> filtered
        _raw("0xc3", oi=0, size=10, avg=0.50, cur=0.50, slug=UFC_SLUG),       # UFC open -> off-category, not captured
    ])
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        res = await paper.poll_pinned(conn, client=client, now_ts=NOW)
        rows = conn.execute("SELECT * FROM pm_paper_trade ORDER BY outcome_index").fetchall()
    assert res["totals"]["captured"] == 2
    assert len(rows) == 2
    assert {r["condition_id"] for r in rows} == {"0xc1"}
    assert {r["outcome_index"] for r in rows} == {0, 1}
    r0 = next(r for r in rows if r["outcome_index"] == 0)
    assert r0["status"] == "open"
    assert r0["entry_observed_ts"] == NOW
    assert r0["category"] == MLB_CAT
    assert r0["size_basis"] == 100.0
    assert abs(r0["cost_basis"] - 100.0 * 0.40) < 1e-9        # size_basis * entry_price
    assert r0["entry_price_avg_at_observation"] == 0.40
    assert r0["whale_size_at_observation"] == 100.0
    assert r0["entry_basis"] == "positions_observation"
    assert r0["market_end_date"] == "2026-09-01"
    assert r0["poll_interval_sec"] == 300


async def test_or_filter_excludes_settled(tmp_path):
    """A row is excluded if redeemable OR curPrice at a settled bound (0/1) -- each firing alone excludes."""
    p = _db(tmp_path)
    client = _Client([
        _raw("0xopen", cur=0.50, redeemable=False),      # genuine open
        _raw("0xred", cur=0.50, redeemable=True),        # redeemable -> settled
        _raw("0xzero", cur=0.0, redeemable=False),       # curPrice 0 -> settled
        _raw("0xone", cur=1.0, redeemable=False),        # curPrice 1 -> settled
    ])
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        res = await paper.poll_pinned(conn, client=client, now_ts=NOW)
        cids = {r["condition_id"] for r in conn.execute("SELECT condition_id FROM pm_paper_trade")}
    assert res["totals"]["captured"] == 1
    assert cids == {"0xopen"}


async def test_idempotent_no_duplicate_on_second_poll(tmp_path):
    p = _db(tmp_path)
    client = _Client([_raw("0xc1", size=100, avg=0.40, cur=0.50)])
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        r1 = await paper.poll_pinned(conn, client=client, now_ts=NOW)
        r2 = await paper.poll_pinned(conn, client=client, now_ts=NOW + 300)   # same snapshot, later poll
        n = conn.execute("SELECT COUNT(1) FROM pm_paper_trade").fetchone()[0]
    assert r1["totals"]["captured"] == 1
    assert r2["totals"]["captured"] == 0        # open guard: already tracked -> no new entry
    assert r2["totals"]["touched"] == 1
    assert n == 1


async def test_scale_in_increments_adds_not_new_entry(tmp_path):
    p = _db(tmp_path)
    client = _Client([_raw("0xc1", size=100, avg=0.40, cur=0.50)])
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        await paper.poll_pinned(conn, client=client, now_ts=NOW)
        client.raws = [_raw("0xc1", size=175, avg=0.42, cur=0.50)]           # whale added to the position
        r2 = await paper.poll_pinned(conn, client=client, now_ts=NOW + 300)
        row = conn.execute("SELECT * FROM pm_paper_trade").fetchone()
    assert r2["totals"]["adds"] == 1
    assert r2["totals"]["captured"] == 0
    assert row["n_observed_adds"] == 1
    assert row["last_add_observed_ts"] == NOW + 300
    assert row["last_observed_size"] == 175.0
    assert row["entry_observed_ts"] == NOW                     # entry provenance unchanged
    assert row["entry_price_avg_at_observation"] == 0.40       # not re-weighted; size_basis is fixed
    assert row["status"] == "open"


async def test_reduction_increments_reductions_no_status_change(tmp_path):
    p = _db(tmp_path)
    client = _Client([_raw("0xc1", size=100, cur=0.50)])
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        await paper.poll_pinned(conn, client=client, now_ts=NOW)
        client.raws = [_raw("0xc1", size=60, cur=0.50)]                      # partial whale exit
        r2 = await paper.poll_pinned(conn, client=client, now_ts=NOW + 300)
        row = conn.execute("SELECT * FROM pm_paper_trade").fetchone()
    assert r2["totals"]["reductions"] == 1
    assert row["n_observed_reductions"] == 1
    assert row["status"] == "open"                            # partial reduction does NOT change status (CP3a)
    assert row["last_observed_size"] == 60.0


async def test_vanish_marks_pending_adjudication(tmp_path):
    p = _db(tmp_path)
    client = _Client([_raw("0xc1", size=100, cur=0.50)])
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        await paper.poll_pinned(conn, client=client, now_ts=NOW)
        client.raws = []                                                     # position gone from /positions
        r2 = await paper.poll_pinned(conn, client=client, now_ts=NOW + 300)
        row = conn.execute("SELECT * FROM pm_paper_trade").fetchone()
    assert r2["totals"]["vanished"] == 1
    assert row["status"] == "pending_adjudication"            # NOT stale -- adjudicator decides (addendum 1)
    assert row["exit_observed_ts"] == NOW + 300
    assert row["close_source"] is None                        # provenance set only at adjudication


async def test_no_pinned_whales_is_honest_empty(tmp_path):
    p = _db(tmp_path)
    client = _Client([_raw("0xc1")])
    with db.connect(p) as conn:
        res = await paper.poll_pinned(conn, client=client, now_ts=NOW)       # nothing pinned
    assert res["per_whale"] == []
    assert res["totals"]["captured"] == 0


def test_get_config_seeded_and_degrades_when_absent(tmp_path):
    p = _db(tmp_path)
    with db.connect(p) as conn:
        assert paper.get_config(conn, "poll_interval_sec") == 300.0
        assert paper.get_config(conn, "grace_window_sec") == 172800.0
        assert paper.get_config(conn, "size_basis") == 100.0
    # honest degradation: a DB with no pm_paper_config table returns the code DEFAULT, never raises
    bare = sqlite3.connect(str(tmp_path / "bare.db"))
    try:
        assert paper.get_config(bare, "poll_interval_sec") == 300.0
        assert paper.get_config(bare, "size_basis") == 100.0
    finally:
        bare.close()
