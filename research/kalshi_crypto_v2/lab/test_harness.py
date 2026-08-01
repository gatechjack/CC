"""S2 harness unit tests — validate the pure-math framework before S3 data lands.
Run: run_capped python -m pytest research/kalshi_crypto_v2/lab/test_harness.py -q"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import breeden_litzenberger as bl  # noqa: E402
import calibration as cal  # noqa: E402
import ev  # noqa: E402
import kelly  # noqa: E402
import split  # noqa: E402


# ── EV ───────────────────────────────────────────────────────────────────
def test_taker_ev_known():
    # model_p=0.6, yes_ask=0.5: fee=ceil(0.07*0.5*0.5*100)/100=0.02; EV=0.6-0.5-0.02=0.08
    r = ev.taker_ev(0.6, "yes", 0.5, 0.5)
    assert abs(r["ev"] - 0.08) < 1e-9 and abs(r["fee"] - 0.02) < 1e-9


def test_taker_ev_no_side():
    r = ev.taker_ev(0.6, "no", 0.5, 0.5)   # P(no)=0.4 -> EV=0.4-0.5-0.02=-0.12
    assert abs(r["ev"] - (-0.12)) < 1e-9


def test_maker_fill_through_and_not():
    cs_fill = [{"ts": 1, "yes_low": 0.48, "no_low": 0.9, "volume": 5}]
    cs_nofill = [{"ts": 1, "yes_low": 0.50, "no_low": 0.9, "volume": 5}]
    assert ev.maker_filled("yes", 0.50, cs_fill, 10, tick=0.01) is True
    assert ev.maker_filled("yes", 0.50, cs_nofill, 10, tick=0.01) is False
    # after close -> not filled
    assert ev.maker_filled("yes", 0.50, [{"ts": 20, "yes_low": 0.0, "volume": 9}], 10) is False


def test_aggregate_maker_reports_fill_rate():
    res = [ev.maker_ev(0.6, "yes", 0.5, [{"ts": 1, "yes_low": 0.4, "volume": 3}], 10),
           ev.maker_ev(0.6, "yes", 0.5, [{"ts": 1, "yes_low": 0.6, "volume": 3}], 10)]
    agg = ev.aggregate_maker(res)
    assert agg["n_attempts"] == 2 and agg["n_fills"] == 1
    assert abs(agg["fill_rate"] - 0.5) < 1e-9 and "mean_ev_on_fills" in agg


# ── calibration ────────────────────────────────────────────────────────────
def test_brier_and_market_skill():
    probs = [0.9, 0.1, 0.8, 0.2]
    outs = [1, 0, 1, 0]
    assert cal.brier(probs, outs) < cal.brier([0.5] * 4, outs)   # confident+correct beats 0.5
    c = cal.compare_to_market([0.9, 0.1], [0.6, 0.4], [1, 0])
    assert c["brier_model"] < c["brier_market"] and c["skill_score_vs_market"] > 0


# ── kelly ────────────────────────────────────────────────────────────────
def test_binary_kelly_values():
    assert abs(kelly.binary_kelly(0.6, 0.5) - 0.2) < 1e-9      # p-(1-p)c/(1-c)=0.6-0.4=0.2
    assert kelly.binary_kelly(0.5, 0.5) == 0.0                  # no edge
    assert kelly.binary_kelly(0.4, 0.5) == 0.0                  # negative -> floored


def test_correlation_reduces_allocation():
    edges = [0.1, 0.1]
    uncorr = kelly.correlation_adjusted(edges, [[1, 0], [0, 1]], frac=0.25)
    corr = kelly.correlation_adjusted(edges, [[1, 0.9], [0.9, 1]], frac=0.25)
    assert sum(corr) < sum(uncorr)                             # positive corr shrinks size


# ── Breeden-Litzenberger ───────────────────────────────────────────────────
def test_monotone_ladder_clean():
    rungs = [{"strike": 100, "p_above": 0.8}, {"strike": 110, "p_above": 0.5},
             {"strike": 120, "p_above": 0.2}]
    assert [v for v in bl.check_monotonic_ladder(rungs) if v["type"] == "monotonicity"] == []
    assert all(not d["negative"] for d in bl.implied_densities(rungs))


def test_monotonicity_violation_spread_flag():
    # p_above increases 0.5->0.8 across strikes -> violation
    tradeable = [{"strike": 100, "p_above": 0.5, "yes_ask": 0.55, "yes_bid": 0.45},
                 {"strike": 110, "p_above": 0.8, "yes_ask": 0.85, "yes_bid": 0.75}]
    v = [x for x in bl.check_monotonic_ladder(tradeable) if x["type"] == "monotonicity"]
    assert v and v[0]["inside_spread"] is False                 # 0.55 < 0.75 -> tradeable
    inside = [{"strike": 100, "p_above": 0.5, "yes_ask": 0.85, "yes_bid": 0.45},
              {"strike": 110, "p_above": 0.8, "yes_ask": 0.95, "yes_bid": 0.75}]
    v2 = [x for x in bl.check_monotonic_ladder(inside) if x["type"] == "monotonicity"]
    assert v2 and v2[0]["inside_spread"] is True                # 0.85 !< 0.75 -> inside spread


def test_bucket_sum_to_one():
    assert bl.check_bucket_sum([0.3, 0.4, 0.3]) == []
    assert any(x["type"] == "sum_to_one" for x in bl.check_bucket_sum([0.3, 0.4, 0.5]))


# ── split ──────────────────────────────────────────────────────────────────
def test_chronological_split_and_flat():
    s = split.chronological_split(list(range(1, 11)), holdout_frac=0.2)
    assert s["n_train"] == 8 and s["n_holdout"] == 2 and s["train"][-1] == 7
    fp = split.flat_partition([0.001, 0.0001, None], 0.0005)
    assert fp["directional"] == [0] and fp["flat"] == [1]
    assert len(split.flat_sensitivity([0.001, 0.0003])) == 3


if __name__ == "__main__":
    sys.exit(os.system(f"python -m pytest {os.path.abspath(__file__)} -q"))
