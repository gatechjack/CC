"""Breeden-Litzenberger ladder consistency for a digital strike ladder.

For an "above-X" digital, p_above(X) = P(settle >= X). No-arbitrage constraints
(the digital form of call monotonicity/convexity):
  - BOUNDS:        0 <= p_above <= 1                      (call monotonicity)
  - MONOTONICITY:  p_above NON-INCREASING in strike       (call convexity => density >= 0)
  - SUM-TO-ONE:    for a DISJOINT bucket ladder, sum(yes bucket prices) = 1
Every violation is logged WITH spread context: a violation is `inside_spread`
(NOT tradeable) when you cannot cross the spread to arb it. Implied bucket
densities are returned for the S5 bucket-density model.
"""
from __future__ import annotations

TOL = 1e-9


def check_monotonic_ladder(rungs: list[dict], tol: float = TOL) -> list[dict]:
    """rungs sorted by strike ASC; each {strike, p_above, yes_bid?, yes_ask?}.
    Returns bounds + monotonicity violations (with inside_spread flag)."""
    v: list[dict] = []
    for i, r in enumerate(rungs):
        p = r.get("p_above")
        if p is None or not (-tol <= p <= 1 + tol):
            v.append({"type": "bounds", "i": i, "strike": r.get("strike"), "p_above": p,
                      "inside_spread": False})
    for i in range(len(rungs) - 1):
        a, b = rungs[i], rungs[i + 1]
        if a.get("p_above") is None or b.get("p_above") is None:
            continue
        if a["p_above"] + tol < b["p_above"]:            # higher strike more likely -> violation
            # tradeable iff you can BUY above-X_a at its ask cheaper than SELL above-X_b at its bid
            aa, bb = a.get("yes_ask"), b.get("yes_bid")
            tradeable = (aa is not None and bb is not None and aa + tol < bb)
            v.append({"type": "monotonicity", "i": i,
                      "strikes": (a.get("strike"), b.get("strike")),
                      "p_above": (a["p_above"], b["p_above"]),
                      "magnitude": round(b["p_above"] - a["p_above"], 6),
                      "inside_spread": not tradeable})
    return v


def check_bucket_sum(bucket_yes: list[float], tol: float = 1e-3) -> list[dict]:
    """Disjoint bucket ladder: yes prices should sum to 1 and each be in [0,1]."""
    v: list[dict] = []
    for i, y in enumerate(bucket_yes):
        if not (-tol <= y <= 1 + tol):
            v.append({"type": "bucket_bounds", "i": i, "yes": y})
    s = sum(bucket_yes)
    if abs(s - 1.0) > tol:
        v.append({"type": "sum_to_one", "sum": round(s, 6), "deviation": round(s - 1.0, 6)})
    return v


def implied_densities(rungs: list[dict]) -> list[dict]:
    """Bucket mass d_i = p_above[i] - p_above[i+1] between adjacent strikes.
    Negative mass = a density/convexity violation (same as a monotonicity break)."""
    out = []
    for i in range(len(rungs) - 1):
        a, b = rungs[i], rungs[i + 1]
        if a.get("p_above") is None or b.get("p_above") is None:
            continue
        d = a["p_above"] - b["p_above"]
        out.append({"strike_lo": a.get("strike"), "strike_hi": b.get("strike"),
                    "mass": round(d, 6), "negative": d < -TOL})
    return out
