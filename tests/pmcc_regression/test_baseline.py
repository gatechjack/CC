"""Phase-0: the pathology detectors reproduce the audit baseline EXACTLY against
the frozen 157-row history. This is the structural regression floor every later
Bucket-B phase re-runs (targeted pathology asserted absent, the rest unchanged).
No `pmcc_robinhood.py` behavior is exercised here.
"""
from tests.pmcc_regression import detectors as D
from tests.pmcc_regression.baseline import BASELINE, load_records


def test_total_counts():
    recs = load_records()
    assert len(recs) == BASELINE["total_recs"]
    assert D.count_over(recs, D.is_roll) == BASELINE["total_rolls"]


def test_close_without_recover_b4():
    assert D.count_over(load_records(), D.close_without_recover) == \
        BASELINE["close_without_recover"]


def test_b4_subtypes_partition_the_51():
    recs = load_records()
    unc = D.count_over(recs, D.b4_uncovered)
    fn = D.count_over(recs, D.b4_fully_naked)
    assert unc == BASELINE["b4_uncovered"]
    assert fn == BASELINE["b4_fully_naked"]
    # the two subtypes partition close_without_recover exactly
    assert unc + fn == BASELINE["close_without_recover"]


def test_b4_naked_short_is_zero_and_must_stay_zero():
    assert D.count_over(load_records(), D.b4_naked_short) == \
        BASELINE["b4_naked_short"] == 0


def test_hold_overridden_b1():
    assert D.count_over(load_records(), D.hold_overridden) == \
        BASELINE["hold_overridden"]


def test_same_expiry_roll_b7():
    assert D.count_over(load_records(), D.same_expiry_roll) == \
        BASELINE["same_expiry_roll"]


def test_net_debit_roll_b2():
    assert D.count_over(load_records(), D.net_debit_roll) == \
        BASELINE["net_debit_roll"]


def test_cost_ignorant_leap_roll_b3():
    assert D.count_over(load_records(), D.cost_ignorant_leap_roll) == \
        BASELINE["cost_ignorant_leap_roll"]


def test_holiday_scan_b11():
    assert D.count_over(load_records(), D.holiday_scan) == \
        BASELINE["holiday_scan"]


def test_short_delta_ge_040_b5():
    assert D.count_over(load_records(), D.short_delta_ge_040) == \
        BASELINE["short_delta_ge_040"]


def test_itm_target_strike_bypass_has_no_historical_baseline():
    # target_strike + spot were never persisted, so no historical row trips this
    # detector; it is exercised synthetically in Phase 2.
    assert D.count_over(load_records(), D.itm_target_strike_bypass) == 0
    assert BASELINE["itm_target_strike_bypass"] is None


def test_override_field_suppresses_hold_and_debit_detectors():
    # Forward-compat guard: a rec the LLM explicitly authorized via the Phase-2
    # override field must NOT count as a pathology.
    authorized_hold = D.RecRecord(
        rec_type="roll_short", llm_action="HOLD", new_strike=9.0,
        override_kind="hold_override",
    )
    authorized_debit = D.RecRecord(
        rec_type="roll_short", new_strike=9.0, net_cash_sh=-0.21,
        override_kind="net_debit_justified",
    )
    assert D.hold_overridden(authorized_hold) is False
    assert D.net_debit_roll(authorized_debit) is False


def test_from_legs_normalizes_code_output_for_phase1():
    # Phase-1 pattern: build a record from the code's ACTUAL proposed legs.
    # A short-roll that closed the short but found no new weekly -> uncovered B4.
    naked_roll = D.RecRecord.from_legs(
        [{"action": "roll_short_call_close", "strike": 8.5, "expiration": "2026-07-24"}],
        llm_action="ROLL_SHORT",
    )
    assert D.close_without_recover(naked_roll) is True
    assert D.b4_uncovered(naked_roll) is True
    assert D.b4_fully_naked(naked_roll) is False
    # A healthy roll that re-opened a later, higher short -> no pathology.
    healthy = D.RecRecord.from_legs([
        {"action": "roll_short_call_close", "strike": 8.5, "expiration": "2026-07-24"},
        {"action": "roll_short_call_open", "strike": 9.0, "expiration": "2026-07-31"},
    ], llm_action="ROLL_SHORT")
    assert D.close_without_recover(healthy) is False
    assert D.same_expiry_roll(healthy) is False
