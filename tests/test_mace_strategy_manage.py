"""Phase-2/4 tests: MACE management precedence (stop > PT > time > exdiv).

The PT branch is the T9 SYNTHETIC profit target (Board ruling 2026-08-10, go-live
on the T9 basis): no resting-GTC order — the manage tick closes when the
cost-to-close `mark` has decayed to <= pt_pct_of_credit x credit received."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from trading_corp.mace import strategy as st
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import (
    CondorSpec, EXIT_EXDIV, EXIT_PT, EXIT_STOP, EXIT_TIME, RungState,
)
from trading_corp.utils.time import ET

ROOT = Path(__file__).resolve().parents[1]
CFG = load_mace_config(ROOT / "config" / "mace.yaml",
                       exdiv_calendar_path=ROOT / "config" / "ex_dividend_calendar.yaml")
SPY = CFG.symbols["SPY"]                # exdiv_guard True
GLD = CFG.symbols["GLD"]               # exdiv_guard False


def _rung(expiry, credit=1.0, short_call=615):
    spec = CondorSpec("SPY", expiry, 585, 582, short_call, short_call + 3, 3.0)
    return RungState(rung_id="r", symbol="SPY", status="open", expiry=expiry,
                     spec=spec, width_dollars=3.0, contracts=1, credit_actual=credit)


def _now(y=2026, mo=8, d=12, h=15, mi=35):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


def test_stop_fires():
    r = _rung(date(2026, 9, 18), credit=1.0)          # far DTE, no time/exdiv
    d = st.evaluate_management(r, mark=2.0, spot=600, now_et=_now(), cfg=CFG,
                               symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason == EXIT_STOP and d.should_exit


def test_stop_boundary_below_holds():
    r = _rung(date(2026, 9, 18), credit=1.0)
    d = st.evaluate_management(r, mark=1.99, spot=600, now_et=_now(), cfg=CFG,
                               symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason is None


def test_time_fires_dte_and_clock():
    r = _rung(date(2026, 9, 1), credit=1.0)           # DTE 20 <= 21
    d = st.evaluate_management(r, mark=1.0, spot=600, now_et=_now(h=15, mi=35),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason == EXIT_TIME


def test_time_needs_clock_after_1530():
    r = _rung(date(2026, 9, 1), credit=1.0)           # DTE 20 <= 21
    d = st.evaluate_management(r, mark=1.0, spot=600, now_et=_now(h=15, mi=0),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason is None                       # before 15:30 -> hold


def test_time_needs_dte():
    r = _rung(date(2026, 9, 18), credit=1.0)          # DTE 37 > 21
    d = st.evaluate_management(r, mark=1.0, spot=600, now_et=_now(h=15, mi=45),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason is None


def test_exdiv_fires_itm_and_guard():
    r = _rung(date(2026, 9, 18), credit=1.0, short_call=615)   # far DTE
    d = st.evaluate_management(r, mark=1.0, spot=620, now_et=_now(h=10),   # spot > 615 ITM
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=True)
    assert d.exit_reason == EXIT_EXDIV


def test_exdiv_needs_itm():
    r = _rung(date(2026, 9, 18), credit=1.0, short_call=615)
    d = st.evaluate_management(r, mark=1.0, spot=610, now_et=_now(h=10),   # spot < 615, not ITM
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=True)
    assert d.exit_reason is None


def test_exdiv_needs_guard_on():
    r = _rung(date(2026, 9, 18), credit=1.0, short_call=615)
    d = st.evaluate_management(r, mark=1.0, spot=620, now_et=_now(h=10),
                               cfg=CFG, symbol_cfg=GLD, exdiv_within=True)  # guard off
    assert d.exit_reason is None


def test_precedence_stop_over_time_over_exdiv():
    r = _rung(date(2026, 9, 1), credit=1.0, short_call=615)   # DTE 20 (time-eligible)
    # all three would fire: mark 3.0 (stop), DTE20@15:35 (time), spot 620 ITM (exdiv)
    d = st.evaluate_management(r, mark=3.0, spot=620, now_et=_now(h=15, mi=35),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=True)
    assert d.exit_reason == EXIT_STOP                 # stop wins


def test_precedence_time_over_exdiv():
    r = _rung(date(2026, 9, 1), credit=1.0, short_call=615)   # DTE 20
    d = st.evaluate_management(r, mark=1.0, spot=620, now_et=_now(h=15, mi=35),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=True)
    assert d.exit_reason == EXIT_TIME                 # time beats exdiv (no stop)


def test_hold_when_nothing_fires():
    r = _rung(date(2026, 9, 18), credit=1.0, short_call=615)
    d = st.evaluate_management(r, mark=1.0, spot=600, now_et=_now(h=12),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason is None and not d.should_exit


def test_stop_gap_tick_0935():
    # the 09:35 tick IS the gap rule — no separate branch, stop fires on the mark
    r = _rung(date(2026, 9, 18), credit=1.0)
    d = st.evaluate_management(r, mark=2.5, spot=600, now_et=_now(h=9, mi=35),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason == EXIT_STOP


# ── T9 SYNTHETIC PROFIT TARGET (mark <= pt_pct_of_credit x credit) ────────────
# credit=1.0, pt_pct_of_credit=0.50 -> PT target = 0.50; stop = 2.0.

def test_pt_synthetic_fires_below_target():
    r = _rung(date(2026, 9, 18), credit=1.0)              # far DTE, no time/exdiv
    d = st.evaluate_management(r, mark=0.40, spot=600, now_et=_now(h=12),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason == EXIT_PT and d.should_exit


def test_pt_boundary_at_target_fires():
    r = _rung(date(2026, 9, 18), credit=1.0)
    d = st.evaluate_management(r, mark=0.50, spot=600, now_et=_now(h=12),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason == EXIT_PT                       # <= target inclusive


def test_pt_boundary_above_target_holds():
    r = _rung(date(2026, 9, 18), credit=1.0)
    d = st.evaluate_management(r, mark=0.51, spot=600, now_et=_now(h=12),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason is None                          # just above target -> hold


def test_precedence_pt_over_time():
    # at PT AND time-eligible (DTE 20 @ 15:35): PT wins -> exit is labelled `pt`,
    # closing at the favorable target rather than a time-forced market exit.
    r = _rung(date(2026, 9, 1), credit=1.0)
    d = st.evaluate_management(r, mark=0.40, spot=600, now_et=_now(h=15, mi=35),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason == EXIT_PT


def test_stop_beats_pt_are_mutually_exclusive():
    # a high mark is a stop, never a PT (stop is evaluated first; the two windows
    # never overlap for positive credit).
    r = _rung(date(2026, 9, 18), credit=1.0)
    d = st.evaluate_management(r, mark=2.5, spot=600, now_et=_now(h=12),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason == EXIT_STOP


def test_pt_needs_a_mark():
    # unpriceable mark -> no PT (and no stop); falls through to time/exdiv/hold.
    r = _rung(date(2026, 9, 18), credit=1.0)
    d = st.evaluate_management(r, mark=None, spot=600, now_et=_now(h=12),
                               cfg=CFG, symbol_cfg=SPY, exdiv_within=False)
    assert d.exit_reason is None


# ── PT MARK-TRUST GUARD (assess_pt_mark_trust) — 2026-09-18 ───────────────────
# CFG.management.mark_guard: enabled=True, frozen_cycles=2, sane_epsilon_usd=0.01,
# max_cycle_drop_pct=0.35. width_dollars=1.0 (XLE-like), time_exit_dte=21.
import dataclasses  # noqa: E402


def _xle(expiry, credit=0.30, sp=60.0, lp=59.0, sc=70.0, lc=71.0, width=1.0):
    spec = CondorSpec("XLE", expiry, sp, lp, sc, lc, width)
    return RungState(rung_id=f"x-{expiry.isoformat()}", symbol="XLE", status="open",
                     expiry=expiry, spec=spec, width_dollars=width, contracts=2,
                     credit_actual=credit)


def _assess(rung, mark, *, sibling_marks=(), unchanged_repeat=1, last_trusted=None, dte=42, cfg=CFG):
    return st.assess_pt_mark_trust(rung, mark, cfg, sibling_marks=list(sibling_marks),
                                   unchanged_repeat=unchanged_repeat,
                                   last_trusted_mark=last_trusted, dte=dte)


def test_guard_9_18_replay_sibling_arbitrage_rejects():
    # The incident: 42-DTE mark 0.13 vs identical-strike 28-DTE sibling 0.205 -> arbitrage-impossible.
    r = _xle(date(2026, 10, 30))
    t = _assess(r, 0.13, sibling_marks=[0.205], dte=42)
    assert (not t.trusted) and t.reason == "arbitrage" and t.alert


def test_guard_fresh_sane_fires():
    # A trusted mark (>= the shorter-dated sibling) -> PT allowed to fire.
    r = _xle(date(2026, 10, 30))
    t = _assess(r, 0.14, sibling_marks=[0.12], dte=42)
    assert t.trusted and t.reason == "ok" and not t.alert


def test_guard_no_sibling_fallback_ok():
    # No sibling, DTE 42, prior-trusted 0.28, mark 0.20 (28.6% drop < 35%) -> trusted.
    r = _xle(date(2026, 10, 30))
    t = _assess(r, 0.20, sibling_marks=[], last_trusted=0.28, dte=42)
    assert t.trusted


def test_guard_no_sibling_fallback_rejects_collapse():
    # No sibling, DTE 42, prior-trusted 0.28, mark 0.13 (53.6% drop > 35%) -> reject.
    r = _xle(date(2026, 10, 30))
    t = _assess(r, 0.13, sibling_marks=[], last_trusted=0.28, dte=42)
    assert (not t.trusted) and t.reason == "arbitrage" and t.alert


def test_guard_fallback_inactive_inside_time_window():
    # DTE 14 (<= time_exit_dte 21): a fast decay near the time-exit window is legit -> not rejected.
    r = _xle(date(2026, 10, 30))
    t = _assess(r, 0.05, sibling_marks=[], last_trusted=0.28, dte=14)
    assert t.trusted


def test_guard_timely_but_insane_rejects():
    # Changed (not frozen) mark but < sibling -> SANE fails.
    r = _xle(date(2026, 10, 30))
    t = _assess(r, 0.13, sibling_marks=[0.205], unchanged_repeat=1, dte=42)
    assert (not t.trusted) and t.reason == "arbitrage"


def test_guard_stale_but_sane_rejects():
    # Frozen (unchanged >= 2 cycles) even though >= sibling -> TIMELY fails (checked first).
    r = _xle(date(2026, 10, 30))
    t = _assess(r, 0.13, sibling_marks=[0.10], unchanged_repeat=2, dte=42)
    assert (not t.trusted) and t.reason == "frozen" and t.alert


def test_guard_structural_rejects():
    r = _xle(date(2026, 10, 30))
    assert (not _assess(r, 1.0, sibling_marks=[0.5]).trusted)      # mark >= width 1.0
    assert (not _assess(r, -0.01, sibling_marks=[0.5]).trusted)    # mark < 0
    for m in (1.0, -0.01):
        assert _assess(r, m, sibling_marks=[0.5]).reason == "structural"


def test_guard_fail_closed_no_baseline_no_sibling_silent_hold():
    # First-ever mark, no sibling, no trusted baseline -> HOLD one tick, NO alert (silent).
    r = _xle(date(2026, 10, 30))
    t = _assess(r, 0.13, sibling_marks=[], last_trusted=None, dte=42)
    assert (not t.trusted) and t.reason == "no_baseline" and not t.alert


def test_guard_disabled_is_passthrough():
    r = _xle(date(2026, 10, 30))
    cfg_off = dataclasses.replace(
        CFG, management=dataclasses.replace(
            CFG.management, mark_guard=dataclasses.replace(CFG.management.mark_guard, enabled=False)))
    t = _assess(r, 0.13, sibling_marks=[0.205], cfg=cfg_off)   # would be arbitrage if enabled
    assert t.trusted and not t.alert


def test_shorter_dated_same_strike_siblings_finder():
    cand = _xle(date(2026, 10, 30))                                   # 60/59/70/71
    sib = _xle(date(2026, 10, 16))                                    # SAME strikes, shorter
    diff_strike = _xle(date(2026, 10, 16), sc=69.5, lc=70.5)          # diff strikes
    longer = _xle(date(2026, 11, 6))                                  # same strikes, LONGER (excluded)
    sibs = st.shorter_dated_same_strike_siblings([cand, sib, diff_strike, longer], cand)
    assert [s.expiry for s in sibs] == [date(2026, 10, 16)]
