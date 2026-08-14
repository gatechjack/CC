"""Unit tests for the /mace view's pure per-rung analytics (UI rebuild).

These cover the derived math the reskin renders — P&L, distance-to-PT/stop, the
PT/stop PROGRESS ratios (mock parity), max profit/loss, breakevens, POP, rail
%s, IV source selection, and the honest empty state when live mark is absent.
No DB or FastAPI — the enrichment is pure arithmetic over a rung dict.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from trading_corp.web import mace_view as mv

UTC = timezone.utc
NOW = datetime(2026, 8, 14, 18, 0, tzinfo=UTC)
TODAY = date(2026, 8, 14)

# The mock's SPY-R1: 739/742P · 802/805C, credit 0.93, mark 0.71, pt 0.47.
_LEGS = json.dumps([
    {"type": "put", "side": "sell", "strike": 742},
    {"type": "put", "side": "buy", "strike": 739},
    {"type": "call", "side": "sell", "strike": 802},
    {"type": "call", "side": "buy", "strike": 805},
])


def _rung(**over):
    r = {"rung_id": "SPY-R1", "symbol": "SPY", "status": "open",
         "expiry": "2026-09-25", "legs_json": _LEGS, "width_dollars": 3.0,
         "contracts": 1, "credit_actual": 0.93, "max_risk_usd": 207.0,
         "pt_debit": 0.47, "entry_ts": "2026-08-04T13:31:00+00:00",
         "entry_atm_iv": 0.142}
    r.update(over)
    return r


def _en(rung, live=None, iv_daily=None):
    return mv._enrich_one(rung, live, iv_daily or {}, 0.5, 2.0, 21, 600.0,
                          TODAY, NOW)


def test_structured_strikes():
    sk = mv._structured_strikes(_LEGS)
    assert sk == {"sp": 742.0, "lp": 739.0, "sc": 802.0, "lc": 805.0}


def test_enrich_with_live_mark():
    live = {"mark": 0.71, "spot": 778.08, "ts": "2026-08-14T18:00:00+00:00"}
    en = _en(_rung(), live)
    assert en["strikes_label"] == "742/739P 802/805C"
    assert en["stale"] is False
    assert en["pnl"] == pytest.approx(22.0)                 # (0.93-0.71)*100
    assert en["dist_pt"] == pytest.approx(24.0)             # (0.71-0.47)*100
    assert en["dist_stop"] == pytest.approx(115.0)          # (1.86-0.71)*100
    assert en["pt_prog"] == pytest.approx(0.22 / 0.46)      # (c-mark)/(c-pt)
    assert en["stop_prog"] == 0.0                           # mark<credit -> clamped 0
    assert en["stop_level"] == pytest.approx(1.86)          # 2*credit
    assert en["max_profit"] == pytest.approx(93.0)          # credit*100
    assert en["max_loss"] == pytest.approx(207.0)           # stored max_risk
    assert en["be_low"] == pytest.approx(741.07)            # sp-credit
    assert en["be_high"] == pytest.approx(802.93)           # sc+credit
    assert en["iv"] == 0.142 and en["iv_source"] == "entry"
    # rail: spot 778.08 sits inside [739-8, 805+8] -> a sane 0..100 %
    assert 0 < en["rail"]["spot"] < 100
    assert en["payoff"]["lo"] == 721.0 and en["payoff"]["hi"] == 823.0


def test_enrich_without_live_mark_is_honest_empty():
    """No mace_rung_live row yet -> live fields None, stale=True, NO fabricated
    P&L. Static analytics (strikes, max profit/loss, breakevens) still present."""
    en = _en(_rung(), live=None)
    assert en["mark"] is None and en["spot"] is None
    assert en["stale"] is True
    assert en["pnl"] is None and en["dist_pt"] is None and en["pt_prog"] is None
    assert en["max_profit"] == pytest.approx(93.0)          # still computed
    assert en["be_low"] == pytest.approx(741.07)


def test_iv_falls_back_to_daily_when_entry_iv_absent():
    """The 2 legacy SPY rungs have no entry_atm_iv -> use the fresh daily IV
    (A4), labeled 'daily' with its as-of date."""
    en = _en(_rung(entry_atm_iv=None),
             live={"mark": 0.71, "spot": 778.0, "ts": "2026-08-14T18:00:00+00:00"},
             iv_daily={"SPY": {"atm_iv": 0.147, "snap_date": "2026-08-14"}})
    assert en["iv"] == 0.147
    assert en["iv_source"] == "daily" and en["iv_asof"] == "2026-08-14"


def test_stale_flag_when_tick_old():
    old = {"mark": 0.71, "spot": 778.0, "ts": "2026-08-14T15:00:00+00:00"}  # 3h old
    en = _en(_rung(), old)
    assert en["stale"] is True                              # > 600s -> stale badge
    assert en["age_sec"] > 600


def test_pop_between_breakevens_is_reasonable():
    # spot inside the profit zone, 44 DTE, iv 0.14 -> a sane majority POP.
    # (Hand-checked: Phi(0.664)-Phi(-0.962) = 0.579 — off-center condor, spot 778
    # vs strike-center 772, so it is < the mock's arbitrary 0.78 static value.)
    p = mv._pop(778.0, 0.142, 44 / 365.0, 741.07, 802.93)
    assert p is not None and 0.5 < p < 0.9
    # spot deep past the call wing -> low POP
    p2 = mv._pop(900.0, 0.142, 44 / 365.0, 741.07, 802.93)
    assert p2 is not None and p2 < 0.1
    # a tighter/centered condor -> higher POP than the off-center one
    p3 = mv._pop(772.0, 0.142, 44 / 365.0, 741.07, 802.93)
    assert p3 > p


def test_pop_none_without_spot_or_iv():
    assert mv._pop(None, 0.14, 0.1, 740, 803) is None
    assert mv._pop(778.0, None, 0.1, 740, 803) is None
