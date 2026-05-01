"""Tests for PMCC agent's position_context builder (Phase 2 Telegram enrichment).

Origin: 2026-04-30 — `comms/approval_format.py` already renders a rich
"📊 Position context" block when `order.extra["position_context"]` is
populated, but no producer was wiring it. PMCC's roll and sell-weekly
proposals are the natural producers (they act on existing pairs). This
file pins:

  1. `_build_position_context(leg)` returns the right shape with all
     fields when LEAP mark + cost are present, and gracefully omits
     fields when data is missing.
  2. `_query_prior_rolls(symbol)` correctly groups fills by pmcc_pair_id
     and computes net dollars (sells positive, buys negative).
  3. End-to-end: `_propose_roll_short` attaches the same context to BOTH
     legs of the roll so approving either shows the same picture.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from trading_corp.agents.divisions.pmcc_robinhood import (
    PMCCAgent, PMCCPosition,
)
from trading_corp.persistence.db import init_db, resolve_db_path


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def pmcc_yaml(tmp_path: Path) -> Path:
    """Minimal strategies.yaml with the PMCC block."""
    p = tmp_path / "strategies.yaml"
    p.write_text(
        """
robinhood_pmcc:
  enabled: true
  auto_execute: false
  universe_source: positions
  watchlist: []
  position_exclude: []
  position_min_shares: 1
  scan_schedule: daily_pre_open
  strategy:
    allocation: {}
    sizing: {}
""".strip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def risk_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "risk.yaml"
    p.write_text(
        """
global:
  per_trade_risk_pct: 0.015
