"""Hand-worked tests for trading_corp.agents.strategies._sports_math.

Per [[kalshi-crypto-shelved]]: EV-at-fill is the load-bearing metric
for the sports arbitrage division. These tests are the calibration
checkpoint — a systematic fee-formula or fill-side error here would
poison every observer row and verdict downstream.
"""
from __future__ import annotations

import math

import pytest

from trading_corp.agents.strategies._sports_math import (
    EVResult,
    LegFill,
    american_to_decimal,
    american_to_implied_raw,
    compute_ev_at_fill_a_arb,
    compute_ev_at_fill_b_directional,
    kalshi_fee,
)


# ── Fee: hand-worked Kalshi fee table ─────────────────────────────────────

class TestKalshiFee:
    """Kalshi taker fee = ceil(0.07 × C × P × (1−P) × 100) / 100.
    Verified by hand against Kalshi fee schedule.
    """

    def test_p_50_c_10_rounds_up_to_18_cents(self):
        # raw = 0.07 × 10 × 0.5 × 0.5 = 0.175 → ceil to 0.18
        assert kalshi_fee(10, 0.50) == 0.18

    def test_p_30_c_10_rounds_up_to_15_cents(self):
        # raw = 0.07 × 10 × 0.3 × 0.7 = 0.147 → ceil to 0.15
        assert kalshi_fee(10, 0.30) == 0.15

    def test_p_78_c_10_rounds_up_to_13_cents(self):
        # raw = 0.07 × 10 × 0.78 × 0.22 = 0.12012 → ceil to 0.13
        assert kalshi_fee(10, 0.78) == 0.13

    def test_p_50_c_1_rounds_up_to_2_cents(self):
        # raw = 0.07 × 1 × 0.5 × 0.5 = 0.0175 → ceil to 0.02
        assert kalshi_fee(1, 0.50) == 0.02

    def test_p_50_c_25_rounds_up_to_44_cents(self):
        # raw = 0.07 × 25 × 0.5 × 0.5 = 0.4375 → ceil to 0.44
        assert kalshi_fee(25, 0.50) == 0.44

    def test_p_99_extreme_favorite_rounds_up_to_2_cents_at_25(self):
        # raw = 0.07 × 25 × 0.99 × 0.01 = 0.017325 → ceil to 0.02
        assert kalshi_fee(25, 0.99) == 0.02

    def test_p_0_or_1_returns_zero(self):
        # 0×anything or anything×0 → 0
        assert kalshi_fee(10, 0.0) == 0.0
        assert kalshi_fee(10, 1.0) == 0.0

    def test_invalid_inputs_return_zero(self):
        assert kalshi_fee(0, 0.5) == 0.0
        assert kalshi_fee(-5, 0.5) == 0.0
        assert kalshi_fee(10, -0.1) == 0.0
        assert kalshi_fee(10, 1.1) == 0.0


# ── American-odds conversion ─────────────────────────────────────────────

class TestAmericanOddsConversion:
    def test_plus_100_is_decimal_2(self):
        assert american_to_decimal(100) == 2.0
        assert american_to_implied_raw(100) == 0.5

    def test_plus_200_underdog(self):
        # +200 → 1 + 200/100 = 3.0; implied = 1/3 = 0.333
        assert american_to_decimal(200) == 3.0
        assert american_to_implied_raw(200) == pytest.approx(1.0 / 3.0)

    def test_minus_200_favorite(self):
        # -200 → 1 + 100/200 = 1.5; implied = 1/1.5 = 0.667
        assert american_to_decimal(-200) == 1.5
        assert american_to_implied_raw(-200) == pytest.approx(2.0 / 3.0)

    def test_minus_110_standard_juice(self):
        # -110 → 1 + 100/110 = 1.909...; implied = 0.5238
        assert american_to_decimal(-110) == pytest.approx(1.0 + 100.0 / 110.0)
        assert american_to_implied_raw(-110) == pytest.approx(110.0 / 210.0)

    def test_invalid_range_raises(self):
        with pytest.raises(ValueError):
            american_to_decimal(0)
        with pytest.raises(ValueError):
            american_to_decimal(50)
        with pytest.raises(ValueError):
            american_to_decimal(-99)


# ── A-arb: hand-worked guaranteed-profit + after-fees-loss ───────────────

