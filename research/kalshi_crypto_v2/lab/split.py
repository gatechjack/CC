"""Train/holdout discipline + flat-window rule (carried from T4).

CHRONOLOGICAL only (no shuffling, no peeking). Holdout is the final block, touched
ONCE per model version. CV on the train block is expanding-window time-series only.
Flat-window rule: |move| < threshold excluded from headline metrics and reported
as a separate bucket; sensitivity at 0.02/0.05/0.10%.
"""
from __future__ import annotations

FLAT_THRESHOLDS = (0.0002, 0.0005, 0.0010)   # 0.02% / 0.05% / 0.10%


def chronological_split(ts_sorted: list[int], holdout_frac: float = 0.2) -> dict:
    """Split by time. ts_sorted must be ascending. Returns train/holdout index
    ranges + the boundary ts. Holdout = last holdout_frac by count."""
    n = len(ts_sorted)
    if n == 0:
        return {"train": [], "holdout": [], "boundary_ts": None}
    cut = int(n * (1.0 - holdout_frac))
    cut = min(max(cut, 1), n - 1) if n > 1 else 1
    return {"train": list(range(cut)), "holdout": list(range(cut, n)),
            "boundary_ts": ts_sorted[cut] if cut < n else None,
            "n_train": cut, "n_holdout": n - cut}


def expanding_cv_folds(n_train: int, k: int = 5) -> list[dict]:
    """Expanding-window time-series CV over the TRAIN block only (no future leak):
    fold f trains on [0, split_f) and validates on [split_f, split_{f+1})."""
    if n_train < k + 1:
        return [{"train": list(range(max(1, n_train - 1))),
                 "val": list(range(max(1, n_train - 1), n_train))}] if n_train > 1 else []
    step = n_train // (k + 1)
    folds = []
    for f in range(1, k + 1):
        tr_end = step * f
        val_end = step * (f + 1) if f < k else n_train
        folds.append({"train": list(range(0, tr_end)), "val": list(range(tr_end, val_end))})
    return folds


def flat_partition(moves: list[float], threshold: float) -> dict:
    """Indices split into directional (|move| >= threshold) and flat buckets."""
    direc = [i for i, m in enumerate(moves) if m is not None and abs(m) >= threshold]
    flat = [i for i, m in enumerate(moves) if m is not None and abs(m) < threshold]
    return {"threshold": threshold, "directional": direc, "flat": flat,
            "n_directional": len(direc), "n_flat": len(flat)}


def flat_sensitivity(moves: list[float], thresholds=FLAT_THRESHOLDS) -> list[dict]:
    return [flat_partition(moves, t) for t in thresholds]