pmcc: {}
""".strip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def initialized_db(tmp_db: str) -> str:
    init_db(tmp_db)
    return tmp_db


def _make_agent(pmcc_yaml: Path, risk_yaml: Path, db_url: str | None) -> PMCCAgent:
    return PMCCAgent(
        strategies_yaml=pmcc_yaml,
        risk_yaml=risk_yaml,
        db_url=db_url,
    )


def _full_leg() -> PMCCPosition:
    """A PMCCPosition with both LEAP and short leg populated, with mark."""
    return PMCCPosition(
        symbol="RKLB",
        long_leg_expiry="2027-01-15",
        long_leg_strike=25.0,
        long_leg_delta=0.85,
        long_leg_dte=400,
        long_leg_qty=1.0,
        long_leg_avg_price=2380.0,    # per-CONTRACT (Robinhood convention)
        long_leg_symbol="RKLB 2027-01-15 C 25.00",
        long_leg_mark=58.05,           # per-share
        short_leg_expiry="2026-05-08",
        short_leg_strike=82.0,
        short_leg_dte=8,
        short_leg_pnl_pct=0.83,
        short_leg_qty=-1.0,
        short_leg_mark=1.74,
        short_leg_avg_price=10.51,
        short_leg_symbol="RKLB 2026-05-08 C 82.00",
    )


def _leg_no_mark() -> PMCCPosition:
    """Same shape but `long_leg_mark` is None (chain query missed it)."""
    leg = _full_leg()
    leg.long_leg_mark = None
    return leg


# ── _build_position_context ──────────────────────────────────────────────


def test_position_context_full_shape(pmcc_yaml, risk_yaml, initialized_db):
    """All fields present + DB empty → leap basics, mark, P&L populated;
    no roll history."""
    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    ctx = agent._build_position_context(_full_leg())

    leap = ctx["leap"]
    assert leap["underlying"] == "RKLB"
    assert leap["strike"] == 25.0
    assert leap["expiration"] == "2027-01-15"
    assert leap["dte"] == 400
    assert leap["cost_basis"] == pytest.approx(23.80)  # 2380 / 100
    assert leap["mark"] == pytest.approx(58.05)

    # Unrealized P&L: (58.05 - 23.80) * 100 * 1 = 3425
    assert ctx["unrealized_pnl_dollars"] == pytest.approx(3425.0)
    # Pct: 58.05 / 23.80 - 1 = 1.4391...
    assert ctx["unrealized_pnl_pct"] == pytest.approx(58.05 / 23.80 - 1)

    # No prior rolls → no roll_count / prior_credit_total fields
    assert "roll_count" not in ctx
    assert "prior_credit_total" not in ctx


def test_position_context_omits_mark_when_missing(pmcc_yaml, risk_yaml, initialized_db):
    """If LEAP mark wasn't captured, mark + P&L fields are omitted (formatter
    handles their absence)."""
    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    ctx = agent._build_position_context(_leg_no_mark())

    leap = ctx["leap"]
    assert leap["underlying"] == "RKLB"
    assert leap["cost_basis"] == pytest.approx(23.80)
    assert "mark" not in leap
    assert "unrealized_pnl_dollars" not in ctx
    assert "unrealized_pnl_pct" not in ctx


def test_position_context_no_db_skips_roll_query(pmcc_yaml, risk_yaml):
    """db_url=None → no DB query attempted, no roll_count fields."""
    agent = _make_agent(pmcc_yaml, risk_yaml, db_url=None)
    ctx = agent._build_position_context(_full_leg())

    assert "roll_count" not in ctx
    assert "prior_credit_total" not in ctx
    # Other fields still populated
    assert ctx["leap"]["strike"] == 25.0


# ── _query_prior_rolls ────────────────────────────────────────────────────


def _insert_roll_pair(
    db_url: str,
    pair_id: str,
    symbol: str,
    close_price: float,
    open_price: float,
    contracts: int = 1,
    fill_ts: str = "2026-04-15T15:00:00+00:00",
    leap_lifetime_key: str | None = None,
) -> None:
    """Insert a synthetic close+open roll pair into proposed_order.

    `leap_lifetime_key`: when set, both legs carry the key in their
    extra_json (mirroring what the producer writes after the fix).
    Leave None to simulate pre-fix DB rows.
    """
    path = resolve_db_path(db_url)
    extra_close = {
        "is_option": True, "underlying": symbol,
        "action": "roll_short_call_close",
        "pmcc_pair_id": pair_id,
    }
    extra_open = {
        "is_option": True, "underlying": symbol,
        "action": "roll_short_call_open",
        "pmcc_pair_id": pair_id,
    }
    if leap_lifetime_key:
        extra_close["leap_lifetime_key"] = leap_lifetime_key
        extra_open["leap_lifetime_key"] = leap_lifetime_key
    with sqlite3.connect(path) as conn:
        # close leg (buy-to-close — debit)
        conn.execute(
            """
            INSERT INTO proposed_order
            (id, ts, strategy, symbol, side, qty, order_type, limit_price,
             rationale, status, fill_price, fill_ts, extra_json)
            VALUES (?, ?, 'robinhood_pmcc', ?, 'buy', ?, 'limit', NULL,
                    'roll close', 'filled', ?, ?, ?)
            """,
            (
                f"close-{pair_id}", fill_ts, symbol, contracts, close_price, fill_ts,
                json.dumps(extra_close),
            ),
        )
        # open leg (sell-to-open — credit)
        conn.execute(
            """
            INSERT INTO proposed_order
            (id, ts, strategy, symbol, side, qty, order_type, limit_price,
             rationale, status, fill_price, fill_ts, extra_json)
            VALUES (?, ?, 'robinhood_pmcc', ?, 'sell', ?, 'limit', NULL,
                    'roll open', 'filled', ?, ?, ?)
            """,
            (
                f"open-{pair_id}", fill_ts, symbol, contracts, open_price, fill_ts,
                json.dumps(extra_open),
            ),
        )
        conn.commit()


def test_query_prior_rolls_single_pair(pmcc_yaml, risk_yaml, initialized_db):
    """One past roll: close $1 (debit $100) + open $2 (credit $200) → net +$100."""
    _insert_roll_pair(initialized_db, "abc12345", "RKLB", close_price=1.0, open_price=2.0)
    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    count, net = agent._query_prior_rolls("RKLB")

    assert count == 1
    assert net == pytest.approx(100.0)


def test_query_prior_rolls_multiple_pairs(pmcc_yaml, risk_yaml, initialized_db):
    """Three past rolls on RKLB, varying credits/debits."""
    _insert_roll_pair(initialized_db, "p1", "RKLB", close_price=1.50, open_price=3.00)
    # nets: -150 + 300 = +150
    _insert_roll_pair(initialized_db, "p2", "RKLB", close_price=2.00, open_price=2.50)
    # nets: -200 + 250 = +50
    _insert_roll_pair(initialized_db, "p3", "RKLB", close_price=1.00, open_price=1.85)
    # nets: -100 + 185 = +85

    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    count, net = agent._query_prior_rolls("RKLB")

    assert count == 3
    assert net == pytest.approx(150.0 + 50.0 + 85.0)


def test_query_prior_rolls_other_symbol_isolated(pmcc_yaml, risk_yaml, initialized_db):
    """Rolls on a different underlying must not bleed into the query."""
    _insert_roll_pair(initialized_db, "p_aapl", "AAPL", close_price=1.0, open_price=2.0)
    _insert_roll_pair(initialized_db, "p_rklb", "RKLB", close_price=2.0, open_price=4.0)

    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    aapl_count, aapl_net = agent._query_prior_rolls("AAPL")
    rklb_count, rklb_net = agent._query_prior_rolls("RKLB")

    assert aapl_count == 1
    assert aapl_net == pytest.approx(100.0)
    assert rklb_count == 1
    assert rklb_net == pytest.approx(200.0)  # (4-2)*100 = 200


def test_query_prior_rolls_ignores_unfilled(pmcc_yaml, risk_yaml, initialized_db):
    """Orders with status='proposed' or fill_price IS NULL must be excluded."""
    path = resolve_db_path(initialized_db)
    with sqlite3.connect(path) as conn:
        # An UNFILLED proposal — should be ignored
        conn.execute(
            """
            INSERT INTO proposed_order
            (id, ts, strategy, symbol, side, qty, order_type, limit_price,
             rationale, status, extra_json)
            VALUES ('unfilled', '2026-04-15T15:00:00+00:00', 'robinhood_pmcc',
                    'RKLB', 'buy', 1, 'limit', NULL, 'roll close',
                    'risk_rejected', '{"action": "roll_short_call_close",
                                       "pmcc_pair_id": "u1"}')
            """,
        )
        conn.commit()

    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    count, net = agent._query_prior_rolls("RKLB")
    assert count == 0
    assert net == 0.0


# ── Integration: position_context → audit query plumbing ──────────────────


def test_position_context_includes_roll_history(pmcc_yaml, risk_yaml, initialized_db):
    """When prior rolls exist + db_url is set, context includes
    roll_count and prior_credit_total."""
    _insert_roll_pair(initialized_db, "p1", "RKLB", close_price=1.50, open_price=3.00)
    _insert_roll_pair(initialized_db, "p2", "RKLB", close_price=2.00, open_price=2.50)

    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    ctx = agent._build_position_context(_full_leg())

    assert ctx["roll_count"] == 2
    # +150 + +50 = +200
    assert ctx["prior_credit_total"] == pytest.approx(200.0)


# ── Integration: _make_option_order stashes context on extra ─────────────


def test_make_option_order_stashes_position_context(pmcc_yaml, risk_yaml, initialized_db):
    """A position_context kwarg should land on the order's extra dict."""
    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    ctx = {"leap": {"underlying": "RKLB", "strike": 25.0}, "roll_count": 4}
    order = agent._make_option_order(
        underlying="RKLB", side="buy", contracts=1,
        expiry="2026-05-08", strike=82.0, mark_price=1.74,
        position_effect="close", action="roll_short_call_close",
        rationale="test", pair_id="abc",
        position_context=ctx,
    )
    assert order.extra is not None
    assert order.extra.get("position_context") == ctx


