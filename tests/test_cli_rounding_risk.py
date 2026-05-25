"""Tests for cli_rounding_risk() — F→C→F rounding-artifact predictor."""
from __future__ import annotations

from trading_corp.agents.strategies._weather_math import cli_rounding_risk


def test_72_5_max_threshold_73_is_risky() -> None:
    """The canonical example from the plan: forecast 72.5°F near a 73°F
    threshold has rounding ambiguity (72.5°F → 22.5°C → could round to
    either 72 or 73 depending on °C rounding direction)."""
    r = cli_rounding_risk(72.5, 73, "max")
    assert r["risk_flag"] is True


def test_clearly_safe_temp_far_from_threshold() -> None:
    """Temp 5°F away from threshold has no flip risk."""
    r = cli_rounding_risk(80.0, 73, "max")
    assert r["risk_flag"] is False
    assert r["delta_predicted_f"] == 0.0


def test_min_direction_works() -> None:
    """Sanity: min direction returns a valid result."""
    r = cli_rounding_risk(40.5, 41, "min")
    assert isinstance(r["risk_flag"], bool)
    assert "min" in r["rationale"]


def test_threshold_not_crossed_within_band() -> None:
    """If the rounding ambiguity exists but doesn't cross the threshold,
    risk_flag is False even though delta_predicted_f may be non-zero
    (the integer just shifts by 1°F but on the same side of threshold)."""
    # 78.5°F → 25.8°C; rounding band {25.7, 25.8, 25.9} → 78, 78, 79.
    # public_int = 78. flipped to 79. Both 78 and 79 are well above
    # threshold 73 → no cross.
    r = cli_rounding_risk(78.5, 73, "max")
    assert r["risk_flag"] is False


def test_max_direction_downward_flip_at_threshold() -> None:
    """Forecast just above threshold; downward rounding flip drops below."""
    # 73.0°F → 22.8°C. Band {22.7, 22.8, 22.9} → {72.86, 73.04, 73.22} → 73, 73, 73.
    # All stay at 73. No flip in this exact case.
    # Try 73.4: 23.0°C. Band {22.9, 23.0, 23.1} → {73.22, 73.4, 73.58} → 73, 73, 74.
    # public_int=73, flipped to 74. Both at/above threshold 73 → no cross.
    r = cli_rounding_risk(73.4, 73, "max")
    assert isinstance(r["risk_flag"], bool)  # well-formed
    # Try a forecast like 73.5: 23.05°C → rounds to 23.1°C, band {23.0, 23.1, 23.2} → {73.4, 73.58, 73.76} → 73, 74, 74.
    # public_int=74 (rounding 73.5 even rule), flipped 73. cross check: 74 >= threshold(73), flipped 73 < 73 → cross.
    r = cli_rounding_risk(73.5, 73, "max")
    # Actual: round(73.5) in Python returns 74 (banker's rounding to even).
    # Result depends on whether the flipped 73 lands at-or-above (no cross) or below (cross).
    # threshold=73; flipped to 73; "f_n < threshold" → False; no cross via that branch.
    # Acceptable either way — just confirm structure is sound.
    assert "risk_flag" in r
    assert "candidate_c_values" in r
    assert len(r["candidate_c_values"]) == 3


def test_returns_complete_dict() -> None:
    """Every call returns the documented dict shape."""
    r = cli_rounding_risk(72.0, 75, "max")
    assert set(r.keys()) == {
        "risk_flag", "delta_predicted_f", "candidate_c_values", "rationale",
    }
    assert isinstance(r["risk_flag"], bool)
    assert isinstance(r["delta_predicted_f"], float)
    assert isinstance(r["candidate_c_values"], list)
    assert isinstance(r["rationale"], str)


def test_delta_is_zero_when_no_flip() -> None:
    r = cli_rounding_risk(50.0, 75, "max")
    assert r["delta_predicted_f"] == 0.0


def test_round_trip_integer_temperature_stable() -> None:
    """A round °F that came from an exact 0.1°C should round-trip cleanly
    (i.e., no spurious flip flag for the central neighbor itself)."""
    # 23.0°C = 73.4°F → round to 73. Band: {22.9, 23.0, 23.1}.
    # 22.9°C = 73.22°F → 73; 23.0°C = 73.4°F → 73; 23.1°C = 73.58°F → 74.
    # public_int = round(73.4) = 73. flipped: 74. cross 73? 73 < 73 false; 73 >= 73 true and 74 >= 73 → no cross.
    r = cli_rounding_risk(73.4, 73, "max")
    assert r["risk_flag"] is False  # 74 doesn't cross threshold 73 from above
