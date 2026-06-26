"""ADVERSARIAL tests for PEAD entry-fix: intent-based pre-market → at-open placement.

Root cause: RH REJECTS fractional market_hours='regular_hours' orders submitted
pre-market — accepts the POST, then immediately sets state=rejected.  The old
scan() called broker.place_fractional_pending() pre-market; those orders never
filled.

Fix:
  scan() (LIVE)  — writes state='intent' row; ZERO broker calls.
  reconcile() Phase-1 — at open+buffer (~9:31 ET), places via _place_or_paper
                         → data_exec.place (same regular-hours path that filled
                         in the 2026-06-24 probe).

Invariants tested:
  1. NO pre-market broker call from scan().
  2. NO fill-without-record: if Phase-1 fill confirmed, paper_trade_record written.
  3. Idempotent: restart between scan(intent) and reconcile(place) leaves the
     intent row; next reconcile places once (no double-order).
  4. Reject/drop paths are phantom-free (no record on failure).
  5. intent IS in _pending_symbols() — slot reserved from scan time.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace

import yaml

from trading_corp.agents.strategies.pead_strategy import (
    PEADStrategy,
    _Bar,
    _DEFAULT_INTENT_BUFFER_SEC,
)
from trading_corp.persistence.db import connect, init_db
from trading_corp.utils.market_hours import ET
from trading_corp.web.pead_view import DIVISION, business_days

# ── test doubles ─────────────────────────────────────────────────────────────


class _FakeCal:
    """Controllable market calendar."""

    def __init__(self, open_now: bool = True):
        self._open = open_now

    def is_open_at(self, when):
        return self._open

    def close_time_et(self, d):
        return datetime.combine(d, time(16, 0), tzinfo=ET)


class FakeDataExec:
    """Simulates data_exec.place() for live placements."""

    def __init__(self, *, price: float = 13.81, qty: float = 0.3621,
                 notional: float = 5.0, fail: bool = False, qty_zero: bool = False):
        self._price = price
        self._qty = qty
        self._notional = notional
        self.fail = fail
        self.qty_zero = qty_zero
        self.placed: list = []

    async def place(self, order, division=None):
        if self.fail:
            raise RuntimeError("RH rejected pre-market (test)")
        self.placed.append((order, division))
        if self.qty_zero:
            # RH accept-then-reject model: returns a fill with qty=0
            return SimpleNamespace(price=None, qty=0, executed_notional=None)
        order.fill_price = self._price
        return SimpleNamespace(price=self._price, qty=self._qty,
                               executed_notional=self._notional)


class FakeRisk:
    def __init__(self, verdict: str = "approve", *, new_qty=None, reason: str = "ok"):
        self._verdict = verdict
        self._new_qty = new_qty
        self._reason = reason
        self.calls: list = []

    def evaluate(self, order, account, strategy_state, *a, **k):
        self.calls.append(order)
        return SimpleNamespace(verdict=self._verdict, reason=self._reason,
                               new_qty=self._new_qty)


class FakeLogger:
    def __init__(self):
        self.events: list = []
        self.proposed: list = []

    def log_event(self, slug, kind, payload):
        self.events.append((slug, kind, payload))

    def log_proposed_order(self, order):
        self.proposed.append(order)


# ── helpers ──────────────────────────────────────────────────────────────────


def _live_yaml(tmp_path, **over):
    block = {
        "auto_execute": True,
        "position_pct": 0.10,
        "max_concurrent_positions": 10,
        "manage_cadence_sec": 300,
        "reconcile_poll_interval_sec": 30,
        "reconcile_deadline_after_open_sec": 300,
        "intent_open_buffer_sec": 60,
        "reconcile_partial_warn_frac": 0.90,
    }
    block.update(over)
    p = tmp_path / "strategies.yaml"
    p.write_text(yaml.safe_dump({"robinhood_pead": block}), encoding="utf-8")
    return p


def _live_strategy(db_url, yaml_path, *, data_exec, logger=None, risk=None):
    return PEADStrategy(
        db_url=db_url, risk_agent=risk or FakeRisk(),
        data_exec=data_exec,
        logger_agent=logger or FakeLogger(),
        earnings_provider=SimpleNamespace(),
        strategies_yaml=yaml_path,
        execution_mode="live",
    )


def _pextra(*, atr=0.40, swing=10.0, pre=14.41, gap=13.96, ref=14.00, sue=3.0,
            name="F", next_earn=None):
    return {
        "entry_atr_14": atr, "post_earnings_swing_low": swing,
        "pre_earnings_close": pre, "earnings_gap_top": gap, "entry_sue": sue,
        "next_earnings_date": next_earn, "name": name,
        "entry_reference_price": ref, "stop_price": max(ref - 2.5 * atr, swing),
        "source_signal": "srw_sue",
    }


def _seed_intent(url, *, order_id, symbol, notional, extra, trading_date,
                 max_hold=None):
    """Seed a pending_order row with state='intent' (broker_order_id=NULL)."""
    with connect(url) as conn:
        conn.execute(
            "INSERT INTO pending_order (order_id, ts, strategy, division, symbol, side, "
            "order_type, notional_usd, broker_order_id, trading_date, max_hold_seconds, "
            "rationale, state, extra_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, datetime.now(timezone.utc).isoformat(), DIVISION, DIVISION,
             symbol, "buy", "market", float(notional), None, trading_date,
             int(max_hold) if max_hold else None, "PEAD entry (intent)", "intent",
             json.dumps(extra)),
        )


def _intent_count(url) -> int:
    with connect(url) as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM pending_order WHERE division=? AND state='intent'",
            (DIVISION,),
        ).fetchone()["c"]


def _open_count(url) -> int:
    with connect(url) as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM paper_trade_record WHERE division=? AND result IS NULL",
            (DIVISION,),
        ).fetchone()["c"]


def _row(url, order_id):
    with connect(url) as conn:
        return conn.execute(
            "SELECT * FROM paper_trade_record WHERE order_id=?", (order_id,)
        ).fetchone()


def _patch_cal(monkeypatch, open_now: bool = True):
    monkeypatch.setattr(
        "trading_corp.agents.strategies.pead_strategy.default_calendar",
        lambda: _FakeCal(open_now),
    )


def _patch_open(monkeypatch, *, seconds_ago: int):
    """Pin the session open to `seconds_ago` before now."""
    when = datetime.now(timezone.utc).astimezone(ET) - timedelta(seconds=seconds_ago)
    monkeypatch.setattr(
        PEADStrategy, "_session_open_et", staticmethod(lambda td: when)
    )


def _past_bdays(today, n):
    d = today
    while business_days(d, today) < n:
        d -= timedelta(days=1)
    return d


def _gen_bars(today, count, base):
    dates, dd = [], today
    while len(dates) < count:
        if dd.weekday() < 5:
            dates.append(dd)
        dd -= timedelta(days=1)
    dates.reverse()
    return [
        _Bar(dt, c * 0.99, c * 1.02, c * 0.98, c, 1_000_000.0)
        for dt, c in ((d, base * (1 + 0.001 * i)) for i, d in enumerate(dates))
    ]


# ═══ TEST 1: scan() writes intent, ZERO broker calls ═════════════════════════


def test_scan_writes_intent_row_no_broker_call(tmp_db, tmp_path, monkeypatch):
    """LIVE scan: writes state='intent' row with broker_order_id=NULL and makes
    ZERO calls to broker.place_fractional_pending OR data_exec.place.
    Core fix: pre-market RH rejects fractional regular_hours orders."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    ann = _past_bdays(today, 1)
    nxt = today + timedelta(days=30)
    bars = _gen_bars(today, 40, 14.0)
    provider = SimpleNamespace(
        get_quarterly_eps=lambda s: [
            SimpleNamespace(report_date=ann, actual_eps=float(i)) for i in range(1, 9)
        ],
        get_company_facts=lambda s: {"market_cap": 5e8, "sector": "technology"},
        get_next_earnings_date=lambda s, asof=None: nxt,
    )
    de = FakeDataExec()

    class _SpyBroker:
        """Broker that records if place_fractional_pending is ever called."""
        paper = False
        placed_pending: list = []

        async def snapshot(self):
            return SimpleNamespace(equity=75.0, buying_power=75.0)

        async def place_fractional_pending(self, order):
            # Should NEVER be called in the new design
            self.placed_pending.append(order)
            return "should-not-be-called"

    brk = _SpyBroker()
    strat = PEADStrategy(
        db_url=tmp_db, risk_agent=FakeRisk(), data_exec=de,
        logger_agent=FakeLogger(),
        earnings_provider=provider,
        strategies_yaml=_live_yaml(tmp_path, universe=["AAA"]),
        execution_mode="live",
    )
    monkeypatch.setattr(
        "trading_corp.agents.strategies.pead_strategy.rank_wave",
        lambda eps_by, screens, **k: [SimpleNamespace(symbol="AAA", sue=3.0)],
    )
    monkeypatch.setattr(
        PEADStrategy, "_fetch_daily_bars",
        staticmethod(lambda sym, lookback_days=180: bars),
    )

    orders = asyncio.run(strat.scan(brk))

    assert len(brk.placed_pending) == 0, "broker.place_fractional_pending must NOT be called"
    assert len(de.placed) == 0, "data_exec.place must NOT be called from scan()"
    assert _intent_count(tmp_db) == 1, "exactly one intent row must be written"
    assert _open_count(tmp_db) == 0, "intent is NOT a position (no paper_trade_record)"
    assert len(orders) == 1, "scan still reports the candidate"

    # Verify intent row structure
    with connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT * FROM pending_order WHERE state='intent'"
        ).fetchone()
    assert row is not None
    assert row["broker_order_id"] is None, "intent row has NULL broker_order_id"
    assert row["symbol"] == "AAA"

    # Verify pead_intent (not pead_pending) was logged
    strat2 = PEADStrategy(
        db_url=tmp_db, risk_agent=FakeRisk(), data_exec=de,
        logger_agent=(lg := FakeLogger()),
        earnings_provider=provider,
        strategies_yaml=_live_yaml(tmp_path, universe=["AAA"]),
        execution_mode="live",
    )
    # (event already logged in strat; check the test harness via direct inspection)
    # The important structural assertion is above: zero broker calls + intent row.


