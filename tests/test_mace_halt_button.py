"""/mace entry-HALT button (Board-added 2026-08-13) — the ONE write surface.

The latch (`agent_state robinhood_mace/entry_halt`) has auto_execute:false
semantics: it halts NEW entries at the next symbol/attempt boundary and NOTHING
else. The Board-required proofs, in order: (a) a mid-round latch stops the next
symbol with an audit; (b) the executor stands down `operator_halt` per attempt
with precedence cutoff > operator_halt > window_budget, and never recalls
in-flight work (honest latency); (c) manage/exits still run while halted; (d)
the endpoints audit BEFORE the state flip (CLAUDE.md #2); (e) the tri-state
pill renders ARMED / HALTED (button) / HALTED (config). The engine read is
FAIL-SAFE: absent row or read error = NOT halted (auto_execute stays the
primary kill).
"""
import json
import sqlite3
from datetime import date

import pytest

from trading_corp.mace import broker_port as bp
from trading_corp.mace import execution as ex
from trading_corp.mace.domain import CondorSpec, iso_week
from trading_corp.persistence import db as dbmod

from tests.test_mace_execution import (
    FakePort, RecChannel, _conn, _entry_quotes, _et, _ev, _executor, _res,
    SESSION as EX_SESSION, RUNG_ID as EX_RUNG_ID,
)
from tests.test_mace_manager_window import (
    _cfg, _Clock, _mgr, _t, _CaptureExecutor, SESSION as MGR_SESSION,
)


def _write_latch(conn, halted=True):
    """Write the latch the way the web endpoint does (upsert into agent_state)."""
    conn.execute(
        "INSERT INTO agent_state(agent,key,value_json,updated_ts) VALUES(?,?,?,?) "
        "ON CONFLICT(agent,key) DO UPDATE SET value_json=excluded.value_json, "
        "updated_ts=excluded.updated_ts",
        ("robinhood_mace", "entry_halt",
         json.dumps({"halted": halted, "ts": "2026-08-12T19:45:00+00:00",
                     "source": "dashboard_button"}),
         "2026-08-12T19:45:00+00:00"))


def _mgr_conn(equity=10_000.0):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(dbmod.SCHEMA)
    conn.execute("INSERT INTO mace_equity_snapshot(snap_date,equity,ts) VALUES(?,?,?)",
                 ("2026-08-12", equity, "2026-08-12T19:40:00+00:00"))
    return conn


class _LatchAfterFirst(_CaptureExecutor):
    """Writes the halt latch into the manager's own conn AFTER the first ladder
    — the operator clicking HALT while symbol 1 is in flight."""

    def __init__(self, conn, **kw):
        super().__init__(**kw)
        self._conn = conn

    async def run_entry(self, ev, session_date, *, deadline=None, halt_fn=None,
                        entry_atm_iv=None):
        out = await super().run_entry(ev, session_date, deadline=deadline)
        _write_latch(self._conn, True)
        return out


# ── (a) manager: latch halts the NEXT symbol mid-round, with audit ───────────

@pytest.mark.asyncio
async def test_latch_mid_round_halts_next_symbol_with_audit(tmp_path):
    cfg = _cfg(tmp_path, ["SPY", "GLD", "USO"], ["SPY", "GLD", "USO"])
    conn = _mgr_conn()
    store = ex.RungStore(conn)
    execu = _LatchAfterFirst(conn)
    audits = []
    mgr = _mgr(cfg, execu, store=store, audits=audits,
               now_et_fn=_Clock(_t(15, 46), _t(15, 47), _t(15, 48)))
    res = await mgr.evaluate_and_enter(MGR_SESSION)

    assert [s for s, _ in execu.calls] == ["GLD"]        # IVR-first symbol only
    halts = [p for k, p in audits if k == "mace_entry_halted_midround"]
    assert halts == [{"remaining": ["USO", "SPY"], "reason": "operator_halt_latch"}]
    assert len(res.outcomes) == 1                        # symbol 1's outcome kept


@pytest.mark.asyncio
async def test_latch_at_round_start_no_placements_evals_still_audited(tmp_path):
    cfg = _cfg(tmp_path, ["SPY", "GLD", "USO"], ["SPY", "GLD", "USO"])
    conn = _mgr_conn()
    _write_latch(conn, True)                             # halted BEFORE the round
    store = ex.RungStore(conn)
    execu = _CaptureExecutor()
    audits = []
    mgr = _mgr(cfg, execu, store=store, audits=audits, now_et_fn=_Clock(_t(15, 46)))
    res = await mgr.evaluate_and_enter(MGR_SESSION)

    assert execu.calls == [] and res.outcomes == []
    halted = [p for k, p in audits if k == "mace_entry_halted"]
    assert halted == [{"reason": "operator_halt_latch", "entered": 3}]
    # evals still audited — a halted round stays diagnosable
    assert sum(1 for k, _ in audits if k == "mace_entry_eval") == 3