def test_make_option_order_omits_context_when_none(pmcc_yaml, risk_yaml, initialized_db):
    """No position_context kwarg → no key on extra (defensive: formatter
    only renders the block when the key exists)."""
    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    order = agent._make_option_order(
        underlying="RKLB", side="buy", contracts=1,
        expiry="2026-05-08", strike=82.0, mark_price=1.74,
        position_effect="close", action="roll_short_call_close",
        rationale="test", pair_id="abc",
    )
    assert order.extra is not None
    assert "position_context" not in order.extra


# ── Cost basis edge: PMCCPosition stores per-contract; we render per-share ──


def test_cost_basis_converted_per_contract_to_per_share(pmcc_yaml, risk_yaml, initialized_db):
    """Robinhood stores avg_price as per-contract (e.g. $2380 = $23.80/sh).
    The formatter expects per-share. We divide by 100 in _build_position_context."""
    leg = _full_leg()
    leg.long_leg_avg_price = 1500.0  # $15/sh

    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    ctx = agent._build_position_context(leg)

    assert ctx["leap"]["cost_basis"] == pytest.approx(15.0)


def test_unrealized_pnl_uses_qty(pmcc_yaml, risk_yaml, initialized_db):
    """P&L should scale with `long_leg_qty`. 2 contracts = 2× the dollar P&L."""
    leg = _full_leg()
    leg.long_leg_qty = 2.0   # was 1.0

    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    ctx = agent._build_position_context(leg)

    # (58.05 - 23.80) * 100 * 2 = 6850
    assert ctx["unrealized_pnl_dollars"] == pytest.approx(6850.0)