class TestAArb:
    """Two-leg arb: Kalshi YES + book opposing side, equal qty sizing.

    Pricing convention: each leg sized to pay qty dollars if it wins.
    Kalshi: pay yes_ask × qty + fee. Book opposing: stake qty / decimal_odds.
    Total cost = kalshi cost + book cost. Arb iff cost < qty.
    """

    def test_clean_arb_no_fee_loss(self):
        # Kalshi YES ask $0.45 × 10 = $4.50 + fee
        # Book opposing at +120 → decimal 2.20 → stake 10/2.20 = $4.545
        # Total cost = 4.50 + 0.17 fee + 4.545 = $9.215; payoff = $10
        # EV = +$0.785 → arb
        fee = kalshi_fee(10, 0.45)            # 0.07×10×0.45×0.55 = 0.1733 → ceil 0.18
        assert fee == 0.18                    # sanity-check our fee
        kalshi = LegFill("kalshi", "yes", qty=10, price_per_unit=0.45, fee=fee)
        book = LegFill(
            "draftkings", "away", qty=10,
            price_per_unit=1.0 / american_to_decimal(120),
        )
        result = compute_ev_at_fill_a_arb(kalshi, book)
        # Total cost: 4.50 + 0.18 + 10/2.20 = 4.68 + 4.5455 = 9.2255
        expected_cost = 4.50 + 0.18 + 10.0 / 2.20
        assert result.cost_paid == pytest.approx(expected_cost, abs=1e-3)
        assert result.expected_payoff == 10.0
        assert result.ev_dollars == pytest.approx(10.0 - expected_cost, abs=1e-3)
        assert result.is_arb is True
        assert result.hypothesis == "A_arb"

    def test_negative_arb_after_fees_at_tiny_sizing(self):
        # Tiny-capital case the user flagged: at $1-$25, fees eat the edge.
        # Kalshi YES $0.50 × 10 = $5.00 + $0.18 fee
        # Book opposing at -110 → decimal 1.909 → stake 10/1.909 = $5.238
        # Total cost = $10.418; payoff $10. EV = -$0.42 — NOT an arb.
        kalshi = LegFill("kalshi", "yes", qty=10, price_per_unit=0.50, fee=kalshi_fee(10, 0.50))
        book = LegFill("fanduel", "away", qty=10, price_per_unit=110.0 / 210.0)
        result = compute_ev_at_fill_a_arb(kalshi, book)
        assert result.ev_dollars < 0
        assert result.is_arb is False
        # Demonstrates why the Verdict's "fraction surviving after fees"
        # metric matters — gross-divergence numbers will look promising
        # while EV-at-fill says no.

    def test_unequal_qty_raises(self):
        k = LegFill("kalshi", "yes", qty=10, price_per_unit=0.5)
        b = LegFill("draftkings", "away", qty=5, price_per_unit=0.5)
        with pytest.raises(ValueError, match="equal qty"):
            compute_ev_at_fill_a_arb(k, b)

    def test_book_leg_with_kalshi_venue_raises(self):
        k = LegFill("kalshi", "yes", qty=10, price_per_unit=0.5)
        b = LegFill("kalshi", "no", qty=10, price_per_unit=0.55)
        with pytest.raises(ValueError, match="book_leg cannot"):
            compute_ev_at_fill_a_arb(k, b)


# ── B-leadlag: hand-worked directional EV ────────────────────────────────

class TestBLeadLag:
    """One-leg directional bet using model_prob from sharp-book proxy.

    EV = qty × model_prob − qty × kalshi_ask − fee.
    """

    def test_clean_positive_ev(self):
        # Sharp book says Lakers ML implies 0.65; Kalshi YES ask $0.55
        # qty=10. EV = 10×0.65 − 10×0.55 − fee = 6.50 − 5.50 − fee = 1.00 − fee
        # fee at p=0.55: 0.07×10×0.55×0.45 = 0.17325 → ceil 0.18
        # So EV = +$0.82
        kalshi = LegFill("kalshi", "yes", qty=10, price_per_unit=0.55, fee=kalshi_fee(10, 0.55))
        assert kalshi.fee == 0.18
        result = compute_ev_at_fill_b_directional(kalshi, model_prob_outcome=0.65)
        assert result.ev_dollars == pytest.approx(0.82, abs=1e-3)
        assert result.is_arb is False
        assert result.hypothesis == "B_leadlag"

    def test_negative_ev_when_model_below_ask(self):
        # Sharp book says 0.40 but Kalshi ask is 0.55 → bad bet
        # EV = 10×0.40 − 10×0.55 − 0.18 = -1.68
        kalshi = LegFill("kalshi", "yes", qty=10, price_per_unit=0.55, fee=kalshi_fee(10, 0.55))
        result = compute_ev_at_fill_b_directional(kalshi, model_prob_outcome=0.40)
        assert result.ev_dollars == pytest.approx(-1.68, abs=1e-3)
        assert result.is_arb is False

    def test_zero_ev_at_breakeven_with_fee(self):
        # If model_prob exactly equals ask + fee/qty/qty (per-unit cost), EV ≈ 0
        # ask=0.50, fee=0.18, qty=10 → per-unit cost = 0.50 + 0.018 = 0.518
        kalshi = LegFill("kalshi", "yes", qty=10, price_per_unit=0.50, fee=kalshi_fee(10, 0.50))
        result = compute_ev_at_fill_b_directional(kalshi, model_prob_outcome=0.518)
        assert result.ev_dollars == pytest.approx(0.0, abs=1e-3)

    def test_invalid_model_prob_raises(self):
        kalshi = LegFill("kalshi", "yes", qty=10, price_per_unit=0.5)
        with pytest.raises(ValueError, match="model_prob_outcome"):
            compute_ev_at_fill_b_directional(kalshi, model_prob_outcome=1.5)
        with pytest.raises(ValueError, match="model_prob_outcome"):
            compute_ev_at_fill_b_directional(kalshi, model_prob_outcome=-0.1)


# ── LegFill properties ───────────────────────────────────────────────────

class TestLegFillProperties:
    def test_cost_total_includes_fee(self):
        leg = LegFill("kalshi", "yes", qty=10, price_per_unit=0.45, fee=0.18)
        assert leg.cost_total == pytest.approx(4.68, abs=1e-9)

    def test_payoff_if_wins_equals_qty(self):
        leg = LegFill("draftkings", "home", qty=25, price_per_unit=0.40)
        assert leg.payoff_if_wins == 25.0
