"""B-ARM #6: startup options-tier check. A live PMCC broker below option_level_3
(spreads need level 3) is surfaced (log + audit) at startup, not via a live reject.
"""
from __future__ import annotations

from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent


def test_option_level_int_parsing():
    f = PMCCAgent._option_level_int
    assert f("option_level_3") == 3
    assert f("option_level_0") == 0
    assert f("2") == 2
    assert f("") is None
    assert f(None) is None
    assert f("garbage") is None


class _Stub:
    def __init__(self):
        self.audits = []

    def _audit_division(self, kind, payload):
        self.audits.append((kind, payload))


_Stub._check_options_tier_once = PMCCAgent._check_options_tier_once
_Stub._option_level_int = staticmethod(PMCCAgent._option_level_int)


class _Broker:
    def __init__(self, paper, option_level):
        self.paper = paper
        self.option_level = option_level


def test_tier_live_level3_ok():
    a = _Stub()
    a._check_options_tier_once(_Broker(paper=False, option_level="option_level_3"))
    assert a.audits == [("pmcc_options_tier_check", {"ok": True, "verified": True, "level": 3})]
    assert a._options_tier_checked is True


def test_tier_live_level2_insufficient():
    a = _Stub()
    a._check_options_tier_once(_Broker(paper=False, option_level="option_level_2"))
    _, payload = a.audits[0]
    assert payload["ok"] is False and payload["verified"] is True and payload["level"] == 2


def test_tier_live_unverified_when_blank():
    a = _Stub()
    a._check_options_tier_once(_Broker(paper=False, option_level=""))
    _, payload = a.audits[0]
    assert payload["ok"] is False and payload["verified"] is False


def test_tier_paper_skips_check():
    a = _Stub()
    a._check_options_tier_once(_Broker(paper=True, option_level="option_level_1"))
    assert a.audits == []                        # paper handle -> no tier check
    assert a._options_tier_checked is True        # but once-guard flag is set


def test_tier_check_runs_once():
    a = _Stub()
    b = _Broker(paper=False, option_level="option_level_3")
    a._check_options_tier_once(b)
    a._check_options_tier_once(b)                 # second call is a no-op
    assert len(a.audits) == 1
