"""Live-side unit tests for the PEAD drift-cadence fix (FIX 2).

`_fired_rule` gates the DRIFT branch on `drift_evaluated` — manage() sets it True
only on a completed-daily-bar tick (never the entry day, never an intraday tick
with no new daily bar). STOP is NEVER gated (it stays the intraday risk layer).
"""
from __future__ import annotations

from trading_corp.agents.strategies.pead_pressures import Pressures
from trading_corp.agents.strategies.pead_strategy import PEADStrategy


def _pr(stop=0.0, drift=0.0, guard=0.0, time=0.0) -> Pressures:
    return Pressures(stop=stop, drift=drift, guard=guard, time=time,
                     governing="stop", fuse_pct=max(stop, drift, guard, time),
                     fuse_color="green", governing_color="#f1556c")


def test_drift_branch_only_honoured_when_evaluated():
    pr = _pr(drift=1.0)                       # drift maxed, everything else calm
    assert PEADStrategy._fired_rule(pr, None, 3, drift_evaluated=True) == "drift"
    assert PEADStrategy._fired_rule(pr, None, 3, drift_evaluated=False) is None   # suppressed
    # default keeps drift honoured (backward-compatible for direct callers)
    assert PEADStrategy._fired_rule(pr, None, 3) == "drift"


def test_stop_fires_even_when_drift_not_evaluated():
    # STOP is the single intraday risk layer — never gated by drift_evaluated.
    pr = _pr(stop=1.0, drift=1.0)
    assert PEADStrategy._fired_rule(pr, None, 3, drift_evaluated=False) == "stop"


def test_guard_and_time_unaffected_by_drift_gate():
    assert PEADStrategy._fired_rule(_pr(), 2, 3, drift_evaluated=False) == "guard"     # d2n<=GUARD_LEAD
    assert PEADStrategy._fired_rule(_pr(time=1.0), None, 3, drift_evaluated=False) == "time"
