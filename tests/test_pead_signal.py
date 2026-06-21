"""Unit tests for the SRW-SUE signal (`pead_signal.py`).

Pins the exact SUE arithmetic on hand-computed fixtures, the screen
rejection reasons, and the threshold + top-quintile + ranking selection.
Pure module — no IO, no fixtures beyond literals.
"""
from __future__ import annotations

import pytest

from trading_corp.agents.strategies.pead_signal import (
    PeadCandidate,
    ScreenInputs,
    ScreenParams,
    SueParams,
    _percentile,
    passes_screen,
    rank_wave,
    select_candidates,
    standardized_ue,
    unexpected_earnings,
)


# ---------------------------------------------------------------------------
# SUE math
# ---------------------------------------------------------------------------

def test_unexpected_earnings_is_seasonal_year_over_year_diff():
    # 4 quarters at 1.0 then 4 quarters at 2.0 -> each UE = 2.0 - 1.0 = 1.0
    eps = [1, 1, 1, 1, 2, 2, 2, 2]
    assert unexpected_earnings(eps) == [1.0, 1.0, 1.0, 1.0]


def test_unexpected_earnings_too_short_is_empty():
    assert unexpected_earnings([1, 2, 3, 4]) == []  # need >= 5 quarters


def test_standardized_ue_hand_computed():
    # eps -> UE = [2,1,3,1,4]; latest=4; denominator window (lookback=3,
    # exclusive of latest) = [1,3,1]; sample stdev = 1.15470; SUE = 4/1.1547.
    eps = [10, 10, 10, 10, 12, 11, 13, 11, 16]
    sue = standardized_ue(eps, lookback=3)
    assert sue == pytest.approx(3.4641, rel=1e-3)


def test_standardized_ue_insufficient_history_returns_none():
    # lookback=8 needs >= lookback+1 = 9 UE values -> >= 13 quarters of EPS.
    eps = [10, 10, 10, 10, 11, 12, 13, 14]  # only 8 quarters -> 4 UE values
    assert standardized_ue(eps, lookback=8) is None


def test_standardized_ue_degenerate_denominator_returns_none():
    # prior UE window is constant -> stdev 0 -> cannot standardize -> None.
    eps = [10, 10, 10, 10, 11, 11, 11, 11, 15]  # UE=[1,1,1,1,4], window=[1,1,1]
    assert standardized_ue(eps, lookback=3) is None


def test_standardized_ue_rejects_tiny_lookback():
    with pytest.raises(ValueError):
        standardized_ue([1, 2, 3, 4, 5, 6, 7, 8, 9], lookback=1)


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

def _ok_inputs(**overrides) -> ScreenInputs:
    base = dict(
        symbol="AAA",
        price=50.0,
        avg_daily_volume_30d=2_000_000.0,
        market_cap=5_000_000_000.0,
        sector="Technology",
        guidance_cut=False,
        days_to_next_earnings=90,
    )
    base.update(overrides)
    return ScreenInputs(**base)


def test_passes_screen_clean():
    assert passes_screen(_ok_inputs(), ScreenParams()) == (True, "ok")


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"price": 5.0}, "price_below_min"),
        ({"price": None}, "missing_price"),
        ({"avg_daily_volume_30d": 500_000.0}, "volume_below_min"),
        ({"avg_daily_volume_30d": None}, "missing_volume"),
        ({"market_cap": 500_000_000.0}, "mktcap_below_min"),
        ({"market_cap": None}, "missing_market_cap"),
        ({"sector": "Utilities"}, "excluded_sector"),
        ({"sector": "Financial Services"}, "excluded_sector"),
        ({"guidance_cut": True}, "guidance_cut"),
        ({"days_to_next_earnings": 30}, "earnings_too_soon"),
    ],
)
def test_passes_screen_rejection_reasons(overrides, reason):
    ok, got = passes_screen(_ok_inputs(**overrides), ScreenParams())
    assert ok is False
    assert got == reason


def test_passes_screen_lenient_on_missing_soft_fields():
    # sector/guidance/next-earnings all None -> not blocked (lenient).
    inp = _ok_inputs(sector=None, guidance_cut=None, days_to_next_earnings=None)
    assert passes_screen(inp, ScreenParams()) == (True, "ok")


# ---------------------------------------------------------------------------
# Selection / ranking
# ---------------------------------------------------------------------------

def test_percentile_linear_interpolation():
    vals = [float(i) for i in range(1, 11)]  # 1..10 ascending
    assert _percentile(vals, 0.80) == pytest.approx(8.2)
    assert _percentile([3.0], 0.5) == 3.0


def test_select_candidates_threshold_and_top_quintile_and_ranking():
    sues = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
    wave = [PeadCandidate(f"S{i}", s, True) for i, s in enumerate(sues)]
    # p80 cutoff = 4.1; AND sue > 1.5 -> only 4.5 and 5.0 survive, ranked desc.
    out = select_candidates(wave, SueParams(sue_threshold=1.5, top_quintile=True))
    assert [c.symbol for c in out] == ["S9", "S8"]
    assert [c.sue for c in out] == [5.0, 4.5]


def test_select_candidates_threshold_only_when_quintile_off():
    sues = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
    wave = [PeadCandidate(f"S{i}", s, True) for i, s in enumerate(sues)]
    out = select_candidates(wave, SueParams(sue_threshold=1.5, top_quintile=False))
    # sue > 1.5 -> 2.0..5.0 (8 names), ranked desc.
    assert [c.sue for c in out] == [5.0, 4.5, 4.0, 3.5, 3.0, 2.5, 2.0]


def test_select_candidates_excludes_screen_failures_and_none_sue():
    wave = [
        PeadCandidate("PASS", 9.0, True),
        PeadCandidate("SCREENED_OUT", 9.0, False, "excluded_sector"),
        PeadCandidate("NO_SUE", None, True),
    ]
    out = select_candidates(wave, SueParams(sue_threshold=1.5, top_quintile=False))
    assert [c.symbol for c in out] == ["PASS"]


def test_rank_wave_integration_threshold_and_screen():
    eps_strong = [10, 10, 10, 10, 12, 11, 13, 11, 16]   # SUE ~3.46 (lookback=3)
    eps_weak = [10, 10, 10, 10, 12, 11, 13, 11, 11.5]   # SUE ~1.30 -> below 1.5
    eps_by_symbol = {
        "STRONG": eps_strong,
        "WEAK": eps_weak,
        "BLOCKED": eps_strong,  # strong SUE but fails the sector screen
    }
    screens = {
        "STRONG": _ok_inputs(symbol="STRONG"),
        "WEAK": _ok_inputs(symbol="WEAK"),
        "BLOCKED": _ok_inputs(symbol="BLOCKED", sector="Utilities"),
    }
    out = rank_wave(
        eps_by_symbol,
        screens,
        sue_params=SueParams(lookback=3, sue_threshold=1.5, top_quintile=False),
        screen_params=ScreenParams(),
    )
    assert [c.symbol for c in out] == ["STRONG"]


def test_rank_wave_missing_screen_inputs_excluded():
    out = rank_wave(
        {"X": [10, 10, 10, 10, 12, 11, 13, 11, 16]},
        {},  # no screen inputs for X
        sue_params=SueParams(lookback=3, sue_threshold=1.5, top_quintile=False),
    )
    assert out == []
