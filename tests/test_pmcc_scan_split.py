"""Build B (2026-07-24) additive pieces: pre-open TRIAGE (Phase A only),
GLOBAL post-settle liveness probe, and the calm two-register digest. These are
pure additions (no scheduler/scan behavior change) tested via stub-bound methods.
"""
from __future__ import annotations

import pytest

from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent, PMCCPosition


def _pos(symbol, short_strike, short_dte):
    return PMCCPosition(
        symbol=symbol,
        long_leg_expiry="2028-01-21", long_leg_strike=1.0, long_leg_delta=0.9,
        long_leg_dte=900, long_leg_qty=1.0, long_leg_avg_price=100.0,
        long_leg_symbol=f"{symbol} LEAP",
        short_leg_expiry="2026-07-31", short_leg_strike=short_strike,
        short_leg_dte=short_dte, short_leg_qty=-1.0, short_leg_mark=0.5,
        short_leg_symbol=f"{symbol} short",
    )


# --------------------------------------------------------------------------- #
# triage() — Phase A only
# --------------------------------------------------------------------------- #


class _TriageStub:
    _cfg: dict = {}

    def __init__(self, legs):
        self._legs = legs
        self.audited = None

    def _reload(self):
        pass

    def _audit_division(self, kind, payload):
        self.audited = (kind, payload)

    async def detect_existing_legs(self, broker):
        return self._legs


_TriageStub.triage = PMCCAgent.triage
_TriageStub._triage_near_dte_days = PMCCAgent._triage_near_dte_days


class _QuoteBroker:
    def __init__(self, spots):
        self._spots = spots

    async def quote(self, sym):
        return self._spots.get(sym, 0.0)


@pytest.mark.asyncio
async def test_triage_classifies_breach_and_routine_and_skips_far_dte():
    legs = [
        _pos("AAA", 10.0, 1),     # near-DTE, spot 12 -> ITM breach
        _pos("BBB", 20.0, 3),     # near-DTE, spot 18 -> OTM routine
        _pos("CCC", 30.0, 20),    # far-DTE -> skipped
    ]
    stub = _TriageStub(legs)
    report = await stub.triage(_QuoteBroker({"AAA": 12.0, "BBB": 18.0, "CCC": 31.0}))
    by = {r["symbol"]: r for r in report}
    assert set(by) == {"AAA", "BBB"}                    # CCC (far DTE) skipped
    assert by["AAA"]["register"] == "breach" and by["AAA"]["itm"] is True
    assert by["BBB"]["register"] == "routine" and by["BBB"]["itm"] is False
    assert stub.audited[0] == "pmcc_morning_triage"


@pytest.mark.asyncio
async def test_triage_skips_uncovered_leap():
    p = _pos("DDD", 10.0, 1)
    p.short_leg_strike = None                            # uncovered LEAP
    stub = _TriageStub([p])
    report = await stub.triage(_QuoteBroker({"DDD": 12.0}))
    assert report == []


@pytest.mark.asyncio
async def test_triage_spot_unavailable_is_routine_not_breach():
    stub = _TriageStub([_pos("EEE", 10.0, 1)])
    report = await stub.triage(_QuoteBroker({}))         # quote -> 0.0 -> None
    assert report[0]["spot"] is None
    assert report[0]["register"] == "routine"            # never escalate on missing data


# --------------------------------------------------------------------------- #
# reference_quotes_live() — GLOBAL liveness probe
# --------------------------------------------------------------------------- #


class _LiveStub:
    _cfg: dict = {}


_LiveStub.reference_quotes_live = PMCCAgent.reference_quotes_live
_LiveStub._liveness_ref_symbols = PMCCAgent._liveness_ref_symbols
_LiveStub._liveness_max_spread_pct = PMCCAgent._liveness_max_spread_pct


class _ChainBroker:
    def __init__(self, calls):
        self._calls = calls

    async def get_expiration_dates(self, sym):
        return ["2026-07-31"]

    async def get_calls_for_expiry(self, sym, date):
        return self._calls


@pytest.mark.asyncio
async def test_liveness_true_on_sane_two_sided_quote():
    live, _ = await _LiveStub().reference_quotes_live(_ChainBroker([{"bid": 1.20, "ask": 1.25}]))
    assert live is True


@pytest.mark.asyncio
async def test_liveness_false_on_zero_bid_opening_rotation():
    live, _ = await _LiveStub().reference_quotes_live(_ChainBroker([{"bid": 0.0, "ask": 1.50}]))
    assert live is False


@pytest.mark.asyncio
async def test_liveness_false_on_wide_spread():
    # spread 1.50 on mid 1.25 = 120% > 15%
    live, _ = await _LiveStub().reference_quotes_live(_ChainBroker([{"bid": 0.50, "ask": 2.00}]))
    assert live is False


# --------------------------------------------------------------------------- #
# _format_triage_digest — calm, two registers
# --------------------------------------------------------------------------- #


def test_digest_two_registers():
    rep = [
        {"symbol": "AAA", "short_strike": 10.0, "short_dte": 1, "spot": 12.0,
         "itm": True, "register": "breach"},
        {"symbol": "BBB", "short_strike": 20.0, "short_dte": 3, "spot": 18.0,
         "itm": False, "register": "routine"},
    ]
    d = PMCCAgent._format_triage_digest(rep)
    assert "BREACH / ASSIGNMENT RISK (1)" in d
    assert "Routine near-DTE (1)" in d
    assert "no manual action needed before then" in d
    assert "ABORT" not in d.upper()                     # no scary abort language


def test_digest_empty():
    assert "no shorts near expiry" in PMCCAgent._format_triage_digest([])
