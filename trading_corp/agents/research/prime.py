"""PositionContext startup-of-day cache prime (Phase 1d, design Q7).

On process startup, after `build_research_firm_deps()` constructs the
research firm, the system primes the PositionContext cache for each
division's configured symbols. The on-alert read path is fail-soft on
miss (returns None — "no signal", per Q7), so a failed prime is not
a blocker; it just means the on-alert path runs uninformed until the
next prime opportunity.

Each prime is one engagement per (division, symbol) pair. Failures
are logged and swallowed per-symbol so a single yfinance hiccup does
not block the rest of the prime.

The scope's `current_position_qty` / `current_position_avg_price` /
`current_position_age_hours` are passed as 0.0 because the prime is
symbol-driven (macro + sentiment for the symbol right now), not
position-driven. Future prime variants may pass real position state
if a synthesis step starts using it.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from trading_corp.agents.research.engagement import (
    ResearchFirmDeps, run_engagement,
)
from trading_corp.agents.research.position_context_cache import (
    write_position_context,
)
from trading_corp.agents.research.schemas import (
    EngagementSpec, PositionContext, PositionContextScope,
)

log = logging.getLogger(__name__)


async def prime_division_position_contexts(
    *,
    division_slug: str,
    asset_class: str,
    symbols: list[str],
    horizon_hours: int,
    research_firm: ResearchFirmDeps,
    db_url: str | None,
) -> dict[str, bool]:
    """Run a PositionContext engagement per symbol, write result to cache.

    Returns {symbol: True} for primed symbols, {symbol: False} for
    failures. Caller logs the summary; this function does not raise.

    `db_url` is required for the cache write; if None, the prime is a
    no-op (in-memory mode = no cache to populate, which the on-alert
    read also no-ops on).
    """
    if not symbols:
        return {}
    if db_url is None:
        log.info(
            "prime_position_contexts(%s): db_url=None — prime skipped",
            division_slug,
        )
        return {s: False for s in symbols}

    results: dict[str, bool] = {}
    for symbol in symbols:
        ok = await _prime_one(
            division_slug=division_slug,
            asset_class=asset_class,
            symbol=symbol,
            horizon_hours=horizon_hours,
            research_firm=research_firm,
            db_url=db_url,
        )
        results[symbol] = ok
    primed = sum(1 for v in results.values() if v)
    log.info(
        "prime_position_contexts(%s): primed %d/%d symbols",
        division_slug, primed, len(symbols),
    )
    return results


async def _prime_one(
    *,
    division_slug: str,
    asset_class: str,
    symbol: str,
    horizon_hours: int,
    research_firm: ResearchFirmDeps,
    db_url: str,
) -> bool:
    spec = EngagementSpec(
        requesting_division=division_slug,  # type: ignore[arg-type]
        product_type="position_context",
        asset_class=asset_class,  # type: ignore[arg-type]
        scope=PositionContextScope(
            symbol=symbol,
            time_horizon_hours=horizon_hours,
            current_position_qty=0.0,
            current_position_avg_price=0.0,
            current_position_age_hours=0.0,
        ),
        triggered_by="division_agent",
        triggered_ts=datetime.now(timezone.utc).isoformat(),
    )
    try:
        product = await run_engagement(spec, deps=research_firm)
    except Exception as e:
        log.warning(
            "prime_position_contexts(%s): engagement raised for %s: %s",
            division_slug, symbol, e,
        )
        return False
    if not isinstance(product, PositionContext):
        log.info(
            "prime_position_contexts(%s): no PositionContext for %s "
            "(engagement returned %s)",
            division_slug, symbol, type(product).__name__,
        )
        return False
    try:
        write_position_context(division_slug, product, db_url=db_url)
    except Exception as e:
        log.warning(
            "prime_position_contexts(%s): cache write failed for %s: %s",
            division_slug, symbol, e,
        )
        return False
    return True


async def prime_all_division_position_contexts(
    *,
    research_firm: ResearchFirmDeps,
    db_url: str | None,
    divisions: list[dict],
) -> None:
    """Run primes for multiple divisions concurrently.

    `divisions` is a list of dicts:
      {"slug", "asset_class", "symbols", "horizon_hours"}

    Caller is responsible for assembling these from the live agents.
    Failures are logged; this function does not raise.
    """
    tasks = [
        prime_division_position_contexts(
            division_slug=d["slug"],
            asset_class=d["asset_class"],
            symbols=d["symbols"],
            horizon_hours=d["horizon_hours"],
            research_firm=research_firm,
            db_url=db_url,
        )
        for d in divisions
    ]
    if not tasks:
        return
    await asyncio.gather(*tasks, return_exceptions=True)
