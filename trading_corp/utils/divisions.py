"""Division registry loader.

Reads `config/divisions.yaml` and returns typed `Division` records grouped
by broker. Used by the web dashboard, the CEO's morning brief, and (later)
broker-account routing logic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml

log = logging.getLogger(__name__)

_DEFAULT_PATH = Path("config/divisions.yaml")

# Visual order — sections render in this order on the dashboard.
_BROKER_ORDER = ["robinhood", "fidelity", "coinbase", "paper"]
_BROKER_LABELS = {
    "robinhood": "Robinhood",
    "fidelity":  "Fidelity",
    "coinbase":  "Coinbase",
    "paper":     "Paper",
}


@dataclass
class Division:
    slug: str
    name: str
    broker: str
    account_filter: str
    intent: str                 # aggressive | balanced | retirement
    benchmark: str
    target_annual_return: float | None = None
    strategy: str | None = None
    enabled: bool = True

    # Filled in at runtime by the dashboard data layer; kept here for type
    # convenience so templates can iterate `division.equity` etc. directly.
    equity: float | None = None
    pnl_today: float | None = None
    pnl_today_pct: float | None = None
    benchmark_pct: float | None = None
    position_count: int = 0
    status: str = "unknown"     # online | offline | not_wired

    @property
    def intent_label(self) -> str:
        return self.intent.capitalize()

    @property
    def is_aggressive(self) -> bool:
        return self.intent == "aggressive"

    @property
    def is_retirement(self) -> bool:
        return self.intent == "retirement"


@dataclass
class BrokerGroup:
    """One broker's bundled divisions plus aggregate metrics."""
    key: str                    # "robinhood"
    label: str                  # "Robinhood"
    divisions: list[Division]
    total_equity: float = 0.0
    total_pnl_today: float = 0.0


@lru_cache(maxsize=1)
def _read_yaml(path_str: str) -> dict:
    p = Path(path_str)
    if not p.exists():
        log.warning("divisions.yaml not found at %s", path_str)
        return {}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_divisions(path: Path = _DEFAULT_PATH) -> list[Division]:
    """Return the list of enabled Division objects, in declared order."""
    data = _read_yaml(str(path))
    out: list[Division] = []
    for entry in (data.get("divisions") or []):
        try:
            d = Division(
                slug=str(entry["slug"]),
                name=str(entry["name"]),
                broker=str(entry["broker"]),
                account_filter=str(entry.get("account_filter", "")),
                intent=str(entry.get("intent", "balanced")),
                benchmark=str(entry.get("benchmark", "SPY")),
                target_annual_return=(
                    float(entry["target_annual_return"])
                    if entry.get("target_annual_return") is not None
                    else None
                ),
                strategy=entry.get("strategy"),
                enabled=bool(entry.get("enabled", True)),
            )
        except KeyError as e:
            log.warning("divisions.yaml: skipping entry missing key %s — %r", e, entry)
            continue
        if d.enabled:
            out.append(d)
    return out


def group_by_broker(divisions: Iterable[Division]) -> list[BrokerGroup]:
    """Bundle divisions by broker, ordered by `_BROKER_ORDER`."""
    by_key: dict[str, list[Division]] = {}
    for d in divisions:
        by_key.setdefault(d.broker, []).append(d)

    out: list[BrokerGroup] = []
    seen: set[str] = set()
    for key in _BROKER_ORDER:
        if key in by_key:
            out.append(BrokerGroup(
                key=key,
                label=_BROKER_LABELS.get(key, key.title()),
                divisions=by_key[key],
            ))
            seen.add(key)
    # Any unrecognized broker keys go at the end
    for key, ds in by_key.items():
        if key in seen:
            continue
        out.append(BrokerGroup(
            key=key,
            label=_BROKER_LABELS.get(key, key.title()),
            divisions=ds,
        ))
    return out


def reload_cache() -> None:
    """Force a re-read of divisions.yaml on next call. Useful for tests."""
    _read_yaml.cache_clear()


__all__ = [
    "Division", "BrokerGroup",
    "load_divisions", "group_by_broker", "reload_cache",
]
