"""CP4 — atomic paper<->live promote/demote + the three MUST-TESTs.

Covers:
  * promote (paper->live): atomic 3-key move + flatten-on-promote (reuse).
  * demote (live->paper): atomic 3-key move, NO live-broker action.
  * (a) PIN-BACK ROUND-TRIP: promote->demote->re-promote, invariant every step.
  * (b) WEEKLY-REFRESH-DOESN'T-RE-ADD: run the REAL refresh_polymarket_selection
        (pins-only) offline; a promoted live whale is NOT re-added to paper.
  * (c) DEMOTE-OPEN-LIVE: an off-roster open live position still MARKS, still
        SETTLES, and still BOOKS to the live division.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from trading_corp.persistence import db as _db
from trading_corp.persistence.db import set_agent_state, load_agent_state
from trading_corp.agents.strategies import roster_split as rs
from trading_corp.agents.strategies.roster_split import (
    promote_whale_to_live, demote_whale_to_paper, extract_wallets,
    LIVE_ACTOR, LIVE_KEY, PAPER_ACTOR, PAPER_KEY, PIN_KEY,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture()
def db_url(tmp_path):
    url = f"sqlite:///{(tmp_path / 'cp4.db').as_posix()}"
    _db.init_db(db_url=url)
    return url


def _wallets(db_url, actor, key):
    rec = load_agent_state(actor, key, db_url=db_url)
    return extract_wallets(rec[0]) if rec else set()


def _list(db_url, actor, key):
    rec = load_agent_state(actor, key, db_url=db_url)
    return rec[0] if rec else []


# ── Basic promote / demote ──────────────────────────────────────────────


def test_promote_moves_across_three_keys(db_url):
    W = "0xabc"
    set_agent_state(PAPER_ACTOR, PAPER_KEY, [{"wallet": W, "user_name": "sd"}], db_url=db_url)
    set_agent_state(PAPER_ACTOR, PIN_KEY, [{"wallet": W, "user_name": "sd"}], db_url=db_url)

    promote_whale_to_live(W, db_url=db_url)

    assert _wallets(db_url, LIVE_ACTOR, LIVE_KEY) == {W}       # +live
    assert _wallets(db_url, PAPER_ACTOR, PAPER_KEY) == set()   # -selected
    assert _wallets(db_url, PAPER_ACTOR, PIN_KEY) == set()     # -pinned (§1.5)
    rs.check_rosters_disjoint(db_url=db_url)                   # invariant


def test_demote_moves_back_and_repins_no_broker(db_url):
    W = "0xabc"
    set_agent_state(LIVE_ACTOR, LIVE_KEY, [{"wallet": W, "user_name": "sd"}], db_url=db_url)

    demote_whale_to_paper(W, db_url=db_url)

    assert _wallets(db_url, LIVE_ACTOR, LIVE_KEY) == set()     # -live
    assert _wallets(db_url, PAPER_ACTOR, PAPER_KEY) == {W}     # +selected
    assert _wallets(db_url, PAPER_ACTOR, PIN_KEY) == {W}       # +pinned (eviction-safe)
    rs.check_rosters_disjoint(db_url=db_url)


def test_promote_flattens_paper_book(db_url):
    """Flatten-on-promote: the whale's open paper position is closed via the
    reused force_close path (its whale_state open positions are emptied)."""
    W = "0xflat"
    set_agent_state(PAPER_ACTOR, PAPER_KEY, [{"wallet": W, "user_name": "f"}], db_url=db_url)
    # Seed one open paper position in the whale's state slot.
    set_agent_state(
        "polymarket_copy_trader", f"whale_state:{W}",
        {"last_seen_ts": 1, "our_positions": {
            "cid1:0": {"condition_id": "cid1", "outcome_index": 0, "outcome": "Yes",
                       "copy_size_usdc": 2.0, "entry_price": 0.5, "entry_ts": 1,
                       "actual_fill_qty": 4.0}}},
        db_url=db_url,
    )
    summary = promote_whale_to_live(W, db_url=db_url)
    assert summary["n_paper_closed"] == 1                       # one paper lot flattened
    # whale_state reset to fresh baseline (no open positions carried to live).
    st = _list(db_url, "polymarket_copy_trader", f"whale_state:{W}")
    assert st.get("our_positions") == {}


# ── (a) PIN-BACK ROUND-TRIP ─────────────────────────────────────────────


def test_pin_back_round_trip_invariant_every_step(db_url):
    """promote -> demote -> re-promote. Invariant live ∩ paper == ∅ holds at
    EVERY step; the whale lands in exactly ONE roster each time; no orphaned
    duplicate entries accumulate across cycles."""
    W = "0xround"
    set_agent_state(PAPER_ACTOR, PAPER_KEY, [{"wallet": W, "user_name": "rt"}], db_url=db_url)
    set_agent_state(PAPER_ACTOR, PIN_KEY, [{"wallet": W, "user_name": "rt"}], db_url=db_url)

    # 1) promote -> live only
    promote_whale_to_live(W, db_url=db_url)
    assert _wallets(db_url, LIVE_ACTOR, LIVE_KEY) == {W}
    assert _wallets(db_url, PAPER_ACTOR, PAPER_KEY) == set()
    assert _wallets(db_url, PAPER_ACTOR, PIN_KEY) == set()
    rs.check_rosters_disjoint(db_url=db_url)

    # 2) demote -> paper only (re-pinned)
    demote_whale_to_paper(W, db_url=db_url)
    assert _wallets(db_url, LIVE_ACTOR, LIVE_KEY) == set()
    assert _wallets(db_url, PAPER_ACTOR, PAPER_KEY) == {W}
    assert _wallets(db_url, PAPER_ACTOR, PIN_KEY) == {W}
    rs.check_rosters_disjoint(db_url=db_url)

    # 3) re-promote -> live only
    promote_whale_to_live(W, db_url=db_url)
    assert _wallets(db_url, LIVE_ACTOR, LIVE_KEY) == {W}
    assert _wallets(db_url, PAPER_ACTOR, PAPER_KEY) == set()
    rs.check_rosters_disjoint(db_url=db_url)

    # No orphan accumulation: exactly ONE entry live, ZERO in paper.
    assert len(_list(db_url, LIVE_ACTOR, LIVE_KEY)) == 1
    assert len(_list(db_url, PAPER_ACTOR, PAPER_KEY)) == 0


# ── (b) WEEKLY-REFRESH-DOESN'T-RE-ADD (the §1.5 test) ───────────────────


class _EmptyLeaderboardClient:
    """Async-context client whose leaderboards are empty -> the refresh scores
    zero whales, so the pins-only write is exactly `selected_whales := pinned`."""
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def fetch_leaderboard(self, *a, **k):
        return []
    async def fetch_market_resolutions(self, ids, *a, **k):
        return {}
    async def fetch_activity(self, *a, **k):
        return []


def test_weekly_refresh_does_not_readd_promoted_whale(db_url, monkeypatch):
    """DIFFERENT trigger than the manual round-trip: run the REAL weekly
    pins->selected refresh (offline). A still-pinned PAPER whale is re-written to
    selected_whales (proves the refresh works), but the PROMOTED LIVE whale —
    removed from pinned by the 3-key move — is NOT re-added. This proves the
    3-key move defeats the §1.5 silent-re-add path."""
    from trading_corp.scripts import refresh_polymarket_whales as rpw

    LIVE_W, PAPER_W = "0xlive", "0xpaper"
    # Both start papered + pinned.
    set_agent_state(PAPER_ACTOR, PAPER_KEY,
                    [{"wallet": LIVE_W, "user_name": "lg"}, {"wallet": PAPER_W, "user_name": "pg"}],
                    db_url=db_url)
    set_agent_state(PAPER_ACTOR, PIN_KEY,
                    [{"wallet": LIVE_W, "user_name": "lg"}, {"wallet": PAPER_W, "user_name": "pg"}],
                    db_url=db_url)

    # Promote LIVE_W -> live (removes it from selected AND pinned).
    promote_whale_to_live(LIVE_W, db_url=db_url)
    assert _wallets(db_url, PAPER_ACTOR, PIN_KEY) == {PAPER_W}   # LIVE_W no longer pinned

    # Run the REAL refresh (pins-only default) offline.
    monkeypatch.setattr(rpw, "PolymarketDataAPIClient", _EmptyLeaderboardClient)
    _run(rpw.refresh_polymarket_selection(db_url=db_url, dry_run=False, algo_select=False))

    selected_after = _wallets(db_url, PAPER_ACTOR, PAPER_KEY)
    assert PAPER_W in selected_after            # still-pinned paper whale IS re-written (refresh works)
    assert LIVE_W not in selected_after         # promoted live whale is NOT re-added (§1.5 defeated)
    assert _wallets(db_url, LIVE_ACTOR, LIVE_KEY) == {LIVE_W}    # still live
    rs.check_rosters_disjoint(db_url=db_url)


# ── (c) DEMOTE-OPEN-LIVE: still marks + settles + books ─────────────────


def _seed_open_live_position(db_url, *, order_id, ticker, fill_price, fill_count):
    """Insert a placed-entry poly_kalshi_order audit row (an OPEN live position),
    exactly as poly_kalshi_marks._fetch_open_positions expects."""
    payload = {"status": "placed", "division": "poly_kalshi_mlb", "action": "entry",
               "outcome": "yes", "ticker": ticker, "order_id": order_id,
               "fill_price": fill_price, "fill_count": fill_count, "whale_wallet": "0xlive"}
    with _db.connect(db_url) as c:
        c.execute("INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?,?,?,?)",
                  ("2026-08-16T18:00:00+00:00", "poly_kalshi_mlb", "poly_kalshi_order",
                   json.dumps(payload)))


class _QuoteBroker:
    def __init__(self, yes_mid):
        self.yes_mid = yes_mid
    async def quote(self, ticker):
        return self.yes_mid


def test_demote_open_live_still_marks_and_books(db_url):
    """A whale with an OPEN live position is demoted off the live roster; the
    position must STILL be marked by the poller AND still book to the live
    division on settlement — because both paths are position/settlement-driven,
    not roster-driven. Ride-to-settlement proven end to end."""
    from trading_corp.agents import poly_kalshi_marks as pkm
    from trading_corp.agents.strategies.poly_kalshi_executor import PolyKalshiExecutor
    from trading_corp.agents.strategies.poly_kalshi_copy_trader import PolyKalshiCopyTrader, _utc_day

    W, OID, TICKER = "0xlive", "oid-W", "KXMLBGAME-26AUG171805BALTB-TB"
    set_agent_state(LIVE_ACTOR, LIVE_KEY, [{"wallet": W, "user_name": "lg"}], db_url=db_url)
    _seed_open_live_position(db_url, order_id=OID, ticker=TICKER, fill_price=0.5, fill_count=10)

    # DEMOTE the whale off the live roster.
    demote_whale_to_paper(W, db_url=db_url)
    assert _wallets(db_url, LIVE_ACTOR, LIVE_KEY) == set()      # off the live roster

    # 1) MARKING still happens — the open position is still selected + marked.
    open_positions = pkm._fetch_open_positions(db_url)
    assert [p["order_id"] for p in open_positions] == [OID]     # position survives demote
    counts = _run(pkm.run_mark_cycle(db_url, _QuoteBroker(yes_mid=0.6)))
    assert counts["marked"] == 1
    with _db.connect(db_url) as c:
        row = c.execute(
            "SELECT unrealized FROM poly_kalshi_mark_live WHERE order_id=?", (OID,),
        ).fetchone()
    assert row is not None and row["unrealized"] == pytest.approx((0.6 - 0.5) * 10)  # +1.0 marked

    # 2) SETTLEMENT still BOOKS to the LIVE division (settlement-driven sweep).
    ex = PolyKalshiExecutor(dry_run=True, db_url=db_url, strategy="poly_kalshi_mlb")
    lp = PolyKalshiCopyTrader(executor=ex, db_url=db_url, stake_usd=5.0,
                              daily_loss_cap_usd=100.0, now_fn=lambda: 1000.0)

    async def _settled():
        return [(TICKER, f"{_utc_day()}T20:00:00+00:00", -120.0)]   # a settled LOSS, today

    halted = _run(lp.run_settlement_sweep(_settled))
    # -120 realized <= -100 cap -> booked to the LIVE division's halt path,
    # even though the whale is no longer on the live roster.
    assert halted is True
