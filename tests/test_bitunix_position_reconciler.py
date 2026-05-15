"""Tests for the trade-plan PR 5 position reconciler.

Covers:
  - `decide_sl_action` — pure decision function across the lifecycle
    (tp1 → BE, tp2 → tp1 floor, tp2+trail → Chandelier, ratchet/idempotency)
  - `BitunixBroker.list_open_positions` — paper-mode DB query, v2 filter
  - `BitunixBroker.modify_position_tp_sl_order` — NotImplementedError stub
  - `reconciler_tick` — end-to-end audit emission

The decision function is exercised by injecting `filled_legs` directly;
in paper mode the broker always returns `filled_legs=[]` (legacy
monolithic resolver), so the lifecycle only fires once Phase 4 wires
real broker fill state.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_corp.agents.divisions.bitunix_position_reconciler import (
    POSITION_SL_UPDATE_KIND,
    RECONCILER_ACTOR,
    ReconcilerConfig,
    _atr_from_bars,
    _extreme_since_tp2,
    decide_sl_action,
    reconciler_tick,
)
from trading_corp.brokers.bitunix import BitunixBroker
from trading_corp.persistence import db
from trading_corp.persistence.models import OpenPosition


# ─── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    p = tmp_path / "reconciler.db"
    url = f"sqlite:///{p}"
    db.init_db(url)
    return url


def _v2_tp_plan(tp1: float, tp2: float, tp3: float) -> list[dict]:
    return [
        {"leg": "tp1", "fraction": 0.25, "target_r": 0.5, "price": tp1, "stop_action": "move_to_breakeven"},
        {"leg": "tp2", "fraction": 0.50, "target_r": 1.0, "price": tp2, "stop_action": "move_to_tp1"},
        {"leg": "tp3", "fraction": 0.25, "target_r": 2.5, "price": tp3, "stop_action": "trail_atr"},
    ]


def _long_position(
    *, current_sl: float = 95.0, entry: float = 100.0,
    tp1: float = 101.0, tp2: float = 102.0, tp3: float = 105.0,
    filled_legs: list[str] | None = None,
) -> OpenPosition:
    return OpenPosition(
        order_id="ord-long-1",
        symbol="BTC/USDT.P",
        side="buy",
        qty=0.1,
        entry_price=entry,
        current_sl=current_sl,
        tp_plan=_v2_tp_plan(tp1, tp2, tp3),
        filled_legs=filled_legs or [],
        opened_ts="2026-05-15T00:00:00+00:00",
    )


def _short_position(
    *, current_sl: float = 105.0, entry: float = 100.0,
    tp1: float = 99.0, tp2: float = 98.0, tp3: float = 95.0,
    filled_legs: list[str] | None = None,
) -> OpenPosition:
    return OpenPosition(
        order_id="ord-short-1",
        symbol="BTC/USDT.P",
        side="sell",
        qty=0.1,
        entry_price=entry,
        current_sl=current_sl,
        tp_plan=_v2_tp_plan(tp1, tp2, tp3),
        filled_legs=filled_legs or [],
        opened_ts="2026-05-15T00:00:00+00:00",
    )


@pytest.fixture
def cfg() -> ReconcilerConfig:
    return ReconcilerConfig(trail_atr_mult=1.5, atr_period=14, bar_history_limit=200)


# ─── decide_sl_action — base cases ──────────────────────────────────────


def test_no_legs_filled_returns_none(cfg: ReconcilerConfig) -> None:
    pos = _long_position(filled_legs=[])
    assert decide_sl_action(pos, atr=0.5, extreme_since_tp2=None, config=cfg) is None


def test_long_tp1_filled_moves_sl_to_entry(cfg: ReconcilerConfig) -> None:
    pos = _long_position(current_sl=95.0, entry=100.0, filled_legs=["tp1"])
    d = decide_sl_action(pos, atr=0.5, extreme_since_tp2=None, config=cfg)
    assert d is not None
    assert d.new_sl == 100.0
    assert d.lifecycle_state == "post_tp1"


def test_short_tp1_filled_moves_sl_to_entry(cfg: ReconcilerConfig) -> None:
    pos = _short_position(current_sl=105.0, entry=100.0, filled_legs=["tp1"])
    d = decide_sl_action(pos, atr=0.5, extreme_since_tp2=None, config=cfg)
    assert d is not None
    assert d.new_sl == 100.0
    assert d.lifecycle_state == "post_tp1"


def test_long_tp1_already_at_entry_idempotent(cfg: ReconcilerConfig) -> None:
    pos = _long_position(current_sl=100.0, entry=100.0, filled_legs=["tp1"])
    assert decide_sl_action(pos, atr=0.5, extreme_since_tp2=None, config=cfg) is None


# ─── decide_sl_action — post-TP2 floor vs trail ─────────────────────────


def test_long_tp2_no_extreme_uses_floor(cfg: ReconcilerConfig) -> None:
    pos = _long_position(
        current_sl=100.0, tp1=101.0, tp2=102.0, filled_legs=["tp1", "tp2"],
    )
    d = decide_sl_action(pos, atr=0.5, extreme_since_tp2=None, config=cfg)
    assert d is not None
    assert d.new_sl == 101.0  # tp1 price
    assert d.lifecycle_state == "post_tp2_floor"


def test_long_tp2_trail_beats_floor(cfg: ReconcilerConfig) -> None:
    pos = _long_position(
        current_sl=100.0, tp1=101.0, tp2=102.0, filled_legs=["tp1", "tp2"],
    )
    # max_high=105, atr=0.5, trail = 105 - 1.5*0.5 = 104.25 > floor(101)
    d = decide_sl_action(pos, atr=0.5, extreme_since_tp2=105.0, config=cfg)
    assert d is not None
    assert d.new_sl == pytest.approx(104.25)
    assert d.lifecycle_state == "post_tp2_trail"


def test_long_tp2_floor_beats_trail(cfg: ReconcilerConfig) -> None:
    pos = _long_position(
        current_sl=100.0, tp1=101.0, tp2=102.0, filled_legs=["tp1", "tp2"],
    )
    # max_high=102, atr=1.0, trail = 102 - 1.5 = 100.5 < floor(101)
    d = decide_sl_action(pos, atr=1.0, extreme_since_tp2=102.0, config=cfg)
    assert d is not None
    assert d.new_sl == 101.0
    assert d.lifecycle_state == "post_tp2_floor"


def test_short_tp2_trail_uses_min_low(cfg: ReconcilerConfig) -> None:
    pos = _short_position(
        current_sl=100.0, tp1=99.0, tp2=98.0, filled_legs=["tp1", "tp2"],
    )
    # min_low=95, atr=0.5, trail = 95 + 1.5*0.5 = 95.75 < floor(99)
    d = decide_sl_action(pos, atr=0.5, extreme_since_tp2=95.0, config=cfg)
    assert d is not None
    assert d.new_sl == pytest.approx(95.75)
    assert d.lifecycle_state == "post_tp2_trail"


# ─── decide_sl_action — ratchet enforcement ─────────────────────────────


def test_long_ratchet_blocks_downward_move(cfg: ReconcilerConfig) -> None:
    # Already past TP2 with trail at 110; bar pullback would now suggest
    # SL=104 — must NOT loosen.
    pos = _long_position(
        current_sl=110.0, tp1=101.0, tp2=102.0, filled_legs=["tp1", "tp2"],
    )
    d = decide_sl_action(pos, atr=0.5, extreme_since_tp2=105.0, config=cfg)
    assert d is None


def test_short_ratchet_blocks_upward_move(cfg: ReconcilerConfig) -> None:
    pos = _short_position(
        current_sl=90.0, tp1=99.0, tp2=98.0, filled_legs=["tp1", "tp2"],
    )
    # Trail would suggest 95.75 > current 90 — must NOT loosen for short
    d = decide_sl_action(pos, atr=0.5, extreme_since_tp2=95.0, config=cfg)
    assert d is None


# ─── _atr_from_bars ─────────────────────────────────────────────────────


def test_atr_insufficient_bars_returns_none() -> None:
    bars = [{"high": 100, "low": 99, "close": 99.5} for _ in range(5)]
    assert _atr_from_bars(bars, period=14) is None


def test_atr_known_value_with_close_gap() -> None:
    # 20 bars; each bar's high-low=1 but it gaps up 1 from prev close
    # (close goes 99.5, 100.5, 101.5, ...). TR = max(1, 1.5, 0.5) = 1.5
    # for every bar after the first → Wilder ATR converges to 1.5.
    bars = [{"high": 100.0 + i, "low": 99.0 + i, "close": 99.5 + i} for i in range(20)]
    atr = _atr_from_bars(bars, period=14)
    assert atr is not None
    assert atr == pytest.approx(1.5, rel=1e-6)


# ─── _extreme_since_tp2 ─────────────────────────────────────────────────


def test_extreme_since_tp2_none_when_tp2_not_filled() -> None:
    pos = _long_position(filled_legs=["tp1"])
    assert _extreme_since_tp2(pos, bars=[{"ts_ms": 0, "high": 100, "low": 99}]) is None


def test_extreme_since_tp2_none_when_no_timestamp() -> None:
    # filled_legs has tp2 but no tp2_filled_ts → fall back to None
    pos = _long_position(filled_legs=["tp1", "tp2"])
    assert _extreme_since_tp2(pos, bars=[{"ts_ms": 0, "high": 100, "low": 99}]) is None


def test_extreme_since_tp2_max_high_for_long() -> None:
    pos = _long_position(filled_legs=["tp1", "tp2"])
    pos.tp2_filled_ts = "2026-05-15T00:05:00+00:00"  # type: ignore[attr-defined]
    cutoff_ms = int(
        datetime.fromisoformat("2026-05-15T00:05:00+00:00").timestamp() * 1000
    )
    bars = [
        {"ts_ms": cutoff_ms - 60_000, "high": 200.0, "low": 99.0},  # before cutoff
        {"ts_ms": cutoff_ms, "high": 103.0, "low": 99.0},
        {"ts_ms": cutoff_ms + 60_000, "high": 107.0, "low": 99.0},
        {"ts_ms": cutoff_ms + 120_000, "high": 105.0, "low": 99.0},
    ]
    assert _extreme_since_tp2(pos, bars=bars) == 107.0


def test_extreme_since_tp2_min_low_for_short() -> None:
    pos = _short_position(filled_legs=["tp1", "tp2"])
    pos.tp2_filled_ts = "2026-05-15T00:05:00+00:00"  # type: ignore[attr-defined]
    cutoff_ms = int(
        datetime.fromisoformat("2026-05-15T00:05:00+00:00").timestamp() * 1000
    )
    bars = [
        {"ts_ms": cutoff_ms - 60_000, "high": 100.0, "low": 50.0},  # before cutoff
        {"ts_ms": cutoff_ms, "high": 100.0, "low": 95.0},
        {"ts_ms": cutoff_ms + 60_000, "high": 100.0, "low": 92.0},
    ]
    assert _extreme_since_tp2(pos, bars=bars) == 92.0


# ─── BitunixBroker — list_open_positions ────────────────────────────────


def _insert_paper_trade(
    db_url: str, *, order_id: str, division: str = "bitunix_futures",
    result: str | None = None, tp_plan_version: str = "v2",
    side: str = "buy", qty: float = 0.1, entry: float = 100.0,
    stop_price: float = 95.0,
) -> None:
    from trading_corp.persistence.db import insert_paper_trade_record
    extra = {
        "tp_plan": _v2_tp_plan(tp1=101.0, tp2=102.0, tp3=105.0),
        "tp_plan_version": tp_plan_version,
    }
    insert_paper_trade_record({
        "order_id": order_id,
        "ts": "2026-05-15T00:00:00+00:00",
        "strategy": "bitunix_futures",
        "division": division,
        "symbol": "BTC/USDT.P",
        "side": side,
        "qty": qty,
        "tier": "PREMIUM",
        "source_signal": "test",
        "entry_reference_price": entry,
        "stop_price": stop_price,
        "tp_price": 105.0,
        "tp_r_multiple": 2.5,
        "expected_loss": -5.0,
        "expected_gain": 5.0,
        "rr_ratio": 2.5,
        "max_hold_seconds": 7200,
        "result": result,
        "result_ts": None,
        "result_price": None,
        "actual_pnl_dollars": None,
        "actual_r_multiple": None,
        "bars_to_resolution": None,
        "extra_json": json.dumps(extra),
    }, db_url=db_url)


def test_list_open_positions_returns_unresolved_v2(db_url: str) -> None:
    _insert_paper_trade(db_url, order_id="o1", result=None)
    broker = BitunixBroker()
    out = broker.list_open_positions(db_url)
    assert len(out) == 1
    p = out[0]
    assert p.order_id == "o1"
    assert p.entry_price == 100.0
    assert p.current_sl == 95.0
    assert p.side == "buy"
    assert len(p.tp_plan) == 3
    assert p.filled_legs == []  # paper mode invariant


def test_list_open_positions_filters_resolved(db_url: str) -> None:
    _insert_paper_trade(db_url, order_id="open", result=None)
    _insert_paper_trade(db_url, order_id="closed", result="win")
    out = BitunixBroker().list_open_positions(db_url)
    assert [p.order_id for p in out] == ["open"]


def test_list_open_positions_filters_legacy_tp_plan(db_url: str) -> None:
    _insert_paper_trade(db_url, order_id="v2_trade", tp_plan_version="v2")
    _insert_paper_trade(db_url, order_id="legacy", tp_plan_version="v1")
    out = BitunixBroker().list_open_positions(db_url)
    assert [p.order_id for p in out] == ["v2_trade"]


def test_list_open_positions_filters_other_divisions(db_url: str) -> None:
    _insert_paper_trade(db_url, order_id="bx", division="bitunix_futures")
    _insert_paper_trade(db_url, order_id="other", division="otter_futures")
    out = BitunixBroker().list_open_positions(db_url)
    assert [p.order_id for p in out] == ["bx"]


# ─── BitunixBroker — modify_position_tp_sl_order stub ───────────────────


@pytest.mark.asyncio
async def test_modify_position_tp_sl_order_raises_not_implemented() -> None:
    broker = BitunixBroker()
    with pytest.raises(NotImplementedError, match="Phase 1 is"):
        await broker.modify_position_tp_sl_order("order-id", new_sl=100.0)


# ─── reconciler_tick — end-to-end audit emission ────────────────────────


def _read_audit_rows(db_url: str, kind: str) -> list[dict]:
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT ts, actor, kind, payload_json FROM audit_event WHERE kind = ? ORDER BY ts",
            (kind,),
        ).fetchall()
    return [dict(r) for r in rows]


@pytest.mark.asyncio
async def test_reconciler_tick_no_positions_no_audit(
    db_url: str, cfg: ReconcilerConfig,
) -> None:
    broker = BitunixBroker()
    written = await reconciler_tick(broker, db_url, cfg)
    assert written == 0
    assert _read_audit_rows(db_url, POSITION_SL_UPDATE_KIND) == []


@pytest.mark.asyncio
async def test_reconciler_tick_paper_mode_dormant_no_audit(
    db_url: str, cfg: ReconcilerConfig,
) -> None:
    # Paper trade exists but filled_legs is [] (legacy resolver),
    # so decision is None → no audit row.
    _insert_paper_trade(db_url, order_id="dormant", result=None)
    broker = BitunixBroker()
    written = await reconciler_tick(broker, db_url, cfg)
    assert written == 0
    assert _read_audit_rows(db_url, POSITION_SL_UPDATE_KIND) == []


class _StubBroker:
    """Broker double that returns synthetic OpenPositions with non-empty
    filled_legs — simulates Phase 4 broker truth so we can exercise the
    end-to-end audit emission path that paper mode can't reach today.
    """
    def __init__(self, positions: list[OpenPosition]) -> None:
        self._positions = positions

    def list_open_positions(self, db_url: str) -> list[OpenPosition]:
        return list(self._positions)


@pytest.mark.asyncio
async def test_reconciler_tick_emits_audit_after_tp1_fill(
    db_url: str, cfg: ReconcilerConfig,
) -> None:
    pos = _long_position(current_sl=95.0, entry=100.0, filled_legs=["tp1"])
    broker = _StubBroker([pos])
    written = await reconciler_tick(broker, db_url, cfg)
    assert written == 1
    rows = _read_audit_rows(db_url, POSITION_SL_UPDATE_KIND)
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert rows[0]["actor"] == RECONCILER_ACTOR
    assert payload["new_sl"] == 100.0
    assert payload["lifecycle_state"] == "post_tp1"
    assert payload["would_call_broker"] is False
    assert payload["filled_legs"] == ["tp1"]


@pytest.mark.asyncio
async def test_reconciler_tick_idempotent_after_ratcheted(
    db_url: str, cfg: ReconcilerConfig,
) -> None:
    # First tick moves SL to entry; on second tick the broker truth
    # would reflect that move (current_sl=entry) so no further audit.
    pos1 = _long_position(current_sl=95.0, entry=100.0, filled_legs=["tp1"])
    pos2 = _long_position(current_sl=100.0, entry=100.0, filled_legs=["tp1"])
    broker_t1 = _StubBroker([pos1])
    broker_t2 = _StubBroker([pos2])
    await reconciler_tick(broker_t1, db_url, cfg)
    await reconciler_tick(broker_t2, db_url, cfg)
    assert len(_read_audit_rows(db_url, POSITION_SL_UPDATE_KIND)) == 1


# ─── ReconcilerConfig.from_dict ─────────────────────────────────────────


def test_reconciler_config_from_dict_defaults() -> None:
    cfg = ReconcilerConfig.from_dict({})
    assert cfg.trail_atr_mult == 1.5
    assert cfg.period_seconds == 60.0
    assert cfg.timeframe == "3m"


def test_reconciler_config_from_dict_overrides() -> None:
    cfg = ReconcilerConfig.from_dict({
        "trail_atr_mult": 2.0,
        "reconciler_period_seconds": 30,
        "reconciler_timeframe": "5m",
    })
    assert cfg.trail_atr_mult == 2.0
    assert cfg.period_seconds == 30.0
    assert cfg.timeframe == "5m"
