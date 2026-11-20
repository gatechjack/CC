"""SPY-spot fix (2026-11-20): direct spot + candle fallback + visibility.

The old manager._spot preferred port.quote, but the MACE port had none, so spot
fell back to the FULL chain fetch inside the 30s bound — SPY's strike grid pushed
it past the bound on every slow tick and the failure was silent (NULL spot in
mace_rung_live, no log, no audit). New behavior:

  port.quote (direct, light)  -> price > 0: use it; <= 0 or raises: unavailable
  broker unavailable          -> freshest mace_candle close, only if younger than
                                 spot_candle_max_age_sec (code default 600s)
  no source                   -> None + mace_spot_unavailable (symbol, reason)
  candle used                 -> mace_spot_fallback (symbol, source, bar age)
  either way                  -> at most ONE audit row per symbol per tick

Unit style mirrors tests/test_mace_mark_quality.py (object.__new__ + SimpleNamespace);
the integration test uses the real constructor like tests/test_mace_manager_live_write.py.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from trading_corp.mace import execution as ex
from trading_corp.mace import strategy as st
from trading_corp.mace.domain import CondorSpec, RungState, iso_week
from trading_corp.mace.manager import MaceManager
from trading_corp.mace.notify import MaceNotifier
from trading_corp.persistence import db as dbmod

from tests.test_mace_manager_window import _cfg

UTC = timezone.utc
NOW_UTC = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
NOW_S = NOW_UTC.timestamp()


# ── stub ports ──────────────────────────────────────────────────────────────

class _QuotePort:
    """Direct quote works; chain() must never be touched."""
    def __init__(self, px):
        self.px = px
        self.chain_calls = 0

    async def quote(self, sym):
        return self.px

    async def chain(self, sym):
        self.chain_calls += 1
        raise AssertionError("chain() fetched — _spot must use the direct quote")


class _RaisingPort(_QuotePort):
    async def quote(self, sym):
        raise RuntimeError("robinhood down")


class _HangingPort(_QuotePort):
    async def quote(self, sym):
        await asyncio.sleep(5)
        return 714.0


# ── unit stub: manager with only what _spot needs ───────────────────────────

def _mgr(port, *, candle=None, timeout=5, max_age=600):
    """candle: None or (bar_time_sec, close) served by the store stub."""
    audits = []
    mgr = object.__new__(MaceManager)
    mgr.cfg = SimpleNamespace(management=SimpleNamespace(
        leg_quote_timeout_sec=timeout, spot_candle_max_age_sec=max_age))
    mgr.port = port
    mgr.store = SimpleNamespace(latest_candle_close=lambda sym: candle)
    mgr._spot_audited = set()
    mgr._now_utc = lambda: NOW_UTC
    mgr._audit = lambda k, **p: audits.append((k, p))
    return mgr, audits


def _run(coro):
    return asyncio.run(coro)


# ── direct path ─────────────────────────────────────────────────────────────

def test_direct_quote_used_no_chain_fetch():
    port = _QuotePort(714.29)
    mgr, audits = _mgr(port)
    assert _run(mgr._spot("SPY")) == 714.29
    assert port.chain_calls == 0          # the chain ride is gone
    assert audits == []                   # healthy path: no audit noise


# ── broker unavailable -> candle fallback ───────────────────────────────────

def test_quote_zero_uses_fresh_candle_and_audits():
    # 11-16 shape: broker returns 0.0 -> NOT a price; fresh candle (120s old) wins.
    mgr, audits = _mgr(_QuotePort(0.0), candle=(NOW_S - 120, 714.29))
    assert _run(mgr._spot("SPY")) == 714.29
    assert len(audits) == 1
    kind, p = audits[0]
    assert kind == "mace_spot_fallback"
    assert p["symbol"] == "SPY" and p["source"] == "mace_candle"
    assert p["bar_age_sec"] == 120 and p["broker_reason"] == "non_positive"


def test_quote_raises_uses_fresh_candle():
    mgr, audits = _mgr(_RaisingPort(None), candle=(NOW_S - 300, 713.5))
    assert _run(mgr._spot("SPY")) == 713.5
    assert audits[0][0] == "mace_spot_fallback"
    assert audits[0][1]["broker_reason"] == "error"


def test_stale_candle_leaves_spot_unavailable():
    # Bar older than the 600s default -> no price, audited unavailable.
    mgr, audits = _mgr(_QuotePort(0.0), candle=(NOW_S - 601, 714.29))
    assert _run(mgr._spot("SPY")) is None
    assert len(audits) == 1
    kind, p = audits[0]
    assert kind == "mace_spot_unavailable"
    assert p["symbol"] == "SPY" and p["reason"] == "non_positive"
    assert p["candle"] == "stale" and p["bar_age_sec"] == 601


def test_no_candle_leaves_spot_unavailable():
    mgr, audits = _mgr(_RaisingPort(None), candle=None)
    assert _run(mgr._spot("SPY")) is None
    kind, p = audits[0]
    assert kind == "mace_spot_unavailable"
    assert p["reason"] == "error" and p["candle"] == "missing"
    assert p["bar_age_sec"] is None


def test_timeout_path_audited():
    # quote hangs past the bound; no candle -> unavailable with reason=timeout.
    mgr, audits = _mgr(_HangingPort(None), timeout=0.2, candle=None)
    assert _run(mgr._spot("SPY")) is None
    kind, p = audits[0]
    assert kind == "mace_spot_unavailable" and p["reason"] == "timeout"


# ── one audit row per symbol per tick ───────────────────────────────────────

def test_one_audit_row_per_symbol_per_tick():
    mgr, audits = _mgr(_RaisingPort(None), candle=None)
    _run(mgr._spot("SPY"))
    _run(mgr._spot("SPY"))                # e.g. the CLOSING self-heal re-call
    assert len(audits) == 1               # capped within the tick
    mgr._spot_audited.clear()             # manage_tick start clears the cap
    _run(mgr._spot("SPY"))
    assert len(audits) == 2               # next tick may audit again


# ── structural stop: arms normally on spot from either source ───────────────

SPEC = CondorSpec(symbol="SPY", expiry=date(2026, 12, 24), short_put=725.0,
                  long_put=720.0, short_call=790.0, long_call=795.0,
                  width_dollars=5.0)
MGMT = SimpleNamespace(stop_multiple=2.0)


def _rung():
    return RungState(rung_id="r-spy", symbol="SPY", status="open",
                     expiry=SPEC.expiry, spec=SPEC, width_dollars=5.0,
                     contracts=1, credit_actual=1.51)


def test_structural_stop_arms_on_broker_spot():
    # mark unpriceable + short put ITM on a broker-sourced spot -> fires (confirmed).
    assert st.stop_triggered(_rung(), None, 714.29, MGMT, structural_confirmed=True)


def test_structural_stop_arms_on_candle_spot():
    # The candle-sourced value is an ordinary float to the strategy — same result.
    mgr, _ = _mgr(_QuotePort(0.0), candle=(NOW_S - 120, 714.29))
    spot = _run(mgr._spot("SPY"))
    assert st.stop_triggered(_rung(), None, spot, MGMT, structural_confirmed=True)


def test_spot_guard_still_refuses_zero():
    # 11-16 guard intact: zero/negative spot is a data failure, never a stop.
    assert not st.stop_triggered(_rung(), None, 0.0, MGMT, structural_confirmed=True)
    assert not st.stop_triggered(_rung(), None, -1.0, MGMT, structural_confirmed=True)
    # And _spot itself never RETURNS 0.0: broker 0.0 with no candle -> None.
    mgr, _ = _mgr(_QuotePort(0.0), candle=None)
    assert _run(mgr._spot("SPY")) is None


# ── integration: candle spot reaches the live row through a real manage tick ─

def _store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(dbmod.SCHEMA)
    return ex.RungStore(conn)


class _SnapShim:
    def __init__(self, mark):
        self._mark = mark

    @property
    def mark(self):
        return self._mark

    @property
    def stop_mark(self):
        return self._mark

    @property
    def wings_two_sided(self):
        return True

    @property
    def natural_debit(self):
        return self._mark

    @property
    def leg_mids(self):
        return {"sp": None, "lp": None, "sc": None, "lc": None}


class _MarkExec:
    def __init__(self, mark_val=0.71):
        self.mark_val = mark_val
        self.closed = []

    async def mark(self, spec):
        return self.mark_val

    async def quote_snapshot(self, spec):
        return _SnapShim(self.mark_val)

    async def close_rung(self, rung, reason, **kw):
        self.closed.append((rung.rung_id, reason))
        return ex.ExitOutcome(rung.rung_id, True, reason=reason)


@pytest.mark.asyncio
async def test_candle_fallback_spot_reaches_live_row(tmp_path):
    cfg = _cfg(tmp_path, ["SPY"], ["SPY"])
    assert cfg.management.spot_candle_max_age_sec == 600   # parse default flowed
    store = _store()
    spec = CondorSpec("SPY", date(2026, 9, 25), 742.0, 739.0, 802.0, 805.0, 3.0)
    rid = spec.rung_id(date(2026, 8, 12))
    store.insert_submitting(rid, spec, 1, entry_ts="2026-08-04T13:31:00+00:00",
                            entry_iso_week=iso_week(date(2026, 8, 12)),
                            max_risk_usd=207.0)
    store.promote_open(rid, credit_actual=0.93, entry_order_id="O1",
                       entry_ts="2026-08-04T13:31:00+00:00")
    now_utc = datetime(2026, 8, 12, 19, 50, tzinfo=UTC)
    # Freshest candle 120s before "now"; broker quote raises.
    store.conn.execute(
        "INSERT INTO mace_candle (symbol, interval, bar_time, open, high, low, "
        "close, volume, vwap, ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("SPY", "5m", int(now_utc.timestamp()) - 120,
         772.0, 772.5, 771.5, 772.25, 1000.0, 772.1, "2026-08-12T19:48:00+00:00"))
    audits = []
    mgr = MaceManager(
        cfg, port=_RaisingPort(None), store=store, executor=_MarkExec(0.71),
        notifier=MaceNotifier(channel=None, enabled=False),
        fetch_metrics=None, auto_execute_fn=lambda: True,
        audit=lambda k, **p: audits.append((k, p)),
        now_utc_fn=lambda: now_utc,
        now_et_fn=lambda: datetime(2026, 8, 12, 15, 50, tzinfo=UTC))

    await mgr.manage_tick(datetime(2026, 8, 12, 15, 50, tzinfo=UTC))

    row = store.conn.execute(
        "SELECT spot FROM mace_rung_live WHERE rung_id=?", (rid,)).fetchone()
    assert row is not None and row["spot"] == 772.25       # candle close, not NULL
    fallbacks = [p for k, p in audits if k == "mace_spot_fallback"]
    assert len(fallbacks) == 1                             # one row, one symbol, one tick
    assert fallbacks[0]["bar_age_sec"] == 120
    assert not any(k == "mace_spot_unavailable" for k, _ in audits)
