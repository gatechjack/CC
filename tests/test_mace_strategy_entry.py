"""Phase-2 tests: MACE entry pipeline (pure, golden fixtures).

Covers every filter's pass + skip, band-edge delta selection, the UNIVERSAL
no_wing check, FXI fallback width, credit-floor + risk-band edges, refill /
weekly / cooldown matrices, IVR stale/unavailable skip-annotation, and blackout
(today / next session / scope / type).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from trading_corp.mace import strategy as st
from trading_corp.mace.config import load_mace_config
import yaml

from trading_corp.mace.domain import (
    CondorSpec, EvalResult, IVR_OK, IVR_STALE, IVR_UNAVAILABLE, OptionQuote, RungState,
    SKIP_BLACKOUT, SKIP_BUDGET, SKIP_CAPACITY, SKIP_COOLDOWN, SKIP_CREDIT_FLOOR,
    SKIP_IVR, SKIP_NO_DELTA_STRIKE, SKIP_NO_EQUITY_SNAPSHOT, SKIP_NO_EXPIRY,
    SKIP_NO_WING, SKIP_RESERVE, SKIP_RISK_BAND, SKIP_RISK_REJECT,
    SKIP_WEEKLY_BUDGET, iso_week,
)
from trading_corp.mace.ivr_provider import IvrReading

ROOT = Path(__file__).resolve().parents[1]
MACE_YAML = ROOT / "config" / "mace.yaml"
EXDIV_YAML = ROOT / "config" / "ex_dividend_calendar.yaml"
CFG = load_mace_config(MACE_YAML, exdiv_calendar_path=EXDIV_YAML)

SESSION = date(2026, 8, 12)     # Wed — mid-week (same-ISO-week refill/cooldown expressible)
NEXT = date(2026, 8, 13)        # Thu
EXPIRY = date(2026, 9, 18)      # Fri, 37 DTE from SESSION (in [30,45])
WK = iso_week(SESSION)


# ── fixtures ─────────────────────────────────────────────────────────────

def _q(opt, strike, delta, bid, ask):
    return OptionQuote(symbol="SPY", expiry=EXPIRY, strike=float(strike),
                       opt_type=opt, bid=bid, ask=ask, delta=delta)


# clean SPY condor at ~600 spot, width 3: shorts 585p/615c (delta 0.20),
# wings 582p/618c; credit 0.50/side -> 1.00 total.
_DEFAULT_LEGS = [
    ("put", 582, -0.15, 1.50, 1.60),   # long put (wing)
    ("put", 585, -0.20, 2.00, 2.10),   # short put
    ("put", 588, -0.25, 2.60, 2.70),
    ("call", 612, 0.25, 2.60, 2.70),
    ("call", 615, 0.20, 2.00, 2.10),   # short call
    ("call", 618, 0.15, 1.50, 1.60),   # long call (wing)
]


def chain(legs=None, spot=600.0, expiries=(EXPIRY,)):
    legs = _DEFAULT_LEGS if legs is None else legs
    quotes = {(EXPIRY, opt, float(k)): _q(opt, k, d, b, a) for opt, k, d, b, a in legs}
    return st.ChainView("SPY", spot=spot, expiries=tuple(expiries), quotes=quotes)


def ivr(value, status=IVR_OK):
    return IvrReading("SPY", status, value, 0.15, None, 0, "", "test")


def ctx(*, ch=None, iv=None, rungs=(), events=(), equity=50_000.0, risk_gate=None):
    return st.EntryContext(
        session_date=SESSION, equity=equity, rungs=list(rungs), events=list(events),
        ivr={"SPY": iv} if iv is not None else {},
        chains={"SPY": ch if ch is not None else chain()},
        next_session_date=NEXT, risk_gate=risk_gate)


def rung(status="open", entry_wk=None, exit_ts=None, exit_reason=None,
         max_risk=200.0, symbol="SPY"):
    spec = CondorSpec("SPY", EXPIRY, 585, 582, 615, 618, 3.0)
    return RungState(rung_id="r", symbol=symbol, status=status, expiry=EXPIRY,
                     spec=spec, width_dollars=3.0, contracts=1, max_risk_usd=max_risk,
                     entry_iso_week=entry_wk, exit_ts=exit_ts, exit_reason=exit_reason)


def _eval(**kw):
    return st.evaluate_entry("SPY", CFG, ctx(**kw))


# ── happy path ───────────────────────────────────────────────────────────

def test_clean_entry():
    r = _eval(iv=ivr(30.0))
    assert r.entered and r.skip_reason is None
    assert r.contracts == 1
    assert abs(r.credit_mid - 1.00) < 1e-9
    assert abs(r.max_risk_usd - 200.0) < 1e-9
    assert r.spec.short_put == 585 and r.spec.short_call == 615
    assert r.spec.long_put == 582 and r.spec.long_call == 618
    assert r.ivr_status == IVR_OK and abs(r.ivr_value - 30.0) < 1e-9


def test_band_edge_delta_selection():
    # add a 0.20-exact strike far away; nearest-to-target still wins the band
    r = _eval(iv=ivr(30.0))
    assert r.spec.short_put == 585    # |delta| 0.20 nearest target


# ── filters 0-5 ──────────────────────────────────────────────────────────

def test_no_equity_snapshot():
    assert st.evaluate_entry("SPY", CFG, ctx(iv=ivr(30.0), equity=None)).skip_reason == SKIP_NO_EQUITY_SNAPSHOT


def test_capacity_skip():
    rungs = [rung(status="open"), rung(status="submitting"),
             rung(status="closing"), rung(status="open")]   # 4 live == max_rungs
    assert _eval(iv=ivr(30.0), rungs=rungs).skip_reason == SKIP_CAPACITY


def test_capacity_ignores_closed():
    rungs = [rung(status="closed") for _ in range(9)]        # closed don't occupy
    assert _eval(iv=ivr(30.0), rungs=rungs).entered


def test_weekly_budget_skip():
    rungs = [rung(entry_wk=WK)]                              # 1 entry this week, budget 1
    assert _eval(iv=ivr(30.0), rungs=rungs).skip_reason == SKIP_WEEKLY_BUDGET


def test_weekly_budget_refill():
    # 1 entry this week + 1 close earlier this week (before today) -> budget 2 -> passes
    rungs = [rung(entry_wk=WK),
             rung(status="closed", exit_ts="2026-08-10T20:00:00+00:00", exit_reason="pt")]
    assert _eval(iv=ivr(30.0), rungs=rungs).entered


def test_cooldown_skip_recent_stop():
    rungs = [rung(status="closed", exit_ts="2026-08-11T20:00:00+00:00", exit_reason="stop")]
    assert _eval(iv=ivr(30.0), rungs=rungs).skip_reason == SKIP_COOLDOWN


def test_cooldown_clears_after_window():
    rungs = [rung(status="closed", exit_ts="2026-08-06T20:00:00+00:00", exit_reason="stop")]
    assert _eval(iv=ivr(30.0), rungs=rungs).entered


def test_blackout_today():
    ev = [{"event_type": "CPI", "symbol_scope": "ALL", "event_date": "2026-08-12"}]
    assert _eval(iv=ivr(30.0), events=ev).skip_reason == SKIP_BLACKOUT


def test_blackout_next_session():
    ev = [{"event_type": "FOMC", "symbol_scope": "ALL", "event_date": "2026-08-13"}]
    assert _eval(iv=ivr(30.0), events=ev).skip_reason == SKIP_BLACKOUT


def test_blackout_type_not_in_symbol_list_passes():
    ev = [{"event_type": "OPEC", "symbol_scope": "ALL", "event_date": "2026-08-12"}]
    assert _eval(iv=ivr(30.0), events=ev).entered      # SPY list is [FOMC, CPI]


def test_blackout_out_of_window_passes():
    ev = [{"event_type": "CPI", "symbol_scope": "ALL", "event_date": "2026-08-20"}]
    assert _eval(iv=ivr(30.0), events=ev).entered


def test_ivr_below_floor_skips():
    r = _eval(iv=ivr(20.0))
    assert r.skip_reason == SKIP_IVR and r.ivr_status == IVR_OK


def test_ivr_stale_bypasses_filter_and_enters():
    r = _eval(iv=ivr(20.0, IVR_STALE))     # below floor, but STALE -> filter skipped
    assert r.entered and r.ivr_status == IVR_STALE


def test_ivr_unavailable_bypasses_filter_and_enters():
    r = _eval(iv=ivr(None, IVR_UNAVAILABLE))
    assert r.entered and r.ivr_status == IVR_UNAVAILABLE


# ── build (filter 6) + credit floor (7) ──────────────────────────────────

def test_no_expiry():
    ch = chain(expiries=(date(2026, 8, 20),))   # 8 DTE, out of [30,45]
    assert st.evaluate_entry("SPY", CFG, ctx(iv=ivr(30.0), ch=ch)).skip_reason == SKIP_NO_EXPIRY


def test_no_delta_strike():
    legs = [("put", 585, -0.05, 2.0, 2.1), ("call", 615, 0.05, 2.0, 2.1)]  # deltas outside band
    assert st.evaluate_entry("SPY", CFG, ctx(iv=ivr(30.0), ch=chain(legs))).skip_reason == SKIP_NO_DELTA_STRIKE


def test_no_wing_universal():
    legs = [l for l in _DEFAULT_LEGS if l[1] != 618]      # drop the long call wing
    assert st.evaluate_entry("SPY", CFG, ctx(iv=ivr(30.0), ch=chain(legs))).skip_reason == SKIP_NO_WING


def test_risk_band_skip_when_credit_too_high():
    # credit 2.20 -> (3-2.2)*100 = 80 < 150 -> risk_band
    legs = [
        ("put", 582, -0.15, 0.90, 1.00), ("put", 585, -0.20, 2.00, 2.10),
        ("call", 615, 0.20, 2.00, 2.10), ("call", 618, 0.15, 0.90, 1.00),
    ]
    assert st.evaluate_entry("SPY", CFG, ctx(iv=ivr(30.0), ch=chain(legs))).skip_reason == SKIP_RISK_BAND


def test_credit_floor_skip():
    # enforce_risk_band is on, so pick a credit that clears risk_band but fails
    # the 0.30*width=0.90 floor is impossible (risk_band lower bound 150 => credit<=1.5,
    # upper 250 => credit>=0.5). credit 0.80 -> risk 220 in band, 0.80 < 0.90 floor.
    legs = [
        ("put", 582, -0.15, 1.60, 1.70), ("put", 585, -0.20, 2.00, 2.10),
        ("call", 615, 0.20, 2.00, 2.10), ("call", 618, 0.15, 1.60, 1.70),
    ]
    r = st.evaluate_entry("SPY", CFG, ctx(iv=ivr(30.0), ch=chain(legs)))
    assert r.skip_reason == SKIP_CREDIT_FLOOR


# ── reserve (9) + risk gate (10) ─────────────────────────────────────────

def test_reserve_skip():
    # open risk 39_900 + candidate 200 > 0.80*50_000 = 40_000
    rungs = [rung(status="open", max_risk=39_900.0)]
    assert _eval(iv=ivr(30.0), rungs=rungs).skip_reason == SKIP_RESERVE


def test_risk_gate_reject():
    r = st.evaluate_entry("SPY", CFG, ctx(iv=ivr(30.0), risk_gate=lambda *a: False))
    assert r.skip_reason == SKIP_RISK_REJECT


def test_risk_gate_approve():
    assert st.evaluate_entry("SPY", CFG, ctx(iv=ivr(30.0), risk_gate=lambda *a: True)).entered


# ── FXI fallback width (build unit; enforce_risk_band off to isolate) ─────

def _cfg_no_riskband(tmp_path):
    import yaml
    d = yaml.safe_load(MACE_YAML.read_text(encoding="utf-8"))
    d["entry"]["enforce_risk_band"] = False
    p = tmp_path / "mace.yaml"
    p.write_text(yaml.safe_dump(d), encoding="utf-8")
    return load_mace_config(p, exdiv_calendar_path=EXDIV_YAML)


def test_fxi_fallback_width(tmp_path):
    cfg = _cfg_no_riskband(tmp_path)
    fxi_cfg = cfg.symbols["FXI"]           # width 2, fallback 1
    # shorts 30p/33c; width-2 wings (28p/35c) UNLISTED, width-1 wings (29p/34c) listed
    legs = [
        ("put", 29, -0.15, 0.30, 0.35), ("put", 30, -0.20, 0.55, 0.60),
        ("call", 33, 0.20, 0.55, 0.60), ("call", 34, 0.15, 0.30, 0.35),
    ]
    quotes = {(EXPIRY, o, float(k)): OptionQuote("FXI", EXPIRY, float(k), o, b, a, delta=d)
              for o, k, d, b, a in legs}
    ch = st.ChainView("FXI", spot=31.5, expiries=(EXPIRY,), quotes=quotes)
    b = st.build_condor("FXI", fxi_cfg, ch, cfg, SESSION)
    assert b.skip_reason is None and b.width == 1.0
    assert b.spec.long_put == 29 and b.spec.long_call == 34


# ── overflow routing (T6) — mechanics tested with risk_band off ───────────

def _cfg_overflow(tmp_path):
    """SPY + GLD primaries, IBIT overflow receiver, risk_band off (so width-2
    IBIT can enter — the shipped risk_band[150,250]+floor make width-2 inert)."""
    d = yaml.safe_load(MACE_YAML.read_text(encoding="utf-8"))
    d["entry"]["enforce_risk_band"] = False
    d["universe"] = ["SPY", "GLD"]
    d["symbols"]["GLD"]["enabled"] = True      # width 3, exdiv_guard false
    d["symbols"]["IBIT"]["enabled"] = True     # overflow_only, exdiv_guard false
    p = tmp_path / "mace.yaml"
    p.write_text(yaml.safe_dump(d), encoding="utf-8")
    return load_mace_config(p, exdiv_calendar_path=EXDIV_YAML)


def condor_chain(sym, sp, sc, width, short_mid, long_mid, spot):
    legs = [
        ("put", sp - width, -0.15, long_mid - 0.05, long_mid + 0.05),
        ("put", sp, -0.20, short_mid - 0.05, short_mid + 0.05),
        ("call", sc, 0.20, short_mid - 0.05, short_mid + 0.05),
        ("call", sc + width, 0.15, long_mid - 0.05, long_mid + 0.05),
    ]
    quotes = {(EXPIRY, o, float(k)): OptionQuote(sym, EXPIRY, float(k), o, b, a, delta=d)
              for o, k, d, b, a in legs}
    return st.ChainView(sym, spot, (EXPIRY,), quotes)


def ivr_for(sym, value, status=IVR_OK):
    return IvrReading(sym, status, value, 0.15, None, 0, "", "test")


def ctx_multi(chains, ivrs, rungs=(), equity=50_000.0):
    return st.EntryContext(session_date=SESSION, equity=equity, rungs=list(rungs),
                           events=[], ivr=ivrs, chains=chains, next_session_date=NEXT)


def _chains_ivrs():
    chains = {
        "SPY": condor_chain("SPY", 585, 615, 3, 2.05, 1.55, 600.0),
        "GLD": condor_chain("GLD", 244, 256, 3, 2.05, 1.55, 250.0),
        "IBIT": condor_chain("IBIT", 54, 66, 2, 0.70, 0.30, 60.0),
    }
    ivrs = {s: ivr_for(s, v) for s, v in (("SPY", 30.0), ("GLD", 28.0), ("IBIT", 40.0))}
    return chains, ivrs


def test_overflow_inert_at_launch():
    # real CFG: universe [SPY], IBIT disabled -> SPY forfeit routes nowhere
    prim = [EvalResult(symbol="SPY", entered=False, skip_reason=SKIP_BLACKOUT)]
    assert st.route_overflow(prim, CFG, ctx(iv=ivr(30.0))) == []


def test_overflow_routes_to_ibit_first(tmp_path):
    cfg = _cfg_overflow(tmp_path)
    chains, ivrs = _chains_ivrs()
    prim = [EvalResult(symbol="SPY", entered=False, skip_reason=SKIP_BLACKOUT),
            EvalResult(symbol="GLD", entered=True)]
    out = st.route_overflow(prim, cfg, ctx_multi(chains, ivrs))
    assert len(out) == 1 and out[0].symbol == "IBIT" and out[0].overflow is True


def test_overflow_falls_back_to_highest_ivr_primary(tmp_path):
    # 3 primaries: TLT forfeits (creates the forfeit); IBIT capped; eligible
    # receivers SPY(30) > GLD(28) by IVR -> SPY receives.
    d = yaml.safe_load(MACE_YAML.read_text(encoding="utf-8"))
    d["entry"]["enforce_risk_band"] = False
    d["universe"] = ["SPY", "GLD", "TLT"]
    d["symbols"]["GLD"]["enabled"] = True
    d["symbols"]["TLT"]["enabled"] = True       # width 2, exdiv_guard true, HAS dates
    d["symbols"]["IBIT"]["enabled"] = True
    p = tmp_path / "mace3.yaml"; p.write_text(yaml.safe_dump(d), encoding="utf-8")
    cfg = load_mace_config(p, exdiv_calendar_path=EXDIV_YAML)
    chains = {
        "SPY": condor_chain("SPY", 585, 615, 3, 2.05, 1.55, 600.0),
        "GLD": condor_chain("GLD", 244, 256, 3, 2.05, 1.55, 250.0),
        "TLT": condor_chain("TLT", 84, 96, 2, 0.70, 0.30, 90.0),
        "IBIT": condor_chain("IBIT", 54, 66, 2, 0.70, 0.30, 60.0),
    }
    ivrs = {s: ivr_for(s, v) for s, v in
            (("SPY", 30.0), ("GLD", 28.0), ("TLT", 26.0), ("IBIT", 40.0))}
    ibit_rungs = [rung(symbol="IBIT", status="open") for _ in range(cfg.entry.ibit_overflow_cap)]
    prim = [EvalResult(symbol="SPY", entered=True),
            EvalResult(symbol="GLD", entered=True),
            EvalResult(symbol="TLT", entered=False, skip_reason=SKIP_BLACKOUT)]
    out = st.route_overflow(prim, cfg, ctx_multi(chains, ivrs, rungs=ibit_rungs))
    assert len(out) == 1 and out[0].symbol == "SPY" and out[0].overflow


def test_overflow_exempts_weekly_budget(tmp_path):
    cfg = _cfg_overflow(tmp_path)
    chains, ivrs = _chains_ivrs()
    # IBIT weekly budget exhausted; overflow (is_overflow) must skip weekly and enter
    ibit_wk = [rung(symbol="IBIT", status="closed", entry_wk=WK,
                    exit_ts="2026-08-10T20:00:00+00:00", exit_reason="pt")]
    prim = [EvalResult(symbol="SPY", entered=False, skip_reason=SKIP_BLACKOUT)]
    out = st.route_overflow(prim, cfg, ctx_multi(chains, ivrs, rungs=ibit_wk))
    assert len(out) == 1 and out[0].symbol == "IBIT"


def test_overflow_excludes_forfeiting_symbol(tmp_path):
    cfg = _cfg_overflow(tmp_path)
    chains, ivrs = _chains_ivrs()
    # Disable IBIT so the ONLY candidate receiver would be a primary. GLD forfeits
    # (weekly) -> GLD must NOT receive its own capital back. SPY entered -> eligible.
    d = yaml.safe_load(MACE_YAML.read_text(encoding="utf-8"))
    d["entry"]["enforce_risk_band"] = False
    d["universe"] = ["SPY", "GLD"]
    d["symbols"]["GLD"]["enabled"] = True
    p = tmp_path / "mace2.yaml"; p.write_text(yaml.safe_dump(d), encoding="utf-8")
    cfg2 = load_mace_config(p, exdiv_calendar_path=EXDIV_YAML)
    prim = [EvalResult(symbol="SPY", entered=True),
            EvalResult(symbol="GLD", entered=False, skip_reason=SKIP_WEEKLY_BUDGET)]
    out = st.route_overflow(prim, cfg2, ctx_multi(chains, ivrs))
    assert all(r.symbol != "GLD" for r in out)      # GLD excluded; SPY may receive