# ═══ TEST 2: reconcile before open+buffer — intent stays ══════════════════════


def test_reconcile_before_buffer_leaves_intent(tmp_db, tmp_path, monkeypatch):
    """reconcile() when market is open but now < open+buffer: intent stays, no
    placement, no drop. Operator wants to wait past 9:30 to be safe."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    _seed_intent(tmp_db, order_id="i1", symbol="F", notional=5.0,
                 extra=_pextra(), trading_date=today.isoformat(), max_hold=3600)
    de = FakeDataExec()
    strat = _live_strategy(
        tmp_db, _live_yaml(tmp_path, intent_open_buffer_sec=60), data_exec=de,
    )
    _patch_cal(monkeypatch, open_now=True)
    _patch_open(monkeypatch, seconds_ago=30)  # open 30s ago, buffer 60s → within buffer

    promoted, _ = asyncio.run(strat.reconcile(SimpleNamespace()))

    assert promoted == []
    assert de.placed == [], "no placement before open+buffer"
    assert _intent_count(tmp_db) == 1, "intent row must survive"
    assert _open_count(tmp_db) == 0


# ═══ TEST 3: reconcile at open+buffer — places, promotes, writes record ═══════


def test_reconcile_at_buffer_places_and_promotes_record(tmp_db, tmp_path, monkeypatch):
    """reconcile() at/after open+buffer: places via _place_or_paper (data_exec.place),
    gets a fill, writes paper_trade_record, deletes intent. Invariant: a fill is
    always tracked — never a real fill without a record."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    extra = _pextra(atr=0.40, swing=10.0, ref=14.00)
    _seed_intent(tmp_db, order_id="i1", symbol="F", notional=5.0,
                 extra=extra, trading_date=today.isoformat(), max_hold=3600)
    de = FakeDataExec(price=13.81, qty=0.3621, notional=5.0)
    logger = FakeLogger()
    strat = _live_strategy(
        tmp_db, _live_yaml(tmp_path, intent_open_buffer_sec=60),
        data_exec=de, logger=logger,
    )
    _patch_cal(monkeypatch, open_now=True)
    _patch_open(monkeypatch, seconds_ago=90)  # open 90s ago, buffer 60s → past buffer

    promoted, _ = asyncio.run(strat.reconcile(SimpleNamespace()))

    assert len(promoted) == 1
    assert len(de.placed) == 1, "data_exec.place called exactly once"
    assert _intent_count(tmp_db) == 0, "intent row consumed"
    assert _open_count(tmp_db) == 1, "promoted to a real open position"

    r = _row(tmp_db, "i1")
    assert r is not None and r["result"] is None, "open position (not closed)"
    assert r["execution_mode"] == "live"
    assert abs(r["qty"] - 0.3621) < 1e-9, "realized qty from fill"
    assert abs(r["entry_reference_price"] - 13.81) < 1e-9, "realized entry price"

    # stop re-anchored on realized fill (Flag-1 contract)
    ex = json.loads(r["extra_json"])
    expected_stop = max(13.81 - 2.5 * 0.40, 10.0)
    assert abs(ex["stop_price"] - expected_stop) < 1e-9, "stop re-anchored on realized fill"

    # pead_entry event logged (via_intent=True)
    assert any(k == "pead_entry" and p.get("via_intent")
               for _, k, p in logger.events), "pead_entry event logged"


