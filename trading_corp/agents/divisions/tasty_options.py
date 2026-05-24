"""Tasty Options Division — minimal portfolio-manager shell.

Sibling division to `robinhood_joint`. Houses the 45 DTE iron-condor
strategy (`tasty_options_iron_condor`) running against Tastytrade
instead of Robinhood. The strategy logic is functionally identical to
RH Joint's IC; the differences live entirely in (a) the broker —
TastytradeBroker via `tastytrade` family, (b) the universe semantics —
"watchlist" rather than predetermined list: the auto-scanner still
uses it, but operators can paste any ticker into the morning grader
and trade it if it grades cleanly.

Shape mirrors `robinhood_joint.py` (intentional sibling-clone, not a
refactor — see plan doc for the rationale). Strategy is attached after
construction via `attach_strategy()`; main.py wires both.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from trading_corp.brokers.base import Broker
from trading_corp.persistence.models import ProposedOrder

log = logging.getLogger(__name__)


# Default cadence (seconds) returned from `manage()` when there's no
# attached strategy or the division is disabled — matches the "no open
# positions" idle cadence documented in the iron-condor strategy plan.
_DEFAULT_IDLE_CADENCE_SEC = 1800


class TastyOptionsAgent:
    """Portfolio-manager shell for the `tasty_options` division.

    Reads its config entry from `config/divisions.yaml` (mtime-cached
    reload), exposes account metadata as properties, and delegates the
    actual scan/manage decision tree to the iron-condor strategy module.
    """

    DIVISION_SLUG = "tasty_options"

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

    # ------------------------------------------------------------------
    # Config reload — mtime-cached so edits to divisions.yaml take effect
    # without restart.
    # ------------------------------------------------------------------

    def _reload(self) -> None:
        try:
            mtime = self._divisions_yaml.stat().st_mtime
        except FileNotFoundError:
            log.warning(
                "TastyOptionsAgent: %s does not exist — division will be inactive",
                self._divisions_yaml,
            )
            self._cfg = {}
            self._mtime = 0.0
            return
        if mtime == self._mtime and self._cfg:
            return
        try:
            with self._divisions_yaml.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            log.warning(
                "TastyOptionsAgent: failed to load %s: %s — keeping prior config",
                self._divisions_yaml, e,
            )
            return
        for entry in (data.get("divisions") or []):
            if entry.get("slug") == self.DIVISION_SLUG:
                self._cfg = entry
                self._mtime = mtime
                return
        log.warning(
            "TastyOptionsAgent: no %r entry in %s — division will be inactive",
            self.DIVISION_SLUG, self._divisions_yaml,
        )
        self._cfg = {}
        self._mtime = mtime

    # ------------------------------------------------------------------
    # Strategy injection
    # ------------------------------------------------------------------

    def attach_strategy(self, strategy: Any) -> None:
        """Wire the iron-condor strategy after construction.

        Idempotent. main.py calls this once the strategy module has been
        constructed. Tests inject a mock here.
        """
        self._strategy = strategy

    @property
    def has_strategy(self) -> bool:
        return self._strategy is not None

    # ------------------------------------------------------------------
    # Config-derived properties (re-stat divisions.yaml on each read).
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Decision-tree dispatch — the strategy module owns the real logic.
    # The shell only enforces the kill-switches (enabled, has_strategy).
    # ------------------------------------------------------------------

    async def scan(
        self, broker: Broker, regime: str = "neutral"
    ) -> list[list[ProposedOrder]]:
        """Daily entry scan. Returns one combo (list of 4 ProposedOrders)
        per qualifying watchlist symbol; empty when no candidates qualify
        OR when the watchlist is empty (operator-only entry via grader).
        """
        if not self.enabled:
            log.info(
                "TastyOptionsAgent: division disabled — scan skipped"
            )
            return []
        if self._strategy is None:
            log.warning(
                "TastyOptionsAgent.scan: no strategy attached — returning []"
            )
            return []
        return await self._strategy.scan(broker, regime=regime)

    async def manage(
        self, broker: Broker
    ) -> tuple[list[list[ProposedOrder]], int]:
        """Position-management tick. Returns
        `(action_combos, next_cadence_seconds)` so the Position Manager
        loop in main.py can dynamically tune its sleep based on
        portfolio state (5/15/30 min cadences per the IC strategy plan).
        """
        if not self.enabled:
            log.info(
                "TastyOptionsAgent: division disabled — manage skipped"
            )
            return [], _DEFAULT_IDLE_CADENCE_SEC
        if self._strategy is None:
            log.warning(
                "TastyOptionsAgent.manage: no strategy attached — returning []"
            )
            return [], _DEFAULT_IDLE_CADENCE_SEC
        return await self._strategy.manage(broker)
