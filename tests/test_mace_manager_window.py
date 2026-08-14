"""OQ-2 entry-window serialization (Board-approved 2026-08-13).

The manager's placement loop must: (1) order entered primaries highest-IVR
first (missing IVR = -1.0, stable sort keeps config order), (2) give each
symbol a DYNAMIC deadline = now + (cutoff - now)/symbols_remaining recomputed
from the actual clock (early finishers donate unused window forward), (3) skip
a symbol whose turn arrives with no window left via an audited
mace_entry_window_skip (never silent starvation), (4) survive one symbol's
ladder failing mid-flight (others still run), and (5) still catch the
reserve/dup gates on the per-placement recheck (the 08-12 dup-entry fix) under
the new ordering.
"""
import sqlite3
from datetime import date, datetime, time as dtime, timezone

import pytest
import yaml

from trading_corp.mace import execution as ex
from trading_corp.mace import strategy as st
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import OptionQuote, SKIP_RESERVE, iso_week
from trading_corp.mace.ivr_provider import FIELD_RANK, FIELD_UPDATED_AT
from trading_corp.mace.manager import MaceManager
from trading_corp.mace.notify import MaceNotifier
from trading_corp.persistence import db as dbmod

from tests.test_mace_overflow_dup_entry import EXDIV_YAML, MACE_YAML, _chain

UTC = timezone.utc
SESSION = date(2026, 8, 12)


def _cfg(tmp_path, universe, enable, mutate=None):
    d = yaml.safe_load(MACE_YAML.read_text(encoding="utf-8"))
    d["entry"]["enforce_risk_band"] = False
    d["universe"] = universe
    for name, blk in d["symbols"].items():
        blk["enabled"] = name in enable      # explicit both ways — survives shipped-yaml flips
    if mutate:
        mutate(d)
    p = tmp_path / "mace.yaml"
    p.write_text(yaml.safe_dump(d), encoding="utf-8")
    return load_mace_config(p, exdiv_calendar_path=EXDIV_YAML)


# Healthy condors for all three (credit 1.0 >= floor 0.30*w): SPY/GLD w3, USO w2.
_SPY = lambda: _chain("SPY", 742, 802, 3, 2.0, 1.5, 772.0)   # noqa: E731
_GLD = lambda: _chain("GLD", 244, 256, 3, 2.0, 1.5, 250.0)   # noqa: E731
_USO = lambda: _chain("USO", 70, 80, 2, 1.5, 1.0, 75.0)      # noqa: E731

# Tasty ranks are 0-1 (x100 trap): SPY 30 / GLD 90 / USO 60 -> IVR order GLD, USO, SPY.
_RANKS = {"SPY": 0.30, "GLD": 0.90, "USO": 0.60}


def _metrics(symbols):
    return [{"symbol": s, FIELD_RANK: _RANKS[s],
             FIELD_UPDATED_AT: datetime(2026, 8, 12, 18, 0, tzinfo=UTC)}
            for s in symbols]


class _Clock:
    """Pops one datetime per now_et call; sticks on the last."""

    def __init__(self, *times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0) if len(self.times) > 1 else self.times[0]


def _t(h, mi, s=0):
    return datetime(2026, 8, 12, h, mi, s, tzinfo=UTC)


class _FakePort:
    def __init__(self, chains):
        self._chains = chains

    async def chain(self, sym):
        return self._chains.get(sym) or st.ChainView(sym, None, (), {})


