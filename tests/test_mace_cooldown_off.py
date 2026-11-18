"""Post-stop cooldown OFF (2026-11-18 Jack ruling).

entry.stop_cooldown_sessions: 0 disables entry-pipeline filter 3 completely:
`since < 0` is never true for any real `sessions_since_last_stop` value (the
helper returns None or an int >= 0, and 0 on the stop's own session — the
"0 sessions since last stop" log line). With the key at 2 the old behaviour is
byte-identical (same-day and next-session stops still skip). Every other gate
is untouched — with the cooldown passed, the same inputs land on the NEXT
filter (no chain -> no_expiry), proving the pipeline order is unchanged.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from trading_corp.mace import strategy as st
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import (
    EXIT_STOP, SKIP_COOLDOWN, SKIP_NO_EXPIRY, CondorSpec, RungState,
)

ROOT = Path(__file__).resolve().parents[1]
CFG = load_mace_config(ROOT / "config" / "mace.yaml",
                       exdiv_calendar_path=ROOT / "config" / "ex_dividend_calendar.yaml")

WED = date(2026, 11, 18)      # the GDX double-stop day
THU = date(2026, 11, 19)
FRI = date(2026, 11, 20)
EXPIRY = date(2026, 12, 18)


def _cooldown(n: int):
    return replace(CFG, entry=replace(CFG.entry, stop_cooldown_sessions=n))


def _stop_rung(exit_day: date, rung_id: str = "r-stop") -> RungState:
    spec = CondorSpec("SPY", EXPIRY, 713.0, 710.0, 790.0, 793.0, 3.0)
    return RungState(rung_id=rung_id, symbol="SPY", status="closed", expiry=EXPIRY,
                     spec=spec, width_dollars=3.0, contracts=1,
                     exit_ts=f"{exit_day.isoformat()}T15:35:59+00:00",
                     exit_reason=EXIT_STOP)


def _ctx(session: date, nxt: date, rungs=()):
    # chains={} -> a symbol that PASSES cooldown lands on SKIP_NO_EXPIRY
    # ("no chain"), the next filter that can fire with these inputs.
    return st.EntryContext(session_date=session, equity=50_000.0,
                           rungs=list(rungs), events=[], ivr={}, chains={},
                           next_session_date=nxt)


def test_shipped_config_cooldown_is_zero():
    assert CFG.entry.stop_cooldown_sessions == 0


def test_same_day_stop_blocks_at_2_passes_at_0():
    # The 11-18 shape: GDX stopped in the morning, 15:45 eval the same day
    # ("0 sessions since last stop").
    ctx = _ctx(WED, THU, [_stop_rung(WED)])
    r2 = st.evaluate_entry("SPY", _cooldown(2), ctx)
    assert r2.skip_reason == SKIP_COOLDOWN and "0 sessions" in r2.detail
    r0 = st.evaluate_entry("SPY", _cooldown(0), ctx)
    assert r0.skip_reason == SKIP_NO_EXPIRY


def test_next_session_stop_blocks_at_2_passes_at_0():
    ctx = _ctx(THU, FRI, [_stop_rung(WED)])
    r2 = st.evaluate_entry("SPY", _cooldown(2), ctx)
    assert r2.skip_reason == SKIP_COOLDOWN and "1 sessions" in r2.detail
    r0 = st.evaluate_entry("SPY", _cooldown(0), ctx)
    assert r0.skip_reason == SKIP_NO_EXPIRY


def test_two_sessions_since_stop_passes_at_2():
    # The old boundary: at 2 sessions the old config already allowed entry.
    ctx = _ctx(FRI, date(2026, 11, 23), [_stop_rung(WED)])
    r2 = st.evaluate_entry("SPY", _cooldown(2), ctx)
    assert r2.skip_reason == SKIP_NO_EXPIRY


def test_no_stop_history_identical_either_way():
    ctx = _ctx(WED, THU, [])
    assert st.evaluate_entry("SPY", _cooldown(2), ctx).skip_reason == SKIP_NO_EXPIRY
    assert st.evaluate_entry("SPY", _cooldown(0), ctx).skip_reason == SKIP_NO_EXPIRY


def test_zero_never_blocks_even_on_repeated_stops():
    rungs = [_stop_rung(WED, "r-stop-1"), _stop_rung(date(2026, 11, 12), "r-stop-2")]
    r0 = st.evaluate_entry("SPY", _cooldown(0), _ctx(WED, THU, rungs))
    assert r0.skip_reason == SKIP_NO_EXPIRY
