"""ADVERSARIAL suite for PEAD Flag-2 — the deferred-fill reconcile (pre-open scan →
queue to the open → reconcile realized fill). Written to BREAK the invariant, not
confirm it. The live-money invariant under test:

  A PENDING order is NOT an open position. It lives in a SEPARATE table
  (`pending_order`), is never counted in the book, and becomes a real
  `paper_trade_record` ONLY when reconcile confirms a fill AND reads realized qty.
  No confirmed fill = no position (the phantom-position rule, in its new form).

And the load-bearing second-order catch: the >5% collar miss MUST cancel the
resting GFD order (it rests all day; un-cancelled it could fill UNWATCHED later =
phantom position), and the cancel deadline is anchored at the 9:30 OPEN, never at
(pre-open) placement.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace

import yaml

from trading_corp.agents.strategies import pead_pressures as pp
from trading_corp.agents.strategies.pead_strategy import PEADStrategy, _Bar
from trading_corp.persistence.db import connect, init_db
from trading_corp.utils.market_hours import ET
from trading_corp.web.pead_view import DIVISION, business_days, query_open_positions

# ── test doubles ─────────────────────────────────────────────────────────────


class _FakeCal:
    """Controllable market calendar: is_open_at + a trading-day close_time_et."""

    def __init__(self, open_now: bool = True):
        self._open = open_now

    def is_open_at(self, when):
        return self._open

    def close_time_et(self, d):
        return datetime.combine(d, time(16, 0), tzinfo=ET)


class FakePendingBroker:
    """Robinhood-shaped broker (paper=False) exposing only the three Flag-2 deferred
    methods the reconcile path uses. `read` is the order-state dict; once cancelled,
    `post_cancel_read` (if set) is returned — to model a partial that completes by the
    deadline cancel."""

    paper = False

    def __init__(self, *, equity=75.0, place_id="rh1", read=None,
                 post_cancel_read=None, fail_place=False):
        self.equity = float(equity)
        self.place_id = place_id
        self._read = dict(read or {"state": "unconfirmed", "filled_qty": 0.0,
                                   "avg_price": 0.0, "executed_notional": None,
                                   "account": "680725082"})
        self._post_cancel = post_cancel_read
        self.fail_place = fail_place
        self.placed: list = []
        self.cancelled: list = []
        self.reads = 0
        self._has_cancelled = False

    async def snapshot(self):
        return SimpleNamespace(equity=self.equity, buying_power=self.equity)

    async def quote(self, symbol):
        return 13.81

    async def place_fractional_pending(self, order):
        if self.fail_place:
            raise RuntimeError("RH rejected (test)")
        self.placed.append(order)
        return self.place_id

    async def read_fractional_order(self, rh_id):
        self.reads += 1
        if self._has_cancelled and self._post_cancel is not None:
            return dict(self._post_cancel)
        return dict(self._read)

    async def cancel_fractional_order(self, rh_id):
        self.cancelled.append(rh_id)
        self._has_cancelled = True
        return True


class FakeRisk:
    def __init__(self, verdict="approve", *, new_qty=None, reason="ok"):
        self._verdict, self._new_qty, self._reason = verdict, new_qty, reason
        self.calls: list = []

    def evaluate(self, order, account, strategy_state, *a, **k):
        self.calls.append(order)
        return SimpleNamespace(verdict=self._verdict, reason=self._reason, new_qty=self._new_qty)


class FakeLogger:
    def __init__(self):
        self.events: list = []
        self.proposed: list = []

    def log_event(self, slug, kind, payload):
        self.events.append((slug, kind, payload))

    def log_proposed_order(self, order):
        self.proposed.append(order)


# ── helpers ──────────────────────────────────────────────────────────────────


def _yaml(tmp_path, **over):
    block = {"auto_execute": False, "position_pct": 0.10,
             "max_concurrent_positions": 10, "manage_cadence_sec": 300,
             "reconcile_poll_interval_sec": 30,
             "reconcile_deadline_after_open_sec": 300,
             "reconcile_partial_warn_frac": 0.90}
    block.update(over)
    p = tmp_path / "strategies.yaml"
    p.write_text(yaml.safe_dump({"robinhood_pead": block}), encoding="utf-8")
    return p


def _strategy(db_url, yaml_path, *, risk, execmode="live", logger=None, provider=None):
    return PEADStrategy(
        db_url=db_url, risk_agent=risk, data_exec=SimpleNamespace(),
        logger_agent=logger or FakeLogger(),
        earnings_provider=provider or SimpleNamespace(),
        strategies_yaml=yaml_path, execution_mode=execmode,
    )


def _pextra(*, atr=0.40, swing=10.0, pre=14.41, gap=13.96, ref=14.00, sue=3.0,
            name="F", next_earn=None):
    return {"entry_atr_14": atr, "post_earnings_swing_low": swing,
            "pre_earnings_close": pre, "earnings_gap_top": gap, "entry_sue": sue,
            "next_earnings_date": next_earn, "name": name,
            "entry_reference_price": ref, "stop_price": max(ref - 2.5 * atr, swing),
            "source_signal": "srw_sue"}


def _seed_pending(url, *, order_id, symbol, rh_id, notional, extra, trading_date,
                  max_hold=None, placed_ts=None):
    with connect(url) as conn:
        conn.execute(
            "INSERT INTO pending_order (order_id, ts, strategy, division, symbol, side, "
            "order_type, notional_usd, broker_order_id, trading_date, max_hold_seconds, "
            "rationale, state, extra_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, placed_ts or datetime.now(timezone.utc).isoformat(), DIVISION,
             DIVISION, symbol, "buy", "market", float(notional), rh_id, trading_date,
             int(max_hold) if max_hold else None, "PEAD entry", "pending",
             json.dumps(extra)),
        )


def _pending_count(url):
    with connect(url) as conn:
        return conn.execute("SELECT COUNT(*) c FROM pending_order WHERE division=? "
                            "AND state='pending'", (DIVISION,)).fetchone()["c"]


def _open_count(url):
    with connect(url) as conn:
        return conn.execute("SELECT COUNT(*) c FROM paper_trade_record WHERE division=? "
                            "AND result IS NULL", (DIVISION,)).fetchone()["c"]


def _row(url, order_id):
    with connect(url) as conn:
        return conn.execute("SELECT * FROM paper_trade_record WHERE order_id=?",
                            (order_id,)).fetchone()


def _patch_cal(monkeypatch, open_now=True):
    monkeypatch.setattr("trading_corp.agents.strategies.pead_strategy.default_calendar",
                        lambda: _FakeCal(open_now))


def _patch_open(monkeypatch, *, seconds_ago):
    """Pin the session open to `seconds_ago` before now (deadline = open + cfg)."""
    when = datetime.now(timezone.utc).astimezone(ET) - timedelta(seconds=seconds_ago)
    monkeypatch.setattr(PEADStrategy, "_session_open_et", staticmethod(lambda td: when))


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
    return [_Bar(dt, c * 0.99, c * 1.02, c * 0.98, c, 1_000_000.0)
            for dt, c in ((d, base * (1 + 0.001 * i)) for i, d in enumerate(dates))]


# ═══ DEFERRED PLACEMENT: live entry queues as PENDING, writes NO record ═══════


def test_deferred_live_entry_writes_pending_not_record(tmp_db, tmp_path, monkeypatch):
    """LIVE scan() writes an INTENT row (state='intent', broker_order_id=NULL) and
    makes NO broker call — NOT a paper_trade_record. intent/pending row != open position."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    ann, nxt = _past_bdays(today, 1), today + timedelta(days=30)
    bars = _gen_bars(today, 40, 14.0)
    provider = SimpleNamespace(
        get_quarterly_eps=lambda s: [SimpleNamespace(report_date=ann, actual_eps=1.0 + 0.1 * i)
                                     for i in range(8)],
        get_company_facts=lambda s: {"market_cap": 5e8, "sector": "technology"},
        get_next_earnings_date=lambda s, asof=None: nxt,
    )
    strat = _strategy(tmp_db, _yaml(tmp_path, auto_execute=True, universe=["AAA"]),
                      risk=FakeRisk("approve"), execmode="live", provider=provider)
    monkeypatch.setattr("trading_corp.agents.strategies.pead_strategy.rank_wave",
                        lambda eps_by, screens, **k: [SimpleNamespace(symbol="AAA", sue=3.0)])
    monkeypatch.setattr(PEADStrategy, "_fetch_daily_bars",
                        staticmethod(lambda symbol, lookback_days=180: bars))
    brk = FakePendingBroker(equity=75.0, place_id="rhAAA")

    orders = asyncio.run(strat.scan(brk))
    assert brk.placed == []                             # NO broker call — intent only
    with connect(tmp_db) as conn:
        intent = conn.execute(
            "SELECT * FROM pending_order WHERE division=? AND state='intent'",
            (DIVISION,),
        ).fetchone()
    assert intent is not None                           # intent row written
    assert intent["broker_order_id"] is None           # no broker_order_id (not yet placed)
    assert _open_count(tmp_db) == 0                     # NO book record — pending != position
    assert query_open_positions(tmp_db) == []          # dashboard book sees nothing
    assert len(orders) == 1                             # scan reports it as submitted