class _CaptureExecutor:
    """Records (symbol, deadline); scriptable per-symbol behavior."""

    def __init__(self, raise_on=(), standdown_on=(), book_into=None):
        self.calls = []                       # (symbol, deadline) in placement order
        self.raise_on = set(raise_on)
        self.standdown_on = set(standdown_on)
        self.book_into = book_into            # RungStore: insert a rung on fill (reserve test)

    async def run_entry(self, ev, session_date, *, deadline=None, halt_fn=None):
        self.calls.append((ev.symbol, deadline))
        if ev.symbol in self.raise_on:
            raise RuntimeError(f"{ev.symbol} ladder blew up mid-flight")
        if ev.symbol in self.standdown_on:
            return ex.EntryOutcome(ev.spec.rung_id(session_date), False, attempts=0,
                                   standdown_reason="risk_reject")
        if self.book_into is not None:
            rid = ev.spec.rung_id(session_date)
            self.book_into.insert_submitting(
                rid, ev.spec, ev.contracts, entry_ts="2026-08-12T19:46:00+00:00",
                entry_iso_week=iso_week(session_date), max_risk_usd=ev.max_risk_usd)
        return ex.EntryOutcome(ev.spec.rung_id(session_date), True,
                               credit=ev.credit_mid, attempts=1)


def _mgr(cfg, execu, *, now_et_fn, fetch_metrics=_metrics, store=None, audits=None):
    if store is None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(dbmod.SCHEMA)
        conn.execute("INSERT INTO mace_equity_snapshot(snap_date,equity,ts) VALUES(?,?,?)",
                     ("2026-08-12", 10_000.0, "2026-08-12T19:40:00+00:00"))
        store = ex.RungStore(conn)
    return MaceManager(
        cfg, port=_FakePort({"SPY": _SPY(), "GLD": _GLD(), "USO": _USO()}),
        store=store, executor=execu,
        notifier=MaceNotifier(channel=None, enabled=False),
        fetch_metrics=fetch_metrics, auto_execute_fn=lambda: True,
        audit=(lambda kind, **p: audits.append((kind, p))) if audits is not None else None,
        now_utc_fn=lambda: datetime(2026, 8, 12, 19, 45, tzinfo=UTC),
        now_et_fn=now_et_fn)


# ── ordering + dynamic budget ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ivr_desc_order_and_deadlines_donate_forward(tmp_path):
    cfg = _cfg(tmp_path, ["SPY", "GLD", "USO"], ["SPY", "GLD", "USO"])
    execu = _CaptureExecutor()
    # one now_et pop per placement: 15:46 -> 15:47 -> 15:48 (cutoff 15:58)
    mgr = _mgr(cfg, execu, now_et_fn=_Clock(_t(15, 46), _t(15, 47), _t(15, 48)))
    await mgr.evaluate_and_enter(SESSION)

    assert [s for s, _ in execu.calls] == ["GLD", "USO", "SPY"]     # IVR 90 > 60 > 30
    deadlines = [d.time() for _, d in execu.calls]
    # 15:46 + 720/3 = 15:50 ; 15:47 + 660/2 = 15:52:30 ; 15:48 + 600/1 = 15:58
    assert deadlines == [dtime(15, 50), dtime(15, 52, 30), dtime(15, 58)]
    assert deadlines == sorted(deadlines)                           # monotonic
    assert deadlines[-1] == dtime(15, 58)   # last symbol inherits the FULL remaining window


@pytest.mark.asyncio
async def test_missing_ivr_falls_back_to_config_order(tmp_path):
    cfg = _cfg(tmp_path, ["SPY", "GLD", "USO"], ["SPY", "GLD", "USO"])
    execu = _CaptureExecutor()
    mgr = _mgr(cfg, execu, now_et_fn=_Clock(_t(15, 46)), fetch_metrics=None)
    await mgr.evaluate_and_enter(SESSION)
    # all ivr_value None -> every key -1.0 -> stable sort == universe order
    assert [s for s, _ in execu.calls] == ["SPY", "GLD", "USO"]


# ── window exhaustion: audited skip, never silent ────────────────────────────

@pytest.mark.asyncio
async def test_window_exhausted_symbol_skipped_with_audit(tmp_path):
    cfg = _cfg(tmp_path, ["SPY", "GLD", "USO"], ["SPY", "GLD", "USO"])
    execu = _CaptureExecutor()
    audits = []
    # third placement's turn arrives AT the 15:58 cutoff -> remaining <= 0
    mgr = _mgr(cfg, execu, audits=audits,
               now_et_fn=_Clock(_t(15, 46), _t(15, 47), _t(15, 58)))
    res = await mgr.evaluate_and_enter(SESSION)

    assert [s for s, _ in execu.calls] == ["GLD", "USO"]            # SPY never laddered
    skips = [p for k, p in audits if k == "mace_entry_window_skip"]
    assert skips == [{"symbol": "SPY", "position": 3, "of": 3,
                      "reason": "window_exhausted"}]
    assert len(res.outcomes) == 2


