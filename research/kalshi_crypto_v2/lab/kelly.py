"""Fractional Kelly sizing, correlation-aware. Reporting-only (no orders).

binary_kelly: full-Kelly fraction for buying a binary contract at cost `price`
paying 1, with win prob p:  f* = p - (1-p)*price/(1-price), floored at 0.
fractional: multiply by KELLY_FRAC in [0.25, 0.5].
correlation_adjusted: multivariate (Gaussian-approx) Kelly f = frac * Cov^-1 @ mu
across simultaneous bets (overlapping windows + ladder strikes), so correlated
exposures are scaled down; negatives floored to 0.
"""
from __future__ import annotations

KELLY_FRAC_RANGE = (0.25, 0.5)


def binary_kelly(p: float, price: float) -> float:
    if not (0.0 < price < 1.0) or not (0.0 <= p <= 1.0):
        return 0.0
    f = p - (1.0 - p) * price / (1.0 - price)
    return max(0.0, f)


def fractional(f_full: float, frac: float = 0.25) -> float:
    frac = min(max(frac, KELLY_FRAC_RANGE[0]), KELLY_FRAC_RANGE[1])
    return frac * f_full


def correlation_adjusted(edges: list[float], cov, frac: float = 0.25) -> list[float]:
    """Multivariate fractional Kelly across correlated bets.
    edges: per-bet expected excess return (mu). cov: covariance matrix (list of
    lists or np array) of bet outcomes. Returns non-negative sizing weights."""
    import numpy as np
    mu = np.asarray(edges, dtype=float)
    C = np.asarray(cov, dtype=float)
    frac = min(max(frac, KELLY_FRAC_RANGE[0]), KELLY_FRAC_RANGE[1])
    try:
        f = frac * np.linalg.solve(C, mu)
    except np.linalg.LinAlgError:
        f = frac * (mu / np.clip(np.diag(C), 1e-12, None))   # fall back to diagonal
    return [float(max(0.0, x)) for x in f]