def test_pending_not_counted_in_book_or_capacity(tmp_db, tmp_path):
    """A seeded PENDING row is invisible to the position book (_open_rows) yet folds
    into the scan's held-set via _pending_symbols (slot reservation)."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    _seed_pending(tmp_db, order_id="p1", symbol="F", rh_id="rh1", notional=5.0,
                  extra=_pextra(), trading_date=today.isoformat())
    strat = _strategy(tmp_db, _yaml(tmp_path), risk=FakeRisk())
    assert _open_count(tmp_db) == 0                     # not in the book
    assert strat._open_rows() == []                    # exit engine sees nothing
    assert strat._pending_symbols() == {"F"}           # but the slot is reserved
    assert strat._held_symbols() == set()              # held != pending


# ═══ RECONCILE: confirmed fill promotes realized qty + re-anchors the stop ════


def test_reconcile_filled_promotes_realized_and_reanchors_stop(tmp_db, tmp_path, monkeypatch):
    """A CONFIRMED fill (state=filled, cum=0.3621 @ 13.81) promotes to a real record
    carrying the REALIZED qty / avg entry / executed_notional — and re-anchors the
    stop on the realized entry (Flag 1, via the locked contract). Pending row gone."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    extra = _pextra(atr=0.40, swing=10.0, ref=14.00)     # scan ref 14.00 → stored stop 13.00
    _seed_pending(tmp_db, order_id="p1", symbol="F", rh_id="rh1", notional=5.0,
                  extra=extra, trading_date=today.isoformat(), max_hold=3600)
    strat = _strategy(tmp_db, _yaml(tmp_path), risk=FakeRisk())
    _patch_cal(monkeypatch, open_now=True)
    brk = FakePendingBroker(read={"state": "filled", "filled_qty": 0.3621,
                                  "avg_price": 13.81, "executed_notional": 5.0,
                                  "account": "680725082"})

    promoted, _ = asyncio.run(strat.reconcile(brk))
    assert len(promoted) == 1
    assert _pending_count(tmp_db) == 0                  # pending consumed
    r = _row(tmp_db, "p1")                              # promoted record reuses the order_id
    assert r is not None and r["result"] is None        # an OPEN position now
    assert abs(r["qty"] - 0.3621) < 1e-9                # REALIZED qty (not requested)
    assert abs(r["entry_reference_price"] - 13.81) < 1e-9
    assert r["execution_mode"] == "live"
    ex = json.loads(r["extra_json"])
    assert abs(ex["executed_notional"] - 5.0) < 1e-9
    # stop RE-ANCHORED on realized 13.81 (was 13.00 off the 14.00 scan ref)
    assert abs(r["stop_price"] - max(13.81 - 2.5 * 0.40, 10.0)) < 1e-9
    assert r["stop_price"] != 13.00


