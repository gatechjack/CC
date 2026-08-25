"""Tests for trading_corp.prediction_markets.paper -- the /positions paper poller (CP3a).

Offline: no network, no engine, no live DB. The fake client returns REAL PositionRow objects built via
PositionRow.from_api(raw), so `.extra` (outcomeIndex / redeemable / curPrice / endDate) is populated
exactly as the live client parses it -- the poller reads those from .extra, so a naive fixture would
hide a bug.
"""
import sqlite3

import pytest

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
    assert res["per_pair"] == []
    assert res["totals"]["captured"] == 0


async def test_skipped_category_counted_and_logged(tmp_path):
    """Ruling F: a genuinely-open position whose derived category is NOT pinned for the whale is counted +
    logged with its slug (not silently dropped), and is NOT captured."""
    p = _db(tmp_path)
    client = _Client([
        _raw("0xmlb", slug=MLB_SLUG, cur=0.5),                    # mlb -- pinned -> captured
        _raw("0xufc", slug=UFC_SLUG, cur=0.5),                    # ufc -- NOT pinned for this whale -> skipped
    ])
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)                               # pinned for mlb only
        res = await paper.poll_pinned(conn, client=client, now_ts=NOW)
        cids = {r["condition_id"] for r in conn.execute("SELECT condition_id FROM pm_paper_trade")}
    assert res["totals"]["captured"] == 1 and cids == {"0xmlb"}
    assert res["totals"]["n_skipped_category"] == 1 and len(res["skipped"]) == 1
    s = res["skipped"][0]
    assert s["derived_category"] == UFC_CAT and s["slug"] == UFC_SLUG and s["wallet"] == WALLET


async def test_last_polled_ts_distinguishes_polled_from_not(tmp_path):
    """Ruling G: a polled (wallet,category) gets pm_roster.last_polled_ts set; a whale whose fetch ERRORS
    leaves its pair's last_polled_ts NULL (absence is never the signal)."""
    p = _db(tmp_path)

    class _ErrClient:
        async def fetch_positions(self, wallet):
            if wallet == "0xerr":
                raise RuntimeError("boom")
            return [PositionRow.from_api(_raw("0xc1", cur=0.5))]

    with db.connect(p) as conn:
        _pin(conn, "0xok", MLB_CAT); _roster(conn, "0xok", MLB_CAT)
        _pin(conn, "0xerr", MLB_CAT); _roster(conn, "0xerr", MLB_CAT)
        res = await paper.poll_pinned(conn, client=_ErrClient(), now_ts=NOW)
        ok = conn.execute("SELECT last_polled_ts FROM pm_roster WHERE wallet='0xok'").fetchone()[0]
        err = conn.execute("SELECT last_polled_ts FROM pm_roster WHERE wallet='0xerr'").fetchone()[0]
    assert ok == NOW                                             # polled
    assert err is None                                          # NOT polled (fetch errored) -- distinguishable
    assert len(res["errors"]) == 1 and res["errors"][0]["wallet"] == "0xerr"


async def test_cap_suspect_flag_on_round_count(tmp_path):
    """Ruling H: a poll returning EXACTLY a round number (50/100/250/500) is flagged cap_suspect."""
    p = _db(tmp_path)
    client = _Client([_raw("0x%d" % i, cur=1.0, redeemable=True) for i in range(50)])   # raw count 50 = cap signature
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        res = await paper.poll_pinned(conn, client=client, now_ts=NOW)
    assert res["totals"]["cap_suspects"] == 1
    assert res["cap_suspects"][0]["positions_returned"] == 50


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


# ---- adjudicator ---------------------------------------------------------------------------------

def _roster(conn, wallet, category):
    conn.execute("INSERT OR IGNORE INTO pm_roster (wallet, category, active) VALUES (?, ?, 1)",
                 (wallet, category))


def _insert_pending(conn, wallet, category, cond, *, oi=0, entry_price=0.40, size_basis=100.0,
                    end_date="2020-01-01", entry_ts=NOW):
    conn.execute(
        "INSERT INTO pm_paper_trade (wallet, category, condition_id, outcome_index, entry_observed_ts, "
        "entry_price_avg_at_observation, size_basis, cost_basis, market_end_date, status, exit_observed_ts, "
        "opened_ts, updated_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_adjudication', ?, ?, ?)",
        (wallet, category, cond, oi, entry_ts, entry_price, size_basis, size_basis * entry_price,
         end_date, entry_ts, entry_ts, entry_ts))


