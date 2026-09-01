"""Guard suite for the shared-RH-auth-outage fix (pead_strategy.py manage() :646 /
scan() :426). Written to BREAK the invariant, not confirm it.

INVARIANT: a DEAD / THROWING broker.snapshot() (401 / stale pickle / mid-reauth on
the shared RH session) must NOT throw the manage()/scan() loop dead. manage() skips
the WHOLE exit tick and returns ([], cadence); scan() skips and returns []. Neither
places an order, neither mutates the ledger (no half-state). The per-symbol
QuoteSymbolUnresolved skip BELOW :646 (Part-3 rename-defense) is preserved unchanged.

Context: on 2026-08-31 a ~48-min shared-session outage threw straight out of manage()
at the unguarded snapshot() ~10x (every cadence) until recovery. This proves the guard.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import yaml

from trading_corp.agents.strategies.pead_strategy import PEADStrategy
from trading_corp.brokers.robinhood import QuoteSymbolUnresolved
from trading_corp.persistence.db import connect, init_db
from trading_corp.persistence.models import ProposedOrder

DIVISION = PEADStrategy.SLUG  # "robinhood_pead"


# ── test doubles ──────────────────────────────────────────────────────────────
class FakeRisk:
    def evaluate(self, order, account, strategy_state, *a, **k):
        return SimpleNamespace(verdict="approve", reason="ok", new_qty=None)


class FakeLogger:
    def __init__(self):
        self.events: list = []
        self.proposed: list = []

    def log_event(self, slug, kind, payload):
        self.events.append((slug, kind, payload))

    def log_proposed_order(self, order):
        self.proposed.append(order)


class ThrowingSnapshotBroker:
    """RH-shaped broker whose snapshot() RAISES -- models the dead shared session
    that threw out of manage() at :646. quote()/place() are tripwires: on a skipped
    tick they must NEVER be reached."""

    paper = False

    def __init__(self):
        self.snap_calls = 0
        self.quote_calls = 0
        self.placed: list = []

    async def snapshot(self):
        self.snap_calls += 1
        raise RuntimeError("401 Unauthorized (dead shared RH session)")

    async def quote(self, symbol, *, strict=False):
        self.quote_calls += 1
        return 100.0

    async def place(self, order, division=None):
        self.placed.append(order)
        return SimpleNamespace(price=100.0, qty=getattr(order, "qty", 0.0))


class UnresolvedQuoteBroker:
    """snapshot() OK, but quote() raises QuoteSymbolUnresolved -- the Part-3 path
    that must still skip ONE symbol (continue), proving the fix did not regress the
    per-symbol guard below :646."""

    paper = False

    def __init__(self, equity=1000.0):
        self.equity = float(equity)
        self.snap_calls = 0
        self.quote_calls = 0
        self.placed: list = []

    async def snapshot(self):
        self.snap_calls += 1
        return SimpleNamespace(equity=self.equity, buying_power=self.equity)

    async def quote(self, symbol, *, strict=False):
        self.quote_calls += 1
        raise QuoteSymbolUnresolved(symbol)

    async def place(self, order, division=None):
        self.placed.append(order)
        return SimpleNamespace(price=100.0, qty=getattr(order, "qty", 0.0))


# ── helpers ───────────────────────────────────────────────────────────────────
def _yaml(tmp_path, **over):
    block = {"auto_execute": False, "manage_cadence_sec": 300,
             "max_concurrent_positions": 10}
    block.update(over)
    p = tmp_path / "strategies.yaml"
    p.write_text(yaml.safe_dump({"robinhood_pead": block}), encoding="utf-8")
    return p


def _strategy(db_url, yaml_path, *, logger=None, provider=None):
    return PEADStrategy(
        db_url=db_url, risk_agent=FakeRisk(), data_exec=SimpleNamespace(),
        logger_agent=logger or FakeLogger(),
        earnings_provider=provider or SimpleNamespace(),
        strategies_yaml=yaml_path, execution_mode="live",
    )


def _pextra(ref=100.0, atr=1.0, swing=80.0):
    return {"entry_atr_14": atr, "post_earnings_swing_low": swing,
            "pre_earnings_close": ref * 1.02, "earnings_gap_top": ref,
            "entry_sue": 3.0, "next_earnings_date": None, "name": "AAA",
            "entry_reference_price": ref,
            "stop_price": max(ref - 2.5 * atr, swing), "source_signal": "srw_sue"}


def _seed_open(strat, *, order_id="o1", symbol="AAA", qty=1.0, ref=100.0):
    """Insert one OPEN paper_trade_record via the strategy's own record path so
    _open_rows() returns it and pp.primitives_from_extra() is non-None."""
    extra = _pextra(ref=ref)
    extra["name"] = symbol
    order = ProposedOrder(
        strategy=strat.SLUG, symbol=symbol, side="buy", qty=qty,
        order_type="market", id=order_id, notional_usd=ref * qty,
        fractional=True, rationale="seed", extra=extra)
    order.execution_mode = "live"
    order.fill_price = ref
    strat._write_record(order, max_hold_seconds=3600)


def _all_records(url):
    with connect(url) as conn:
        rows = conn.execute(
            "SELECT order_id, symbol, qty, result, result_ts, extra_json "
            "FROM paper_trade_record ORDER BY order_id").fetchall()
        pend = conn.execute("SELECT COUNT(*) c FROM pending_order").fetchone()["c"]
    return [dict(r) for r in rows], pend


def _open_count(url):
    with connect(url) as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM paper_trade_record WHERE division=? "
            "AND result IS NULL", (DIVISION,)).fetchone()["c"]


def _force_session(monkeypatch):
    # market-hours gate -> "session" so manage() reaches the guarded snapshot().
    monkeypatch.setattr(PEADStrategy, "_exit_window_state",
                        staticmethod(lambda now, cfg: ("session", True)))


# ═══ TEST 1 (mandated): dead snapshot() -> manage() skips the tick cleanly ════
def test_manage_dead_snapshot_skips_tick_no_throw_no_place(tmp_db, tmp_path, monkeypatch):
    init_db(tmp_db)
    strat = _strategy(tmp_db, _yaml(tmp_path))
    _seed_open(strat, symbol="AAA")          # >=1 open row -> manage passes the empty-guard
    _force_session(monkeypatch)
    assert _open_count(tmp_db) == 1
    before, pend_before = _all_records(tmp_db)
    brk = ThrowingSnapshotBroker()

    # The whole point: a throwing snapshot() must NOT propagate out of manage().
    exits, cadence = asyncio.run(strat.manage(brk))

    assert exits == []                       # no exit produced
    assert cadence == 300                    # normal cadence returned (skip + resume next tick)
    assert brk.snap_calls == 1               # reached the guarded snapshot()
    assert brk.quote_calls == 0              # WHOLE tick skipped -- never entered the per-symbol loop
    assert brk.placed == []                  # no order placed
    after, pend_after = _all_records(tmp_db)
    assert after == before                   # NO half-state: ledger byte-identical before/after
    assert pend_after == pend_before == 0    # no pending/phantom row created
    assert _open_count(tmp_db) == 1          # position still open, untouched


# ═══ TEST 2 (mandated): per-symbol QuoteSymbolUnresolved skip still works ═════
def test_manage_quote_unresolved_still_skips_one_symbol(tmp_db, tmp_path, monkeypatch):
    init_db(tmp_db)
    logger = FakeLogger()
    strat = _strategy(tmp_db, _yaml(tmp_path), logger=logger)
    _seed_open(strat, symbol="AAA")
    _force_session(monkeypatch)
    brk = UnresolvedQuoteBroker(equity=1000.0)

    exits, cadence = asyncio.run(strat.manage(brk))   # must NOT throw

    assert exits == []                                # symbol skipped, no exit fired
    assert cadence == 300
    assert brk.snap_calls == 1                        # snapshot succeeded (guard added is transparent)
    assert brk.quote_calls >= 1                       # reached the per-symbol quote() below :646
    assert brk.placed == []                           # nothing placed on a not-found symbol
    assert any(k == "pead_symbol_unresolved" for _, k, _ in logger.events)  # Part-3 flag fired
    assert _open_count(tmp_db) == 1                    # row still open (skipped, not exited)


# ═══ TEST 3 (bonus): dead snapshot() -> scan() returns [] cleanly ════════════
def test_scan_dead_snapshot_returns_empty_no_throw(tmp_db, tmp_path, monkeypatch):
    init_db(tmp_db)
    # Minimal candidate so scan() reaches the guarded snapshot() at :426: the wave
    # loop skips cleanly (empty eps) and rank_wave is stubbed to yield one candidate.
    provider = SimpleNamespace(get_quarterly_eps=lambda s: [])
    strat = _strategy(tmp_db, _yaml(tmp_path, auto_execute=True, universe=["AAA"]),
                      provider=provider)
    monkeypatch.setattr("trading_corp.agents.strategies.pead_strategy.rank_wave",
                        lambda eps_by, screens, **k: [SimpleNamespace(symbol="AAA", sue=3.0)])
    before, pend_before = _all_records(tmp_db)
    brk = ThrowingSnapshotBroker()

    orders = asyncio.run(strat.scan(brk))    # must NOT throw

    assert orders == []                      # scan skipped -- no entries
    assert brk.snap_calls == 1               # reached the guarded snapshot()
    assert brk.placed == []                  # no order placed
    after, pend_after = _all_records(tmp_db)
    assert after == before                   # NO half-state
    assert pend_after == pend_before == 0    # no intent/pending row written
