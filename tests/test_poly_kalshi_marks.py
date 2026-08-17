"""Phase 2b CP2 tests — Poly->Kalshi live mark poller (no network; fake quote broker).
Covers schema, open-position gate, unrealized math, volatile live+history writes,
bounded history, prune-on-resolve, quote-miss-leaves-prior-row, and the invariant that
marks NEVER touch the audit journal."""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from trading_corp.persistence import db as _db
from trading_corp.agents import poly_kalshi_marks as pkm


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def hdb(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    _db.init_db(url)
    return url


def _open_row(db_url, *, order_id, ticker, fill_price, fill_count, status="placed", action="entry"):
    payload = {"status": status, "division": "poly_kalshi_mlb", "action": action, "outcome": "yes",
               "ticker": ticker, "order_id": order_id, "fill_price": fill_price, "fill_count": fill_count}
    with _db.connect(db_url) as c:
        c.execute("INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?,?,?,?)",
                  ("2026-08-16T18:00:00+00:00", "poly_kalshi_mlb", "poly_kalshi_order", json.dumps(payload)))


def _resolve(db_url, order_id, division="poly_kalshi_mlb"):
    with _db.connect(db_url) as c:
        c.execute(
            "INSERT INTO kalshi_round_trips (order_id, ticker, strategy, division, outcome_bet, qty, "
            "entry_price, notional, entry_ts, resolved_ts, market_result, won, realized_pnl, roi_pct) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, "KX-" + order_id, division, division, "yes", 9.0, 0.5, 4.5,
             "2026-08-16T18:00:00+00:00", "2026-08-16T20:00:00+00:00", "yes", 1, 4.5, 100.0))


class _FakeBroker:
    """quotes: ticker -> float | Exception. Missing ticker -> 0.0 (KalshiBroker miss)."""
    def __init__(self, quotes):
        self.quotes = quotes
        self.calls = []

    async def quote(self, ticker):
        self.calls.append(ticker)
        q = self.quotes.get(ticker, 0.0)
        if isinstance(q, Exception):
            raise q
        return q


def test_schema_creates_mark_tables(hdb):
    with sqlite3.connect(_db.resolve_db_path(hdb)) as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "poly_kalshi_mark_live" in names and "poly_kalshi_mark_history" in names


def test_fetch_open_positions_gate(hdb):
    _open_row(hdb, order_id="live", ticker="KX-L", fill_price=0.5, fill_count=10)          # open
    _open_row(hdb, order_id="", ticker="KX-P", fill_price=0.5, fill_count=9)               # pre-CP3: no order_id
    _open_row(hdb, order_id="res", ticker="KX-R", fill_price=0.5, fill_count=10)
    _resolve(hdb, "res")                                                                   # resolved -> excluded
    pos = pkm._fetch_open_positions(hdb)
    assert [p["order_id"] for p in pos] == ["live"]
    assert pos[0]["fill_price"] == 0.5 and pos[0]["fill_count"] == 10.0


def test_run_mark_cycle_writes_live_and_history(hdb):
    _open_row(hdb, order_id="o1", ticker="KX-1", fill_price=0.54, fill_count=10)
    counts = _run(pkm.run_mark_cycle(hdb, _FakeBroker({"KX-1": 0.60})))
    assert counts == {"open": 1, "marked": 1, "quote_miss": 0}
    with _db.connect(hdb) as c:
        live = c.execute("SELECT yes_mid, unrealized, unrealized_pct FROM poly_kalshi_mark_live "
                         "WHERE order_id='o1'").fetchone()
        n_hist = c.execute("SELECT COUNT(*) FROM poly_kalshi_mark_history WHERE order_id='o1'").fetchone()[0]
    assert live[0] == pytest.approx(0.60)
    assert live[1] == pytest.approx((0.60 - 0.54) * 10)          # unrealized +0.60
    assert live[2] == pytest.approx(100.0 * (0.60 - 0.54) / 0.54)
    assert n_hist == 1


