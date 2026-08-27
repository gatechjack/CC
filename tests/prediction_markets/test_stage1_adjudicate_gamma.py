"""Stage 1 -- gamma adjudicator anti-drift tests.

Tests the KEY load-bearing change: `paper.adjudicate()` is re-based on gamma (fetch_market_resolutions)
instead of pm_closed_position. The critical case is the PM FOUNDATION FINDING (2026-08-26): /closed-positions
systematically drops held losses (~63% for evanng), so the OLD adjudicator could NEVER book those losses.
Gamma /markets reports every market's outcome independently -- it does NOT have the omission.

These are BASIS tests: each proves what the adjudicator CLASSIFIES on, not just that it produces a row.
Offline: no network, no engine, no live DB. tmp_path sqlite, db.init_db, helper seeders.
"""
import pytest

from trading_corp.prediction_markets import db, paper
from trading_corp.prediction_markets.category import derive_category_from_slug

WALLET = "0xwhale"
MLB_CAT = derive_category_from_slug("mlb-team-a-team-b-2026-09-01")[0]
NOW = 1_700_000_000


def _db(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    return p


def _pin(conn, wallet, category):
    conn.execute("INSERT OR IGNORE INTO pm_watchlist (wallet, category, status) VALUES (?, ?, 'pinned')",
                 (wallet, category))


def _roster(conn, wallet, category):
    conn.execute("INSERT OR IGNORE INTO pm_roster (wallet, category, active) VALUES (?, ?, 1)",
                 (wallet, category))


def _insert_pending(conn, wallet, category, cond, *, oi=0, entry_price=0.40, size_basis=100.0,
                    end_date="2020-01-01", entry_ts=NOW):
    """Seed one pending_adjudication paper trade row."""
    conn.execute(
        "INSERT INTO pm_paper_trade (wallet, category, condition_id, outcome_index, entry_observed_ts, "
        "entry_price_avg_at_observation, size_basis, cost_basis, market_end_date, status, exit_observed_ts, "
        "opened_ts, updated_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_adjudication', ?, ?, ?)",
        (wallet, category, cond, oi, entry_ts, entry_price, size_basis, size_basis * entry_price,
         end_date, entry_ts, entry_ts, entry_ts))


def _resolved(winning_outcome_index: int) -> dict:
    """A gamma record for a resolved market, with the given winning outcome index."""
    return {"status": "resolved", "winning_outcome_index": winning_outcome_index,
            "yes_won": winning_outcome_index == 1, "outcomes": ["No", "Yes"],
            "outcome_prices": [0.0, 1.0], "closed": True, "title": "Test market"}


def _void_rec() -> dict:
    return {"status": "void", "winning_outcome_index": None,
            "yes_won": None, "outcomes": ["No", "Yes"],
            "outcome_prices": [0.5, 0.5], "closed": True, "title": "Void market"}


# ---- THE KEY CASE: loss-omission fix -------------------------------------------------------

def test_gamma_loss_omission_fix(tmp_path):
    """THE load-bearing test (PM FOUNDATION FINDING 2026-08-26).

    A pending_adjudication trade has outcome_index=0. NO pm_closed_position row exists (the /closed-positions
    omission bug). Under the OLD adjudicator this trade would NEVER resolve (no cp row -> stays pending or goes
    stale). Under the NEW gamma adjudicator, winning_outcome_index=1 means outcome_index=0 LOST -> it MUST be
    booked as closed/won=0/realized_pnl<0 (a LOSS), not stale.

    This test FAILS against the old adjudicate() implementation and PASSES with the gamma re-base."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        _roster(conn, WALLET, MLB_CAT)
        # outcome_index=0 trades YES (No wins -> we bet No but the market says Yes won -> we LOST)
        _insert_pending(conn, WALLET, MLB_CAT, "0xloss", oi=0, entry_price=0.40, size_basis=100.0)
        # NO pm_closed_position row -- this is the loss-omission scenario
        assert conn.execute("SELECT COUNT(*) FROM pm_closed_position").fetchone()[0] == 0
        # gamma says winning_outcome_index=1 (Yes won) -> outcome_index=0 LOSES
        resolutions = {"0xloss": _resolved(winning_outcome_index=1)}
        res = paper.adjudicate(conn, resolutions, now_ts=NOW)
        row = conn.execute("SELECT * FROM pm_paper_trade WHERE condition_id='0xloss'").fetchone()

    assert res["closed"] == 1, "must book this as closed, not pending/stale"
    assert res["staled"] == 0, "must NOT be stale -- gamma has a definitive resolution"
    assert row["status"] == "closed"
    assert row["close_source"] == "gamma_resolution"
    assert row["won"] == 0, "outcome_index=0 lost (winning was 1)"
    # realized_pnl = -cost_basis = -(100 * 0.40) = -40
    assert row["realized_pnl"] is not None
    assert row["realized_pnl"] < 0, "a loss MUST be negative realized_pnl"
    assert abs(row["realized_pnl"] - (-40.0)) < 1e-9


# ---- win case ----------------------------------------------------------------------------

def test_gamma_win(tmp_path):
    """outcome_index == winning_outcome_index -> won=1, realized_pnl>0."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        _roster(conn, WALLET, MLB_CAT)
        _insert_pending(conn, WALLET, MLB_CAT, "0xwin", oi=1, entry_price=0.40, size_basis=100.0)
        resolutions = {"0xwin": _resolved(winning_outcome_index=1)}
        res = paper.adjudicate(conn, resolutions, now_ts=NOW)
        row = conn.execute("SELECT * FROM pm_paper_trade WHERE condition_id='0xwin'").fetchone()

    assert res["closed"] == 1
    assert row["status"] == "closed"
    assert row["close_source"] == "gamma_resolution"
    assert row["won"] == 1
    # realized_pnl = size_basis - cost_basis = 100 - 40 = 60
    assert abs(row["realized_pnl"] - 60.0) < 1e-9
    assert row["pnl_suspect"] == 0
    assert row["suspect_reason"] is None


# ---- void case ---------------------------------------------------------------------------

def test_gamma_void(tmp_path):
    """status='void' -> paper row goes to status='void', close_source='market_void'. Excluded from win/loss."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        _roster(conn, WALLET, MLB_CAT)
        _insert_pending(conn, WALLET, MLB_CAT, "0xvoid", end_date="2020-01-01")
        res = paper.adjudicate(conn, {"0xvoid": _void_rec()}, now_ts=NOW)
        row = conn.execute("SELECT * FROM pm_paper_trade WHERE condition_id='0xvoid'").fetchone()

    assert res["voided"] == 1
    assert res["staled"] == 0
    assert row["status"] == "void"
    assert row["close_source"] == "market_void"
    assert row["won"] is None
    assert row["realized_pnl"] is None


# ---- pending + past grace -> stale -------------------------------------------------------

def test_pending_past_grace_goes_stale(tmp_path):
    """A condition_id NOT in resolutions (or status='pending') AND past market_end_date+grace -> stale."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        _roster(conn, WALLET, MLB_CAT)
        # market_end_date far in the past relative to NOW; grace=0 so past_grace is True
        _insert_pending(conn, WALLET, MLB_CAT, "0xstale", end_date="2020-01-01")
        # not in resolutions -> rec = {"status": "not_found"} -> falls through to grace check
        res = paper.adjudicate(conn, {}, now_ts=NOW, grace_window_sec=0)
        row = conn.execute("SELECT * FROM pm_paper_trade WHERE condition_id='0xstale'").fetchone()

    assert res["staled"] == 1
    assert row["status"] == "stale"
    assert row["close_source"] == "whale_exit"
    assert row["stale_reason"] == "vanished_pre_resolution_grace_elapsed"
    assert row["realized_pnl"] is None                        # stale is excluded from realized


def test_pending_status_in_resolutions_past_grace_goes_stale(tmp_path):
    """A resolutions record with status='pending' is treated the same as not_found: falls to grace check."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        _roster(conn, WALLET, MLB_CAT)
        _insert_pending(conn, WALLET, MLB_CAT, "0xpend", end_date="2020-01-01")
        pending_rec = {"status": "pending", "winning_outcome_index": None}
        res = paper.adjudicate(conn, {"0xpend": pending_rec}, now_ts=NOW, grace_window_sec=0)
        row = conn.execute("SELECT * FROM pm_paper_trade WHERE condition_id='0xpend'").fetchone()

    assert res["staled"] == 1
    assert row["status"] == "stale"


# ---- pending + within grace -> stays pending --------------------------------------------

def test_pending_within_grace_stays_pending(tmp_path):
    """Within grace (huge grace_window_sec) -> stays pending_adjudication."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        _roster(conn, WALLET, MLB_CAT)
        _insert_pending(conn, WALLET, MLB_CAT, "0xwait", end_date="2020-01-01")
        res = paper.adjudicate(conn, {}, now_ts=NOW, grace_window_sec=10 ** 12)
        row = conn.execute("SELECT * FROM pm_paper_trade WHERE condition_id='0xwait'").fetchone()

    assert res["still_pending"] == 1
    assert row["status"] == "pending_adjudication"


# ---- collect_pending_condition_ids -------------------------------------------------------

def test_collect_pending_condition_ids(tmp_path):
    """Returns distinct condition_ids of pending_adjudication rows; ignores other statuses."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        _roster(conn, WALLET, MLB_CAT)
        _insert_pending(conn, WALLET, MLB_CAT, "0xpA")
        _insert_pending(conn, WALLET, MLB_CAT, "0xpB")
        # seed one 'open' row -- must NOT appear in the result
        conn.execute(
            "INSERT INTO pm_paper_trade (wallet, category, condition_id, outcome_index, entry_observed_ts, "
            "opened_ts, updated_ts, status) VALUES (?, ?, '0xopen', 0, ?, ?, ?, 'open')",
            (WALLET, MLB_CAT, NOW, NOW, NOW))
        cids = paper.collect_pending_condition_ids(conn)

    assert set(cids) == {"0xpA", "0xpB"}
    assert "0xopen" not in cids


def test_collect_pending_condition_ids_empty_when_no_pending(tmp_path):
    p = _db(tmp_path)
    with db.connect(p) as conn:
        cids = paper.collect_pending_condition_ids(conn)
    assert cids == []


# ---- subset assertion still fires -------------------------------------------------------

def test_subset_assertion_still_fires_for_unrefreshed(tmp_path):
    """The C2.3 subset assertion still FAILS LOUD before any row is touched (gamma re-base preserves this)."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)   # pinned, NOT in pm_roster
        _insert_pending(conn, WALLET, MLB_CAT, "0xany")
        resolutions = {"0xany": _resolved(winning_outcome_index=1)}
        with pytest.raises(paper.PaperSubsetError):
            paper.adjudicate(conn, resolutions, now_ts=NOW)
        # assert no rows were mutated (assertion ran FIRST)
        row = conn.execute("SELECT status FROM pm_paper_trade").fetchone()
    assert row["status"] == "pending_adjudication"


# ---- return dict shape -------------------------------------------------------------------

def test_adjudicate_return_dict_has_voided_key(tmp_path):
    """The new return dict includes 'voided' (not present in old adjudicate)."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT)
        _roster(conn, WALLET, MLB_CAT)
        res = paper.adjudicate(conn, {}, now_ts=NOW)
    assert "voided" in res
    assert "pending_in" in res and "closed" in res and "staled" in res
    assert "still_pending" in res and "grace_window_sec" in res and "subset" in res
