"""CP2 — atomic ``set_agent_state_multi`` + roster-split invariant helper.

The whole point is atomicity: a promote/demote moves a whale across THREE
agent_state keys in ONE transaction, so a crash mid-move can NEVER leave the
whale in two rosters (papering AND live) or in none. These tests prove the
transaction boundary (BEGIN IMMEDIATE … COMMIT / ROLLBACK) and the invariant
helper CP3/CP4 will reuse.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trading_corp.persistence import db
from trading_corp.agents.strategies import roster_split as rs


# ── Fixtures / helpers ──────────────────────────────────────────────────


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    p = tmp_path / "agent_state_multi.db"
    url = f"sqlite:///{p.as_posix()}"
    db.init_db(db_url=url)
    return url


LIVE_ACTOR, LIVE_KEY = rs.LIVE_ACTOR, rs.LIVE_KEY          # poly_kalshi_mlb / live_whales
PAPER_ACTOR, PAPER_KEY = rs.PAPER_ACTOR, rs.PAPER_KEY      # polymarket_copy_trader / selected_whales
PIN_KEY = "pinned_whales"

WHALE = {"wallet": "0xabc123", "user_name": "SDTrading"}


def _load(db_url: str, actor: str, key: str):
    rec = db.load_agent_state(actor, key, db_url=db_url)
    return rec[0] if rec else None


# ── Happy path: the 3-key move commits atomically ───────────────────────


def test_three_key_move_commits_all(db_url: str):
    """Promote shape: write live_whales AND clear selected_whales AND
    clear pinned_whales in ONE call -> whale is live-only afterward."""
    # Start: whale W papering (in selected + pinned), live empty.
    db.set_agent_state(PAPER_ACTOR, PAPER_KEY, [WHALE], db_url=db_url)
    db.set_agent_state(PAPER_ACTOR, PIN_KEY, [WHALE], db_url=db_url)

    db.set_agent_state_multi(
        [
            (LIVE_ACTOR, LIVE_KEY, [WHALE]),      # add to live
            (PAPER_ACTOR, PAPER_KEY, []),         # remove from paper
            (PAPER_ACTOR, PIN_KEY, []),           # remove from pins (the §1.5 fix)
        ],
        db_url=db_url,
    )

    assert _load(db_url, LIVE_ACTOR, LIVE_KEY) == [WHALE]
    assert _load(db_url, PAPER_ACTOR, PAPER_KEY) == []
    assert _load(db_url, PAPER_ACTOR, PIN_KEY) == []
    # And the invariant holds: live ∩ paper == ∅.
    rs.check_rosters_disjoint(db_url=db_url)


def test_empty_updates_is_noop(db_url: str):
    db.set_agent_state(PAPER_ACTOR, PAPER_KEY, [WHALE], db_url=db_url)
    db.set_agent_state_multi([], db_url=db_url)          # must not raise / not touch anything
    assert _load(db_url, PAPER_ACTOR, PAPER_KEY) == [WHALE]


# ── Atomicity proof #1: forced crash AFTER row 1, BEFORE the rest ───────


def test_forced_crash_mid_move_rolls_back_no_split_state(db_url: str, monkeypatch):
    """Simulate a process crash between key writes. The first write (add to
    live_whales) really executes inside the transaction; the second write
    (remove from selected_whales) raises. WITHOUT rollback the whale would be
    in BOTH rosters (live AND paper) = the split state we must prevent. WITH
    rollback, live_whales reverts and the whale stays paper-only (its original,
    single state)."""
    # Start: whale W papering only.
    db.set_agent_state(PAPER_ACTOR, PAPER_KEY, [WHALE], db_url=db_url)

    real_upsert = db._upsert_agent_state_row
    calls = {"n": 0}

    def flaky(conn, agent, key, value, ts):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_upsert(conn, agent, key, value, ts)   # row 1: REAL insert of live_whales
        raise RuntimeError("simulated crash mid-move")         # row 2: crash before COMMIT

    monkeypatch.setattr(db, "_upsert_agent_state_row", flaky)

    with pytest.raises(RuntimeError, match="simulated crash mid-move"):
        db.set_agent_state_multi(
            [
                (LIVE_ACTOR, LIVE_KEY, [WHALE]),   # row 1 (really executes, then rolled back)
                (PAPER_ACTOR, PAPER_KEY, []),      # row 2 (never runs — crash)
            ],
            db_url=db_url,
        )

    # ROLLBACK reverted row 1: live_whales is still empty/absent...
    assert _load(db_url, LIVE_ACTOR, LIVE_KEY) in (None, [])
    # ...and the whale remains in EXACTLY ONE roster (paper), unchanged.
    assert _load(db_url, PAPER_ACTOR, PAPER_KEY) == [WHALE]
    # Invariant intact: no split state.
    rs.check_rosters_disjoint(db_url=db_url)


# ── Atomicity proof #2: a REAL DB error mid-move (no monkeypatch) ────────


def test_real_integrity_error_mid_move_rolls_back(db_url: str):
    """A genuine sqlite failure on row 2 (agent=None violates NOT NULL) must
    roll back row 1 — proving atomicity without any fault injection seam."""
    db.set_agent_state(PAPER_ACTOR, PAPER_KEY, [WHALE], db_url=db_url)

    with pytest.raises(Exception):
        db.set_agent_state_multi(
            [
                (LIVE_ACTOR, LIVE_KEY, [WHALE]),   # row 1: valid insert
                (None, LIVE_KEY, [WHALE]),         # row 2: NOT NULL violation -> IntegrityError
            ],
            db_url=db_url,
        )

    assert _load(db_url, LIVE_ACTOR, LIVE_KEY) in (None, [])   # row 1 rolled back
    assert _load(db_url, PAPER_ACTOR, PAPER_KEY) == [WHALE]    # untouched


def test_successful_move_after_a_rolled_back_one(db_url: str):
    """Lock/transaction is cleanly released after a ROLLBACK — a subsequent
    move commits normally (no lingering write-lock from the aborted txn)."""
    db.set_agent_state(PAPER_ACTOR, PAPER_KEY, [WHALE], db_url=db_url)
    with pytest.raises(Exception):
        db.set_agent_state_multi(
            [(LIVE_ACTOR, LIVE_KEY, [WHALE]), (None, LIVE_KEY, [WHALE])],
            db_url=db_url,
        )
    # Now a clean move must succeed.
    db.set_agent_state_multi(
        [(LIVE_ACTOR, LIVE_KEY, [WHALE]), (PAPER_ACTOR, PAPER_KEY, [])],
        db_url=db_url,
    )
    assert _load(db_url, LIVE_ACTOR, LIVE_KEY) == [WHALE]
    assert _load(db_url, PAPER_ACTOR, PAPER_KEY) == []


# ── Invariant helper: wallet extraction across shapes ───────────────────


def test_extract_wallets_shapes():
    assert rs.extract_wallets([{"wallet": "0xAbC"}]) == {"0xabc"}          # dict + lowercased
    assert rs.extract_wallets([{"proxy_wallet": "0xDEF"}]) == {"0xdef"}    # watch_only shape
    assert rs.extract_wallets(["0xGHI", "0xghi"]) == {"0xghi"}            # bare strings, dedup by case
    assert rs.extract_wallets([{"wallet": "0x1"}, "0x2"]) == {"0x1", "0x2"}  # mixed
    assert rs.extract_wallets([{"user_name": "no_wallet"}, {"wallet": ""}]) == set()  # blanks skipped
    assert rs.extract_wallets(None) == set()
    assert rs.extract_wallets("not-a-list") == set()


def test_assert_disjoint_passes_and_raises():
    # Disjoint -> ok, returns empty overlap.
    assert rs.assert_disjoint({"0xa"}, {"0xb"}) == set()
    # Case-insensitive overlap -> raise naming the wallet.
    with pytest.raises(rs.RosterInvariantError) as ei:
        rs.assert_disjoint(["0xAAA"], ["0xaaa"])
    assert "0xaaa" in str(ei.value)


def test_check_rosters_disjoint_from_db(db_url: str):
    # Clean split -> passes and reports the two wallet sets.
    db.set_agent_state(LIVE_ACTOR, LIVE_KEY, [{"wallet": "0xlive"}], db_url=db_url)
    db.set_agent_state(PAPER_ACTOR, PAPER_KEY, [{"wallet": "0xpaper"}], db_url=db_url)
    live, paper = rs.check_rosters_disjoint(db_url=db_url)
    assert live == {"0xlive"} and paper == {"0xpaper"}

    # Corrupt into a split state (same wallet both sides) -> the assert fires.
    db.set_agent_state(PAPER_ACTOR, PAPER_KEY, [{"wallet": "0xLIVE"}], db_url=db_url)
    with pytest.raises(rs.RosterInvariantError):
        rs.check_rosters_disjoint(db_url=db_url)