def test_reconcile_noop_when_market_closed(tmp_db, tmp_path, monkeypatch):
    """Pre-open / closed: reconcile is a NO-OP — it does NOT poll, cancel, or promote
    (cancelling a queued order before 9:30 is the exact bug Flag-2 fixes). Pending
    survives to the open."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    _seed_pending(tmp_db, order_id="p1", symbol="F", rh_id="rh1", notional=5.0,
                  extra=_pextra(), trading_date=today.isoformat())
    strat = _strategy(tmp_db, _yaml(tmp_path), risk=FakeRisk())
    _patch_cal(monkeypatch, open_now=False)            # market CLOSED
    brk = FakePendingBroker()

    promoted, _ = asyncio.run(strat.reconcile(brk))
    assert promoted == []
    assert brk.reads == 0 and brk.cancelled == []      # no poll, no cancel pre-open
    assert _pending_count(tmp_db) == 1                  # still queued


# ═══ THE LOAD-BEARING BRANCH: collar miss cancels the resting order + drops ═══


def test_reconcile_collar_miss_cancels_resting_order_and_drops(tmp_db, tmp_path, monkeypatch):
    """>5% collar miss: unfilled past (open + deadline) → CANCEL the resting GFD order
    (else it could fill UNWATCHED at 2pm = phantom) + log + drop. NO record written."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    logger = FakeLogger()
    _seed_pending(tmp_db, order_id="p1", symbol="F", rh_id="rh1", notional=5.0,
                  extra=_pextra(), trading_date=today.isoformat())
    strat = _strategy(tmp_db, _yaml(tmp_path), risk=FakeRisk(), logger=logger)
    _patch_cal(monkeypatch, open_now=True)
    _patch_open(monkeypatch, seconds_ago=10_000)       # open long ago → past open+300
    brk = FakePendingBroker(read={"state": "unconfirmed", "filled_qty": 0.0,
                                  "avg_price": 0.0, "executed_notional": None,
                                  "account": None})

    promoted, _ = asyncio.run(strat.reconcile(brk))
    assert promoted == []
    assert brk.cancelled == ["rh1"]                    # the resting order WAS cancelled
    assert _open_count(tmp_db) == 0                     # NO record (the miss is accepted)
    assert _pending_count(tmp_db) == 0                  # pending cleared
    assert any(k == "pead_pending_collar_miss" for _, k, _ in logger.events)  # logged


