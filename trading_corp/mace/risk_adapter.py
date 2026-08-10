"""MACE RiskAgent per-leg adapter — the safety-critical real-money gate that
plugs into the STRUCTURAL single-risk-chokepoint (funnel + AST pin + raise-not-
place tests already committed at `f6c94b3`).

The chokepoint is enforced in two places, both consuming ONE adapter instance:

  * `mace/execution.py` — `MaceExecutor._require_risk` calls the injected
    `risk_gate(spec, contracts, direction) -> bool` BEFORE every `port.place_condor`
    / `port.place_resting_close`. A missing gate RAISES (fail-closed); any
    non-approval RAISES `MaceRiskRejected` and NO order is placed. `executor_gate`
    is that callable.
  * `mace/strategy.py` — entry-pipeline filter 10 calls
    `ctx.risk_gate(symbol, spec, contracts) -> bool`; any non-approval records a
    `risk_reject` skip and the condor is never authorized. `strategy_gate` is that
    callable.

Both callables funnel through `_evaluate_legs`, which runs `RiskAgent.evaluate`
over EVERY one of the condor's four legs with `extra["is_option"]=True` (the
joint-IC per-leg pattern, V2) and returns True IFF every leg approves. The MACE
gate is REJECT-ONLY (V2): a `resize` verdict is ignored (the strategy already
sized the whole condor to 5% risk); only a `reject` verdict (RiskVerdict.
is_blocking()) aborts — and any single leg's reject aborts the WHOLE condor.

FAIL-CLOSED, NEVER RAISES: an internal error (RiskAgent throws, account build
fails) is treated as a rejection → returns False. This is deliberate on both
seams: `executor_gate` returning False makes `_require_risk` raise (no
placement); `strategy_gate` returning False records a clean `risk_reject` skip
without sinking the whole entry round (the strategy calls it inside a per-symbol
comprehension where a raised exception would crash every other symbol's eval).

The T5 risk.yaml `overrides.robinhood_mace` block neutralizes the daily-loss /
drawdown AUTOHALTS for this strategy (they would deadlock EXITS); the per-leg
`RiskAgent.evaluate` stays ACTIVE — this adapter is how it stays active. A clean
`AccountState(halted=False)` + `StrategyState(halted=False, realized_pnl=0.0)`
means only reject conditions that genuinely apply to a well-formed condor
(strategy/account halt, originating-side-flip) can fire; equity feeds only the
per-trade-cap RESIZE threshold, which MACE ignores — so equity is audit-only and
a transient snapshot miss never spuriously rejects a correctly-sized condor.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from trading_corp.mace.broker_port import DIR_CREDIT, DIR_DEBIT
from trading_corp.mace.domain import CondorLeg, CondorSpec
from trading_corp.persistence.models import AccountState, ProposedOrder, StrategyState

_LOG = logging.getLogger("mace.risk_adapter")

# Default strategy tag — MUST equal the risk.yaml overrides key so
# RiskAgent._params(order.strategy) picks up the T5 robinhood_mace override.
MACE_STRATEGY_SLUG = "robinhood_mace"


class MaceRiskAdapter:
    """Wraps the shared `RiskAgent` into the two MACE risk-gate signatures.

    Inject one instance; hand `.executor_gate` to `MaceExecutor(risk_gate=...)`
    and `.strategy_gate` to the manager (which threads it onto
    `EntryContext.risk_gate`). Both share `_evaluate_legs` so entry-side legs are
    evaluated identically whichever seam fires first.
    """

    def __init__(
        self,
        risk_agent,
        *,
        account_number: str = "",
        equity_provider: Optional[Callable[[], Optional[float]]] = None,
        db_url: Optional[str] = None,
        strategy_slug: str = MACE_STRATEGY_SLUG,
        audit: Optional[Callable[..., None]] = None,
    ) -> None:
        self._risk = risk_agent
        self._account_number = str(account_number or strategy_slug)
        self._equity_provider = equity_provider
        self._db_url = db_url
        self._slug = strategy_slug
        self._audit_fn = audit

    # ── public gates (the two chokepoint signatures) ─────────────────────
    def strategy_gate(self, symbol: str, spec: CondorSpec, contracts: int) -> bool:
        """Entry-pipeline filter 10: evaluate the OPENING (net-credit) legs.
        `symbol` is redundant with `spec.symbol` (the strategy passes both);
        we key everything off `spec` so the two seams can never diverge."""
        return self._evaluate_legs(spec, contracts, spec.opening_legs(), DIR_CREDIT)

    def executor_gate(self, spec: CondorSpec, contracts: int, direction: str) -> bool:
        """Executor chokepoint: OPENING legs on a net-credit (entry) placement,
        CLOSING legs on a net-debit (exit / resting-PT) placement."""
        legs = spec.opening_legs() if direction == DIR_CREDIT else spec.closing_legs()
        return self._evaluate_legs(spec, contracts, legs, direction)

    # ── the single per-leg evaluation funnel ─────────────────────────────
    def _evaluate_legs(self, spec: CondorSpec, contracts: int,
                       legs: tuple[CondorLeg, ...], direction: str) -> bool:
        """Run RiskAgent over EVERY leg. True IFF all approve. Any reject (or any
        internal error) -> False. NEVER raises (fail-closed to rejection)."""
        try:
            account = self._account_state()
            strat_state = StrategyState(strategy=self._slug, halted=False)
        except Exception as exc:  # noqa: BLE001 — cannot build inputs -> reject
            self._audit("mace_risk_gate_error", symbol=spec.symbol,
                        direction=direction, error=str(exc))
            _LOG.exception("MACE risk gate: input build failed -> reject")
            return False

        for leg in legs:
            order = self._leg_order(spec, leg, contracts, direction)
            try:
                verdict = self._risk.evaluate(
                    order, account, strat_state, db_url=self._db_url)
            except Exception as exc:  # noqa: BLE001 — a gate error is a rejection
                self._audit("mace_risk_gate_error", symbol=spec.symbol,
                            direction=direction, leg=self._leg_role(leg), error=str(exc))
                _LOG.exception("MACE risk gate: RiskAgent.evaluate raised -> reject")
                return False
            if verdict is None or verdict.is_blocking():
                reason = getattr(verdict, "reason", "no verdict")
                self._audit("mace_risk_leg_reject", symbol=spec.symbol,
                            direction=direction, leg=self._leg_role(leg),
                            contracts=contracts, reason=reason)
                _LOG.info("MACE risk REJECT %s %s leg %s x%s — %s", spec.symbol,
                          direction, self._leg_role(leg), contracts, reason)
                return False
            # A `resize` verdict is intentionally IGNORED (V2): the strategy sized
            # the whole condor; per-leg resize would produce asymmetric legs.
        return True

    # ── input builders ───────────────────────────────────────────────────
    def _account_state(self) -> AccountState:
        equity = 0.0
        if self._equity_provider is not None:
            try:
                e = self._equity_provider()
                equity = float(e) if e is not None else 0.0
            except Exception:  # noqa: BLE001 — equity is audit-only for MACE (see docstring)
                equity = 0.0
        # peak == equity -> drawdown_pct() == 0; halted=False. The T5 override
        # neutralizes the autohalts; this keeps the per-leg evaluate active with
        # no spurious drawdown/daily-loss rejects on a correctly-sized condor.
        return AccountState(account=self._account_number, equity=equity,
                            peak_equity=equity, halted=False)

    def _leg_order(self, spec: CondorSpec, leg: CondorLeg, contracts: int,
                   direction: str) -> ProposedOrder:
        # limit_price stays None on purpose: MACE ignores the per-trade-cap resize,
        # and the executor enforces price at fill — fabricating a per-leg premium
        # here would only affect the (ignored) resize threshold. is_option=True is
        # the load-bearing flag (option whole-contract + option-aware caps).
        return ProposedOrder(
            strategy=self._slug,
            symbol=spec.symbol,
            side=leg.side,  # type: ignore[arg-type]  ("buy" | "sell")
            qty=float(contracts),
            order_type="limit",
            limit_price=None,
            rationale=f"mace {direction} condor leg {self._leg_role(leg)}",
            extra={
                "is_option": True,
                "is_multi_leg": True,
                "combo_direction": direction,
                "combo_role": self._leg_role(leg),
                "position_effect": leg.effect,
                "underlying": spec.symbol,
                "expiration": spec.expiry.isoformat(),
                "strike": float(leg.strike),
                "option_type": leg.opt_type,
                "ratio_quantity": 1,
            },
        )

    @staticmethod
    def _leg_role(leg: CondorLeg) -> str:
        """Human role for audit: short_put / long_call / … from (side, type)."""
        wing = "short" if leg.side == "sell" else "long"
        return f"{wing}_{leg.opt_type}"

    def _audit(self, kind: str, **payload) -> None:
        if self._audit_fn is None:
            return
        try:
            self._audit_fn(kind, **payload)
        except Exception:  # noqa: BLE001 — audit must never break the gate
            _LOG.exception("mace risk adapter audit hook failed: %s", kind)
