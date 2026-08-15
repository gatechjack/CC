"""Unit tests for the recency-weighted whale scorer (pure functions).

Deterministic: every test pins `as_of_ts` and constructs synthetic trades at
known ages, so the decay math is exact and reproducible.
"""
from __future__ import annotations

import math

import pytest

from trading_corp.scripts.polymarket_whale_recency import (
    CLEAN_HOLD_SELL_SHARE_MAX,
    FAVORITE_FARM_PRICE,
    ResolvedTrade,
    classify_trend,
    decay_weight,
    score_recency,
)

H = 45.0
AS_OF = 1_800_000_000
DAY = 86400


def trade(age_days, pnl, *, clean_hold=None, avg_price=0.5, held_pnl=None, cid=None, oi=0):
    return ResolvedTrade(
        condition_id=cid or f"c{age_days}_{pnl}",
        outcome_index=oi,
        realized_pnl=pnl,
        resolution_ts=int(AS_OF - age_days * DAY),
        avg_price=avg_price,
        clean_hold=clean_hold,
        held_pnl=held_pnl,
    )


# --- 1. decay math ----------------------------------------------------------
@pytest.mark.parametrize("n", [0, 1, 2, 3, 5, 8])
def test_decay_weight_halves_each_half_life(n):
    assert decay_weight(n * H, H) == pytest.approx(2.0 ** -n)


def test_decay_weight_edges():
    assert decay_weight(0.0, H) == 1.0
    assert decay_weight(-10.0, H) == 1.0          # future ts clamps to age 0
    with pytest.raises(ValueError):
        decay_weight(10.0, 0.0)


# --- 2. rw_realized: an N-half-life-old trade contributes 2**-N of its pnl ---
def test_rw_realized_decay_contribution():
    s = score_recency("w", "W", [trade(0, 100.0), trade(2 * H, 100.0)],
                      half_life_days=H, as_of_ts=AS_OF)
    # 100*1.0 + 100*0.25 = 125 ; weight_mass = 1.25
    assert s.rw_realized == pytest.approx(125.0)
    assert s.weight_mass == pytest.approx(1.25)
    assert s.flat_realized == pytest.approx(200.0)
    assert s.rw_realized_mean == pytest.approx(100.0)   # 125/1.25


# --- 3. recent_vs_lifetime ratio (the decline signal) -----------------------
def test_ratio_fading_when_recent_worse():
    # big OLD win, small recent wins -> recent mean << lifetime mean
    s = score_recency("w", "W", [trade(5 * H, 1000.0), trade(0, 10.0), trade(0, 10.0)],
                      half_life_days=H, as_of_ts=AS_OF)
    assert s.recent_vs_lifetime is not None and s.recent_vs_lifetime < 1.0
    assert s.trend == "fading"


def test_ratio_accelerating_when_recent_better():
    s = score_recency("w", "W", [trade(5 * H, 10.0), trade(0, 1000.0), trade(0, 1000.0)],
                      half_life_days=H, as_of_ts=AS_OF)
    assert s.recent_vs_lifetime > 1.0
    assert s.trend == "accelerating"


def test_ratio_steady_when_flat():
    s = score_recency("w", "W", [trade(0, 100.0), trade(H, 100.0), trade(2 * H, 100.0)],
                      half_life_days=H, as_of_ts=AS_OF)
    assert s.recent_vs_lifetime == pytest.approx(1.0)
    assert s.trend == "steady"


def test_ratio_undefined_when_lifetime_nonpositive():
    # lifetime negative -> ratio None, difference still defined
    s = score_recency("w", "W", [trade(3 * H, 50.0), trade(0, -200.0)],
                      half_life_days=H, as_of_ts=AS_OF)
    assert s.recent_vs_lifetime is None
    assert s.recent_minus_lifetime < 0
    assert s.trend == "fading"          # recent worse than a negative lifetime avg


# --- 4. trend classifier branches (direct) ----------------------------------
def test_trend_dormant_no_recent():
    s = score_recency("w", "W", [trade(3 * H, 100.0)], half_life_days=H, as_of_ts=AS_OF)
    assert s.recent_n == 0
    assert s.trend == "dormant"


def test_trend_thin_low_mass():
    # one half-recent trade -> recent_n=1 but weight_mass<1.0
    s = score_recency("w", "W", [trade(0.5 * H, 100.0)], half_life_days=H, as_of_ts=AS_OF)
    assert s.recent_n == 1 and s.weight_mass < 1.0
    assert s.trend == "thin"


