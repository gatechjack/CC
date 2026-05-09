"""Risk Agent — DETERMINISTIC caps in code; LLM is used only to narrate verdicts.

Hard rules (from config/risk.yaml):
  - per_trade_risk_pct: notional > pct * equity → auto-resize down to cap
  - per_strategy_daily_loss_pct: strategy already at cap → REJECT
  - per_account_max_drawdown_pct: account at cap → REJECT (+ flag for flatten)
  - counter_trend_size_multiplier: regime disagrees with trade direction → size *= mult
  - vol scalar: size *= min(1, target_vol / realized_vol) (when realized vol available)
  - correlation_cap: placeholder until 30d returns are tracked

The LLM never decides approve/reject. It only provides a one-sentence
rationale string after the deterministic verdict.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from trading_corp.persistence.models import (
    AccountState, ProposedOrder, StrategyState,
)

log = logging.getLogger(__name__)

Verdict = Literal["approve", "reject", "resize"]


@dataclass
class RiskVerdict:
    verdict: Verdict
    reason: str                # human-readable rationale (deterministic)
    new_qty: float | None = None
    flatten_account: bool = False
    halt_strategy: bool = False
    narration: str | None = None  # optional LLM enrichment

    def is_blocking(self) -> bool:
        return self.verdict == "reject"


class RiskAgent:
    def __init__(
        self,
        risk_yaml: Path = Path("config/risk.yaml"),
        narrator_enabled: bool = True,
    ) -> None:
        self._yaml_path = risk_yaml
        self._mtime: float = 0.0
        self._cfg: dict = {}
        self._narrator_enabled = narrator_enabled
        self._chat = None  # built lazily
        self._reload_if_changed()

    # -- config hot-reload --
    def _reload_if_changed(self) -> None:
        try:
            mtime = self._yaml_path.stat().st_mtime
        except FileNotFoundError:
            self._cfg = {}
            return
        if mtime != self._mtime:
            with self._yaml_path.open("r", encoding="utf-8") as f:
                self._cfg = yaml.safe_load(f) or {}
            self._mtime = mtime
            log.info("RiskAgent reloaded %s", self._yaml_path)

    def _params(self, strategy: str) -> dict:
        self._reload_if_changed()
        g = self._cfg.get("global", {}) or {}
        overrides = (self._cfg.get("overrides", {}) or {}).get(strategy, {}) or {}
        merged = {**g, **overrides}
        merged["counter_trend_size_multiplier"] = (
            self._cfg.get("trend_alignment", {}) or {}
        ).get("counter_trend_size_multiplier", 0.5)
        return merged

    # -- core deterministic evaluator --
    def evaluate(
        self,
        order: ProposedOrder,
        account: AccountState,
        strategy_state: StrategyState,
        regime: str | None = None,
        realized_vol: float | None = None,
    ) -> RiskVerdict:
        params = self._params(order.strategy)

        # 1. Strategy halt check
        if strategy_state.halted:
            return RiskVerdict(
                verdict="reject",
                reason=f"strategy '{order.strategy}' is halted: {strategy_state.halt_reason or 'unspecified'}",
            )

        # 2. Account halt check
        if account.halted:
            return RiskVerdict(
                verdict="reject",
                reason=f"account '{account.account}' is halted: {account.halt_reason or 'drawdown breach'}",
            )

        # 3. Daily loss cap (per strategy)
        daily_cap = float(params.get("per_strategy_daily_loss_pct", 0.03))
        if (
            strategy_state.realized_pnl < 0
            and account.equity > 0
            and abs(strategy_state.realized_pnl) / account.equity >= daily_cap
        ):
            return RiskVerdict(
                verdict="reject",
                reason=f"daily loss cap reached for {order.strategy}: {strategy_state.realized_pnl:.2f} ≥ {daily_cap*100:.1f}% of equity",
                halt_strategy=True,
            )

        # 4. Account max drawdown
        # Per-strategy opt-out for 100%-in/out strategies (e.g.
        # coinbase_btc_donchian) whose edge requires riding through volatility
        # to the next exit signal — auto-flattening mid-position would defeat
        # the strategy. Opt-in only; unset/false preserves today's safety net.
        if not bool(params.get("max_drawdown_disabled", False)):
            max_dd = float(params.get("per_account_max_drawdown_pct", 0.15))
            if account.drawdown_pct() >= max_dd:
                return RiskVerdict(
                    verdict="reject",
                    reason=f"account drawdown {account.drawdown_pct()*100:.1f}% ≥ {max_dd*100:.1f}% cap — flatten and halt",
                    flatten_account=True,
                )

        # Detect options up-front — several caps below have option-specific
        # semantics (covered-call sells aren't bearish; contracts must be whole).
        is_option = bool((order.extra or {}).get("is_option", False))

        # 5. Counter-trend sizing — STOCKS ONLY.
        # Selling a covered call against a long LEAP is income generation,
        # NOT a counter-trend bet on the underlying. Applying the 0.5x
        # multiplier to one leg of a roll produces an asymmetric pair
        # (e.g. close 5 contracts, open 2.5) which is structurally broken.
        new_qty = float(order.qty)
        if not is_option and regime and regime != "unknown":
            counter = (
                (regime == "downtrend" and order.side == "buy")
                or (regime == "uptrend" and order.side == "sell")
            )
            if counter:
                mult = float(params["counter_trend_size_multiplier"])
                new_qty *= mult

        # 6. Volatility scalar — STOCKS ONLY.
        # Sized against realized vol of the UNDERLYING. Option premium is
        # already volatility-priced; double-applying the scalar is wrong.
        if not is_option and realized_vol and realized_vol > 0:
            target = float(params.get("target_annualized_vol", 0.25))
            scalar = min(1.0, target / realized_vol)
            new_qty *= scalar

        # 7. Per-trade risk cap → resize if notional too large
        per_trade = float(params.get("per_trade_risk_pct", 0.015))
        ref_price = order.limit_price if order.limit_price is not None else None
        if ref_price is None or ref_price <= 0:
            # No price reference yet; we can only validate after the executor
            # quotes the symbol. Approve as-is and let executor enforce at fill.
            ref_price = 0.0
        if ref_price > 0 and account.equity > 0:
            risk_cap_notional = account.equity * per_trade
            # Options control 100 shares; dollar exposure per contract is
            # 100 × premium, not just premium.
            contract_multiplier = 100.0 if is_option else 1.0
            current_notional = abs(new_qty) * ref_price * contract_multiplier
            if current_notional > risk_cap_notional:
                resized = risk_cap_notional / (ref_price * contract_multiplier)
                # Options must be whole contracts; floor (never round up).
                if is_option:
                    import math
                    resized = float(math.floor(resized))
                else:
                    # Preserve sign convention: ProposedOrder.qty is unsigned magnitude.
                    resized = float(f"{resized:.6f}")
                if resized <= 0:
                    units = "1 contract" if is_option else "smallest size"
                    return RiskVerdict(
                        verdict="reject",
                        reason=f"per-trade risk cap (${risk_cap_notional:,.2f}) is below {units} for {order.symbol} @ ${ref_price:,.2f}",
                    )
                return RiskVerdict(
                    verdict="resize",
                    new_qty=resized,
                    reason=f"resized {order.qty} → {resized} to honor per-trade risk cap of {per_trade*100:.2f}% (${risk_cap_notional:,.2f})",
                )

        # If we modified qty (counter-trend / vol scalar — stocks only by now),
        # surface as resize too. Options reach here unmodified so this branch
        # is a no-op for them.
        if abs(new_qty - order.qty) > 1e-9:
            # Defensive guard: if a future code path ever modifies an option's
            # qty, floor to whole contracts here too.
            if is_option:
                import math
                new_qty = float(math.floor(new_qty))
                if new_qty <= 0:
                    return RiskVerdict(
                        verdict="reject",
                        reason=f"qty adjustment produced 0 contracts for {order.symbol}",
                    )
            return RiskVerdict(
                verdict="resize",
                new_qty=new_qty,
                reason=f"adjusted qty {order.qty}→{new_qty} for regime/volatility alignment",
            )

        return RiskVerdict(verdict="approve", reason="within all risk caps")

    # -- optional LLM narration --
    async def narrate(self, order: ProposedOrder, verdict: RiskVerdict) -> RiskVerdict:
        if not self._narrator_enabled:
            return verdict
        try:
            if self._chat is None:
                from trading_corp.agents.llm import build_chat_model, is_llm_available
                if not is_llm_available():
                    return verdict
                self._chat = build_chat_model("risk", max_tokens=160)
            from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore
            sys = SystemMessage(content=(
                "You narrate a trading risk verdict in ONE concise sentence. "
                "Be factual and dispassionate; never override the verdict. "
                "Just paraphrase the 'reason' for a busy CEO."
            ))
            user = HumanMessage(content=(
                f"Order: {order.side} {order.qty} {order.symbol} (strategy={order.strategy}). "
                f"Verdict: {verdict.verdict}. Reason: {verdict.reason}."
            ))
            resp = await self._chat.ainvoke([sys, user])
            verdict.narration = (resp.content or "").strip() if hasattr(resp, "content") else None
        except Exception as e:
            log.warning("Risk narration failed: %s", e)
        return verdict