@pytest.mark.asyncio
async def test_latch_read_error_fails_safe_not_halted(tmp_path):
    # agent_state table GONE -> read raises -> NOT halted (fail-safe; the latch
    # only ever ADDS a halt — auto_execute stays the primary kill).
    cfg = _cfg(tmp_path, ["SPY", "GLD", "USO"], ["SPY", "GLD", "USO"])
    conn = _mgr_conn()
    conn.execute("DROP TABLE agent_state")
    execu = _CaptureExecutor()
    mgr = _mgr(cfg, execu, store=ex.RungStore(conn),
               now_et_fn=_Clock(_t(15, 46), _t(15, 47), _t(15, 48)))
    await mgr.evaluate_and_enter(MGR_SESSION)
    assert [s for s, _ in execu.calls] == ["GLD", "USO", "SPY"]


@pytest.mark.asyncio
async def test_cleared_latch_runs_normally(tmp_path):
    cfg = _cfg(tmp_path, ["SPY", "GLD", "USO"], ["SPY", "GLD", "USO"])
    conn = _mgr_conn()
    _write_latch(conn, False)                            # armed via the ARM button
    execu = _CaptureExecutor()
    mgr = _mgr(cfg, execu, store=ex.RungStore(conn),
               now_et_fn=_Clock(_t(15, 46), _t(15, 47), _t(15, 48)))
    await mgr.evaluate_and_enter(MGR_SESSION)
    assert [s for s, _ in execu.calls] == ["GLD", "USO", "SPY"]


# ── (b) executor: per-attempt operator_halt + reason precedence ──────────────

class _SeqFlag:
    """Pops one bool per halt_fn() call; sticks on the last."""

    def __init__(self, *vals):
        self.vals = list(vals)

    def __call__(self):
        return self.vals.pop(0) if len(self.vals) > 1 else self.vals[0]


@pytest.mark.asyncio
async def test_executor_halt_stands_down_before_placing():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    out = await _executor(port, store, chan).run_entry(
        _ev(), EX_SESSION, deadline=_et(15, 57), halt_fn=lambda: True)
    assert not out.filled and out.standdown_reason == "operator_halt"
    assert store.get(EX_RUNG_ID) is None                 # clean: anchor deleted
    assert port.place_calls == []                        # nothing ever placed


@pytest.mark.asyncio
async def test_cutoff_wins_reason_over_operator_halt():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    out = await _executor(port, store, chan, et_h=15, et_mi=59).run_entry(
        _ev(), EX_SESSION, halt_fn=lambda: True)
    assert out.standdown_reason == "cutoff"              # global cutoff always wins


@pytest.mark.asyncio
async def test_operator_halt_wins_reason_over_window_budget():
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    out = await _executor(port, store, chan, et_h=15, et_mi=47).run_entry(
        _ev(), EX_SESSION, deadline=_et(15, 46), halt_fn=lambda: True)
    assert out.standdown_reason == "operator_halt"       # deliberate action > timeout


@pytest.mark.asyncio
async def test_mid_ladder_halt_stands_down_after_confirmed_dead():
    # Attempt 1 places + cancels + CONFIRMS dead; the operator halts before
    # attempt 2 -> clean operator_halt stand-down, exactly one place.
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    port.place_script = [_res(bp.STATE_QUEUED, "O1")]
    port.status_script = {"O1": _res(bp.STATE_CANCELLED, "O1")}
    out = await _executor(port, store, chan).run_entry(
        _ev(), EX_SESSION, halt_fn=_SeqFlag(False, True))
    assert not out.filled and out.standdown_reason == "operator_halt"
    assert out.attempts == 1 and len(port.place_calls) == 1
    assert store.get(EX_RUNG_ID) is None                 # attempt 1 dead -> clean


@pytest.mark.asyncio
async def test_halt_never_recalls_inflight_fill_honest_latency():
    # The resting order fills during its cancel-and-confirm cycle while the
    # operator halts -> the fill BOOKS (the fake-fill guard's confirmed `filled`
    # path). The button's stated latency is honest: it cannot recall a resting
    # order, only stop the NEXT attempt/symbol.
    conn = _conn(); store = ex.RungStore(conn); port = FakePort(); chan = RecChannel()
    _entry_quotes(port)
    port.place_script = [_res(bp.STATE_QUEUED, "O1")]
    port.status_script = {"O1": _res(bp.STATE_FILLED, "O1")}
    out = await _executor(port, store, chan).run_entry(
        _ev(), EX_SESSION, halt_fn=_SeqFlag(False, True))
    assert out.filled and store.get(EX_RUNG_ID).status == "open"


# ── (c) Board proof: manage/exits still run while halted ─────────────────────

