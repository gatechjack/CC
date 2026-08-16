"""CP4 - Poly->Kalshi resolver adapter + the realized-P&L reconciliation HARD GATE.

THE GATE: the resolver's computed realized P&L for poly_kalshi_mlb (what the dashboard
shows) MUST equal the settlement-sweep's `_realized_pnl_day` (the number driving the $100
loss-halt). Both are settlement-based; a divergence is the failure this checkpoint exists
to catch (a dashboard P&L that differs from the halt P&L).

Network-free. The stub broker returns canned market resolutions; the sweep is fed the
GROSS Kalshi settlement P&L per position (`qty*(1-p)` won / `-qty*p` lost / 0 void) -- the
value `get_settlements().pnl_dollars` reports IF settlement P&L is gross-of-fee. These
tests prove the two CODE PATHS implement the identical settlement model and agree to the
cent; the residual real-world assumption (Kalshi reports gross, not net-of-fee) is the
operator's KAREN-settlement confirmation -- see the `has_teeth` test + the CP4 report.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from trading_corp.agents import kalshi_resolver as kr
from trading_corp.agents.strategies.poly_kalshi_executor import PolyKalshiExecutor
from trading_corp.agents.strategies.poly_kalshi_copy_trader import PolyKalshiCopyTrader
from trading_corp.persistence import db as _db


@pytest.fixture
def fresh_db(tmp_path):
    p = tmp_path / "pkrecon.db"
    url = f"sqlite:///{p}"
    _db.init_db(url)
    return url


def _audit(db_url, payload, *, ts="2026-08-16T18:00:00+00:00"):
    with _db.connect(db_url) as c:
        c.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?,?,?,?)",
            (ts, "poly_kalshi_mlb", "poly_kalshi_order", json.dumps(payload)),
        )


def _pk(**over):
    """A post-CP3 poly_kalshi_order payload (division + Flag-1 fill fields)."""
    p = {
        "status": "placed", "division": "poly_kalshi_mlb", "whale": "w", "whale_wallet": "0x",
        "action": "entry", "ticker": "KXMLBGAME-X-MIA", "side": "bid", "outcome": "yes",
        "count": 9, "price": "0.5600", "order_id": "oid",
        "fill_count": 9, "fill_price": 0.54, "fill_fee": 0.0,
    }
    p.update(over)
    return p


class _StubBroker:
    def __init__(self, resolutions):
        self.res = resolutions
        self.calls: list[str] = []

    async def get_market_resolution(self, ticker):
        self.calls.append(ticker)
        return self.res.get(ticker, {"status": "not_found", "result": None})


def _sum_resolver_realized(db_url, division="poly_kalshi_mlb"):
    with _db.connect(db_url) as c:
        cur = c.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0.0) FROM kalshi_round_trips WHERE division=?",
            (division,),
        )
        return float(cur.fetchone()[0])


# ── resolver booking: poly_kalshi_order -> kalshi_round_trips ────────────────
def test_resolver_books_poly_kalshi_from_real_fill_not_limit(fresh_db):
    db_url = fresh_db
    _audit(db_url, _pk(order_id="mia", ticker="KXMLBGAME-A-MIA",
                       count=9, fill_count=9, fill_price=0.54, price="0.5600"))
    broker = _StubBroker({"KXMLBGAME-A-MIA": {"status": "resolved", "result": "yes"}})
    counts = asyncio.run(kr.resolve_pending_round_trips(db_url, broker))
    assert counts["resolved"] == 1
    with _db.connect(db_url) as c:
        qty, price, won, realized, div, strat, at = c.execute(
            "SELECT qty, entry_price, won, realized_pnl, division, strategy, arb_type "
            "FROM kalshi_round_trips WHERE order_id='mia'"
        ).fetchone()
    assert qty == pytest.approx(9.0)         # fill_count, not requested count
    assert price == pytest.approx(0.54)      # REAL fill price, not the 0.56 limit
    assert won == 1
    assert realized == pytest.approx(9 * (1 - 0.54))     # +4.14
    assert div == "poly_kalshi_mlb" and strat == "poly_kalshi_mlb" and at == "poly_kalshi_copy"


def test_resolver_skips_prefix_rows_without_fill_data(fresh_db):
    """The 3 real live fills were journaled PRE-CP3 (no order_id / no fill data). The
    resolver must NOT book them off the limit price -- it excludes them entirely
    (honest: booking the wrong basis would corrupt the reconciliation)."""
    db_url = fresh_db
    with _db.connect(db_url) as c:
        c.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?,?,?,?)",
            ("2026-08-16T13:41:00+00:00", "poly_kalshi_mlb", "poly_kalshi_order",
             json.dumps({"status": "placed", "action": "entry", "outcome": "yes",
                         "ticker": "KXMLBGAME-A-MIA", "side": "bid", "count": 9,
                         "price": "0.5400"})),   # pre-CP3: no order_id / fill_count / fill_price
        )
    broker = _StubBroker({"KXMLBGAME-A-MIA": {"status": "resolved", "result": "yes"}})
    asyncio.run(kr.resolve_pending_round_trips(db_url, broker))
    with _db.connect(db_url) as c:
        n = c.execute("SELECT COUNT(*) FROM kalshi_round_trips").fetchone()[0]
    assert n == 0
    assert "KXMLBGAME-A-MIA" not in broker.calls   # excluded at fetch, never looked up


def test_resolver_additive_arb_actors_untouched(fresh_db):
    """A kalshi_llm arb would_have_placed row still resolves to its OWN division and
    coexists with a poly_kalshi fill in one pass -- the arb path is unaffected."""
    db_url = fresh_db
    with _db.connect(db_url) as c:
        c.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?,?,?,?)",
            ("2026-07-20T00:00:00+00:00", "kalshi_llm_arbitrage", "would_have_placed",
             json.dumps({"order_id": "llm-1", "ticker": "KXLLM-1", "outcome": "yes",
                         "qty": 10.0, "limit_price": 0.30, "division": "kalshi_llm_arbitrage"})),
        )
    _audit(db_url, _pk(order_id="mia", ticker="KXMLBGAME-A-MIA"))
    broker = _StubBroker({
        "KXLLM-1": {"status": "resolved", "result": "yes"},
        "KXMLBGAME-A-MIA": {"status": "resolved", "result": "yes"},
    })
    counts = asyncio.run(kr.resolve_pending_round_trips(db_url, broker))
    assert counts["resolved"] == 2
    with _db.connect(db_url) as c:
        rows = {r[0]: r[1] for r in c.execute("SELECT order_id, division FROM kalshi_round_trips")}
    assert rows == {"llm-1": "kalshi_llm_arbitrage", "mia": "poly_kalshi_mlb"}


# ── THE HARD GATE: resolver realized == sweep _realized_pnl_day ─────────────
# The 3 real fills (MIA won, CIN lost, AZ won) + a void; realized from first principles.
#   (order_id, ticker, fill_count, fill_price, (status, result), expected_realized)
_POSITIONS = [
    ("mia", "KXMLBGAME-A-MIA", 9,  0.54, ("resolved", "yes"),  9 * (1 - 0.54)),   # +4.14 won
    ("cin", "KXMLBGAME-A-CIN", 10, 0.48, ("resolved", "no"),  -10 * 0.48),        # -4.80 lost
    ("az",  "KXMLBGAME-B-AZ",  10, 0.47, ("resolved", "yes"), 10 * (1 - 0.47)),   # +5.30 won
    ("vd",  "KXMLBGAME-C-VD",  5,  0.50, ("void", "void"),     0.0),              # 0 void
]


def _run_resolver_side(db_url):
    for oid, tkr, fc, fp, _res, _exp in _POSITIONS:
        _audit(db_url, _pk(order_id=oid, ticker=tkr, count=fc, fill_count=fc, fill_price=fp))
    broker = _StubBroker({tkr: {"status": res[0], "result": res[1]}
                          for _oid, tkr, _fc, _fp, res, _exp in _POSITIONS})
    asyncio.run(kr.resolve_pending_round_trips(db_url, broker))
    return _sum_resolver_realized(db_url)


def _run_sweep_side(db_url, pnl_by_ticker):
    ex = PolyKalshiExecutor(dry_run=True, db_url=db_url, strategy="poly_kalshi_mlb")
    trader = PolyKalshiCopyTrader(executor=ex, db_url=db_url, day_key_fn=lambda: "2026-08-16")

    async def _get_settled():
        return [(tkr, "2026-08-16T22:00:00+00:00", pnl) for tkr, pnl in pnl_by_ticker.items()]

    asyncio.run(trader.run_settlement_sweep(_get_settled))
    return trader._realized_pnl_day


def test_HARD_GATE_resolver_realized_equals_sweep_realized(fresh_db):
    db_url = fresh_db
    expected = sum(exp for *_head, exp in _POSITIONS)      # first principles: +4.64
    resolver_realized = _run_resolver_side(db_url)
    # Kalshi settlement pnl_dollars, GROSS = the same per-position gross settlement value.
    pnl_by_ticker = {tkr: exp for _oid, tkr, _fc, _fp, _res, exp in _POSITIONS}
    sweep_realized = _run_sweep_side(db_url, pnl_by_ticker)
    assert resolver_realized == pytest.approx(expected)         # dashboard side: +4.64
    assert sweep_realized == pytest.approx(expected)            # halt side:      +4.64
    assert resolver_realized == pytest.approx(sweep_realized)   # THE GATE: they agree


def test_HARD_GATE_has_teeth_detects_net_of_fee_drift(fresh_db):
    """If Kalshi reported NET-of-fee settlement while the resolver books GROSS, the two
    numbers DIVERGE -- proving the gate is not a rubber stamp. This is exactly the
    dashboard-vs-halt drift CP4 exists to catch (would be STOP-AND-REPORT on real data)."""
    db_url = fresh_db
    resolver_realized = _run_resolver_side(db_url)
    fee_per_contract = 0.01
    pnl_by_ticker = {tkr: exp - fee_per_contract * fc
                     for _oid, tkr, fc, _fp, _res, exp in _POSITIONS}   # net-of-fee
    sweep_realized = _run_sweep_side(db_url, pnl_by_ticker)
    assert resolver_realized != pytest.approx(sweep_realized)   # gate would FIRE -> STOP