def _closed(conn, wallet, cond, *, oi=0, won=1, pnl_suspect=0, suspect_reason=None, resolved_ts=NOW):
    conn.execute(
        "INSERT OR REPLACE INTO pm_closed_position (wallet, condition_id, outcome_index, won, realized_pnl, "
        "resolved_ts, pnl_suspect, suspect_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (wallet, cond, oi, won, 12.34, resolved_ts, pnl_suspect, suspect_reason))   # 12.34 = WHALE's pnl, ignored by paper


def test_adjudicate_books_resolution_won(tmp_path):
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT); _roster(conn, WALLET, MLB_CAT)
        _insert_pending(conn, WALLET, MLB_CAT, "0xc1", entry_price=0.40, size_basis=100.0)
        _closed(conn, WALLET, "0xc1", won=1, resolved_ts=NOW)
        res = paper.adjudicate(conn, now_ts=NOW + 10)
        row = conn.execute("SELECT * FROM pm_paper_trade").fetchone()
    assert res["closed"] == 1
    assert row["status"] == "closed"
    assert row["close_source"] == "resolution"
    assert row["won"] == 1
    assert abs(row["realized_pnl"] - (100.0 - 40.0)) < 1e-9      # size_basis - cost_basis (won), NOT the whale's pnl
    assert row["resolved_ts"] == NOW


def test_adjudicate_books_resolution_lost(tmp_path):
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT); _roster(conn, WALLET, MLB_CAT)
        _insert_pending(conn, WALLET, MLB_CAT, "0xc1", entry_price=0.40, size_basis=100.0)
        _closed(conn, WALLET, "0xc1", won=0)
        paper.adjudicate(conn, now_ts=NOW)
        row = conn.execute("SELECT * FROM pm_paper_trade").fetchone()
    assert row["status"] == "closed"
    assert abs(row["realized_pnl"] - (-40.0)) < 1e-9             # -cost_basis (lost)


def test_adjudicate_marks_stale_past_grace(tmp_path):
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT); _roster(conn, WALLET, MLB_CAT)
        _insert_pending(conn, WALLET, MLB_CAT, "0xgone", end_date="2020-01-01")   # long past; NO pm_closed_position
        res = paper.adjudicate(conn, now_ts=NOW, grace_window_sec=172800)
        row = conn.execute("SELECT * FROM pm_paper_trade").fetchone()
    assert res["staled"] == 1
    assert row["status"] == "stale"
    assert row["close_source"] == "whale_exit"
    assert row["realized_pnl"] is None                          # stale is EXCLUDED from realized


def test_adjudicate_stays_pending_within_grace(tmp_path):
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT); _roster(conn, WALLET, MLB_CAT)
        _insert_pending(conn, WALLET, MLB_CAT, "0xrecent", end_date="2020-01-01")
        res = paper.adjudicate(conn, now_ts=NOW, grace_window_sec=10**12)         # grace so large nothing is past
        row = conn.execute("SELECT * FROM pm_paper_trade").fetchone()
    assert res["still_pending"] == 1
    assert row["status"] == "pending_adjudication"


def test_adjudicate_unparseable_end_date_stays_pending(tmp_path):
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT); _roster(conn, WALLET, MLB_CAT)
        _insert_pending(conn, WALLET, MLB_CAT, "0xnoend", end_date="")            # unparseable end_date
        res = paper.adjudicate(conn, now_ts=NOW, grace_window_sec=1)              # tiny grace
        row = conn.execute("SELECT * FROM pm_paper_trade").fetchone()
    assert res["still_pending"] == 1                            # bias-down: never stale on a guess
    assert row["status"] == "pending_adjudication"


def test_adjudicate_imports_pnl_suspect_parity(tmp_path):
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT); _roster(conn, WALLET, MLB_CAT)
        _insert_pending(conn, WALLET, MLB_CAT, "0xq")
        _closed(conn, WALLET, "0xq", won=1, pnl_suspect=1, suspect_reason="row_invariant")
        paper.adjudicate(conn, now_ts=NOW)
        row = conn.execute("SELECT * FROM pm_paper_trade").fetchone()
    assert row["pnl_suspect"] == 1
    assert row["suspect_reason"] == "row_invariant"


def test_subset_assertion_fails_loud_on_unrefreshed_pinned(tmp_path):
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)                            # pinned but NOT in pm_roster active
        with pytest.raises(paper.PaperSubsetError) as ei:
            paper.assert_pinned_subset_of_refresh(conn)
    assert WALLET in str(ei.value)


def test_adjudicate_fails_loud_and_mutates_nothing_when_pinned_not_refreshed(tmp_path):
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)                            # pinned, not refreshed
        _insert_pending(conn, WALLET, MLB_CAT, "0xc1")
        with pytest.raises(paper.PaperSubsetError):
            paper.adjudicate(conn, now_ts=NOW)
        st = conn.execute("SELECT status FROM pm_paper_trade").fetchone()[0]
    assert st == "pending_adjudication"                        # assertion ran BEFORE any row was touched


# ---- roster/watchlist seed from pm_category_stats (C2.4 REVERSED 2026-08-24; Ruling B) -----------