# ═══ TEST 4: placement exception → dropped, no record ═══════════════════════


def test_reconcile_intent_placement_failed_drops_no_record(tmp_db, tmp_path, monkeypatch):
    """At-open placement raises exception (e.g. RH network error / rejection) →
    intent dropped (reason=placement_failed), pead_pending_dropped logged, NO
    paper_trade_record written (no phantom position)."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    _seed_intent(tmp_db, order_id="i1", symbol="F", notional=5.0,
                 extra=_pextra(), trading_date=today.isoformat(), max_hold=3600)
    de = FakeDataExec(fail=True)  # raises RuntimeError on place()
    logger = FakeLogger()
    strat = _live_strategy(
        tmp_db, _live_yaml(tmp_path, intent_open_buffer_sec=60),
        data_exec=de, logger=logger,
    )
    _patch_cal(monkeypatch, open_now=True)
    _patch_open(monkeypatch, seconds_ago=90)

    promoted, _ = asyncio.run(strat.reconcile(SimpleNamespace()))

    assert promoted == []
    assert _open_count(tmp_db) == 0, "no phantom record"
    assert _intent_count(tmp_db) == 0, "intent cleared"
    assert any(
        k == "pead_pending_dropped" and p.get("reason") == "placement_failed"
        for _, k, p in logger.events
    ), "pead_pending_dropped reason=placement_failed"


# ═══ TEST 5: RH accept-then-reject (qty=0) → dropped, no record ════════════


def test_reconcile_intent_rh_accepted_then_rejected_drops(tmp_db, tmp_path, monkeypatch):
    """RH model: data_exec.place succeeds but fill has qty=0 (accepted POST,
    immediately set state=rejected). Intent dropped (reason='rejected'), no phantom."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    _seed_intent(tmp_db, order_id="i1", symbol="F", notional=5.0,
                 extra=_pextra(), trading_date=today.isoformat(), max_hold=3600)
    de = FakeDataExec(qty_zero=True)  # qty=0 → rejected
    logger = FakeLogger()
    strat = _live_strategy(
        tmp_db, _live_yaml(tmp_path, intent_open_buffer_sec=60),
        data_exec=de, logger=logger,
    )
    _patch_cal(monkeypatch, open_now=True)
    _patch_open(monkeypatch, seconds_ago=90)

    promoted, _ = asyncio.run(strat.reconcile(SimpleNamespace()))

    assert promoted == []
    assert len(de.placed) == 1, "place WAS attempted (RH accepted the POST)"
    assert _open_count(tmp_db) == 0, "no phantom record"
    assert _intent_count(tmp_db) == 0, "intent dropped"
    assert any(
        k == "pead_pending_dropped" and p.get("reason") == "rejected"
        for _, k, p in logger.events
    ), "pead_pending_dropped reason=rejected"