# ── leap_lifetime_key — scoping rolls to one LEAP ────────────────────────
#
# Background: each ROLL gets a fresh `pmcc_pair_id`, so that field can't
# identify "all rolls on the same LEAP." `leap_lifetime_key` is stable
# across rolls on the same `(symbol, strike, expiry)`. These tests pin
# that the producer writes the key, the query filters on it, and pre-fix
# rows (no key) still aggregate so we don't lose history.


def test_compute_leap_lifetime_key_format():
    """Format pin: 2-decimal strike, exact symbol + expiry."""
    leg = _full_leg()
    key = PMCCAgent._compute_leap_lifetime_key(leg)
    assert key == "RKLB:25.00:2027-01-15"


def test_compute_leap_lifetime_key_none_inputs():
    """Missing leg or fields → None (caller falls back to symbol-only)."""
    assert PMCCAgent._compute_leap_lifetime_key(None) is None
    leg = _full_leg()
    leg.long_leg_expiry = ""
    assert PMCCAgent._compute_leap_lifetime_key(leg) is None


def test_query_prior_rolls_scoped_to_one_leap(pmcc_yaml, risk_yaml, initialized_db):
    """Two LEAPs on RKLB, each with its own roll history. Query scoped to
    one leap_lifetime_key returns only that LEAP's rolls."""
    key_a = "RKLB:25.00:2027-01-15"   # LEAP A
    key_b = "RKLB:50.00:2027-06-18"   # LEAP B

    # LEAP A: 2 rolls, nets +150 + +50 = +200
    _insert_roll_pair(initialized_db, "a1", "RKLB", 1.50, 3.00, leap_lifetime_key=key_a)
    _insert_roll_pair(initialized_db, "a2", "RKLB", 2.00, 2.50, leap_lifetime_key=key_a)
    # LEAP B: 1 roll, net +85
    _insert_roll_pair(initialized_db, "b1", "RKLB", 1.00, 1.85, leap_lifetime_key=key_b)

    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)

    a_count, a_net = agent._query_prior_rolls("RKLB", leap_lifetime_key=key_a)
    assert a_count == 2
    assert a_net == pytest.approx(200.0)

    b_count, b_net = agent._query_prior_rolls("RKLB", leap_lifetime_key=key_b)
    assert b_count == 1
    assert b_net == pytest.approx(85.0)


def test_query_prior_rolls_no_key_aggregates_all(pmcc_yaml, risk_yaml, initialized_db):
    """No leap_lifetime_key arg → legacy behavior (aggregate by symbol).
    Backward-compat: callers that don't know the LEAP still work."""
    key_a = "RKLB:25.00:2027-01-15"
    key_b = "RKLB:50.00:2027-06-18"
    _insert_roll_pair(initialized_db, "a1", "RKLB", 1.50, 3.00, leap_lifetime_key=key_a)
    _insert_roll_pair(initialized_db, "b1", "RKLB", 1.00, 1.85, leap_lifetime_key=key_b)

    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    count, net = agent._query_prior_rolls("RKLB")  # no key
    assert count == 2
    assert net == pytest.approx(150.0 + 85.0)


