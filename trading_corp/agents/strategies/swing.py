"""Fractal swing-point helpers for adaptive stop-loss placement.

Re-exports `find_swing_points` from `bitunix_htf_regime` (which already
implements the symmetric-fractal n=2 detector used by HTF market-structure
analysis). Adds `get_recent_swing` — the "most recent confirmed swing of
this side, as of bar i" helper that adaptive SL placement needs.

A swing high at index j requires `n` bars on each side strictly lower than
highs[j] (mirror for swing low). A swing is **confirmed** only at bar
`j + n` — live code must respect this buffer or it leaks future
information. `find_swing_points`'s natural index range
`[n, len(highs) - n)` enforces this when callers pass a slice ending at
`current_idx + 1`.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from trading_corp.agents.strategies.bitunix_htf_regime import find_swing_points
from trading_corp.data.live_bar_cache import Bar

__all__ = ["find_swing_points", "get_recent_swing"]


def get_recent_swing(
    bars: Sequence[Bar],
    current_idx: int,
    side: Literal["high", "low"],
    n: int = 2,
    max_lookback: int = 30,
) -> float | None:
    """Most recent confirmed fractal swing price as of bar `current_idx`.

    Returns the price of the swing (highs[j] or lows[j]) at the latest
    confirmed swing index j within [current_idx - max_lookback,
    current_idx - n]. Returns None if no confirmed swing exists in
    that window.

    The slice `bars[: current_idx + 1]` is what's fed to the detector,
    so future bars past `current_idx` cannot influence the result.
    """
    if current_idx < 0 or current_idx >= len(bars):
        return None
    if side not in ("high", "low"):
        raise ValueError(f"side must be 'high' or 'low', got {side!r}")

    sliced = list(bars[: current_idx + 1])
    highs = [b.high for b in sliced]
    lows = [b.low for b in sliced]
    sh, sl = find_swing_points(highs, lows, n=n)
    candidates = sh if side == "high" else sl
    if not candidates:
        return None

    floor_idx = max(0, current_idx - max_lookback)
    valid = [j for j in candidates if j >= floor_idx]
    if not valid:
        return None

    j = valid[-1]
    return highs[j] if side == "high" else lows[j]
