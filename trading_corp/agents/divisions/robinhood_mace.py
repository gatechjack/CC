"""Robinhood MACE Division — thin portfolio-manager shell (PEAD pattern).

Houses the Multi-Asset Condor Engine (zero-HITL iron condors on the JOINT
account). Mirrors `robinhood_pead.py`: a deliberately thin shell that reads the
hot kill-switches, exposes account metadata, and delegates all real logic to the
attached `MaceManager`. Two config surfaces (plan T1):

  config/strategies.yaml `robinhood_mace: {enabled, auto_execute, ...}` — HOT
      kill-switches (mtime-cached). `auto_execute: false` halts new placements on
      the next decision; exits continue.
  config/divisions.yaml  `slug: robinhood_mace {broker, account_filter, standby}`
      — HOT `standby` + account binding (registration needs a restart).

The MACE-specific addition over the PEAD shell is the FAIL-CLOSED startup
assertion (plan § Startup assertion + [A2026-08-09]): account identity,
option-level >= 3, account-EXCLUSIVITY (no other enabled division binds this
account), and the FOREIGN-POSITION guard (entries disabled while unattributed
option positions/orders exist unless `acknowledge_foreign_positions`). Exclusivity
and account-mismatch are HARD (refuse to arm); option-level and foreign positions
are SOFT (entries disabled, exits + reconcile still run).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

log = logging.getLogger(__name__)


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class MaceArmDecision:
    """Outcome of the fail-closed startup assertion."""
    armed: bool                 # False => refuse to arm (HARD: exclusivity / acct mismatch)
    entries_enabled: bool       # False => entries disabled (SOFT: L<3 / foreign positions)
    exits_enabled: bool = True  # exits + reconcile run even when entries are disabled
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.armed and self.entries_enabled


class RobinhoodMaceAgent:
    """Portfolio-manager shell for the `robinhood_mace` division."""

    DIVISION_SLUG = "robinhood_mace"

    def __init__(
        self,
        cfg: Any,                        # MaceConfig (frozen; account_number + ack flag)
        *,
        divisions_yaml: Path = Path("config/divisions.yaml"),
        strategies_yaml: Path = Path("config/strategies.yaml"),
        manager: Any = None,
        notifier: Any = None,
    ) -> None:
        self._cfg = cfg
        self._divisions_yaml = Path(divisions_yaml)
        self._strategies_yaml = Path(strategies_yaml)
        self._div_mtime = 0.0
        self._div_cfg: dict = {}
        self._div_all: list[dict] = []
        self._strat_mtime = 0.0
        self._strat_cfg: dict = {}
        self._manager = manager
        self._notifier = notifier
        self._reload_divisions()
        self._reload_strategies()

    # ── config reload (mtime-cached) ─────────────────────────────────────
    def _reload_divisions(self) -> None:
        try:
            mtime = self._divisions_yaml.stat().st_mtime
        except FileNotFoundError:
            self._div_cfg, self._div_all, self._div_mtime = {}, [], 0.0
            return
        if mtime == self._div_mtime and self._div_cfg:
            return
        try:
            data = yaml.safe_load(self._divisions_yaml.read_text(encoding="utf-8")) or {}
        except Exception as e:  # noqa: BLE001
            log.warning("RobinhoodMaceAgent: failed to load %s: %s — keeping prior",
                        self._divisions_yaml, e)
            return
        self._div_all = list(data.get("divisions") or [])
        self._div_cfg = next((d for d in self._div_all
                              if d.get("slug") == self.DIVISION_SLUG), {})
        self._div_mtime = mtime

    def _reload_strategies(self) -> None:
        try:
            mtime = self._strategies_yaml.stat().st_mtime
        except FileNotFoundError:
            self._strat_cfg, self._strat_mtime = {}, 0.0
            return
        if mtime == self._strat_mtime and self._strat_cfg:
            return
        try:
            data = yaml.safe_load(self._strategies_yaml.read_text(encoding="utf-8")) or {}
        except Exception as e:  # noqa: BLE001
            log.warning("RobinhoodMaceAgent: failed to load %s: %s — keeping prior",
                        self._strategies_yaml, e)
            return
        self._strat_cfg = dict(data.get(self.DIVISION_SLUG) or {})
        self._strat_mtime = mtime

    # ── injection ────────────────────────────────────────────────────────
    def attach_manager(self, manager: Any) -> None:
        self._manager = manager

    @property
    def has_manager(self) -> bool:
        return self._manager is not None

    # ── config-derived properties (re-stat each read) ────────────────────
    @property
    def slug(self) -> str:
        return self.DIVISION_SLUG

    @property
    def enabled(self) -> bool:
        """Both surfaces must agree: divisions.yaml enabled AND strategies.yaml enabled."""
        self._reload_divisions(); self._reload_strategies()
        return bool(self._div_cfg.get("enabled", False)) and \
            bool(self._strat_cfg.get("enabled", False))

    @property
    def standby(self) -> bool:
        self._reload_divisions()
        return bool(self._div_cfg.get("standby", True))

    @property
    def auto_execute(self) -> bool:
        self._reload_strategies()
        return bool(self._strat_cfg.get("auto_execute", False))

    @property
    def account_filter(self) -> str:
        self._reload_divisions()
        return str(self._div_cfg.get("account_filter", ""))

    @property
    def broker_family(self) -> str:
        self._reload_divisions()
        return str(self._div_cfg.get("broker", ""))

    @property
    def strategy_name(self) -> str | None:
        self._reload_divisions()
        return self._div_cfg.get("strategy")

    def _active(self) -> bool:
        return self.enabled and not self.standby and self._manager is not None

    # ── fail-closed startup assertion (plan § Startup + [A2026-08-09]) ────
    async def assert_startup(self, port) -> MaceArmDecision:
        """Resolve the account + guards against live broker state. Returns a
        decision; NEVER raises on a guard (fail-closed = disable, not crash). HARD
        failures (acct mismatch / exclusivity) set armed=False; SOFT failures
        (option_level<3 / foreign positions) disable entries only."""
        self._reload_divisions()
        reasons: list[str] = []
        armed = True
        entries_enabled = True

        info = await port.account_assertions()
        cfg_acct = str(getattr(self._cfg, "account_number", "") or "")
        div_acct = self.account_filter
        live_acct = str(info.account_number or "")

        # 1) account identity — number must match config + divisions filter (HARD).
        if not live_acct or live_acct != cfg_acct or (div_acct and div_acct != cfg_acct):
            armed = False
            reasons.append(
                f"account mismatch: live={live_acct!r} mace.yaml={cfg_acct!r} "
                f"divisions.filter={div_acct!r}")

        # 2) option level >= 3 (SOFT — entries off, exits/reconcile still run).
        if info.option_level is None or info.option_level < 3:
            entries_enabled = False
            reasons.append(f"option_level {info.option_level} < 3 — entries disabled")

        # 3) exclusivity — no OTHER enabled division may bind this account (HARD).
        conflict = self._exclusivity_conflict(info)
        if conflict:
            armed = False
            reasons.append(f"account not exclusive — also bound by: {conflict}")

        # 4) foreign-position guard (SOFT unless acknowledged).
        foreign = await self._foreign_inventory(port)
        if foreign and not bool(getattr(self._cfg, "acknowledge_foreign_positions", False)):
            entries_enabled = False
            reasons.append(f"foreign positions/orders present: {foreign} — entries disabled "
                           f"(set acknowledge_foreign_positions to override)")

        decision = MaceArmDecision(armed=armed, entries_enabled=(armed and entries_enabled),
                                   exits_enabled=True, reasons=reasons)
        self._notify(decision)
        return decision

    def _exclusivity_conflict(self, info) -> list[str]:
        """Other ENABLED robinhood divisions that would bind the SAME account.
        Heuristic + conservative (errs toward flagging = safe refuse-to-arm): a
        numeric filter equal to the account number, or a keyword filter that is a
        substring of the account type/number (catches joint-IC 'joint' vs the
        joint account), is a conflict."""
        num = str(info.account_number or "")
        atype = str(info.account_type or "").lower()
        my_filter = self.account_filter.lower()
        conflicts: list[str] = []
        for d in self._div_all:
            if d.get("slug") == self.DIVISION_SLUG:
                continue
            if not d.get("enabled", False):
                continue
            if str(d.get("broker", "")).lower() != "robinhood":
                continue
            f = str(d.get("account_filter", "")).strip().lower()
            if not f:
                continue
            if f.isdigit():
                if f == num:
                    conflicts.append(str(d.get("slug")))
            else:
                # keyword filter — conflict if it maps to this account (substring
                # either direction) or matches MACE's own filter keyword.
                if f == my_filter or (atype and (f in atype or atype in f)):
                    conflicts.append(str(d.get("slug")))
        return conflicts

    async def _foreign_inventory(self, port) -> list[str]:
        """Open option positions/orders on the account NOT attributable to MACE.
        Positions match a live mace_rung leg by (symbol, expiry, type, strike);
        orders match by the `mace-` ref_id prefix. Anything else is foreign."""
        foreign: list[str] = []
        own_legs = self._own_leg_keys()

        try:
            positions = await port.open_positions()
        except Exception as exc:  # noqa: BLE001 — cannot verify -> flag (fail-closed)
            return [f"open_positions() failed: {exc}"]
        for p in positions:
            raw = p.raw or {}
            key = (str(raw.get("chain_symbol") or p.symbol or "").upper(),
                   str(raw.get("expiration_date") or ""),
                   str(raw.get("option_type") or "").lower(),
                   round(_f(raw.get("strike_price")), 4))
            if key not in own_legs:
                foreign.append(f"position {p.symbol} {raw.get('option_type')} "
                               f"{raw.get('strike_price')} x{p.quantity}")

        try:
            orders = await port.open_orders()
        except Exception as exc:  # noqa: BLE001
            foreign.append(f"open_orders() failed: {exc}")
            return foreign
        for o in orders:
            if not (o.ref_id and str(o.ref_id).startswith("mace-")):
                foreign.append(f"order {o.order_id} ref={o.ref_id}")
        return foreign

    def _own_leg_keys(self) -> set:
        """Leg keys for MACE's live rungs (submitting/open/closing) — the set a
        broker option position must belong to, else it is foreign."""
        keys: set = set()
        if self._manager is None:
            return keys
        try:
            rungs = self._manager.store.load_by_status("submitting", "open", "closing")
        except Exception:  # noqa: BLE001
            return keys
        for r in rungs:
            for leg in r.spec.opening_legs():
                keys.add((r.symbol.upper(), r.expiry.isoformat(),
                          leg.opt_type.lower(), round(float(leg.strike), 4)))
        return keys

    def _notify(self, decision: MaceArmDecision) -> None:
        if self._notifier is None or (decision.armed and decision.entries_enabled):
            return
        cond = "ARM REFUSED" if not decision.armed else "entries disabled at startup"
        try:
            self._notifier.breaker(
                condition=f"MACE startup — {cond}",
                lines=list(decision.reasons),
                suggested_action="review divisions.yaml / account / positions before go-live")
        except Exception:  # noqa: BLE001
            log.exception("RobinhoodMaceAgent: startup notify failed")