# ── one ladder fails mid-flight; others proceed ──────────────────────────────

@pytest.mark.asyncio
async def test_ladder_exception_on_second_symbol_third_still_runs(tmp_path):
    cfg = _cfg(tmp_path, ["SPY", "GLD", "USO"], ["SPY", "GLD", "USO"])
    execu = _CaptureExecutor(raise_on={"USO"})                      # symbol 2 of 3
    audits = []
    mgr = _mgr(cfg, execu, audits=audits,
               now_et_fn=_Clock(_t(15, 46), _t(15, 47), _t(15, 48)))
    res = await mgr.evaluate_and_enter(SESSION)

    assert [s for s, _ in execu.calls] == ["GLD", "USO", "SPY"]     # SPY still ran
    errs = [p for k, p in audits if k == "mace_entry_exception"]
    assert len(errs) == 1 and errs[0]["symbol"] == "USO"
    assert len(res.outcomes) == 2                                   # GLD + SPY booked outcomes


@pytest.mark.asyncio
async def test_risk_reject_standdown_on_second_symbol_third_still_runs(tmp_path):
    cfg = _cfg(tmp_path, ["SPY", "GLD", "USO"], ["SPY", "GLD", "USO"])
    execu = _CaptureExecutor(standdown_on={"USO"})
    mgr = _mgr(cfg, execu, now_et_fn=_Clock(_t(15, 46), _t(15, 47), _t(15, 48)))
    res = await mgr.evaluate_and_enter(SESSION)

    assert [s for s, _ in execu.calls] == ["GLD", "USO", "SPY"]
    reasons = {o.rung_id: o.standdown_reason for o in res.outcomes if not o.filled}
    assert list(reasons.values()) == ["risk_reject"]                # clean, recorded
    assert sum(1 for o in res.outcomes if o.filled) == 2


# ── reserve cap binds mid-eval via the fresh-rungs recheck ───────────────────

@pytest.mark.asyncio
async def test_reserve_binds_after_first_fill_second_superseded(tmp_path):
    # equity 350, deployment_target 0.80 -> cap 280. Max-risks: GLD/SPY w3 = 200,
    # USO w2 = 100. All three pass the primary eval (each alone fits); GLD (IVR
    # 90) ladders first and books its rung; USO's RECHECK sees it (200+100 > 280)
    # and SPY's too (200+200 > 280) -> both superseded SKIP_RESERVE.
    def bump(d):
        d["sizing"]["rung_risk_pct"] = 0.8      # keep budget filter out of the way
        d["sizing"]["deployment_target_pct"] = 0.80   # pin the 280-cap math (shipped 0.95 -> 332.5 would fit USO)
    cfg = _cfg(tmp_path, ["SPY", "GLD", "USO"], ["SPY", "GLD", "USO"], mutate=bump)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(dbmod.SCHEMA)
    conn.execute("INSERT INTO mace_equity_snapshot(snap_date,equity,ts) VALUES(?,?,?)",
                 ("2026-08-12", 350.0, "2026-08-12T19:40:00+00:00"))
    store = ex.RungStore(conn)
    execu = _CaptureExecutor(book_into=store)                       # a fill lands a rung
    audits = []
    mgr = _mgr(cfg, execu, store=store, audits=audits,
               now_et_fn=_Clock(_t(15, 46), _t(15, 47), _t(15, 48)))
    res = await mgr.evaluate_and_enter(SESSION)

    assert len(execu.calls) == 1                                    # ONLY the first laddered
    sup = [p for k, p in audits if k == "mace_entry_superseded"]
    assert len(sup) == 2 and all(p["skip_reason"] == SKIP_RESERVE for p in sup)
    assert len(res.outcomes) == 1 and res.outcomes[0].filled
