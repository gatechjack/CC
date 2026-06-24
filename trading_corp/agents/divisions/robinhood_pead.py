"""Robinhood PEAD Division — minimal portfolio-manager shell.

Houses the long-only post-earnings-announcement-drift strategy
(`pead_strategy.py`). Mirrors `robinhood_joint.py` but **flat**: PEAD enters
single-leg equity buys and exits single-leg sells, so `scan()` /`manage()`
return `list[ProposedOrder]` (not the iron-condor combo `list[list[...]]`).

The shell is deliberately thin — reads `config/divisions.yaml` (mtime-cached),
exposes account metadata, enforces the kill-switches (`enabled`, `standby`,
`has_strategy`), and routes `scan()` / `manage()` to the attached strategy.
The strategy owns all real logic (entry signal + sizing + the live exit engine).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from trading_corp.brokers.base import Broker
from trading_corp.persistence.models import ProposedOrder

log = logging.getLogger(__name__)

# Idle cadence (seconds) returned from manage() when disabled / no strategy.
_DEFAULT_IDLE_CADENCE_SEC = 1800


class RobinhoodPEADAgent:
    """Portfolio-manager shell for the `robinhood_pead` division.

    Reads its `config/divisions.yaml` entry (mtime-cached reload) and delegates
    scan/manage to the attached PEAD strategy module.
    """

    DIVISION_SLUG = "robinhood_pead"

    def __init__(
        self,
        divisions_yaml: Path = Path("config/divisions.yaml"),
        *,
        strategy: Any = None,
    ) -> None:
        self._divisions_yaml = Path(divisions_yaml)
        self._mtime: float = 0.0
        self._cfg: dict = {}
        self._strategy = strategy
        self._reload()

    # ── config reload (mtime-cached; divisions.yaml edits take effect live) ──
    def _reload(self) -> None:
        try:
            mtime = self._divisions_yaml.stat().st_mtime
        except FileNotFoundError:
            log.warning("RobinhoodPEADAgent: %s missing — division inactive",
                        self._divisions_yaml)
            self._cfg = {}
            self._mtime = 0.0
            return
        if mtime == self._mtime and self._cfg:
            return
        try:
            with self._divisions_yaml.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            log.warning("RobinhoodPEADAgent: failed to load %s: %s — keeping prior",
                        self._divisions_yaml, e)
            return
        for entry in (data.get("divisions") or []):
            if entry.get("slug") == self.DIVISION_SLUG:
                self._cfg = entry
                self._mtime = mtime
                return
        log.warning("RobinhoodPEADAgent: no %r entry in %s — division inactive",
                    self.DIVISION_SLUG, self._divisions_yaml)
        self._cfg = {}
        self._mtime = mtime

    # ── strategy injection ──
    def attach_strategy(self, strategy: Any) -> None:
        """Wire the PEAD strategy after construction (main.py). Idempotent."""
        self._strategy = strategy

    @property
    def has_strategy(self) -> bool:
        return self._strategy is not None

    # ── config-derived properties (re-stat divisions.yaml each read) ──
    @property
    def slug(self) -> str:
        return self.DIVISION_SLUG

    @property
    def enabled(self) -> bool:
        self._reload()
        return bool(self._cfg.get("enabled", False))

    @property
    def broker_family(self) -> str:
        self._reload()
        return str(self._cfg.get("broker", ""))

    @property
    def account_filter(self) -> str:
        self._reload()
        return str(self._cfg.get("account_filter", ""))

    @property
    def strategy_name(self) -> str | None:
        self._reload()
        return self._cfg.get("strategy")

    @property
    def standby(self) -> bool:
        self._reload()
        return bool(self._cfg.get("standby", False))

    # ── decision dispatch — flat single-leg equity orders ──
    def _active(self) -> bool:
        return self.enabled and not self.standby and self._strategy is not None

    async def scan(
        self, broker: Broker, regime: str = "neutral"
    ) -> list[ProposedOrder]:
        """Daily post-announcement entry scan → flat list of buy ProposedOrders
        (empty when disabled / standby / no candidates)."""
        if not self.enabled or self.standby:
            log.info("RobinhoodPEADAgent: disabled/standby — scan skipped")
            return []
        if self._strategy is None:
            log.warning("RobinhoodPEADAgent.scan: no strategy attached — returning []")
            return []
        return await self._strategy.scan(broker, regime=regime)

    async def manage(
        self, broker: Broker
    ) -> tuple[list[ProposedOrder], int]:
        """Live exit-engine tick → `(sell ProposedOrders, next_cadence_seconds)`.
        The strategy computes the four exit pressures (the locked pead_pressures
        contract) on the open book and emits flat sells when a rule fires."""
        if not self.enabled or self.standby:
            log.info("RobinhoodPEADAgent: disabled/standby — manage skipped")
            return [], _DEFAULT_IDLE_CADENCE_SEC
        if self._strategy is None:
            log.warning("RobinhoodPEADAgent.manage: no strategy attached — returning []")
            return [], _DEFAULT_IDLE_CADENCE_SEC
        return await self._strategy.manage(broker)

    async def reconcile(
        self, broker: Broker
    ) -> tuple[list[ProposedOrder], int]:
        """Flag-2 deferred-fill reconcile tick → `(promoted ProposedOrders,
        next_poll_seconds)`. Drains the PENDING store at/after the open (promotes a
        confirmed fill to a record, or cancels the >5% collar miss). No-op while
        disabled/standby (returns idle cadence) so it ships INERT — same kill-switch
        as scan()/manage()."""
        if not self.enabled or self.standby:
            return [], _DEFAULT_IDLE_CADENCE_SEC
        if self._strategy is None:
            log.warning("RobinhoodPEADAgent.reconcile: no strategy attached — returning []")
            return [], _DEFAULT_IDLE_CADENCE_SEC
        return await self._strategy.reconcile(broker)