def _cat_stats(conn, wallet, category, n_resolved):
    conn.execute("INSERT OR IGNORE INTO pm_category_stats (wallet, category, n_resolved) VALUES (?, ?, ?)",
                 (wallet, category, n_resolved))


def _whale(conn, wallet, user_name):
    conn.execute("INSERT OR IGNORE INTO pm_whale (wallet, user_name) VALUES (?, ?)", (wallet, user_name))


def test_seed_farm_roster_from_category_stats(tmp_path):
    """Every (wallet, category) in pm_category_stats for the migrated wallets becomes a pm_roster(active=1)
    + pm_watchlist(pinned) pair -- NO floor (n=3 stays), 'unknown' included, all categories (C2.4 reversed)."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _whale(conn, "0xaaa", "kutsumiakia")
        _cat_stats(conn, "0xaaa", "ufc", 121)
        _cat_stats(conn, "0xaaa", "cs2", 3)          # below any plausible floor -> STILL seeded (no floor)
        _cat_stats(conn, "0xaaa", "unknown", 1429)   # 'unknown' STAYS
        res = paper.seed_farm_roster(conn, wallets=["0xAAA"], now_ts=NOW)   # case-insensitive
        roster = {(r["wallet"], r["category"], r["active"])
                  for r in conn.execute("SELECT wallet, category, active FROM pm_roster")}
        watch = {(r["wallet"], r["category"], r["status"])
                 for r in conn.execute("SELECT wallet, category, status FROM pm_watchlist")}
    assert res["n_seeded"] == 3 and res["n_wallets"] == 1
    assert ("0xaaa", "ufc", 1) in roster
    assert ("0xaaa", "cs2", 1) in roster             # no floor
    assert ("0xaaa", "unknown", 1) in roster         # unknown stays
    assert ("0xaaa", "unknown", "pinned") in watch
    by = {s["category"]: s for s in res["seeded"]}   # eyeball fields: user_name joined, rows from n_resolved
    assert by["ufc"]["rows_in_category"] == 121 and by["ufc"]["user_name"] == "kutsumiakia"
    assert by["ufc"]["status"] == "pinned" and by["unknown"]["rows_in_category"] == 1429


def test_seed_scopes_to_migrated_wallets_only(tmp_path):
    """A wallet with pm_category_stats rows but NOT in the migrated set is NOT seeded."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _cat_stats(conn, "0xaaa", "ufc", 10)
        _cat_stats(conn, "0xother", "nba", 99)       # not in the migrated wallet set
        res = paper.seed_farm_roster(conn, wallets=["0xaaa"], now_ts=NOW)
        seeded_wallets = {r["wallet"] for r in conn.execute("SELECT wallet FROM pm_roster")}
    assert res["n_seeded"] == 1 and seeded_wallets == {"0xaaa"}


def test_seed_farm_roster_idempotent(tmp_path):
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _cat_stats(conn, "0xaaa", "ufc", 10)
        paper.seed_farm_roster(conn, wallets=["0xaaa"], now_ts=NOW)
        paper.seed_farm_roster(conn, wallets=["0xaaa"], now_ts=NOW + 1)   # re-run
        n_roster = conn.execute("SELECT COUNT(1) FROM pm_roster").fetchone()[0]
        n_watch = conn.execute("SELECT COUNT(1) FROM pm_watchlist").fetchone()[0]
    assert n_roster == 1 and n_watch == 1


def test_seeded_pairs_table_is_the_eyeball_list(tmp_path):
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _whale(conn, "0xaaa", "kutsumiakia")
        _cat_stats(conn, "0xaaa", "ufc", 121)
        _cat_stats(conn, "0xaaa", "unknown", 1429)
        paper.seed_farm_roster(conn, wallets=["0xaaa"], now_ts=NOW)
        table = paper.seeded_pairs_table(conn)
    assert set(table[0].keys()) == {"wallet", "user_name", "category", "rows_in_category", "status"}
    by = {t["category"]: t for t in table}
    assert by["ufc"]["rows_in_category"] == 121 and by["ufc"]["user_name"] == "kutsumiakia"
    assert by["unknown"]["rows_in_category"] == 1429 and by["unknown"]["status"] == "pinned"


def test_seed_then_subset_assertion_passes(tmp_path):
    """Seed writes pm_roster + pm_watchlist in one pass -> pinned == roster -> the C2.3 subset assertion passes."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _cat_stats(conn, "0xaaa", "ufc", 10)
        _cat_stats(conn, "0xaaa", "nba", 5)
        paper.seed_farm_roster(conn, wallets=["0xaaa"], now_ts=NOW)
        report = paper.assert_pinned_subset_of_refresh(conn)   # must NOT raise
    assert report["unrefreshed"] == [] and report["n_pinned"] == 1