def test_query_prior_rolls_pre_fix_rows_preserved(pmcc_yaml, risk_yaml, initialized_db):
    """Pre-fix rows (no leap_lifetime_key) must still count when scoped to
    a key — losing them would silently drop real history. The new
    behavior is "rows with no key fall through; rows with a different
    key are filtered out."""
    key_a = "RKLB:25.00:2027-01-15"

    # Pre-fix: no key
    _insert_roll_pair(initialized_db, "old1", "RKLB", 1.50, 3.00)  # +150
    # New, scoped to LEAP A
    _insert_roll_pair(initialized_db, "new1", "RKLB", 2.00, 2.50, leap_lifetime_key=key_a)  # +50

    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    count, net = agent._query_prior_rolls("RKLB", leap_lifetime_key=key_a)

    assert count == 2  # old1 + new1
    assert net == pytest.approx(150.0 + 50.0)


def test_query_prior_rolls_other_leap_key_excluded(pmcc_yaml, risk_yaml, initialized_db):
    """Pairs tagged with a DIFFERENT leap_lifetime_key are filtered out
    when querying for a specific key."""
    key_a = "RKLB:25.00:2027-01-15"
    key_b = "RKLB:50.00:2027-06-18"

    _insert_roll_pair(initialized_db, "a1", "RKLB", 1.50, 3.00, leap_lifetime_key=key_a)
    _insert_roll_pair(initialized_db, "b1", "RKLB", 5.00, 10.00, leap_lifetime_key=key_b)

    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    count, net = agent._query_prior_rolls("RKLB", leap_lifetime_key=key_a)

    assert count == 1
    assert net == pytest.approx(150.0)  # only LEAP A's roll


def test_position_context_scopes_to_legs_leap(pmcc_yaml, risk_yaml, initialized_db):
    """End-to-end: _build_position_context computes the LEAP key from the
    leg and scopes the prior-rolls query so multi-LEAP underliers report
    accurate per-LEAP history."""
    leg_a = _full_leg()  # RKLB:25.00:2027-01-15
    key_a = "RKLB:25.00:2027-01-15"
    key_b = "RKLB:50.00:2027-06-18"

    # 2 rolls on LEAP A, 5 rolls on LEAP B (different LEAP, same symbol)
    _insert_roll_pair(initialized_db, "a1", "RKLB", 1.50, 3.00, leap_lifetime_key=key_a)
    _insert_roll_pair(initialized_db, "a2", "RKLB", 2.00, 2.50, leap_lifetime_key=key_a)
    for i in range(5):
        _insert_roll_pair(
            initialized_db, f"b{i}", "RKLB", 1.0, 1.5, leap_lifetime_key=key_b,
        )

    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    ctx = agent._build_position_context(leg_a)

    # LEAP A's history only — the 5 LEAP-B rolls must NOT bleed in.
    assert ctx["roll_count"] == 2
    assert ctx["prior_credit_total"] == pytest.approx(200.0)


# ── Producer-side: orders carry the lifetime key ─────────────────────────


def test_make_option_order_stashes_lifetime_key(pmcc_yaml, risk_yaml, initialized_db):
    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    order = agent._make_option_order(
        underlying="RKLB", side="buy", contracts=1,
        expiry="2026-05-08", strike=82.0, mark_price=1.74,
        position_effect="close", action="roll_short_call_close",
        rationale="test", pair_id="abc",
        leap_lifetime_key="RKLB:25.00:2027-01-15",
    )
    assert order.extra is not None
    assert order.extra.get("leap_lifetime_key") == "RKLB:25.00:2027-01-15"


def test_make_option_order_omits_lifetime_key_when_none(pmcc_yaml, risk_yaml, initialized_db):
    """No leap_lifetime_key kwarg → key absent from extra (so legacy
    queries on those rows still aggregate as pre-fix history)."""
    agent = _make_agent(pmcc_yaml, risk_yaml, initialized_db)
    order = agent._make_option_order(
        underlying="RKLB", side="buy", contracts=1,
        expiry="2026-05-08", strike=82.0, mark_price=1.74,
        position_effect="close", action="roll_short_call_close",
        rationale="test", pair_id="abc",
    )
    assert order.extra is not None
    assert "leap_lifetime_key" not in order.extra
