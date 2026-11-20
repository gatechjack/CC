"""Gate-0 sizing-basis fix (2026-11-19).

The 2026-11-19 15:40 snapshot recorded equity 0.0 while available_buying_power
was 924.22, and every symbol skipped on no_equity_snapshot — a blanked round on
a healthy sizing basis. Gate 0 now skips only when NO usable basis exists:

  equity 0/None + usable available BP  -> round proceeds (sizes on BP)
  equity 0/None + no usable BP         -> still skips (unchanged fail-safe)
  equity valid  + no BP (legacy)       -> proceeds on equity (unchanged fallback)
  both valid                           -> unchanged
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from trading_corp.mace import strategy as st
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import IVR_OK, OptionQuote, SKIP_NO_EQUITY_SNAPSHOT
from trading_corp.mace.ivr_provider import IvrReading

ROOT = Path(__file__).resolve().parents[1]
CFG = load_mace_config(ROOT / "config" / "mace.yaml",
                       exdiv_calendar_path=ROOT / "config" / "ex_dividend_calendar.yaml")

SESSION = date(2026, 11, 19)
NEXT = date(2026, 11, 20)
EXPIRY = date(2026, 12, 24)          # 35 DTE from SESSION (inside [30, 45])

_LEGS = [
    ("put", 582, -0.15, 1.50, 1.60),
    ("put", 585, -0.20, 2.00, 2.10),
    ("put", 588, -0.25, 2.60, 2.70),
    ("call", 612, 0.25, 2.60, 2.70),
    ("call", 615, 0.20, 2.00, 2.10),
    ("call", 618, 0.15, 1.50, 1.60),
]


def _chain():
    quotes = {(EXPIRY, opt, float(k)): OptionQuote(
        symbol="SPY", expiry=EXPIRY, strike=float(k), opt_type=opt,
        bid=b, ask=a, delta=d) for opt, k, d, b, a in _LEGS}
    return st.ChainView("SPY", spot=600.0, expiries=(EXPIRY,), quotes=quotes)


def _ctx(equity, abp):
    return st.EntryContext(
        session_date=SESSION, equity=equity, rungs=[], events=[],
        ivr={"SPY": IvrReading("SPY", IVR_OK, 30.0, 0.15, None, 0, "", "test")},
        chains={"SPY": _chain()}, next_session_date=NEXT,
        available_buying_power=abp)


def _skip_reason(equity, abp):
    return st.evaluate_entry("SPY", CFG, _ctx(equity, abp)).skip_reason


def test_premise_shipped_basis_is_available_bp():
    # The fix targets the Option-A basis the shipped config runs on.
    assert CFG.sizing.deployment_basis == "available_buying_power"


def test_zero_equity_with_valid_bp_no_longer_skips():
    # The 2026-11-19 incident shape: equity 0.0, ABP 924.22 -> must NOT blank.
    assert _skip_reason(0.0, 924.22) != SKIP_NO_EQUITY_SNAPSHOT


def test_missing_equity_with_valid_bp_no_longer_skips():
    assert _skip_reason(None, 924.22) != SKIP_NO_EQUITY_SNAPSHOT


def test_no_usable_basis_still_skips():
    # Missing/zero buying power with unusable equity: the fail-safe skip stands.
    assert _skip_reason(None, None) == SKIP_NO_EQUITY_SNAPSHOT
    assert _skip_reason(0.0, None) == SKIP_NO_EQUITY_SNAPSHOT
    assert _skip_reason(None, 0.0) == SKIP_NO_EQUITY_SNAPSHOT


def test_legacy_equity_fallback_unchanged():
    # No BP captured but a healthy equity snapshot: gate passes (equity basis).
    assert _skip_reason(50_000.0, None) != SKIP_NO_EQUITY_SNAPSHOT


def test_normal_case_unchanged():
    assert _skip_reason(50_000.0, 924.22) != SKIP_NO_EQUITY_SNAPSHOT
