"""Mark-quality confirmation for the mark-path stop (2026-11-19).

The 11-19 SPY 729/726-782/785 stop fired on mid 2.47 >= 2.40 while the quoted
natural sat >= 3.00 and the close printed 1.64 — a wide-quote mid is quote
geometry, not value. New rule (mark-path only; structural/PT/time unchanged):

  tight quotes            -> fires tick one (byte-identical to the old rule)
  wide + anti >= level    -> fires tick one (past the stop on ANY displayed reading)
  wide + anti <  level    -> one-tick confirmation (mirrors the structural set)

Style mirrors tests/test_mace_durability.py: object.__new__ + SimpleNamespace.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from trading_corp.mace import strategy as st
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import (
    CondorSpec, EXIT_STOP, OptionQuote, QuoteSnapshot, RungState,
)
from trading_corp.mace.manager import MaceManager

ROOT = Path(__file__).resolve().parents[1]

SPEC = CondorSpec(symbol="SPY", expiry=date(2026, 12, 24), short_put=729.0,
                  long_put=726.0, short_call=782.0, long_call=785.0,
                  width_dollars=3.0)
NOW = datetime(2026, 11, 19, 15, 30)      # ET; DTE 35 -> no time exit in play
MGMT = SimpleNamespace(stop_multiple=2.0, pt_pct_of_credit=0.50,
                       time_exit_dte=21, time_exit_at_et="15:30",
                       stop_quote_width_max_pct=0.15)
CFG = SimpleNamespace(management=MGMT)
SYM = SimpleNamespace(exdiv_guard=False)


def _q(strike, opt, bid, ask):
    return OptionQuote(symbol="SPY", expiry=SPEC.expiry, strike=float(strike),
                       opt_type=opt, bid=bid, ask=ask)


def _snap(sp, lp, sc, lc):
    return QuoteSnapshot(sp=_q(729, "put", *sp), lp=_q(726, "put", *lp),
                         sc=_q(782, "call", *sc), lc=_q(785, "call", *lc))


# The 11-19 shape: mid 2.47, natural 3.96 (>= 3.00), anti 0.98 — stop level 2.40.
WIDE_SNAP = _snap(sp=(24.00, 26.00), lp=(22.21, 23.01),
                  sc=(0.70, 0.80), lc=(0.63, 0.71))
# Tight: mid 2.50, natural 2.62 (4.8% over mid), anti 2.38.
TIGHT_SNAP = _snap(sp=(25.00, 25.08), lp=(22.60, 22.68),
                   sc=(0.75, 0.79), lc=(0.65, 0.69))
# Wide but unambiguous: mid 4.37, natural 6.15, anti 2.59 >= 2.40.
FAR_TOUCH_SNAP = _snap(sp=(27.00, 30.00), lp=(24.00, 24.40),
                       sc=(0.10, 0.20), lc=(0.05, 0.11))
# Recovered: mid 2.20 < 2.40.
RECOVERED_SNAP = _snap(sp=(24.00, 25.00), lp=(22.20, 22.80),
                       sc=(0.70, 0.80), lc=(0.63, 0.71))


def _rung(**kw):
    d = dict(rung_id="r-spy", symbol="SPY", status="open", expiry=SPEC.expiry,
             spec=SPEC, width_dollars=3.0, contracts=2, credit_actual=1.20)
    d.update(kw)
    return RungState(**d)


def _mgr():
    audits = []
    mgr = object.__new__(MaceManager)
    mgr.cfg = CFG
    mgr._mark_stop_pending = set()
    mgr._audit = lambda k, **p: audits.append((k, p))
    return mgr, audits


def _fixture_sanity():
    assert abs(WIDE_SNAP.mark - 2.47) < 1e-9
    assert WIDE_SNAP.natural_debit >= 3.00
    assert WIDE_SNAP.anti_natural < 2.40


def test_fixture_matches_the_incident_shape():
    _fixture_sanity()


# ── the 11-19 replay ───────────────────────────────────────────────────────

def test_spy_1119_no_immediate_stop_then_confirms_next_tick():
    mgr, audits = _mgr()
    rung = _rung()
    # tick 1: mid 2.47 >= 2.40 on wide quotes, far touch 0.98 under the level -> pending.
    confirmed, rule = mgr._mark_stop_confirmation(rung, WIDE_SNAP, WIDE_SNAP.mark)
    assert (confirmed, rule) == (False, "mark_pending")
    d1 = st.evaluate_management(rung, WIDE_SNAP.mark, 717.98, NOW, CFG, SYM,
                                exdiv_within=False, mark_stop_confirmed=confirmed)
    assert d1.exit_reason is None, d1
    assert any(k == "mace_mark_stop_pending" for k, _ in audits), audits
    # tick 2: condition persists -> confirmed, fires.
    confirmed, rule = mgr._mark_stop_confirmation(rung, WIDE_SNAP, WIDE_SNAP.mark)
    assert (confirmed, rule) == (True, "mark_confirmed")
    d2 = st.evaluate_management(rung, WIDE_SNAP.mark, 717.98, NOW, CFG, SYM,
                                exdiv_within=False, mark_stop_confirmed=confirmed)
    assert d2.exit_reason == EXIT_STOP, d2


def test_tight_quotes_fire_on_tick_one():
    mgr, _ = _mgr()
    rung = _rung()
    confirmed, rule = mgr._mark_stop_confirmation(rung, TIGHT_SNAP, TIGHT_SNAP.mark)
    assert (confirmed, rule) == (True, "mark_tight")
    d = st.evaluate_management(rung, TIGHT_SNAP.mark, 718.0, NOW, CFG, SYM,
                               exdiv_within=False, mark_stop_confirmed=confirmed)
    assert d.exit_reason == EXIT_STOP
    assert not mgr._mark_stop_pending


def test_wide_but_far_touch_over_level_fires_on_tick_one():
    mgr, _ = _mgr()
    confirmed, rule = mgr._mark_stop_confirmation(_rung(), FAR_TOUCH_SNAP,
                                                  FAR_TOUCH_SNAP.mark)
    assert (confirmed, rule) == (True, "mark_far_touch")


def test_wide_recovers_under_stop_clears_pending_no_exit():
    mgr, _ = _mgr()
    rung = _rung()
    mgr._mark_stop_confirmation(rung, WIDE_SNAP, WIDE_SNAP.mark)        # pending
    assert "r-spy" in mgr._mark_stop_pending
    confirmed, rule = mgr._mark_stop_confirmation(rung, RECOVERED_SNAP,
                                                  RECOVERED_SNAP.mark)
    assert (confirmed, rule) == (True, None)
    assert "r-spy" not in mgr._mark_stop_pending
    d = st.evaluate_management(rung, RECOVERED_SNAP.mark, 719.0, NOW, CFG, SYM,
                               exdiv_within=False, mark_stop_confirmed=confirmed)
    assert d.exit_reason is None


def test_pending_survives_an_unpriceable_tick():
    mgr, _ = _mgr()
    rung = _rung()
    mgr._mark_stop_confirmation(rung, WIDE_SNAP, WIDE_SNAP.mark)        # pending
    none_snap = QuoteSnapshot(None, None, None, None)
    confirmed, rule = mgr._mark_stop_confirmation(rung, none_snap, None)
    assert rule is None
    assert "r-spy" in mgr._mark_stop_pending      # kept: no evidence either way
    confirmed, rule = mgr._mark_stop_confirmation(rung, WIDE_SNAP, WIDE_SNAP.mark)
    assert (confirmed, rule) == (True, "mark_confirmed")


def test_restart_costs_at_most_one_extra_tick():
    mgr1, _ = _mgr()
    rung = _rung()
    mgr1._mark_stop_confirmation(rung, WIDE_SNAP, WIDE_SNAP.mark)       # pending, then restart
    mgr2, _ = _mgr()                                                    # fresh set
    c1, r1 = mgr2._mark_stop_confirmation(rung, WIDE_SNAP, WIDE_SNAP.mark)
    c2, r2 = mgr2._mark_stop_confirmation(rung, WIDE_SNAP, WIDE_SNAP.mark)
    assert (c1, r1) == (False, "mark_pending")    # one extra tick...
    assert (c2, r2) == (True, "mark_confirmed")   # ...never a missed stop


def test_closing_self_heal_judges_by_the_same_rule():
    # The heal calls stop_triggered with defaults (armed test): wide-but-over-level
    # quotes keep a closing rung CLOSING (no reopen on ambiguity)...
    assert st.stop_triggered(_rung(status="closing"), WIDE_SNAP.mark, 717.98, MGMT) is True
    # ...and only a mid genuinely under the level reopens — the same boundary that
    # clears the pending set.
    assert st.stop_triggered(_rung(status="closing"), RECOVERED_SNAP.mark, 719.0, MGMT) is False


def test_stop_triggered_unconfirmed_mark_path_holds():
    assert st.stop_triggered(_rung(), 2.47, 717.98, MGMT, mark_stop_confirmed=False) is False
    assert st.stop_triggered(_rung(), 2.47, 717.98, MGMT) is True       # default unchanged


def test_evidence_row_carries_all_leg_quotes():
    mgr, audits = _mgr()
    mgr._emit_stop_evidence(_rung(), WIDE_SNAP, WIDE_SNAP.mark, 717.98,
                            rule="mark_pending")
    rows = [p for k, p in audits if k == "mace_stop_quote_evidence"]
    assert len(rows) == 1
    p = rows[0]
    assert p["rule"] == "mark_pending" and p["stop_level"] == 2.40
    assert p["mark"] == WIDE_SNAP.mark and p["spot"] == 717.98
    assert p["natural"] == WIDE_SNAP.natural_debit
    assert p["anti_natural"] == WIDE_SNAP.anti_natural
    assert p["legs"]["sp"] == {"bid": 24.00, "ask": 26.00, "mid": 25.00}
    assert p["legs"]["lp"] == {"bid": 22.21, "ask": 23.01, "mid": (22.21 + 23.01) / 2}
    assert p["legs"]["sc"] == {"bid": 0.70, "ask": 0.80, "mid": 0.75}
    assert p["legs"]["lc"] == {"bid": 0.63, "ask": 0.71, "mid": (0.63 + 0.71) / 2}


def test_shipped_config_defaults_width_threshold_without_yaml_key():
    import yaml
    raw = yaml.safe_load((ROOT / "config" / "mace.yaml").read_text(encoding="utf-8"))
    assert "stop_quote_width_max_pct" not in raw.get("management", {})   # config_hash preserved
    cfg = load_mace_config(ROOT / "config" / "mace.yaml",
                           exdiv_calendar_path=ROOT / "config" / "ex_dividend_calendar.yaml")
    assert cfg.management.stop_quote_width_max_pct == 0.15


def test_mark_is_wide_boundaries():
    assert st.mark_is_wide(2.47, 3.96, MGMT) is True            # 60% wide
    assert st.mark_is_wide(2.50, 2.62, MGMT) is False           # 4.8% -> tight
    assert st.mark_is_wide(2.47, None, MGMT) is False           # no natural -> old rule
    assert st.mark_is_wide(None, 3.0, MGMT) is False
    assert st.mark_is_wide(0.0, 3.0, MGMT) is False             # degenerate mid