def test_reconcile_before_deadline_leaves_pending_no_cancel(tmp_db, tmp_path, monkeypatch):
    """Unfilled but WITHIN the open+deadline window → leave it queued, do NOT cancel,
    do NOT record. It gets more ticks to fill at the open."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    _seed_pending(tmp_db, order_id="p1", symbol="F", rh_id="rh1", notional=5.0,
                  extra=_pextra(), trading_date=today.isoformat())
    strat = _strategy(tmp_db, _yaml(tmp_path), risk=FakeRisk())
    _patch_cal(monkeypatch, open_now=True)
    _patch_open(monkeypatch, seconds_ago=30)           # opened 30s ago, deadline 300 → within
    brk = FakePendingBroker(read={"state": "unconfirmed", "filled_qty": 0.0,
                                  "avg_price": 0.0, "executed_notional": None, "account": None})

    promoted, _ = asyncio.run(strat.reconcile(brk))
    assert promoted == []
    assert brk.cancelled == []                         # NOT cancelled — still inside the window
    assert _pending_count(tmp_db) == 1                 # left queued
    assert _open_count(tmp_db) == 0


def test_reconcile_deadline_anchored_at_open_not_placement(tmp_db, tmp_path, monkeypatch):
    """THE TRAP: an order PLACED pre-open (hours before) is NOT cancelled just because
    much > deadline elapsed since PLACEMENT — the deadline measures from the 9:30
    OPEN. Same pending: not-cancelled when open was 60s ago; cancelled once open is
    long past. (Placement-anchored would have cancelled everything ~9:00, pre-open.)"""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    placed_pre_open = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _seed_pending(tmp_db, order_id="p1", symbol="F", rh_id="rh1", notional=5.0,
                  extra=_pextra(), trading_date=today.isoformat(), placed_ts=placed_pre_open)
    strat = _strategy(tmp_db, _yaml(tmp_path), risk=FakeRisk())
    _patch_cal(monkeypatch, open_now=True)
    unfilled = {"state": "unconfirmed", "filled_qty": 0.0, "avg_price": 0.0,
                "executed_notional": None, "account": None}

    # open was 60s ago → within open+300 even though placement was 2h ago → NO cancel
    _patch_open(monkeypatch, seconds_ago=60)
    brk = FakePendingBroker(read=unfilled)
    asyncio.run(strat.reconcile(brk))
    assert brk.cancelled == [] and _pending_count(tmp_db) == 1

    # now the open is well past the deadline → cancel fires
    _patch_open(monkeypatch, seconds_ago=10_000)
    brk2 = FakePendingBroker(read=unfilled)
    asyncio.run(strat.reconcile(brk2))
    assert brk2.cancelled == ["rh1"] and _pending_count(tmp_db) == 0


# ═══ PARTIAL FILL: accept the realized partial + carry the <90% warning ══════


def test_reconcile_terminal_partial_promotes_realized_partial(tmp_db, tmp_path, monkeypatch):
    """A terminal order that filled a realized PARTIAL (state=cancelled, cum=0.10 of a
    $5 request) promotes the REALIZED partial (decision #2) — never the requested
    notional — and records realized cumulative_quantity."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    _seed_pending(tmp_db, order_id="p1", symbol="F", rh_id="rh1", notional=5.0,
                  extra=_pextra(), trading_date=today.isoformat(), max_hold=3600)
    strat = _strategy(tmp_db, _yaml(tmp_path), risk=FakeRisk())
    _patch_cal(monkeypatch, open_now=True)
    brk = FakePendingBroker(read={"state": "cancelled", "filled_qty": 0.10,
                                  "avg_price": 13.90, "executed_notional": 1.39,
                                  "account": "680725082"})

    promoted, _ = asyncio.run(strat.reconcile(brk))
    assert len(promoted) == 1
    r = _row(tmp_db, "p1")
    assert abs(r["qty"] - 0.10) < 1e-9                  # REALIZED partial, not the $5 request
    assert abs(json.loads(r["extra_json"])["executed_notional"] - 1.39) < 1e-9
    assert _pending_count(tmp_db) == 0


