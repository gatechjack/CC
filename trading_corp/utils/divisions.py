"""Division registry loader.

Reads `config/divisions.yaml` and returns typed `Division` records grouped
by investment type (Individual / Crypto / Retirement). Used by the web
dashboard, the CEO's morning brief, and (later) broker-account routing
logic.
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
_INVESTMENT_TYPE_ORDER = ["individual", "crypto", "prediction_markets", "retirement"]
_INVESTMENT_TYPE_LABELS = {
    "individual":          "Individual",
    "crypto":              "Crypto",
    "prediction_markets":  "Prediction Markets",
    "retirement":          "Retirement",
}
_CRYPTO_BROKERS = {"coinbase", "bitunix"}
_PREDICTION_MARKET_BROKERS = {"polymarket", "kalshi"}
# Slug prefixes → "prediction_markets" group. Some prediction-market divisions
# use `broker: paper` as a placeholder until later phases wire the real
# strategy (e.g. polymarket_copy_trading), so they can't be classified by
# broker family alone. Slug-prefix matching routes both polymarket_* and
# kalshi_* divisions into the "prediction_markets" group regardless of their
# broker family. (Brokers that ARE polymarket / kalshi also land in the
# group; either path works.)
_PREDICTION_MARKET_SLUG_PREFIXES = ("polymarket_", "kalshi_")


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
    standby: bool = False       # true = no order path; UI shows STANDBY badge
    paper_capital: float = 0.0  # starting equity for `broker: paper` divisions
                                # (used by Kelly-sized strategies — see main.py
                                # `family == "paper"` branch). $0 = legacy default.

    # Filled in at runtime by the dashboard data layer; kept here for type
    # convenience so templates can iterate `division.equity` etc. directly.
    equity: float | None = None
    pnl_today: float | None = None
    pnl_today_pct: float | None = None
    benchmark_pct: float | None = None
    position_count: int = 0
    status: str = "unknown"     # online | offline | not_wired
    # Donchian-strategy overview for the home tile widget. Only set on
    # divisions running a Donchian strategy (today: coinbase_spot). Keys:
    # state ('cash'|'btc'), cost_basis, current_close, donchian_low,
    # donchian_high, dial_position (0..1 clamped, None pre-first-eval),
    # last_eval_ts. None for everyone else.
    donchian: dict | None = None
    # Prediction-market tile overview (K2.4 dashboard). Only set on the 4
    # (later 5) divisions in the "prediction_markets" investment group. Keys:
    # n_resolved, n_pending, n_wins, n_losses, win_rate_pct (None pre-first
    # resolve), total_realized_pnl. None for everyone else.
    pm_overview: dict | None = None

    @property
    def intent_label(self) -> str:
        return self.intent.capitalize()

    @property
    def is_aggressive(self) -> bool:
        return self.intent == "aggressive"

    @property
    def is_retirement(self) -> bool:
        return self.intent == "retirement"


def classify_investment_type(d: Division) -> str:
    """Map a division to its investment-type group."""
    if d.intent == "retirement":
        return "retirement"
    if d.broker in _PREDICTION_MARKET_BROKERS or any(
        d.slug.startswith(p) for p in _PREDICTION_MARKET_SLUG_PREFIXES
    ):
        return "prediction_markets"
    if d.broker in _CRYPTO_BROKERS:
        return "crypto"
    return "individual"


@dataclass
class InvestmentGroup:
    """One investment-type bundle (Individual / Crypto / Retirement)."""
    key: str                    # "individual" | "crypto" | "retirement"
    label: str                  # "Individual" | "Crypto" | "Retirement"
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
                standby=bool(entry.get("standby", False)),
                paper_capital=float(entry.get("paper_capital", 0.0) or 0.0),
            )
        except KeyError as e:
            log.warning("divisions.yaml: skipping entry missing key %s — %r", e, entry)
            continue
        if d.enabled:
            out.append(d)
    return out


def group_by_investment_type(divisions: Iterable[Division]) -> list[InvestmentGroup]:
    """Bundle divisions by investment type, ordered per `_INVESTMENT_TYPE_ORDER`."""
    by_key: dict[str, list[Division]] = {}
    for d in divisions:
        by_key.setdefault(classify_investment_type(d), []).append(d)

    out: list[InvestmentGroup] = []
    seen: set[str] = set()
    for key in _INVESTMENT_TYPE_ORDER:
        if key in by_key:
            out.append(InvestmentGroup(
                key=key,
                label=_INVESTMENT_TYPE_LABELS.get(key, key.title()),
                divisions=by_key[key],
            ))
            seen.add(key)
    # Any unrecognized investment-type keys go at the end (defensive)
    for key, ds in by_key.items():
        if key in seen:
            continue
        out.append(InvestmentGroup(
            key=key,
            label=_INVESTMENT_TYPE_LABELS.get(key, key.title()),
            divisions=ds,
        ))
    return out


def reload_cache() -> None:
    """Force a re-read of divisions.yaml on next call. Useful for tests."""
    _read_yaml.cache_clear()


__all__ = [
    "Division", "InvestmentGroup", "classify_investment_type",
    "load_divisions", "group_by_investment_type", "reload_cache",
]
