"""Probability calibration — Brier score + reliability curve, benchmarked against
the MARKET's own implied probability. Accuracy may be reported but NEVER gates.
The bar to beat is the market price, not 50%."""
from __future__ import annotations


def brier(probs: list[float], outcomes: list[int]) -> float:
    if not probs:
        return float("nan")
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


def reliability_curve(probs: list[float], outcomes: list[int], n_bins: int = 10) -> list[dict]:
    """Binned reliability: per bin, mean predicted p vs observed frequency + count."""
    bins: list[dict] = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        sel = [(p, o) for p, o in zip(probs, outcomes)
               if (lo <= p < hi) or (i == n_bins - 1 and p == hi)]
        if not sel:
            bins.append({"bin": (round(lo, 3), round(hi, 3)), "n": 0,
                         "mean_pred": None, "obs_freq": None})
            continue
        mp = sum(p for p, _ in sel) / len(sel)
        of = sum(o for _, o in sel) / len(sel)
        bins.append({"bin": (round(lo, 3), round(hi, 3)), "n": len(sel),
                     "mean_pred": round(mp, 4), "obs_freq": round(of, 4)})
    return bins


def compare_to_market(model_p: list[float], market_p: list[float],
                      outcomes: list[int]) -> dict:
    """Brier(model) vs Brier(market). skill_score = 1 - Brier_model/Brier_market
    (>0 means the model beats the market price; <=0 means it does not)."""
    bm, bk = brier(model_p, outcomes), brier(market_p, outcomes)
    skill = (1.0 - bm / bk) if bk and bk == bk else float("nan")
    return {"brier_model": round(bm, 5), "brier_market": round(bk, 5),
            "skill_score_vs_market": round(skill, 4) if skill == skill else float("nan"),
            "n": len(outcomes)}
