"""B-AE (2026-07-24) assignment/exercise MONITORING: pre-event risk (near-expiry
ITM shorts), events (pending_*), calm→urgent alert with the manual remedy options.
Monitoring only — HITL for any action; the engine never auto-closes.
"""
from __future__ import annotations

import pytest

from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent


def _short(sym, strike, dte, pa=0, pe=0, px=0, otype="call"):
    return {"chain_symbol": sym, "option_type": otype, "strike_price": strike,
            "expiration_date": "2026-07-25", "dte": dte, "quantity": -1.0,
            "pending_assignment_quantity": pa, "pending_exercise_quantity": pe,
            "pending_expiration_quantity": px}


def test_assignment_risk_items():
    shorts = [_short("AAA", 10.0, 1), _short("BBB", 20.0, 1), _short("CCC", 30.0, 5)]
    spots = {"AAA": 12.0, "BBB": 18.0, "CCC": 31.0}
    risk = PMCCAgent._assignment_risk_items(shorts, spots, 1)
    assert {r["symbol"] for r in risk} == {"AAA"}     # AAA ITM near; BBB OTM; CCC far-DTE
    assert risk[0]["itm"] is True


def test_assignment_risk_skips_unknown_spot():
    assert PMCCAgent._assignment_risk_items([_short("AAA", 10.0, 0)], {"AAA": None}, 1) == []


def test_assignment_event_items():
    shorts = [_short("AAA", 10.0, 0, pa=1), _short("BBB", 20.0, 0),
              _short("CCC", 30.0, 0, pe=1), _short("DDD", 40.0, 0, px=1)]
    ev = PMCCAgent._assignment_event_items(shorts)
    assert {e["symbol"] for e in ev} == {"AAA", "CCC", "DDD"}      # BBB has no pending


def test_format_alert_states_manual_remedy():
    risk = [{"symbol": "AAA", "strike": 10.0, "dte": 0, "spot": 12.0, "itm": True}]
    d = PMCCAgent._format_assignment_alert(risk, "risk")
    assert "ASSIGNMENT RISK" in d and "EXERCISE THE LEAP" in d and "BUY-TO-CLOSE" in d
    ev = [{"symbol": "AAA", "strike": 10.0, "expiration": "2026-07-25",
           "pending_assignment": 1, "pending_exercise": 0, "pending_expiration": 0}]
    de = PMCCAgent._format_assignment_alert(ev, "event")
    assert "ASSIGNMENT/EXERCISE PENDING" in de and "EXERCISE THE LEAP" in de


class _Stub:
    _cfg: dict = {}

    def __init__(self):
        self.audits = []

    def _audit_division(self, kind, payload):
        self.audits.append((kind, payload))


_Stub.assignment_watch = PMCCAgent.assignment_watch
_Stub._emit_assignment_alerts = PMCCAgent._emit_assignment_alerts
_Stub._assignment_risk_dte = PMCCAgent._assignment_risk_dte
_Stub._assignment_event_items = staticmethod(PMCCAgent._assignment_event_items)
_Stub._assignment_risk_items = staticmethod(PMCCAgent._assignment_risk_items)
_Stub._format_assignment_alert = staticmethod(PMCCAgent._format_assignment_alert)
_Stub._emit_assignment_exec_alert = staticmethod(PMCCAgent._emit_assignment_exec_alert)


class _Broker:
    def __init__(self, positions, spots=None):
        self._positions = positions
        self._spots = spots or {}

    async def get_option_positions_detail(self):
        return self._positions

    async def quote(self, sym):
        return self._spots.get(sym, 0.0)


@pytest.mark.asyncio
async def test_assignment_watch_orchestration():
    positions = [
        _short("AAA", 10.0, 1),                       # ITM near-DTE -> risk
        _short("BBB", 20.0, 0, pa=1),                 # pending assignment -> event (OTM, no risk)
        {"chain_symbol": "LEAP", "option_type": "call", "strike_price": 5.0,
         "dte": 900, "quantity": 2.0},                # long LEAP -> ignored (not a short)
    ]
    b = _Broker(positions, spots={"AAA": 12.0, "BBB": 19.0})
    a = _Stub()
    res = await a.assignment_watch(b)
    assert res["risk"] == 1 and res["events"] == 1
    kinds = {k for k, _ in a.audits}
    assert "pmcc_assignment_risk" in kinds and "pmcc_assignment_detected" in kinds


@pytest.mark.asyncio
async def test_assignment_watch_no_positions_is_quiet():
    a = _Stub()
    res = await a.assignment_watch(_Broker([]))
    assert res == {"risk": 0, "events": 0}
    assert a.audits == []
