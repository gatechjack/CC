"""PEAD derived-sizing helpers — the SINGLE source of truth for the settled-cash,
self-balancing size and the live ``max_concurrent`` dial.

Both the live scan (``pead_strategy.scan``) and the dashboard readout
(``web.pead_view``) import these so the number the operator sees on screen is the
exact number the sizer would place — they can never drift.

Mechanic (Part A):
    per_name = (remaining_settled_cash / remaining_open_slots) * safety_factor
recomputed after each fill against ACTUAL remaining cash and ACTUAL remaining
slots. Because ``safety_factor`` < 1 we always reserve a sliver, so remaining
cash never crosses zero and the Nth (last) slot is fundable by construction. A
name whose derived size is below ``min_notional`` (RH's $1 fractional floor) is
skipped cleanly — and since the per-name size only grows as the wave fills, if
the first slot is sub-floor they all are (nothing partial-funds, nothing errors).

Dial (Part B): ``max_concurrent`` is read fresh every scan from strategies.yaml
(runtime-retune, no restart). A dashboard override is persisted to
``agent_state robinhood_pead/max_concurrent_override`` and takes precedence when
set. PEAD-scoped; nothing here touches another division.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from trading_corp.persistence.db import load_agent_state

log = logging.getLogger(__name__)

DIVISION = "robinhood_pead"
OVERRIDE_KEY = "max_concurrent_override"

# Kept in lock-step with pead_strategy's module defaults.
_DEFAULT_MAX_CONCURRENT = 7
_DEFAULT_SAFETY_FACTOR = 0.95
_DEFAULT_SIZE_MIN_USD = 50.0  # per-name $ floor: fund FEWER names, never a sub-floor one
_MIN_NOTIONAL = 1.0          # RH fractional-order floor ($1) — hard skip beneath the $ floor
_DEFAULT_STRATEGIES_YAML = "config/strategies.yaml"


def derive_wave_sizes(
    settled_cash: float | None,
    slots_remaining: int,
    *,
    safety_factor: float = _DEFAULT_SAFETY_FACTOR,
    min_notional: float = _MIN_NOTIONAL,
    size_min_usd: float = 0.0,
) -> list[float]:
    """Return the list of per-name notional $ the sizer would place for a wave of
    ``slots_remaining`` empty slots against ``settled_cash``. ``len(result)`` is
    the fundable count; each element is that name's $ (sizes rise slightly across
    the wave as the reserved sliver is redistributed). Empty list => nothing
    fundable.

    THE SINGLE SOURCE OF TRUTH — both the live scan and the dashboard readout call
    this, so the on-screen "funds ~N at ~$X" can never disagree with what is placed.

    ``size_min_usd`` (the per-name $ FLOOR): fund FEWER names at >= the floor rather
    than many tiny ones. We shrink the slot count until the derived wave's SMALLEST
    (first) name meets the floor, so NO sub-floor name is ever opened. This is the
    floor-guaranteeing count; because the 0.95 safety-factor haircut can pull the
    first name below the floor, it is <= floor(settled_cash / size_min_usd) at some
    cash values (always erring toward never-sub-floor). No-op when size_min_usd<=0,
    which preserves the pre-floor behaviour. The ``min_notional`` ($1) hard skip
    stays BENEATH the floor."""
    cash = max(0.0, float(settled_cash or 0.0))
    slots = int(slots_remaining or 0)
    sf = float(safety_factor)
    floor_usd = float(size_min_usd or 0.0)
    # POLICY floor: shrink slots until the smallest (first) derived name >= floor.
    if floor_usd > 0.0:
        while slots > 0 and (cash / slots) * sf < floor_usd:
            slots -= 1
    sizes: list[float] = []
    while slots > 0:
        per_name = (cash / slots) * sf
        if per_name < float(min_notional):
            break                       # too small — this and every later slot skip cleanly
        sizes.append(per_name)
        cash -= per_name
        slots -= 1
    return sizes


def read_max_concurrent_override(db_url) -> int | None:
    """The dashboard-set override from agent_state, or None when unset/invalid."""
    try:
        rec = load_agent_state(DIVISION, OVERRIDE_KEY, db_url=db_url)
    except Exception as e:  # noqa: BLE001 — a missing store must never break the scan
        log.debug("pead_sizing: override read failed: %s", e)
        return None
    val = rec[0] if (rec and isinstance(rec[0], dict)) else None
    if not isinstance(val, dict):
        return None
    try:
        n = int(val.get("max_concurrent"))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _pead_cfg(strategies_yaml: str = _DEFAULT_STRATEGIES_YAML) -> dict:
    try:
        with open(strategies_yaml, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get(DIVISION, {}) or {}
    except Exception as e:  # noqa: BLE001
        log.debug("pead_sizing: yaml read failed: %s", e)
        return {}


def yaml_max_concurrent(strategies_yaml: str = _DEFAULT_STRATEGIES_YAML) -> int:
    try:
        return int(_pead_cfg(strategies_yaml).get(
            "max_concurrent_positions", _DEFAULT_MAX_CONCURRENT))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_CONCURRENT


def yaml_safety_factor(strategies_yaml: str = _DEFAULT_STRATEGIES_YAML) -> float:
    try:
        return float(_pead_cfg(strategies_yaml).get(
            "size_safety_factor", _DEFAULT_SAFETY_FACTOR))
    except (TypeError, ValueError):
        return _DEFAULT_SAFETY_FACTOR


def yaml_size_min_usd(strategies_yaml: str = _DEFAULT_STRATEGIES_YAML) -> float:
    try:
        return float(_pead_cfg(strategies_yaml).get(
            "size_min_usd", _DEFAULT_SIZE_MIN_USD))
    except (TypeError, ValueError):
        return _DEFAULT_SIZE_MIN_USD


def effective_max_concurrent(
    db_url, strategies_yaml: str = _DEFAULT_STRATEGIES_YAML,
) -> tuple[int, bool]:
    """Return ``(effective_max_concurrent, override_active)``. The override wins
    when set; otherwise the live strategies.yaml value is used."""
    ov = read_max_concurrent_override(db_url)
    if ov is not None:
        return ov, True
    return yaml_max_concurrent(strategies_yaml), False