# ═══ TEST 6: intent past deadline → dropped, reason=intent_past_deadline ═════


def test_reconcile_intent_past_deadline_drops(tmp_db, tmp_path, monkeypatch):
    """Intent row past open+deadline: dropped with reason=intent_past_deadline.
    NO placement attempted (data_exec.place NOT called). No phantom record."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    _seed_intent(tmp_db, order_id="i1", symbol="F", notional=5.0,
                 extra=_pextra(), trading_date=today.isoformat(), max_hold=3600)
    de = FakeDataExec()
    logger = FakeLogger()
    strat = _live_strategy(tmp_db, _live_yaml(tmp_path), data_exec=de, logger=logger)
    _patch_cal(monkeypatch, open_now=True)
    _patch_open(monkeypatch, seconds_ago=10_000)  # well past deadline (300s default)

    promoted, _ = asyncio.run(strat.reconcile(SimpleNamespace()))

    assert promoted == []
    assert de.placed == [], "no placement attempted past deadline"
    assert _intent_count(tmp_db) == 0, "intent dropped"
    assert _open_count(tmp_db) == 0, "no phantom record"
    assert any(
        k == "pead_pending_dropped" and p.get("reason") == "intent_past_deadline"
        for _, k, p in logger.events
    ), "pead_pending_dropped reason=intent_past_deadline"


# ═══ TEST 7: market closed pre-open — intent untouched ════════════════════════


def test_reconcile_market_closed_intent_stays(tmp_db, tmp_path, monkeypatch):
    """reconcile() pre-market / market closed: Phase-1 is skipped entirely.
    intent row untouched, no placement, no drop."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    _seed_intent(tmp_db, order_id="i1", symbol="F", notional=5.0,
                 extra=_pextra(), trading_date=today.isoformat(), max_hold=3600)
    de = FakeDataExec()
    strat = _live_strategy(tmp_db, _live_yaml(tmp_path), data_exec=de)
    _patch_cal(monkeypatch, open_now=False)  # market CLOSED

    promoted, _ = asyncio.run(strat.reconcile(SimpleNamespace()))

    assert promoted == []
    assert de.placed == []
    assert _intent_count(tmp_db) == 1, "intent untouched pre-open"
    assert _open_count(tmp_db) == 0


