"""Plain dataclasses for the trading domain. No ORM; thin SQL helpers in db.py."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal

OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]
OrderStatus = Literal[
    "proposed",
    "risk_approved",
    "risk_rejected",
    "board_approved",
    "board_rejected",
    "filled",
    "cancelled",
]


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class ProposedOrder:
    """A trade proposal originating from a strategy/division agent."""
    strategy: str
    symbol: str
    side: OrderSide
    qty: float
    order_type: OrderType = "market"
    limit_price: float | None = None
    rationale: str = ""
    extra: dict = field(default_factory=dict)

    id: str = field(default_factory=_new_id)
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    status: OrderStatus = "proposed"
    risk_reason: str | None = None
    board_reason: str | None = None
    fill_price: float | None = None
    fill_ts: str | None = None

    def to_db_row(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "rationale": self.rationale,
            "status": self.status,
            "risk_reason": self.risk_reason,
            "board_reason": self.board_reason,
            "fill_price": self.fill_price,
            "fill_ts": self.fill_ts,
            "extra_json": json.dumps(self.extra),
        }

    def notional(self) -> float:
        ref = self.limit_price if self.limit_price is not None else 0.0
        return abs(self.qty) * ref


@dataclass
class FillEvent:
    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    price: float
    ts: str
    venue: str


@dataclass
class AuditEvent:
    actor: str            # agent name or 'board'
    kind: str             # 'proposed_order','risk_approved','risk_rejected','board_approved','board_rejected','filled','halt','brief','debate'
    payload: dict
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_db_row(self) -> dict:
        return {
            "ts": self.ts,
            "actor": self.actor,
            "kind": self.kind,
            "payload_json": json.dumps(self.payload, default=str),
        }


@dataclass
class Position:
    account: str
    symbol: str
    qty: float
    avg_price: float
    opened_ts: str
    extra: dict = field(default_factory=dict)


@dataclass
class OpenPosition:
    """Reconciler-facing view of one open trade.

    Distinct from `Position` (venue-aggregated) — one OpenPosition per
    unresolved `paper_trade_record` row in paper mode, one per active
    BitUnix position in Phase 4 live mode. `filled_legs` and `current_sl`
    come from broker truth, never inferred from price + plan.
    """
    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    entry_price: float
    current_sl: float
    tp_plan: list[dict]
    filled_legs: list[str] = field(default_factory=list)
    opened_ts: str = ""


@dataclass
class StrategyState:
    strategy: str
    halted: bool = False
    halt_reason: str | None = None
    realized_pnl: float = 0.0
    realized_pnl_day: str | None = None  # YYYY-MM-DD; cleared on rollover
    updated_ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


@dataclass
class PaperTradeRecord:
    """Structured row in `paper_trade_record` — one per `would_have_placed`
    emission. Phase C replay job populates the result_* fields later."""
    order_id: str
    ts: str
    strategy: str
    division: str
    symbol: str
    side: OrderSide
    qty: float
    tier: str | None = None
    source_signal: str | None = None
    entry_reference_price: float | None = None
    stop_price: float | None = None
    tp_price: float | None = None
    tp_r_multiple: float | None = None
    expected_loss: float | None = None
    expected_gain: float | None = None
    rr_ratio: float | None = None
    max_hold_seconds: int | None = None
    result: str | None = None
    result_ts: str | None = None
    result_price: float | None = None
    actual_pnl_dollars: float | None = None
    actual_r_multiple: float | None = None
    bars_to_resolution: int | None = None
    extra: dict = field(default_factory=dict)

    def to_db_row(self) -> dict:
        return {
            "order_id": self.order_id,
            "ts": self.ts,
            "strategy": self.strategy,
            "division": self.division,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "tier": self.tier,
            "source_signal": self.source_signal,
            "entry_reference_price": self.entry_reference_price,
            "stop_price": self.stop_price,
            "tp_price": self.tp_price,
            "tp_r_multiple": self.tp_r_multiple,
            "expected_loss": self.expected_loss,
            "expected_gain": self.expected_gain,
            "rr_ratio": self.rr_ratio,
            "max_hold_seconds": self.max_hold_seconds,
            "result": self.result,
            "result_ts": self.result_ts,
            "result_price": self.result_price,
            "actual_pnl_dollars": self.actual_pnl_dollars,
            "actual_r_multiple": self.actual_r_multiple,
            "bars_to_resolution": self.bars_to_resolution,
            "extra_json": json.dumps(self.extra) if self.extra else None,
        }

    @classmethod
    def from_order(
        cls,
        order: "ProposedOrder",
        *,
        strategy: str,
        division: str,
        max_hold_seconds: int | None,
    ) -> "PaperTradeRecord":
        """Build a record from a ProposedOrder + strategy context, pulling
        the Phase A trade-card fields out of `order.extra`. Missing fields
        degrade to None (legacy orders predating Phase A still write a row,
        just with NULLs in the trade-spec columns)."""
        extra = order.extra or {}
        max_dollar_risk = extra.get("max_dollar_risk")
        expected_loss = -float(max_dollar_risk) if max_dollar_risk is not None else None
        expected_gain = extra.get("expected_gain_if_tp_hit")
        rr_ratio = None
        if max_dollar_risk and expected_gain:
            try:
                rr_ratio = float(expected_gain) / float(max_dollar_risk)
            except (TypeError, ValueError, ZeroDivisionError):
                rr_ratio = None
        return cls(
            order_id=order.id,
            ts=order.ts,
            strategy=strategy,
            division=division,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            tier=extra.get("tier"),
            source_signal=extra.get("source_signal"),
            entry_reference_price=extra.get("entry_reference_price"),
            stop_price=extra.get("stop_price"),
            tp_price=extra.get("take_profit_price"),
            tp_r_multiple=extra.get("tp_r_multiple"),
            expected_loss=expected_loss,
            expected_gain=float(expected_gain) if expected_gain is not None else None,
            rr_ratio=rr_ratio,
            max_hold_seconds=max_hold_seconds,
        )


@dataclass
class AccountState:
    account: str
    equity: float
    peak_equity: float
    halted: bool = False
    halt_reason: str | None = None
    updated_ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity)
