"""PositionContext pre-emptive cache (Phase 1d, Q7).

Division agents (Lord Otter / Market Cypher) read PositionContext from
this cache on alert. Misses return None — the consumer treats that as
"no signal," NOT a small bearish signal (per design Q7).

Cache keyspace lives in `agent_state` under (division_slug,
'position_context:<symbol>:<horizon_hours>h'). Per-division TTLs are
read from `config/research.yaml` `position_context_ttls` block; missing
divisions default to 3600s.

The cache is hot (latency-critical for on-alert reads) so all access
goes through these helpers; raw `agent_state` reads/writes are not the
contract.

See planning/research_firm_design.md §Q7, Phase 1d.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

from trading_corp.agents.research.schemas import PositionContext
from trading_corp.persistence.db import (
    delete_agent_state, load_agent_state, set_agent_state,
)

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RESEARCH_YAML = _REPO_ROOT / "config" / "research.yaml"

_DEFAULT_TTL_SECONDS = 3600


def cache_key(symbol: str, time_horizon_hours: int) -> str:
    """Cache key format from design Q7."""
    return f"position_context:{symbol}:{time_horizon_hours}h"


def ttl_seconds_for(division: str) -> int:
    """Per-division TTL from research.yaml. Falls back to 3600s on any
    config issue — fail-soft is the design contract."""
    try:
        with _RESEARCH_YAML.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return _DEFAULT_TTL_SECONDS
    except Exception as e:
        log.warning("position_context_cache: yaml read failed: %s", e)
        return _DEFAULT_TTL_SECONDS
    block = cfg.get("position_context_ttls") or {}
    val = block.get(division)
    if isinstance(val, (int, float)) and val > 0:
        return int(val)
    return _DEFAULT_TTL_SECONDS


def write_position_context(
    division: str,
    pc: PositionContext,
    *,
    db_url: str,
) -> None:
    """Persist a freshly synthesized PositionContext to the cache.
    `db_url` is required — there is no in-memory mode here. Caller is
    expected to drop the call entirely when persistence is disabled."""
    set_agent_state(
        division,
        cache_key(pc.symbol, pc.time_horizon_hours),
        pc.model_dump(mode="json"),
        db_url=db_url,
    )


def read_position_context(
    division: str,
    symbol: str,
    time_horizon_hours: int,
    *,
    db_url: str,
    now: datetime | None = None,
) -> PositionContext | None:
    """Return the cached PositionContext if present and within TTL,
    otherwise None. Stale entries are deleted on read (lazy GC).

    `now` is injectable for tests; production passes None and gets
    `datetime.now(timezone.utc)`.
    """
    key = cache_key(symbol, time_horizon_hours)
    try:
        result = load_agent_state(division, key, db_url=db_url)
    except Exception as e:
        log.warning(
            "position_context_cache: read failed division=%s key=%s: %s",
            division, key, e,
        )
        return None
    if result is None:
        return None
    value, updated_at = result
    now_ = now or datetime.now(timezone.utc)
    age_s = (now_ - updated_at).total_seconds()
    ttl = ttl_seconds_for(division)
    if age_s > ttl:
        try:
            delete_agent_state(division, key, db_url=db_url)
        except Exception:
            pass
        return None
    try:
        return PositionContext.model_validate(value)
    except Exception as e:
        # Schema drift between writer and reader (e.g. PositionContext
        # gained a required field). Don't fail the alert path; treat as
        # cache miss and let the next prime overwrite.
        log.warning(
            "position_context_cache: schema-validate failed for "
            "division=%s key=%s: %s — treating as miss", division, key, e,
        )
        return None


def evict_position_context(
    division: str,
    symbol: str,
    time_horizon_hours: int,
    *,
    db_url: str,
) -> None:
    """Manual eviction. Used by tests; production lazy-GCs on stale read."""
    try:
        delete_agent_state(
            division,
            cache_key(symbol, time_horizon_hours),
            db_url=db_url,
        )
    except Exception:
        pass