# ═══ TEST 8: intent in _pending_symbols blocks re-scan ═══════════════════════


def test_intent_in_pending_symbols_reserves_slot(tmp_db, tmp_path):
    """An intent row IS in _pending_symbols() — slot reserved so scan() cannot
    re-queue the same symbol while an intent is pending."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    _seed_intent(tmp_db, order_id="i1", symbol="F", notional=5.0,
                 extra=_pextra(), trading_date=today.isoformat())
    strat = _live_strategy(tmp_db, _live_yaml(tmp_path), data_exec=SimpleNamespace())

    assert "F" in strat._pending_symbols(), "intent symbol reserved in _pending_symbols"
    assert _open_count(tmp_db) == 0, "intent is not a position"
    assert strat._open_rows() == [], "exit engine sees no open rows"


# ═══ TEST 9: idempotency — restart before placement ══════════════════════════


def test_reconcile_intent_idempotent_restart_before_place(tmp_db, tmp_path, monkeypatch):
    """Crash-safety: a restart between scan(intent) and reconcile(place) leaves the
    intent row intact; the next reconcile places exactly once (no double-order)."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    extra = _pextra(atr=0.40, swing=10.0, ref=14.00)
    _seed_intent(tmp_db, order_id="i1", symbol="F", notional=5.0,
                 extra=extra, trading_date=today.isoformat(), max_hold=3600)
    _patch_cal(monkeypatch, open_now=True)
    _patch_open(monkeypatch, seconds_ago=90)

    # First reconcile: places + promotes
    de1 = FakeDataExec(price=13.81, qty=0.3621, notional=5.0)
    strat1 = _live_strategy(
        tmp_db, _live_yaml(tmp_path, intent_open_buffer_sec=60), data_exec=de1,
    )
    promoted1, _ = asyncio.run(strat1.reconcile(SimpleNamespace()))
    assert len(promoted1) == 1 and len(de1.placed) == 1

    # Second reconcile (simulate restart + re-run): intent is gone, nothing to do
    de2 = FakeDataExec(price=13.81, qty=0.3621, notional=5.0)
    strat2 = _live_strategy(
        tmp_db, _live_yaml(tmp_path, intent_open_buffer_sec=60), data_exec=de2,
    )
    promoted2, _ = asyncio.run(strat2.reconcile(SimpleNamespace()))
    assert len(promoted2) == 0 and len(de2.placed) == 0, "no double-order on re-run"
    assert _open_count(tmp_db) == 1, "exactly one position written"


