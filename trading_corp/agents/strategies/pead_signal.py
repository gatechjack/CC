"""SRW-SUE signal for the `robinhood_pead` (Post-Earnings-Announcement-Drift)
division — pure decision math, no IO / broker / config.

The agent layer (`pead_strategy.py`, ships in Phase 2) feeds in per-symbol
quarterly EPS history + screening inputs; this module computes Standardized
Unexpected Earnings under the **Seasonal Random Walk (SRW)** model and returns
ranked long candidates. Mirrors the `donchian_btc.py` / `*_agent.py` split:
math here, wiring there.

SUE definition (Foster-Olsen-Shevlin 1984; Bernard-Thomas 1989; Livnat-
Mendenhall 2006, seasonal-random-walk variant — no analyst consensus needed):

    UE(q)  = EPS_actual(q) - EPS_actual(q-4)          # vs the same quarter, prior year
    SUE(q) = UE(q) / stdev( UE over the trailing `lookback` quarters )

Denominator convention: the standard deviation is taken over the `lookback`
UE values **immediately preceding** the current quarter (exclusive of the
current shock, so a large UE doesn't inflate its own denominator). This is a
documented choice; `lookback` is a parameter, and the exact arithmetic is
pinned in tests.

All thresholds (SUE cutoff, quintile, screen limits) are **literature priors**
passed as parameters — the backtest tunes them. v1 is **SUE-only** (no EAR,
no NLP, no revenue filter).
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Parameters (literature priors — tuned by the backtest, never hardcoded deep
# in the logic).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SueParams:
    """Knobs for the SUE computation + candidate selection."""
    lookback: int = 8            # trailing quarters of UE for the stdev denominator
    sue_threshold: float = 1.5   # long candidate requires SUE strictly above this (z)
    top_quintile: bool = True    # also require top-quintile SUE within the wave
    quintile_pct: float = 0.80   # "top quintile" = at/above the 80th percentile


@dataclass(frozen=True)
class ScreenParams:
    """Liquidity / universe screen applied BEFORE the signal.

    Floors are tuned for THIS account, not institutional PEAD. Tiny orders +
    fractional shares on Robinhood mean the binding constraint is "can a tiny
    order fill", NOT "can I move size" — so the floors only screen out names
    too illiquid/dead to fill at all. The >$1B literature floor (which dodges
    small-cap slippage-for-size) does NOT apply here and would delete the
    Nasdaq Composite's small-cap tail, where PEAD's edge concentrates.
    Config-driven via strategies.yaml (see `screen_params_from_config`) so the
    floors can be retuned without a code change.
    """
    min_price: float = 5.0
    min_avg_daily_volume: float = 200_000.0         # 30d avg shares
    min_market_cap: float = 100_000_000.0           # $100M
    # Exclude financials & utilities (standard PEAD universe convention).
    excluded_sectors: frozenset[str] = frozenset({
        "financial services", "financials", "financial", "utilities", "utility",
    })
    min_days_to_next_earnings: int = 65             # trading days of drift room


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScreenInputs:
    """Per-symbol facts consumed by the screen. Hard liquidity fields
    (price/volume/market_cap) are required: missing data → excluded
    (we will not trade a name we cannot verify or size). Soft fields
    (sector / guidance_cut / days_to_next_earnings) are lenient on None,
    matching the `get_next_earnings` "None = don't block" contract.
    """
    symbol: str
    price: float | None
    avg_daily_volume_30d: float | None
    market_cap: float | None
    sector: str | None = None
    guidance_cut: bool | None = None
    days_to_next_earnings: int | None = None


@dataclass
class PeadCandidate:
    """One name in the current earnings wave after SUE + screen."""
    symbol: str
    sue: float | None
    screen_ok: bool
    screen_reason: str = "ok"


# ---------------------------------------------------------------------------
# SUE math
# ---------------------------------------------------------------------------

def unexpected_earnings(eps: Sequence[float]) -> list[float]:
    """UE series under the seasonal random walk: `eps[i] - eps[i-4]`.

    `eps` is chronological (oldest -> newest) actual EPS. Returns a list of
    length `len(eps) - 4` (empty if fewer than 5 quarters).
    """
    if len(eps) < 5:
        return []
    return [float(eps[i]) - float(eps[i - 4]) for i in range(4, len(eps))]


def standardized_ue(eps: Sequence[float], lookback: int = 8) -> float | None:
    """SUE for the most recent quarter, or None if it can't be computed.

    Needs `lookback + 5` quarters of EPS: 4 to seed the first UE, then
    `lookback + 1` UE values (the latest plus `lookback` prior values for the
    denominator). Returns None on insufficient history or a degenerate
    (zero / non-finite) denominator.
    """
    if lookback < 2:
        raise ValueError("lookback must be >= 2 to estimate a stdev")
    ue = unexpected_earnings(eps)
    if len(ue) < lookback + 1:
        return None
    latest = ue[-1]
    window = ue[-(lookback + 1):-1]   # the `lookback` UE values BEFORE the latest
    try:
        sd = statistics.stdev(window)
    except statistics.StatisticsError:
        return None
    if not math.isfinite(sd) or sd == 0.0:
        return None
    sue = latest / sd
    return sue if math.isfinite(sue) else None


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

def passes_screen(inp: ScreenInputs, params: ScreenParams) -> tuple[bool, str]:
    """Return (ok, reason). `reason` is a short machine-readable tag; on pass
    it is "ok"."""
    # Hard liquidity filters — require data.
    if inp.price is None:
        return False, "missing_price"
    if inp.price < params.min_price:
        return False, "price_below_min"
    if inp.avg_daily_volume_30d is None:
        return False, "missing_volume"
    if inp.avg_daily_volume_30d < params.min_avg_daily_volume:
        return False, "volume_below_min"
    if inp.market_cap is None:
        return False, "missing_market_cap"
    if inp.market_cap < params.min_market_cap:
        return False, "mktcap_below_min"
    # Soft filters — lenient on missing data.
    if inp.sector is not None and inp.sector.strip().lower() in params.excluded_sectors:
        return False, "excluded_sector"
    if inp.guidance_cut is True:
        return False, "guidance_cut"
    if (
        inp.days_to_next_earnings is not None
        and inp.days_to_next_earnings < params.min_days_to_next_earnings
    ):
        return False, "earnings_too_soon"
    return True, "ok"


# ---------------------------------------------------------------------------
# Selection / ranking
# ---------------------------------------------------------------------------

def _percentile(sorted_vals: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile (numpy default method); `pct` in [0,1].
    `sorted_vals` must be ascending and non-empty."""
    n = len(sorted_vals)
    if n == 0:
        raise ValueError("empty sequence")
    if n == 1:
        return float(sorted_vals[0])
    rank = pct * (n - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(sorted_vals[lo])
    frac = rank - lo
    return float(sorted_vals[lo]) + (float(sorted_vals[hi]) - float(sorted_vals[lo])) * frac


def select_candidates(
    candidates: Sequence[PeadCandidate], params: SueParams
) -> list[PeadCandidate]:
    """From the current wave, keep names that pass the screen, have a SUE
    strictly above `sue_threshold`, and (if enabled) sit at/above the
    top-quintile SUE of the wave. Returns survivors ranked by SUE descending.

    The quintile cutoff is computed across the full screened wave (every name
    with a SUE), then AND-ed with the absolute threshold.
    """
    wave = [c for c in candidates if c.screen_ok and c.sue is not None]
    if not wave:
        return []
    cutoff = float("-inf")
    if params.top_quintile:
        sues = sorted(float(c.sue) for c in wave)  # type: ignore[arg-type]
        cutoff = _percentile(sues, params.quintile_pct)
    survivors = [
        c for c in wave
        if float(c.sue) > params.sue_threshold  # type: ignore[arg-type]
        and (not params.top_quintile or float(c.sue) >= cutoff)  # type: ignore[arg-type]
    ]
    survivors.sort(key=lambda c: float(c.sue), reverse=True)  # type: ignore[arg-type]
    return survivors


# ---------------------------------------------------------------------------
# Top-level convenience — the entry point the Phase-2 strategy calls.
# ---------------------------------------------------------------------------

def rank_wave(
    eps_by_symbol: Mapping[str, Sequence[float]],
    screens: Mapping[str, ScreenInputs],
    *,
    sue_params: SueParams = SueParams(),
    screen_params: ScreenParams = ScreenParams(),
) -> list[PeadCandidate]:
    """Compute SUE + screen for every symbol in the wave and return the
    ranked long candidates. Pure: all data is passed in.

    `eps_by_symbol`: chronological EPS actuals per symbol.
    `screens`: ScreenInputs per symbol (symbols absent here are treated as
    failing the screen with reason "missing_screen_inputs").
    """
    candidates: list[PeadCandidate] = []
    for symbol, eps in eps_by_symbol.items():
        sue = standardized_ue(eps, lookback=sue_params.lookback)
        inp = screens.get(symbol)
        if inp is None:
            candidates.append(PeadCandidate(symbol, sue, False, "missing_screen_inputs"))
            continue
        ok, reason = passes_screen(inp, screen_params)
        candidates.append(PeadCandidate(symbol, sue, ok, reason))
    return select_candidates(candidates, sue_params)


# ---------------------------------------------------------------------------
# Post-reaction CONFIRMATION GATE — SHARED by the live scan AND the backtest so
# the two paths cannot diverge (the whole point: validate the rule the engine
# actually runs). Pure; no bar-inference of the slot; None slot => not tradeable.
# ---------------------------------------------------------------------------

def reaction_index(report_time: str | None, ann_idx: int | None) -> int | None:
    """Index of the POST-REACTION session: `ann_idx` for BeforeMarket (reaction
    is the announcement-day bar), `ann_idx + 1` for AfterMarket (reaction is the
    next session). None if the slot is unknown (report_time not BMO/AMC) — an
    un-slotted name is NOT tradeable. No inference from bars."""
    if report_time == "BeforeMarket":
        return ann_idx
    if report_time == "AfterMarket":
        return None if ann_idx is None else ann_idx + 1
    return None


def confirmation_verdict(
    report_time: str | None, closes: Sequence[float], ann_idx: int | None,
) -> str:
    """The confirmation gate: enter long only if BAR 1 (the reaction session)
    CLOSES ABOVE BAR 0 (the last full session with NO earnings info). BAR 0 =
    BAR 1 - 1 for BOTH slots (AMC baseline = day a; BMO baseline = day a-1); the
    slot only fixes WHICH days these are, not the logic. Entry is BAR 2's open
    (reaction_index + 1).

    `closes`: session closes oldest->newest. `ann_idx`: index of the first bar
    on/after the announcement date. Returns exactly one of:
      'pass'           — reaction close > pre_earnings_close (enter)
      'reject_gate'    — reaction close <= pre_earnings_close (declined)
      'reject_no_slot' — report_time unknown -> EXCLUDED (no fallback/inference)
      'reject_no_bar'  — the pre-earnings or the reaction session is unavailable
    """
    if report_time not in ("BeforeMarket", "AfterMarket"):
        return "reject_no_slot"
    bar1 = reaction_index(report_time, ann_idx)      # first session trading ON the news (reaction)
    if bar1 is None or bar1 < 1 or bar1 >= len(closes):
        return "reject_no_bar"
    bar0 = bar1 - 1                                   # last full session with NO earnings info (baseline)
    return "pass" if closes[bar1] > closes[bar0] else "reject_gate"


# ---------------------------------------------------------------------------
# Config builders — let strategies.yaml drive the params (retune w/o code change)
# ---------------------------------------------------------------------------

def screen_params_from_config(cfg: Mapping) -> ScreenParams:
    """Build ScreenParams from a strategies.yaml `screen:` block; unset keys
    fall back to the (account-tuned) defaults above."""
    base = ScreenParams()
    sectors = cfg.get("excluded_sectors")
    return ScreenParams(
        min_price=float(cfg.get("min_price", base.min_price)),
        min_avg_daily_volume=float(cfg.get("min_avg_volume_30d", base.min_avg_daily_volume)),
        min_market_cap=float(cfg.get("min_market_cap", base.min_market_cap)),
        excluded_sectors=(
            frozenset(str(s).strip().lower() for s in sectors)
            if sectors else base.excluded_sectors
        ),
        min_days_to_next_earnings=int(
            cfg.get("min_days_to_next_earnings", base.min_days_to_next_earnings)
        ),
    )


def sue_params_from_config(cfg: Mapping) -> SueParams:
    """Build SueParams from a strategies.yaml `signal:` block."""
    base = SueParams()
    return SueParams(
        lookback=int(cfg.get("lookback", base.lookback)),
        sue_threshold=float(cfg.get("sue_threshold", base.sue_threshold)),
        top_quintile=bool(cfg.get("top_quintile", base.top_quintile)),
        quintile_pct=float(cfg.get("quintile_pct", base.quintile_pct)),
    )