def test_classify_trend_direct_branches():
    common = dict(recent_minus_lifetime=0.0, last_active_age_days=0.0, recent_n=5,
                  weight_mass=10.0, half_life_days=H)
    assert classify_trend(recent_vs_lifetime=1.5, **common) == "accelerating"
    assert classify_trend(recent_vs_lifetime=1.0, **common) == "steady"
    assert classify_trend(recent_vs_lifetime=0.5, **common) == "fading"
    # dormant by stale last-active even with recent_n>0 guard flipped
    assert classify_trend(recent_vs_lifetime=1.0, recent_minus_lifetime=0.0,
                          last_active_age_days=3 * H, recent_n=1, weight_mass=10.0,
                          half_life_days=H) == "dormant"


# --- 5. clean-hold component (exit-edge-only whales caught) ------------------
def test_clean_hold_zero_when_all_partial_sells():
    s = score_recency("w", "W", [trade(0, 100.0, clean_hold=False),
                                  trade(0, 100.0, clean_hold=False)],
                      half_life_days=H, as_of_ts=AS_OF)
    assert s.rw_realized > 0
    assert s.rw_clean_hold == pytest.approx(0.0)
    assert s.rw_clean_hold_share == pytest.approx(0.0)      # exit-edge whale flagged
    assert s.clean_hold_coverage == pytest.approx(1.0)      # both had fills info


def test_clean_hold_full_when_all_clean():
    s = score_recency("w", "W", [trade(0, 100.0, clean_hold=True),
                                  trade(H, 100.0, clean_hold=True)],
                      half_life_days=H, as_of_ts=AS_OF)
    assert s.rw_clean_hold_share == pytest.approx(1.0)


def test_clean_hold_coverage_partial_when_old_trade_lacks_fills():
    s = score_recency("w", "W", [trade(0, 100.0, clean_hold=True),
                                  trade(2 * H, 100.0, clean_hold=None)],
                      half_life_days=H, as_of_ts=AS_OF)
    assert 0.0 < s.clean_hold_coverage < 1.0    # only the recent trade has fills info


# --- 6. integrity: held-inflation cannot pump the recency score -------------
def test_held_inflation_does_not_score():
    # realized ~0 but held_pnl huge -> rw_realized must stay ~0 (realized basis)
    s = score_recency("w", "W", [trade(0, 0.0, held_pnl=5000.0),
                                  trade(0, 0.0, held_pnl=5000.0)],
                      half_life_days=H, as_of_ts=AS_OF, held_inflation_ratio=0.99)
    assert s.rw_realized == pytest.approx(0.0)
    assert s.rw_realized_mean == pytest.approx(0.0)
    assert s.held_inflation_ratio == 0.99      # flag carried through


# --- 7. integrity: favorite-farm flag (incl. recency-weighted) --------------
def test_favorite_farm_flags():
    s = score_recency("w", "W", [trade(0, 5.0, avg_price=0.95),
                                 trade(0, 5.0, avg_price=0.90),
                                 trade(0, 5.0, avg_price=0.40)],
                      half_life_days=H, as_of_ts=AS_OF)
    assert s.favorite_farm_share == pytest.approx(2.0 / 3.0)
    # all same age so weighted == count share
    assert s.favorite_farm_weighted == pytest.approx(2.0 / 3.0)


def test_favorite_farm_boundary_not_counted():
    # exactly 0.85 is NOT > 0.85 -> not a favorite
    s = score_recency("w", "W", [trade(0, 5.0, avg_price=FAVORITE_FARM_PRICE)],
                      half_life_days=H, as_of_ts=AS_OF)
    assert s.favorite_farm_share == 0.0


# --- 8. determinism + empty -------------------------------------------------
def test_determinism():
    ts = [trade(0, 100.0, clean_hold=True), trade(H, -20.0, clean_hold=False)]
    a = score_recency("w", "W", ts, half_life_days=H, as_of_ts=AS_OF)
    b = score_recency("w", "W", ts, half_life_days=H, as_of_ts=AS_OF)
    assert a == b


def test_empty_is_dormant():
    s = score_recency("w", "W", [], half_life_days=H, as_of_ts=AS_OF)
    assert s.n_resolved == 0 and s.trend == "dormant" and s.rw_realized == 0.0


# --- 9. drift guard: mirrored constants must equal the audit module's --------
def test_constants_match_audit_module():
    from trading_corp.data.polymarket_whale_audit import (
        DEFAULT_PARTIAL_SELL_THRESHOLD,
        FAVORITE_FARMING_PRICE_THRESHOLD,
    )
    assert CLEAN_HOLD_SELL_SHARE_MAX == DEFAULT_PARTIAL_SELL_THRESHOLD
    assert FAVORITE_FARM_PRICE == FAVORITE_FARMING_PRICE_THRESHOLD