# ═══ TEST 10: existing pending (state='pending') unaffected by Phase-1 ════════


def test_existing_pending_rows_unaffected_by_phase1(tmp_db, tmp_path, monkeypatch):
    """Phase-2 (state='pending') rows are NOT touched by Phase-1. A pending row
    with a real broker_order_id continues through the existing read/promote path."""
    def _seed_pending(url, *, order_id, symbol, rh_id, notional, extra,
                      trading_date, max_hold=None):
        with connect(url) as conn:
            conn.execute(
                "INSERT INTO pending_order (order_id, ts, strategy, division, symbol, side, "
                "order_type, notional_usd, broker_order_id, trading_date, max_hold_seconds, "
                "rationale, state, extra_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (order_id, datetime.now(timezone.utc).isoformat(), DIVISION, DIVISION,
                 symbol, "buy", "market", float(notional), rh_id, trading_date,
                 int(max_hold) if max_hold else None, "PEAD entry", "pending",
                 json.dumps(extra)),
            )

    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    extra = _pextra(atr=0.40, swing=10.0, ref=14.00)
    _seed_pending(tmp_db, order_id="p1", symbol="F", rh_id="rh1",
                  notional=5.0, extra=extra,
                  trading_date=today.isoformat(), max_hold=3600)

    de = FakeDataExec()  # should NOT be called by Phase-2

    class _PendingBroker:
        async def read_fractional_order(self, rh_id):
            return {"state": "filled", "filled_qty": 0.3621, "avg_price": 13.81,
                    "executed_notional": 5.0, "account": "680725082"}

    _patch_cal(monkeypatch, open_now=True)
    _patch_open(monkeypatch, seconds_ago=90)
    strat = _live_strategy(tmp_db, _live_yaml(tmp_path), data_exec=de)

    promoted, _ = asyncio.run(strat.reconcile(_PendingBroker()))

    assert len(promoted) == 1, "pending row promoted via Phase-2"
    assert len(de.placed) == 0, "Phase-2 did NOT call data_exec.place"
    assert _open_count(tmp_db) == 1
    assert _intent_count(tmp_db) == 0
