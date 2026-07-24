"""Item-4 (2026-07-24): abort ALERT wording is reassuring, and the liquidity
sub-gate that bound is classified/surfaced. The alarming
"sparse_chain_no_weekly ... missing new_short" body triggered a panic manual roll.
"""
from __future__ import annotations

import trading_corp.comms.exec_alert as ea
from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent


def test_classify_liquidity_reason_buckets():
    c = PMCCAgent._classify_liquidity_reason
    assert c("OI=5 < 100 AND vol=0 < 500") == "liveness"
    assert c("vol=3 < 50") == "volume"
    assert c("spread=22.0% > 10.0%") == "spread"
    assert c("no ask price") == "no_ask"
    assert c("") == "other"


class _StubAgent:
    _logger_agent = None

    def _audit_division(self, kind, payload):   # no-op stand-in
        pass


_StubAgent._audit_roll_abort = PMCCAgent._audit_roll_abort


def test_abort_alert_is_reassuring_and_shows_subgate(monkeypatch):
    captured = {}
    monkeypatch.setattr(ea, "emit_exec_alert", lambda o: captured.setdefault("o", o))
    a = _StubAgent()
    a._audit_roll_abort(
        reason="sparse_chain_no_weekly", symbol="RKLB", missing_leg="new_short",
        diag={"considered": 91, "liquid": 0,
              "failed_by_gate": {"volume": 50, "spread": 41}},
    )
    o = captured["o"]
    assert o.tier == "ABORTED"
    assert o.position_changed is False
    # Reassuring body.
    assert "no order sent" in o.reason
    assert "position unchanged" in o.reason
    assert "will retry next scan" in o.reason
    # Still actionable: the sub-gate that bound is present.
    assert "failed_by=" in o.reason and "volume" in o.reason
