"""Prove the existing reconcile_position_state is symbol+division-agnostic
and correctly handles bitunix_sfp rows alongside bitunix_futures rows.

Invariants verified:
  - Only rows with extra_json.execution_mode=="live" AND result IS NULL are tracked.
  - bitunix_futures paper rows (execution_mode="paper" in extra_json) are ignored.
  - Symbol matching is wire-format-aware: "BTC/USDT.P" bot → "BTCUSDT" broker match.
  - Non-BTC symbols reconcile cleanly (symbol-agnostic).
"""
import asyncio
import json
import types
import uuid

import pytest

from trading_corp.agents.divisions.bitunix_position_reconciler import (
    reconcile_position_state,
)
from trading_corp.persistence import db


def _db_url(tmp_path, name="t.db"):
    return f"sqlite:///{tmp_path.as_posix()}/{name}"


def _init(tmp_path, name="t.db"):
    url = _db_url(tmp_path, name)
    db.init_db(url)
    return url


def _row(
    *,
    symbol,
    side,
    qty,
    execution_mode,
    division,
    result=None,
):
    """Build a minimal paper_trade_record row dict for insert_paper_trade_record."""
    return {
        "order_id": str(uuid.uuid4()),
        "ts": "2026-06-25T00:00:00+00:00",
        "strategy": division,
        "division": division,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "tier": None,
        "source_signal": None,
        "entry_reference_price": None,
        "stop_price": None,
        "tp_price": None,
        "tp_r_multiple": None,
        "expected_loss": None,
        "expected_gain": None,
        "rr_ratio": None,
        "max_hold_seconds": None,
        "result": result,
        "result_ts": None,
        "result_price": None,
        "actual_pnl_dollars": None,
        "actual_r_multiple": None,
        "bars_to_resolution": None,
        "extra_json": json.dumps({"execution_mode": execution_mode}),
        "execution_mode": execution_mode,
    }


class FakeBroker:
    """Minimal broker stub: returns a fixed list of pending positions."""

    _stub = True  # tells reconciler P2 auto-book to skip (no signed-fetch)

    def __init__(self, positions):
        self._positions = positions
        self._halt_new_orders = False

    async def get_pending_positions(self):
        return self._positions


def _pos(symbol, qty):
    """Build a fake broker position (positive qty = long/buy)."""
    return types.SimpleNamespace(symbol=symbol, qty=qty)


# ── Main coexist test ───────────────────────────────────────────────────────

def test_sfp_live_row_matched_futures_paper_ignored(tmp_path):
    """SFP live row matched; futures paper row ignored; clean reconcile."""
    db_url = _init(tmp_path)

    # SFP live row (should be tracked)
    db.insert_paper_trade_record(
        _row(
            symbol="BTC/USDT.P", side="buy", qty=0.01,
            execution_mode="live", division="bitunix_sfp",
        ),
        db_url=db_url,
    )
    # Futures paper row (must be ignored — execution_mode=paper in extra_json)
    db.insert_paper_trade_record(
        _row(
            symbol="BTC/USDT.P", side="buy", qty=0.02,
            execution_mode="paper", division="bitunix_futures",
        ),
        db_url=db_url,
    )

    broker = FakeBroker([_pos("BTCUSDT", 0.01)])
    res = asyncio.run(reconcile_position_state(broker, db_url))

    assert res.has_divergence is False
    assert len(res.matches) == 1
    assert res.missing_on_broker == []
    assert res.orphan_on_broker == []


# ── Symbol-agnostic: ETH reconciles cleanly ────────────────────────────────

def test_sfp_eth_live_row_matched(tmp_path):
    """ETH SFP live row matches ETHUSDT broker position — non-BTC symbol works."""
    db_url = _init(tmp_path, "eth.db")

    db.insert_paper_trade_record(
        _row(
            symbol="ETH/USDT.P", side="buy", qty=0.5,
            execution_mode="live", division="bitunix_sfp",
        ),
        db_url=db_url,
    )

    broker = FakeBroker([_pos("ETHUSDT", 0.5)])
    res = asyncio.run(reconcile_position_state(broker, db_url))

    assert res.has_divergence is False
    assert len(res.matches) == 1
    assert res.missing_on_broker == []
    assert res.orphan_on_broker == []


# ── Closed row (result set) is not tracked ─────────────────────────────────

def test_closed_sfp_row_not_tracked(tmp_path):
    """A closed (result IS NOT NULL) SFP live row must not be tracked."""
    db_url = _init(tmp_path, "closed.db")

    db.insert_paper_trade_record(
        _row(
            symbol="BTC/USDT.P", side="buy", qty=0.01,
            execution_mode="live", division="bitunix_sfp",
            result="win",
        ),
        db_url=db_url,
    )

    broker = FakeBroker([])  # broker has nothing
    res = asyncio.run(reconcile_position_state(broker, db_url))

    # closed row excluded → nothing to reconcile, no divergence
    assert res.has_divergence is False
    assert len(res.matches) == 0
    assert res.missing_on_broker == []
    assert res.orphan_on_broker == []


# ── Divergence: SFP row missing on broker ──────────────────────────────────

def test_sfp_live_row_missing_on_broker_is_divergence(tmp_path):
    """Bot has SFP live row, broker has nothing → divergence on first tick."""
    db_url = _init(tmp_path, "miss.db")

    db.insert_paper_trade_record(
        _row(
            symbol="BTC/USDT.P", side="buy", qty=0.01,
            execution_mode="live", division="bitunix_sfp",
        ),
        db_url=db_url,
    )

    broker = FakeBroker([])
    # First tick: missing recorded but NOT auto-booked yet (needs 2 consecutive ticks)
    # Use halt_on_divergence=False to avoid the halt latch in unit test context.
    res = asyncio.run(
        reconcile_position_state(broker, db_url, halt_on_divergence=False)
    )

    # The row should appear as missing (not yet auto-booked — that requires prev audit)
    assert res.has_divergence is True
    assert len(res.missing_on_broker) == 1
    assert res.orphan_on_broker == []