def test_reconcile_collar_partial_at_deadline_records_realized(tmp_db, tmp_path, monkeypatch):
    """A non-terminal order that filled a partial by the deadline: cancel the
    remainder, RE-READ the final realized, and RECORD the realized partial (never drop
    a real fill). The cancel-then-read mirrors the synchronous discipline."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    _seed_pending(tmp_db, order_id="p1", symbol="F", rh_id="rh1", notional=5.0,
                  extra=_pextra(), trading_date=today.isoformat(), max_hold=3600)
    strat = _strategy(tmp_db, _yaml(tmp_path), risk=FakeRisk())
    _patch_cal(monkeypatch, open_now=True)
    _patch_open(monkeypatch, seconds_ago=10_000)       # past deadline
    brk = FakePendingBroker(
        read={"state": "partially_filled", "filled_qty": 0.0, "avg_price": 0.0,
              "executed_notional": None, "account": None},
        post_cancel_read={"state": "cancelled", "filled_qty": 0.07, "avg_price": 13.95,
                          "executed_notional": 0.98, "account": "680725082"},
    )

    promoted, _ = asyncio.run(strat.reconcile(brk))
    assert brk.cancelled == ["rh1"]                    # remainder cancelled
    assert len(promoted) == 1                          # realized partial RECORDED, not dropped
    r = _row(tmp_db, "p1")
    assert abs(r["qty"] - 0.07) < 1e-9
    assert _pending_count(tmp_db) == 0


# ═══ IDEMPOTENCY: a promote can't double-write the position ══════════════════


def test_promote_is_idempotent_single_record(tmp_db, tmp_path):
    """Crash-safety: promoting the same pending row twice (same order_id) writes the
    position EXACTLY once (INSERT OR IGNORE) — a restart that re-runs reconcile after
    the record landed but before the pending delete can't create a phantom duplicate."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    extra = _pextra()
    _seed_pending(tmp_db, order_id="p1", symbol="F", rh_id="rh1", notional=5.0,
                  extra=extra, trading_date=today.isoformat(), max_hold=3600)
    strat = _strategy(tmp_db, _yaml(tmp_path), risk=FakeRisk())
    row = {"order_id": "p1", "symbol": "F", "side": "buy", "order_type": "market",
           "notional_usd": 5.0, "broker_order_id": "rh1", "trading_date": today.isoformat(),
           "max_hold_seconds": 3600, "rationale": "PEAD entry", "extra": extra}
    info = {"filled_qty": 0.36, "avg_price": 13.81, "executed_notional": 5.0, "account": "x"}

    strat._promote_pending(row, info, 0.90)
    strat._promote_pending(row, info, 0.90)            # replay (idempotent)
    assert _open_count(tmp_db) == 1                     # exactly one position row


def test_partial_warning_logged_below_threshold(tmp_db, tmp_path, caplog):
    """<90%-of-requested realized notional trips a WARNING (observability only, no
    top-up) — so a chronically-underfilling collar shows up in the logs."""
    init_db(tmp_db)
    today = datetime.now(timezone.utc).date()
    extra = _pextra()
    _seed_pending(tmp_db, order_id="p1", symbol="F", rh_id="rh1", notional=5.0,
                  extra=extra, trading_date=today.isoformat(), max_hold=3600)
    strat = _strategy(tmp_db, _yaml(tmp_path), risk=FakeRisk())
    row = {"order_id": "p1", "symbol": "F", "side": "buy", "order_type": "market",
           "notional_usd": 5.0, "broker_order_id": "rh1", "trading_date": today.isoformat(),
           "max_hold_seconds": 3600, "rationale": "PEAD entry", "extra": extra}
    info = {"filled_qty": 0.20, "avg_price": 13.90, "executed_notional": 2.78, "account": "x"}

    import logging
    with caplog.at_level(logging.WARNING):
        strat._promote_pending(row, info, 0.90)        # $2.78 < 90% of $5
    assert any("PARTIAL entry" in rec.message for rec in caplog.records)
