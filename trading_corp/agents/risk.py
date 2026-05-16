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
        db_url: str | None = None,
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

        # 2.5. Polymarket-specific atomic + aggregate caps. Routed by the
        # `is_prediction_market` flag on extra so future Polymarket
        # strategies (copy_trading) reuse the same gate without us
        # adding a second branch. Runs BEFORE the standard per-trade
        # risk cap because Polymarket's cap structure is fundamentally
        # different — implied-probability bounds + notional ceilings,
        # not per-trade-pct sizing.
        if (order.extra or {}).get("is_prediction_market") and not order.strategy.startswith("kalshi_"):
            # Kalshi strategies also set is_prediction_market but trade deep-OTM
            # markets at $0.01/$0.99 implied — polymarket's [0.05, 0.95] bound
            # would misfire. Kalshi falls through to the generic per-trade-risk-pct
            # path until a dedicated kalshi evaluator is built (caps already
            # configured in risk.yaml kalshi: section, awaiting wiring).
            poly_verdict = self._evaluate_polymarket(order, account, db_url=db_url)
            if poly_verdict is not None:
                return poly_verdict

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

    # -- Polymarket-specific cap evaluator --
    def _evaluate_polymarket(
        self,
        order: ProposedOrder,
        account: AccountState,
        *,
        db_url: str | None = None,
    ) -> RiskVerdict | None:
        """Apply Polymarket's bespoke cap structure to a prediction-market
        order. Returns a RiskVerdict if the order is rejected/resized, or
        None to fall through to the generic evaluator (which will then
        approve since the per-trade-pct caps don't bind on $1-shakedown
        orders).

        Caps applied:
          - implied-probability bounds (5-95%) — re-check the strategy's
            pre-filter at the gate as defense-in-depth
          - max % division equity per position — atomic
          - max single-market notional — atomic
          - daily aggregate exposure cap (25% equity, capped at $1K) —
            queries audit_event for today's filled + would_have_placed
            polymarket rows
          - total open-aggregate cap ($1K) — queries audit_event for
            currently-open positions

        Aggregate-cap queries are cheap (single audit_event scan with
        actor='polymarket_arbitrage' filter, today's date partition). They
        gracefully degrade to "approve" if db_url is None or query fails —
        the deterministic atomic caps are the load-bearing safety net,
        aggregates are belt-and-suspenders.
        """
        self._reload_if_changed()
        poly_cfg = self._cfg.get("polymarket", {}) or {}
        if not poly_cfg:
            log.warning("RiskAgent: no polymarket caps in risk.yaml; falling through")
            return None

        extra = order.extra or {}
        implied = extra.get("implied_prob_at_entry")
        if implied is not None:
            try:
                implied = float(implied)
            except (TypeError, ValueError):
                implied = None
        prob_lo = float(poly_cfg.get("min_implied_probability", 0.05))
        prob_hi = float(poly_cfg.get("max_implied_probability", 0.95))
        if implied is not None and not (prob_lo <= implied <= prob_hi):
            return RiskVerdict(
                verdict="reject",
                reason=(
                    f"polymarket: implied prob {implied:.3f} outside "
                    f"[{prob_lo:.2f}, {prob_hi:.2f}] bounds"
                ),
            )

        # Notional = qty × limit_price (cost in USDC; binary outcome
        # caps loss at notional).
        ref_price = order.limit_price or 0.0
        notional = abs(float(order.qty)) * float(ref_price)

        # Per-position % of division equity. account.equity here is the
        # division's equity (caller passes the right account snapshot).
        # If equity is zero/None, skip the % check (strategy is in
        # shakedown / wallet not funded yet).
        max_pct = float(poly_cfg.get("max_pct_division_equity_per_position", 0.05))
        if account.equity > 0 and max_pct > 0:
            cap_dollars = account.equity * max_pct
            if notional > cap_dollars:
                return RiskVerdict(
                    verdict="reject",
                    reason=(
                        f"polymarket: ${notional:.2f} notional > "
                        f"{max_pct*100:.1f}% of ${account.equity:.2f} "
                        f"division equity (cap ${cap_dollars:.2f})"
                    ),
                )

        # Single-market notional cap (hard $).
        max_single = float(poly_cfg.get("max_single_market_notional_usd", 250.0))
        if notional > max_single:
            return RiskVerdict(
                verdict="reject",
                reason=(
                    f"polymarket: ${notional:.2f} notional > "
                    f"${max_single:.2f} single-market cap"
                ),
            )

        # Aggregate caps — best-effort against audit_event.
        if db_url:
            try:
                today_notional = self._sum_polymarket_today(db_url)
                open_notional = self._sum_polymarket_open(db_url)
            except Exception as e:
                log.debug("RiskAgent: polymarket aggregate query failed: %s", e)
                today_notional = 0.0
                open_notional = 0.0

            # Daily new exposure cap. Apply both the equity-pct AND the
            # absolute $ cap; the tighter of the two binds.
            daily_pct = float(poly_cfg.get("daily_aggregate_max_pct_of_equity", 0.25))
            daily_abs = float(poly_cfg.get("daily_aggregate_cap_usd", 1_000.0))
            daily_cap = daily_abs
            if account.equity > 0 and daily_pct > 0:
                daily_cap = min(daily_abs, account.equity * daily_pct)
            if today_notional + notional > daily_cap:
                return RiskVerdict(
                    verdict="reject",
                    reason=(
                        f"polymarket: daily aggregate cap reached "
                        f"(${today_notional:.2f} today + ${notional:.2f} "
                        f"new > ${daily_cap:.2f})"
                    ),
                )

            # Total open-position cap.
            total_cap = float(poly_cfg.get("total_open_aggregate_cap_usd", 1_000.0))
            if open_notional + notional > total_cap:
                return RiskVerdict(
                    verdict="reject",
                    reason=(
                        f"polymarket: total open aggregate cap "
                        f"(${open_notional:.2f} open + ${notional:.2f} "
                        f"new > ${total_cap:.2f})"
                    ),
                )

        # All atomic + aggregate Polymarket caps cleared. Fall through to
        # the generic evaluator (which will run the per-trade-pct cap;
        # for $1-shakedown orders that's a non-binding pass-through).
        return None

    # -- audit_event aggregation helpers (polymarket caps) --

    @staticmethod
    def _sum_polymarket_today(db_url: str) -> float:
        """Sum notional of polymarket orders proposed today.

        Counts kinds: would_have_placed, board_approved, filled — the
        states that represent committed-or-likely-committed exposure
        for the day's aggregate cap. Resets at UTC midnight; future:
        switch to the division's local TZ if it matters for the cap
        semantics.
        """
        import sqlite3
        import json
        from datetime import datetime, timezone
        path = db_url.replace("sqlite:///", "")
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        total = 0.0
        with sqlite3.connect(path) as conn:
            cur = conn.execute(
                "SELECT payload_json FROM audit_event "
                "WHERE actor='polymarket_arbitrage' "
                "AND kind IN ('would_have_placed','board_approved','filled') "
                "AND substr(ts,1,10) = ?",
                (today_iso,),
            )
            for (raw,) in cur.fetchall():
                try:
                    p = json.loads(raw)
                    qty = float(p.get("qty") or 0.0)
                    px = float(p.get("limit_price") or p.get("fill_price") or p.get("price") or 0.0)
                    total += abs(qty) * px
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
        return total

    @staticmethod
    def _sum_polymarket_open(db_url: str) -> float:
        """Sum notional of polymarket positions still considered open.

        Phase 2a operates in paper mode (auto_execute=false), so "open"
        means filled + not-yet-resolved. We use the position table when
        available; fall back to filled audit rows minus resolution
        rows. For Phase 2a (no fills will happen until Phase 3) this
        returns 0.0 in practice — the cap is a forward-compat scaffold.
        """
        # Phase 2a: no live fills, so this is always 0. Phase 3 will
        # implement the real position-aggregation query.
        return 0.0

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
