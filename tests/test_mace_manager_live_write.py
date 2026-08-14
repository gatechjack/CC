"""Tests for the manager-side UI-rebuild writes (2026-08-14, Track A):
  - A1/A2: _manage_one persists live mark+spot to mace_rung_live each tick, and
    a write failure is swallowed (never sinks a manage tick).
  - A4: the daily IV snapshot widens to defined ∪ open-rung symbols, so a
    retired-but-managed symbol (disabled + open rung) reaches mace_iv_history.
  - A3 wiring: evaluate_and_enter threads the symbol's fresh ATM IV into run_entry.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from trading_corp.mace import execution as ex
from trading_corp.mace import strategy as st
from trading_corp.mace.domain import CondorSpec, iso_week
from trading_corp.mace.ivr_provider import FIELD_ATM_IV, FIELD_RANK, FIELD_UPDATED_AT
from trading_corp.mace.manager import MaceManager
from trading_corp.mace.notify import MaceNotifier
from trading_corp.persistence import db as dbmod

from tests.test_mace_manager_window import _cfg, _Clock, _mgr, _t, _CaptureExecutor

UTC = timezone.utc
SESSION = date(2026, 8, 12)
EXPIRY = date(2026, 9, 25)


def _store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(dbmod.SCHEMA)
    return ex.RungStore(conn)


def _open_rung(store, symbol="SPY", credit=0.93):
    spec = CondorSpec(symbol, EXPIRY, 742.0, 739.0, 802.0, 805.0, 3.0)
    rid = spec.rung_id(SESSION)
    store.insert_submitting(rid, spec, 1, entry_ts="2026-08-04T13:31:00+00:00",
                            entry_iso_week=iso_week(SESSION), max_risk_usd=207.0)
    store.promote_open(rid, credit_actual=credit, entry_order_id="O1",
                       entry_ts="2026-08-04T13:31:00+00:00")
    return rid


class _FakePort:
    def __init__(self, spot=772.0):
        self._spot = spot

    async def quote(self, sym):          # _spot() prefers this
        return self._spot

    async def chain(self, sym):
        return st.ChainView(sym, None, (), {})


class _MarkExec:
    """Manage-path fake: benign mark (no exit), records any close."""
    def __init__(self, mark_val=0.71):
        self.mark_val = mark_val
        self.closed = []

    async def mark(self, spec):
        return self.mark_val

    async def close_rung(self, rung, reason):
        self.closed.append((rung.rung_id, reason))
        return ex.ExitOutcome(rung.rung_id, True, reason=reason)


def _mk(cfg, store, *, port=None, executor=None, fetch_metrics=None, audits=None):
    return MaceManager(
        cfg, port=port or _FakePort(), store=store,
        executor=executor or _MarkExec(),
        notifier=MaceNotifier(channel=None, enabled=False),
        fetch_metrics=fetch_metrics, auto_execute_fn=lambda: True,
        audit=(lambda k, **p: audits.append((k, p))) if audits is not None else None,
        now_utc_fn=lambda: datetime(2026, 8, 12, 19, 50, tzinfo=UTC),
        now_et_fn=lambda: datetime(2026, 8, 12, 15, 50, tzinfo=UTC))


# ── A1/A2: _manage_one live-state write ─────────────────────────────────────

@pytest.mark.asyncio
async def test_manage_tick_writes_live_state(tmp_path):
    cfg = _cfg(tmp_path, ["SPY"], ["SPY"])
    store = _store()
    rid = _open_rung(store, "SPY", credit=0.93)
    mgr = _mk(cfg, store, port=_FakePort(spot=778.08), executor=_MarkExec(0.71))

    await mgr.manage_tick(datetime(2026, 8, 12, 15, 50, tzinfo=UTC))

    row = store.conn.execute(
        "SELECT symbol, mark, spot, ts FROM mace_rung_live WHERE rung_id=?",
        (rid,)).fetchone()
    assert row is not None
    assert row["symbol"] == "SPY"
    assert row["mark"] == 0.71          # per-contract combo mid from executor.mark
    assert row["spot"] == 778.08        # underlying spot from port.quote
    assert row["ts"]                    # freshness stamp present


@pytest.mark.asyncio
async def test_manage_live_write_failure_never_sinks_tick(tmp_path):
    """A dashboard-write failure is logged + swallowed — the manage tick still
    completes and evaluates the exit decision (fail-safe on the loop)."""
    cfg = _cfg(tmp_path, ["SPY"], ["SPY"])
    store = _store()
    _open_rung(store, "SPY", credit=0.93)

    def _boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    store.set_live_state = _boom       # every live write raises

    audits = []
    mgr = _mk(cfg, store, port=_FakePort(), executor=_MarkExec(0.71), audits=audits)

    # must NOT raise despite the failing write
    outs = await mgr.manage_tick(datetime(2026, 8, 12, 15, 50, tzinfo=UTC))
    assert outs == []                                  # benign mark -> no exit
    assert any(k == "mace_live_state_error" for k, _ in audits)


# ── A4: snapshot widen ──────────────────────────────────────────────────────

def test_snapshot_symbols_includes_disabled_open_rung(tmp_path):
    cfg = _cfg(tmp_path, ["GLD"], ["GLD"])             # only GLD enabled
    mgr = _mk(cfg, _store())
    rungs = [SimpleNamespace(symbol="SPY", status="open"),   # disabled + open
             SimpleNamespace(symbol="GLD", status="open")]
    snap = mgr._snapshot_symbols(rungs)
    assert "SPY" in snap                               # retired-but-managed included
    assert "GLD" in snap
    assert set(mgr._enabled_symbols()).issubset(set(snap))   # superset of enabled


@pytest.mark.asyncio
async def test_a4_widen_persists_disabled_open_rung_iv(tmp_path):
    """The 15:45 snapshot must write a fresh daily ATM IV row for a disabled
    symbol that still holds open rungs (SPY) — the row must REACH the DB, not be
    filtered out downstream."""
    cfg = _cfg(tmp_path, ["GLD"], ["GLD"])             # SPY disabled
    store = _store()
    _open_rung(store, "SPY", credit=0.93)              # SPY holds an open rung

    def fetch(symbols):
        iv = {"SPY": 0.147, "GLD": 0.239}
        rank = {"SPY": 0.30, "GLD": 0.28}
        return [{"symbol": s, FIELD_RANK: rank[s], FIELD_ATM_IV: iv[s],
                 FIELD_UPDATED_AT: datetime(2026, 8, 12, 18, 0, tzinfo=UTC)}
                for s in symbols if s in iv]

    mgr = _mk(cfg, store, fetch_metrics=fetch)
    ctx = await mgr.build_entry_context(SESSION)

    row = store.conn.execute(
        "SELECT atm_iv FROM mace_iv_history WHERE symbol='SPY'").fetchone()
    assert row is not None and row["atm_iv"] == 0.147   # reached the DB
    assert "SPY" in ctx.ivr and ctx.ivr["SPY"].atm_iv == 0.147


# ── A3: entry-IV threaded into run_entry ────────────────────────────────────

class _IVCapExec(_CaptureExecutor):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.seen: dict[str, float | None] = {}

    async def run_entry(self, ev, session_date, *, deadline=None, halt_fn=None,
                        entry_atm_iv=None):
        self.seen[ev.symbol] = entry_atm_iv
        return await super().run_entry(ev, session_date, deadline=deadline)


@pytest.mark.asyncio
async def test_entry_iv_threaded_from_ctx_ivr(tmp_path):
    cfg = _cfg(tmp_path, ["SPY", "GLD", "USO"], ["SPY", "GLD", "USO"])
    execu = _IVCapExec()

    def fetch(symbols):
        iv = {"SPY": 0.14, "GLD": 0.24, "USO": 0.30}
        rank = {"SPY": 0.30, "GLD": 0.90, "USO": 0.60}
        return [{"symbol": s, FIELD_RANK: rank[s], FIELD_ATM_IV: iv[s],
                 FIELD_UPDATED_AT: datetime(2026, 8, 12, 18, 0, tzinfo=UTC)}
                for s in symbols if s in iv]

    mgr = _mgr(cfg, execu, fetch_metrics=fetch,
               now_et_fn=_Clock(_t(15, 46), _t(15, 47), _t(15, 48)))
    await mgr.evaluate_and_enter(SESSION)

    assert execu.seen == {"SPY": 0.14, "GLD": 0.24, "USO": 0.30}
