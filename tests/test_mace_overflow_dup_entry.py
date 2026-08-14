"""Regression: the 2026-08-12 live duplicate-SPY-entry.

Overflow routing re-routed a forfeited 2nd symbol (GLD) onto the just-entered
primary (SPY), firing a duplicate order (RH rejected it on the duplicate ref_id).
The router must NOT re-route to a symbol that entered this round; the manager must
fire exactly one placement; the legitimate IBIT overflow path must still work; and
every symbol's eval decision must be audited (per-symbol observability).
"""
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

from trading_corp.mace import execution as ex
from trading_corp.mace import strategy as st
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import EvalResult, OptionQuote, SKIP_CREDIT_FLOOR
from trading_corp.mace.manager import MaceManager
from trading_corp.mace.notify import MaceNotifier
from trading_corp.persistence import db as dbmod

UTC = timezone.utc
EXPIRY = date(2026, 9, 25)
SESSION = date(2026, 8, 12)
NEXT = date(2026, 8, 13)
ROOT = Path(__file__).resolve().parent.parent
MACE_YAML = ROOT / "config" / "mace.yaml"
EXDIV_YAML = ROOT / "config" / "ex_dividend_calendar.yaml"


def _chain(sym, sp, sc, width, short_mid, long_mid, spot):
    """A 0.20-delta condor chain: short strikes sp/sc, wings +/- width."""
    dmap = {sp - width: -0.15, sp: -0.20, sc: 0.20, sc + width: 0.15}
    legs = [("put", sp - width, long_mid), ("put", sp, short_mid),
            ("call", sc, short_mid), ("call", sc + width, long_mid)]
    quotes = {(EXPIRY, o, float(k)): OptionQuote(sym, EXPIRY, float(k), o,
              m - 0.05, m + 0.05, delta=dmap[k]) for o, k, m in legs}
    return st.ChainView(sym, spot, (EXPIRY,), quotes)


def _cfg(tmp_path, universe, enable):
    d = yaml.safe_load(MACE_YAML.read_text(encoding="utf-8"))
    d["entry"]["enforce_risk_band"] = False        # isolate credit-floor as GLD's skip
    d["universe"] = universe
    for s in enable:
        d["symbols"][s]["enabled"] = True
    p = tmp_path / "mace.yaml"
    p.write_text(yaml.safe_dump(d), encoding="utf-8")
    return load_mace_config(p, exdiv_calendar_path=EXDIV_YAML)


def _ctx(chains, rungs=(), equity=10_000.0):
    return st.EntryContext(session_date=SESSION, equity=equity, rungs=list(rungs),
                           events=[], ivr={}, chains=chains, next_session_date=NEXT)


# SPY builds a healthy condor (credit 1.0 >= 0.90 floor); GLD's credit (0.10) is
# below its 0.90 floor -> SKIP_CREDIT_FLOOR, which is a FORFEITING skip.
_SPY = lambda: _chain("SPY", 742, 802, 3, 2.0, 1.5, 772.0)   # noqa: E731
_GLD = lambda: _chain("GLD", 244, 256, 3, 0.5, 0.45, 250.0)  # noqa: E731
_IBIT = lambda: _chain("IBIT", 54, 66, 2, 0.7, 0.3, 60.0)    # noqa: E731


# ── pure route_overflow: today's exact scenario ──────────────────────────────
def test_router_does_not_reroute_to_entered_primary(tmp_path):
    cfg = _cfg(tmp_path, ["SPY", "GLD"], ["GLD"])          # IBIT stays disabled
    prim = [EvalResult(symbol="SPY", entered=True),
            EvalResult(symbol="GLD", entered=False, skip_reason=SKIP_CREDIT_FLOOR)]
    ctx = _ctx({"SPY": _SPY(), "GLD": _GLD()})
    assert st.route_overflow(prim, cfg, ctx) == []         # NOT re-routed to SPY


# ── pure route_overflow: legit IBIT receiver still works ──────────────────────
def test_router_still_routes_to_eligible_ibit(tmp_path):
    cfg = _cfg(tmp_path, ["SPY", "GLD"], ["GLD", "IBIT"])
    prim = [EvalResult(symbol="SPY", entered=True),
            EvalResult(symbol="GLD", entered=False, skip_reason=SKIP_CREDIT_FLOOR)]
    ctx = _ctx({"SPY": _SPY(), "GLD": _GLD(), "IBIT": _IBIT()})
    out = st.route_overflow(prim, cfg, ctx)
    assert len(out) == 1 and out[0].symbol == "IBIT" and out[0].overflow


# ── end-to-end manager: exactly one placement, both evals audited ─────────────
class _FakePort:
    def __init__(self, chains):
        self._chains = chains

    async def chain(self, sym):
        return self._chains.get(sym) or st.ChainView(sym, None, (), {})


class _FakeExecutor:
    def __init__(self):
        self.entries = []

    async def run_entry(self, ev, session_date, **kw):  # kw: OQ-2 deadline=
        self.entries.append(ev.symbol)
        return ex.EntryOutcome(ev.spec.rung_id(session_date), True,
                               credit=ev.credit_mid, attempts=1)


@pytest.mark.asyncio
async def test_manager_places_once_when_second_symbol_forfeits(tmp_path):
    cfg = _cfg(tmp_path, ["SPY", "GLD"], ["GLD"])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(dbmod.SCHEMA)
    conn.execute("INSERT INTO mace_equity_snapshot(snap_date,equity,ts) VALUES(?,?,?)",
                 ("2026-08-12", 10_000.0, "2026-08-12T19:40:00+00:00"))
    execu = _FakeExecutor()
    audits = []
    mgr = MaceManager(
        cfg, port=_FakePort({"SPY": _SPY(), "GLD": _GLD()}),
        store=ex.RungStore(conn), executor=execu,
        notifier=MaceNotifier(channel=None, enabled=False),
        fetch_metrics=None, auto_execute_fn=lambda: True,
        audit=lambda kind, **p: audits.append((kind, p)),
        now_utc_fn=lambda: datetime(2026, 8, 12, 19, 45, tzinfo=UTC),
        now_et_fn=lambda: datetime(2026, 8, 12, 15, 45, tzinfo=UTC))

    await mgr.evaluate_and_enter(SESSION)

    assert execu.entries == ["SPY"]                    # exactly one, no duplicate SPY
    evals = {p["symbol"]: p for k, p in audits if k == "mace_entry_eval"}
    assert evals["SPY"]["entered"] is True
    assert evals["GLD"]["entered"] is False
    assert evals["GLD"]["skip_reason"] == SKIP_CREDIT_FLOOR   # per-symbol observability
