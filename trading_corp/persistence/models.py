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
class StrategyState:
    strategy: str
    halted: bool = False
    halt_reason: str | None = None
    realized_pnl: float = 0.0
    realized_pnl_day: str | None = None  # YYYY-MM-DD; cleared on rollover
    updated_ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


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
