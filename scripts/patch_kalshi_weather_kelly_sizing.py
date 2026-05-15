"""Surgical strategies.yaml patcher: upgrade kalshi_weather_arb to
fractional-Kelly sizing + ensemble σ + nowcast blend.

What this does:
  1. config/strategies.yaml — replace the kalshi_weather_arb block's
     `sizing:` section with the new kelly_fractional mode + caps.
     Add open_meteo_enabled / metar_enabled / ensemble_sigma_floor_f
     / nowcast_blend_horizon_hours alongside existing knobs.

Prerequisites (must already be on prod, scp'd before running this):
  - trading_corp/data/open_meteo_client.py (NEW)
  - trading_corp/data/metar_client.py (NEW)
  - trading_corp/agents/strategies/_weather_math.py (UPDATED — adds kelly_fraction)
  - trading_corp/agents/strategies/kalshi_weather_arb.py (UPDATED — new pipeline)
  - trading_corp/main.py (UPDATED — equity snapshot pre-scan + audit allowlist)

Idempotent: re-running detects already-patched files and exits clean.

Why this is its own patcher (not amending the original):
  - Local config/strategies.yaml has no kalshi_weather_arb block (prod-only
    content per `trading_corp_prod_git_drift.md`). This patcher operates on
    the prod yaml in-place.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

BASE = Path("/home/azureuser/trading_corp")
TAG = f"pre-weather-kelly-{time.strftime('%Y%m%d-%H%M', time.gmtime())}"


def _backup(p: Path) -> None:
    bak = p.with_suffix(p.suffix + f".{TAG}")
    shutil.copy(p, bak)
    print(f"  backup: {bak.name}")


def _assert_anchor(src: str, anchor: str, fname: str, n: int) -> None:
    if anchor not in src:
        sys.exit(f"FAIL: anchor #{n} not found in {fname}")


def patch_strategies_yaml() -> None:
    p = BASE / "config/strategies.yaml"
    src = p.read_text()
    if "kelly_fractional" in src and "open_meteo_enabled" in src:
        print(f"  {p.name}: already patched (skipping)")
        return
    _backup(p)

    # The existing block (verbatim from patch_kalshi_weather_division.py)
    old_block = (
        "kalshi_weather_arb:\n"
        "  enabled: true\n"
        "  auto_execute: false               # paper-mode until validation gate\n"
        "  division: kalshi_weather\n"
        "  poll_interval_sec: 300            # 5 min — weather markets don't churn\n"
        "  discovery:\n"
        "    max_series_per_category: 30\n"
        "    max_markets_per_series: 50\n"
        "    cache_ttl_sec: 600              # 10 min cache on Kalshi list_markets\n"
        "  k_markets_per_cycle: 30           # candidates evaluated per cycle\n"
        "  market_cooldown_hours: 4          # don't re-emit same ticker within 4h\n"
        "  min_divergence_pct: 10.0          # |P(YES) - implied| × 100 ≥ this → fire\n"
        "  max_horizon_hours: 72             # NWS forecast precision degrades past 72h\n"
        "  sizing:\n"
        "    mode: fixed_usd\n"
        "    fixed_amount: 1.0               # $1 per shakedown trade\n"
    )
    new_block = (
        "kalshi_weather_arb:\n"
        "  enabled: true\n"
        "  auto_execute: false               # paper-mode until validation gate\n"
        "  division: kalshi_weather\n"
        "  poll_interval_sec: 300            # 5 min — weather markets don't churn\n"
        "  discovery:\n"
        "    max_series_per_category: 30\n"
        "    max_markets_per_series: 50\n"
        "    cache_ttl_sec: 600              # 10 min cache on Kalshi list_markets\n"
        "  k_markets_per_cycle: 30           # candidates evaluated per cycle\n"
        "  market_cooldown_hours: 4          # don't re-emit same ticker within 4h\n"
        "  min_divergence_pct: 10.0          # |P(YES) - implied| × 100 ≥ this → fire\n"
        "  max_horizon_hours: 72             # NWS forecast precision degrades past 72h\n"
        "  # ── Tier-1 upgrades (2026-05-15) ──────────────────────────────\n"
        "  # Open-Meteo cross-model ensemble for measured σ (GFS+ICON+ECMWF+...).\n"
        "  # When ≥3 model members are available, σ = max(ensemble_std, floor).\n"
        "  # Falls back to sigma_for_horizon heuristic if the API is unavailable.\n"
        "  open_meteo_enabled: true\n"
        "  ensemble_sigma_floor_f: 0.5       # never go below this even on tight ensembles\n"
        "  # METAR nowcast blend on sub-6h horizons. Weight w(t) ramps linearly:\n"
        "  #   w=0 at horizon=0 (pure nowcast); w=1 at horizon=horizon_cap (pure forecast).\n"
        "  # Daily HIGH/LOW markets are excluded — extrema aren't well-modelled by a\n"
        "  # linear trend off the current obs.\n"
        "  metar_enabled: true\n"
        "  nowcast_blend_horizon_hours: 6.0\n"
        "  sizing:\n"
        "    mode: kelly_fractional          # was fixed_usd; flipped 2026-05-15\n"
        "    kelly_fraction: 0.25            # quarter-Kelly — robust to misestimation\n"
        "    min_usd: 1.0                    # below this, skip — sub-$1 = fee-dominated\n"
        "    max_per_market_pct: 5.0         # per-order cap (% of bankroll)\n"
        "    max_per_day_pct: 25.0           # sum of today's weather $ ≤ this\n"
        "    max_per_city_pct: 15.0          # sum per city ≤ this (correlated bets)\n"
    )
    _assert_anchor(src, old_block, p.name, 1)
    src = src.replace(old_block, new_block, 1)
    p.write_text(src)
    print(f"  PATCHED: {p.name}")


def main() -> None:
    print(f"TAG={TAG}")
    patch_strategies_yaml()
    print("DONE")


if __name__ == "__main__":
    main()