class _ManageExecutor(_CaptureExecutor):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.closed = []

    async def close_rung(self, rung, reason):
        self.closed.append((rung.rung_id, reason))
        return ex.ExitOutcome(rung.rung_id, True, reason=reason)


@pytest.mark.asyncio
async def test_manage_exits_still_run_while_halted(tmp_path):
    cfg = _cfg(tmp_path, ["SPY", "GLD", "USO"], ["SPY", "GLD", "USO"])
    conn = _mgr_conn()
    store = ex.RungStore(conn)
    spec = CondorSpec("SPY", date(2026, 9, 25), 742.0, 739.0, 802.0, 805.0, 3.0)
    rid = spec.rung_id(MGR_SESSION)
    store.insert_submitting(rid, spec, 1, entry_ts="2026-08-12T19:00:00+00:00",
                            entry_iso_week=iso_week(MGR_SESSION), max_risk_usd=200.0)
    conn.execute("UPDATE mace_rung SET status='closing', exit_reason='stop' "
                 "WHERE rung_id=?", (rid,))
    _write_latch(conn, True)                             # HALTED

    execu = _ManageExecutor()
    mgr = _mgr(cfg, execu, store=store, now_et_fn=_Clock(_t(15, 50)))

    outs = await mgr.manage_tick(_t(15, 50))             # exits: UNAFFECTED
    assert execu.closed == [(rid, "stop")]
    assert len(outs) == 1 and outs[0].closed

    res = await mgr.evaluate_and_enter(MGR_SESSION)      # entries: BLOCKED
    assert execu.calls == [] and res.outcomes == []


# ── (d)+(e) web endpoints: audit-before-state + tri-state render ─────────────

from tests.test_mace_web_wiring import _app, _scratch_db  # noqa: E402


def _division(auto=True):
    from types import SimpleNamespace
    return SimpleNamespace(standby=False, enabled=True, auto_execute=auto,
                           has_manager=True)


def test_halt_routes_registered(tmp_path):
    app = _app(_scratch_db(tmp_path))
    paths = {r.path for r in app.routes}
    assert {"/mace/partials/halt", "/mace/halt", "/mace/arm"} <= paths


def test_tri_state_renders_and_latch_is_durable(tmp_path):
    from fastapi.testclient import TestClient
    db_url = _scratch_db(tmp_path)
    c = TestClient(_app(db_url, mace_division=_division(auto=True)))

    r = c.get("/mace/partials/halt")
    assert r.status_code == 200 and "ENTRIES: ARMED" in r.text
    assert 'hx-post="/mace/halt"' in r.text              # HALT button offered

    r = c.post("/mace/halt")
    assert "HALTED (button)" in r.text and 'hx-post="/mace/arm"' in r.text
    got = dbmod.load_agent_state("robinhood_mace", "entry_halt", db_url=db_url)
    assert got is not None and got[0]["halted"] is True  # durable latch

    r = c.post("/mace/arm")
    assert "ENTRIES: ARMED" in r.text
    got = dbmod.load_agent_state("robinhood_mace", "entry_halt", db_url=db_url)
    assert got[0]["halted"] is False

    # auto_execute=false in config outranks the latch in the DISPLAY too
    c2 = TestClient(_app(db_url, mace_division=_division(auto=False)))
    r = c2.get("/mace/partials/halt")
    assert "HALTED (config)" in r.text and "hx-post" not in r.text


def test_full_page_includes_halt_pill(tmp_path):
    from fastapi.testclient import TestClient
    r = TestClient(_app(_scratch_db(tmp_path),
                        mace_division=_division(True))).get("/mace")
    assert r.status_code == 200 and 'id="mace-halt"' in r.text


def test_endpoints_audit_before_state(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from trading_corp.agents.logger import LoggerAgent
    from trading_corp.web import mace_view

    db_url = _scratch_db(tmp_path)
    c = TestClient(_app(db_url, mace_division=_division(True)))
    c.get("/mace/partials/halt")                         # warm up before spying

    events = []
    real_log = LoggerAgent.log_event

    def spy_log(self, actor, kind, *a, **kw):
        events.append(("audit", actor, kind))
        return real_log(self, actor, kind, *a, **kw)

    real_set = mace_view.db.set_agent_state

    def spy_set(agent, key, value, db_url="sqlite:///data/trading_corp.db"):
        events.append(("state", value.get("halted")))
        return real_set(agent, key, value, db_url=db_url)

    monkeypatch.setattr(LoggerAgent, "log_event", spy_log)
    monkeypatch.setattr(mace_view.db, "set_agent_state", spy_set)

    c.post("/mace/halt")
    assert events == [("audit", "mace_operations", "mace_ui_halt"), ("state", True)]
    events.clear()
    c.post("/mace/arm")
    assert events == [("audit", "mace_operations", "mace_ui_arm"), ("state", False)]
