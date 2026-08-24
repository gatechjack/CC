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


# ---- roster/watchlist seed from scout provenance (C2.4) ------------------------------------------

def test_seed_farm_roster_seeds_pins_and_flags_unresolved(tmp_path):
    p = _db(tmp_path)
    provenance = [
        {"wallet": "0xaaa", "user_name": "Kh4mz4t", "category": "ufc", "source": "ufc_scout"},
        {"user_name": "SDTrading", "category": "mlb", "source": "live"},     # name-only (wallet from agent_state)
    ]
    pinned_entries = [
        {"wallet": "0xAAA", "user_name": "Kh4mz4t"},           # wallet match (case-insensitive)
        {"wallet": "0xbbb", "user_name": "SDTrading"},         # name match (provenance carries no wallet)
        {"wallet": "0xccc", "user_name": "MysteryWhale"},      # NO provenance -> UNRESOLVED (never guessed)
    ]
    with db.connect(p) as conn:
        res = paper.seed_farm_roster(conn, pinned_entries=pinned_entries, provenance=provenance, now_ts=NOW)
        roster = {(r["wallet"], r["category"]) for r in conn.execute("SELECT wallet, category FROM pm_roster")}
        watch = {(r["wallet"], r["category"], r["status"])
                 for r in conn.execute("SELECT wallet, category, status FROM pm_watchlist")}
    assert res["n_seeded"] == 2
    assert res["n_unresolved"] == 1
    assert res["unresolved"][0]["wallet"] == "0xccc"
    assert ("0xaaa", "ufc") in roster
    assert ("0xbbb", "mlb") in roster                          # name-matched provenance
    assert ("0xaaa", "ufc", "pinned") in watch


def test_seed_farm_roster_idempotent(tmp_path):
    p = _db(tmp_path)
    provenance = [{"wallet": "0xaaa", "user_name": "K", "category": "ufc"}]
    entries = [{"wallet": "0xaaa", "user_name": "K"}]
    with db.connect(p) as conn:
        paper.seed_farm_roster(conn, pinned_entries=entries, provenance=provenance, now_ts=NOW)
        paper.seed_farm_roster(conn, pinned_entries=entries, provenance=provenance, now_ts=NOW + 1)   # re-run
        n_roster = conn.execute("SELECT COUNT(1) FROM pm_roster").fetchone()[0]
        n_watch = conn.execute("SELECT COUNT(1) FROM pm_watchlist").fetchone()[0]
    assert n_roster == 1 and n_watch == 1


def test_seed_then_subset_assertion_passes(tmp_path):
    """A seeded pin lands in pm_roster active AND pm_watchlist pinned -> the C2.3 subset assertion passes."""
    p = _db(tmp_path)
    provenance = [{"wallet": "0xaaa", "user_name": "K", "category": "ufc"}]
    entries = [{"wallet": "0xaaa", "user_name": "K"}]
    with db.connect(p) as conn:
        paper.seed_farm_roster(conn, pinned_entries=entries, provenance=provenance, now_ts=NOW)
        report = paper.assert_pinned_subset_of_refresh(conn)   # must NOT raise
    assert report["unrefreshed"] == []
    assert report["n_pinned"] == 1


def test_load_pin_provenance_reads_yaml(tmp_path):
    y = tmp_path / "prov.yaml"
    y.write_text("pins:\n  - {wallet: '0xAbc', user_name: 'W', category: 'ufc', source: 's'}\n",
                 encoding="utf-8")
    prov = paper.load_pin_provenance(str(y))
    assert len(prov) == 1 and prov[0]["category"] == "ufc"
    assert paper.load_pin_provenance(None) == []               # missing path -> []
    assert paper.load_pin_provenance(str(tmp_path / "nope.yaml")) == []


def test_seeded_pairs_validate_against_history(tmp_path):
    """pm_closed_position VALIDATES a pair (does the whale have rows in that category) -- never generates one."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _closed(conn, "0xaaa", "0xc1", oi=0, won=1)
        conn.execute("UPDATE pm_closed_position SET category='ufc' WHERE wallet='0xaaa'")
        val = paper.validate_pairs_have_history(
            conn, [{"wallet": "0xaaa", "category": "ufc"}, {"wallet": "0xaaa", "category": "nba"}])
    by = {(v["wallet"], v["category"]): v for v in val}
    assert by[("0xaaa", "ufc")]["has_history"] is True
    assert by[("0xaaa", "nba")]["has_history"] is False        # validated (no rows), NOT generated