def test_history_is_bounded(hdb):
    _open_row(hdb, order_id="o1", ticker="KX-1", fill_price=0.5, fill_count=10)
    b = _FakeBroker({"KX-1": 0.55})

    async def _many():
        for _ in range(pkm._HISTORY_CAP + 5):
            await pkm.run_mark_cycle(hdb, b)
    _run(_many())
    with _db.connect(hdb) as c:
        n = c.execute("SELECT COUNT(*) FROM poly_kalshi_mark_history WHERE order_id='o1'").fetchone()[0]
    assert n == pkm._HISTORY_CAP


def test_resolved_position_pruned_from_marks(hdb):
    _open_row(hdb, order_id="o1", ticker="KX-1", fill_price=0.5, fill_count=10)
    b = _FakeBroker({"KX-1": 0.6})
    _run(pkm.run_mark_cycle(hdb, b))
    with _db.connect(hdb) as c:
        assert c.execute("SELECT COUNT(*) FROM poly_kalshi_mark_live WHERE order_id='o1'").fetchone()[0] == 1
    _resolve(hdb, "o1")                       # now resolved -> not in the open set
    _run(pkm.run_mark_cycle(hdb, b))
    with _db.connect(hdb) as c:
        assert c.execute("SELECT COUNT(*) FROM poly_kalshi_mark_live WHERE order_id='o1'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM poly_kalshi_mark_history WHERE order_id='o1'").fetchone()[0] == 0


def test_quote_miss_leaves_prior_row(hdb):
    _open_row(hdb, order_id="o1", ticker="KX-1", fill_price=0.5, fill_count=10)
    _run(pkm.run_mark_cycle(hdb, _FakeBroker({"KX-1": 0.6})))        # good mark 0.6
    c2 = _run(pkm.run_mark_cycle(hdb, _FakeBroker({"KX-1": 0.0})))    # quote miss (0.0)
    assert c2["quote_miss"] == 1 and c2["marked"] == 0
    with _db.connect(hdb) as c:
        yes_mid = c.execute("SELECT yes_mid FROM poly_kalshi_mark_live WHERE order_id='o1'").fetchone()[0]
    assert yes_mid == pytest.approx(0.6)      # prior mark preserved, not nulled


def test_quote_exception_is_survived(hdb):
    _open_row(hdb, order_id="o1", ticker="KX-1", fill_price=0.5, fill_count=10)
    c = _run(pkm.run_mark_cycle(hdb, _FakeBroker({"KX-1": RuntimeError("boom")})))
    assert c["quote_miss"] == 1 and c["marked"] == 0                 # error -> miss, loop survives


def test_never_writes_audit_event(hdb):
    _open_row(hdb, order_id="o1", ticker="KX-1", fill_price=0.5, fill_count=10)
    with _db.connect(hdb) as c:
        before = c.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0]
    _run(pkm.run_mark_cycle(hdb, _FakeBroker({"KX-1": 0.6})))
    with _db.connect(hdb) as c:
        after = c.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0]
    assert after == before                    # marks are ephemeral -> NEVER in the journal


def test_tick_log_survives_redacting_filter():
    """Regression: the per-cycle tick log must render through the shared RedactingFilter
    without raising. The prior `log.info("...%s", counts)` (lone dict arg) tripped the filter
    (dict -> keys-tuple -> getMessage TypeError) every cycle; the scalar-arg `_log_tick` fixes
    it. A handler whose handleError re-raises turns any emit-time format failure into a test
    failure (the old code fails this; the fix passes)."""
    import io
    import logging
    from trading_corp.utils.secrets import RedactingFilter

    class _StrictHandler(logging.StreamHandler):
        def handleError(self, record):          # surface emit failures instead of swallowing
            raise

    buf = io.StringIO()
    h = _StrictHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    h.addFilter(RedactingFilter())
    pkm.log.addHandler(h)
    prev_propagate, prev_level = pkm.log.propagate, pkm.log.level
    pkm.log.propagate = False
    pkm.log.setLevel(logging.INFO)
    try:
        pkm._log_tick({"open": 1, "marked": 0, "quote_miss": 2})   # would raise pre-fix
    finally:
        pkm.log.removeHandler(h)
        pkm.log.propagate, pkm.log.level = prev_propagate, prev_level
    out = buf.getvalue()
    assert "open=1" in out and "marked=0" in out and "quote_miss=2" in out   # counts rendered
