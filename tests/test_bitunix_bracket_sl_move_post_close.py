"""Regression for the 2026-07-02 bitunix_futures SL-trail diagnosis.

`move_bracket_sls` must NOT mis-read a fully-closed / absent position as a
TP-fill. On a full close the venue drops the position from
`get_pending_positions()`, so `pos_qty.get(key, 0.0)` defaulted to 0.0 and was
read as "TP1+TP2 filled" — driving a positionId-less `modify_position_sl` that
fail-soft-skipped and logged a WARNING ("positionId absent for BTC/USDT.P")
that read like a live-risk protection failure. It was a post-close no-op
(nothing to trail; the close is booked by the auto-book path).

Two paths are pinned here:
  * post-close  (current_qty == 0.0)  -> NO modify, NO misleading warning.
  * genuine TP1 (0 < current_qty < entry_qty, positionId present)
                                       -> SL moves to breakeven (as live trade #2
                                          a3622d4c did at 05:48 UTC).

Root cause + evidence:
reports/2026-07-02_futures_sltrail_positionid_absent_diagnosis.md
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from trading_corp.agents.divisions.bitunix_position_reconciler import (
    POSITION_SL_UPDATE_KIND,
    move_bracket_sls,
)
from trading_corp.persistence import db
from trading_corp.persistence.models import Position

RECON_LOGGER = "trading_corp.agents.divisions.bitunix_position_reconciler"


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    p = tmp_path / "bracket_sl.db"
    url = f"sqlite:///{p}"
    db.init_db(url)
    return url


def _insert_bracket_short(
    db_url: str, *, order_id: str = "brk-1", entry: float = 100.0,
    current_sl: float = 105.0, tp1: float = 99.0, entry_qty: float = 1.0,
) -> None:
    """A LIVE bracket-managed OPEN short row (result NULL) as move_bracket_sls
    selects: extra_json carries execution_mode=live + bracket_entry_qty +
    tp1_price (the fields the SL-ratchet reads)."""
    from trading_corp.persistence.db import insert_paper_trade_record
    extra = {
        "execution_mode": "live",
        "bracket_entry_qty": entry_qty,
        "entry_reference_price": entry,
        "current_sl": current_sl,
        "tp1_price": tp1,
    }
    insert_paper_trade_record({
        "order_id": order_id,
        "ts": "2026-07-02T05:00:00+00:00",
        "strategy": "bitunix_futures",
        "division": "bitunix_futures",
        "symbol": "BTC/USDT.P",
        "side": "sell",
        "qty": entry_qty,
        "tier": "PREMIUM",
        "source_signal": "test",
        "entry_reference_price": entry,
        "stop_price": current_sl,
        "tp_price": tp1,
        "tp_r_multiple": 2.0,
        "expected_loss": -5.0,
        "expected_gain": 10.0,
        "rr_ratio": 2.0,
        "max_hold_seconds": 7200,
        "result": None,
        "result_ts": None,
        "result_price": None,
        "actual_pnl_dollars": None,
        "actual_r_multiple": None,
        "bars_to_resolution": None,
        "extra_json": json.dumps(extra),
    }, db_url=db_url)


class _FakeBroker:
    """Minimal broker: async get_pending_positions + records modify_position_sl
    calls (never touches a venue)."""

    def __init__(self, positions: list[Position]) -> None:
        self._positions = positions
        self.modify_calls: list[dict] = []

    async def get_pending_positions(self) -> list[Position]:
        return list(self._positions)

    async def modify_position_sl(
        self, symbol: str, new_sl: float, *, position_id: str | None = None,
        sl_stop_type: str = "MARK_PRICE", sl_order_type: str = "MARKET",
    ) -> bool:
        self.modify_calls.append(
            {"symbol": symbol, "new_sl": new_sl, "position_id": position_id}
        )
        return True


def _read_sl_update_rows(db_url: str) -> list[dict]:
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event WHERE kind = ? ORDER BY ts",
            (POSITION_SL_UPDATE_KIND,),
        ).fetchall()
    return [dict(r) for r in rows]


def _read_extra(db_url: str, order_id: str) -> dict:
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT extra_json FROM paper_trade_record WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    return json.loads(row["extra_json"])


@pytest.mark.asyncio
async def test_full_close_is_not_treated_as_tp_fill(db_url: str, caplog) -> None:
    """current_qty == 0.0 (venue reports the position closed) must NOT drive an
    SL-modify or a misleading 'positionId absent' warning."""
    _insert_bracket_short(db_url)
    broker = _FakeBroker(positions=[])  # position fully closed -> absent from venue

    with caplog.at_level(logging.INFO):
        await move_bracket_sls(broker, db_url, division="bitunix_futures")

    # 1. No SL-modify attempted on a position that no longer exists.
    assert broker.modify_calls == []
    # 2. No warning that masquerades as a protection failure; a clear,
    #    reduced-severity post-close breadcrumb instead.
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("positionId absent" in m for m in msgs)
    assert any("post-close no-op" in m for m in msgs)
    assert all(
        r.levelno < logging.WARNING
        for r in caplog.records if "bracket SL-move" in r.getMessage()
    )
    # 3. No spurious position_sl_update audit row for the closed position.
    assert _read_sl_update_rows(db_url) == []


@pytest.mark.asyncio
async def test_partial_tp_fill_moves_sl_to_breakeven(db_url: str, caplog) -> None:
    """A genuine partial TP1 fill (position still open, positionId present) still
    ratchets the SL to breakeven — the live trade #2 (a3622d4c) behavior."""
    _insert_bracket_short(db_url, entry=100.0, current_sl=105.0)
    # Short position 50% closed (qty -0.5 of entry 1.0), venue positionId present.
    pos = Position(
        account="bitunix_futures", symbol="BTC/USDT.P", qty=-0.5,
        avg_price=100.0, opened_ts="2026-07-02T05:00:00+00:00",
        extra={"positionId": "pos-open-1"},
    )
    broker = _FakeBroker(positions=[pos])

    with caplog.at_level(logging.INFO):
        await move_bracket_sls(broker, db_url, division="bitunix_futures")

    # 1. SL moved to breakeven (entry) with the venue positionId threaded.
    assert len(broker.modify_calls) == 1
    call = broker.modify_calls[0]
    assert call["position_id"] == "pos-open-1"
    assert call["new_sl"] == 100.0
    # 2. The genuine path is NOT logged as a post-close no-op.
    assert not any("post-close no-op" in r.getMessage() for r in caplog.records)
    # 3. New SL persisted + a moved=True audit row.
    rows = _read_sl_update_rows(db_url)
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert payload["moved"] is True
    assert payload["new_sl"] == 100.0
    assert _read_extra(db_url, "brk-1")["current_sl"] == 100.0
