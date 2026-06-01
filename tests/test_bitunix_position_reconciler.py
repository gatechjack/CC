"""Tests for the BitUnix position reconciler module.

Two orthogonal reconciliation concerns share this module + test file:

(A) SL lifecycle reconciler (`reconciler_tick`, `decide_sl_action`) —
    decides per-leg stop-loss moves on already-open positions
    (tp1 → BE, tp2 → tp1 floor, tp2+trail → Chandelier).

(B) Position-state reconciler (Phase 3 Session A,
    `reconcile_position_state`) — compares bot-tracked live rows
    against broker truth (`get_pending_positions`) to detect symmetry
    violations (bot tracks but broker doesn't, or vice versa).

The two are independent functions that run on different cadences:
the SL one ticks every 60s on open positions; the position-state one
runs on startup / after reconnect.

The (A) decision function is exercised by injecting `filled_legs`
directly; in paper mode the broker returns `filled_legs=[]`, so the
lifecycle only fires once Phase 4 wires real broker fill state.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions.bitunix_position_reconciler import (
    POSITION_SL_UPDATE_KIND,
    POSITION_STATE_DIVERGENCE_KIND,
    POSITION_STATE_RECONCILED_KIND,
    RECONCILER_ACTOR,
    PositionStateMissingOnBroker,
    PositionStateOrphanOnBroker,
    PositionStateReconciliation,
    ReconcilerConfig,
    _atr_from_bars,
    _extreme_since_tp2,
    decide_sl_action,
    reconcile_position_state,
    reconciler_tick,
)
from trading_corp.brokers.bitunix import BitunixBroker
from trading_corp.persistence import db
from trading_corp.persistence.models import OpenPosition, Position


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


# ════════════════════════════════════════════════════════════════════════
# (B) Position-state reconciler (Phase 3 Session A — separate concern)
# ════════════════════════════════════════════════════════════════════════
#
# Validates `reconcile_position_state`: compares bot-tracked live rows
# (paper_trade_record WHERE result IS NULL AND extra.execution_mode='live')
# against broker.get_pending_positions(). Surfaces missing_on_broker
# and orphan_on_broker as halt-and-alert; sets broker._halt_new_orders
# on any divergence (Phase 1a §9c — exits NOT halted, only entries).


def _insert_live_row(
    db_url: str,
    order_id: str,
    *,
    symbol: str = "BTCUSDT",
    side: str = "buy",
    qty: float = 0.001,
    broker_order_id: str = "bx-entry-1",
) -> None:
    """Seed a Path C live row in `result IS NULL` state."""
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record ("
            " order_id, ts, strategy, division, symbol, side, qty, "
            " entry_reference_price, stop_price, tp_price, "
            " max_hold_seconds, result, extra_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                order_id, "2026-06-01T10:00:00+00:00",
                "bitunix_futures", "bitunix_futures",
                symbol, side, qty,
                80_000.0, 79_500.0, 81_000.0,
                7200, None,
                json.dumps({
                    "execution_mode": "live",
                    "broker_order_id": broker_order_id,
                }),
            ),
        )


def _insert_paper_row(db_url: str, order_id: str) -> None:
    """Seed a paper-mode row — must NOT be considered by the
    position-state reconciler."""
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record ("
            " order_id, ts, strategy, division, symbol, side, qty, "
            " entry_reference_price, stop_price, tp_price, "
            " max_hold_seconds, result, extra_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                order_id, "2026-06-01T10:00:00+00:00",
                "bitunix_futures", "bitunix_futures",
                "BTCUSDT", "buy", 0.001,
                80_000.0, 79_500.0, 81_000.0,
                7200, None,
                json.dumps({"tier": "PREMIUM"}),  # no execution_mode
            ),
        )


def _broker_pos(
    symbol: str,
    *,
    qty: float,
) -> Position:
    """Build a Position dataclass matching the shape `get_pending_positions`
    returns — signed qty (negative for SHORT)."""
    return Position(
        account="bitunix-futures",
        symbol=symbol,
        qty=qty,
        avg_price=80_000.0,
        opened_ts="2026-06-01T10:00:00+00:00",
        extra={"side": "LONG" if qty > 0 else "SHORT"},
    )


def _make_broker_stub(positions: list[Position]) -> MagicMock:
    """Broker stub with `get_pending_positions` returning the given list
    + `_halt_new_orders`/`_halt_reason` attrs so the reconciler can
    toggle the latch."""
    broker = MagicMock()
    broker.get_pending_positions = AsyncMock(return_value=positions)
    broker._halt_new_orders = False
    broker._halt_reason = None
    return broker


# ─── clean match (no discrepancies) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_clean_match_no_divergence_no_halt(db_url):
    _insert_live_row(db_url, "ord-1", side="buy", qty=0.001)
    broker = _make_broker_stub([_broker_pos("BTCUSDT", qty=0.001)])

    result = await reconcile_position_state(broker, db_url)

    assert isinstance(result, PositionStateReconciliation)
    assert len(result.matches) == 1
    assert result.matches[0].order_id == "ord-1"
    assert result.matches[0].bot_qty == 0.001
    assert result.matches[0].broker_qty == 0.001
    assert result.missing_on_broker == []
    assert result.orphan_on_broker == []
    assert result.has_divergence is False
    # Broker latch stays untouched on clean match
    assert broker._halt_new_orders is False
    # `position_state_reconciled` audit written
    with db.connect(db_url) as conn:
        kinds = [r["kind"] for r in conn.execute(
            "SELECT kind FROM audit_event "
            "WHERE actor=? AND kind IN (?, ?)",
            (RECONCILER_ACTOR,
             POSITION_STATE_RECONCILED_KIND,
             POSITION_STATE_DIVERGENCE_KIND),
        ).fetchall()]
    assert kinds == [POSITION_STATE_RECONCILED_KIND]


# ─── bot tracks, broker doesn't ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_bot_tracks_broker_missing_surfaces_as_divergence(db_url):
    """Causes: broker closed via TP/SL/liquidation while bot was off;
    operator manual close on the BitUnix UI; broker_order_id drift."""
    _insert_live_row(db_url, "ord-m1", side="buy", qty=0.001)
    broker = _make_broker_stub([])  # broker has no positions

    result = await reconcile_position_state(broker, db_url)

    assert result.matches == []
    assert len(result.missing_on_broker) == 1
    assert result.missing_on_broker[0].order_id == "ord-m1"
    assert result.missing_on_broker[0].side == "buy"
    assert result.missing_on_broker[0].bot_qty == 0.001
    assert result.has_divergence is True
    # Broker halted from new entries (exits still flow per Phase 1a §9c)
    assert broker._halt_new_orders is True
    assert broker._halt_reason == "position_state_reconciler_divergence"
    # Divergence audit written with the diff details
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind=?",
            (POSITION_STATE_DIVERGENCE_KIND,),
        ).fetchall()
    assert len(rows) == 1
    p = json.loads(rows[0]["payload_json"])
    assert p["missing_on_broker_count"] == 1
    assert p["orphan_on_broker_count"] == 0
    assert p["missing_on_broker"][0]["order_id"] == "ord-m1"


# ─── broker has, bot doesn't ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_broker_has_bot_missing_surfaces_as_orphan(db_url):
    """Causes: Path C row-write failed silently after a successful
    broker entry; operator placed an order outside the bot; broker
    auto-opened via residual TP/SL."""
    # No tracked rows seeded.
    broker = _make_broker_stub([_broker_pos("BTCUSDT", qty=0.005)])

    result = await reconcile_position_state(broker, db_url)

    assert result.matches == []
    assert result.missing_on_broker == []
    assert len(result.orphan_on_broker) == 1
    assert result.orphan_on_broker[0].symbol == "BTCUSDT"
    assert result.orphan_on_broker[0].broker_qty == 0.005
    assert result.orphan_on_broker[0].broker_side == "buy"
    assert result.has_divergence is True
    assert broker._halt_new_orders is True


# ─── SHORT broker positions render as buy/sell correctly ────────────────


@pytest.mark.asyncio
async def test_short_position_matches_sell_side_row(db_url):
    _insert_live_row(db_url, "ord-short", side="sell", qty=0.001)
    # Broker returns SHORT (signed-qty negative per snapshot convention).
    broker = _make_broker_stub([_broker_pos("BTCUSDT", qty=-0.001)])

    result = await reconcile_position_state(broker, db_url)

    assert len(result.matches) == 1
    assert result.matches[0].side == "sell"
    assert result.has_divergence is False


@pytest.mark.asyncio
async def test_short_orphan_renders_as_sell_side(db_url):
    broker = _make_broker_stub([_broker_pos("BTCUSDT", qty=-0.005)])
    result = await reconcile_position_state(broker, db_url)
    assert len(result.orphan_on_broker) == 1
    assert result.orphan_on_broker[0].broker_side == "sell"
    assert result.orphan_on_broker[0].broker_qty == 0.005


# ─── paper-mode rows ignored ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_paper_mode_rows_ignored(db_url):
    """Rows without `extra.execution_mode == "live"` must NOT be
    considered. The replay loop's bar-walk handles them."""
    _insert_paper_row(db_url, "ord-paper-1")
    broker = _make_broker_stub([])  # no broker positions

    result = await reconcile_position_state(broker, db_url)

    # Paper row is invisible to the position-state reconciler
    assert result.matches == []
    assert result.missing_on_broker == []
    assert result.orphan_on_broker == []
    assert result.has_divergence is False
    assert broker._halt_new_orders is False


# ─── multiple rows + multiple positions ─────────────────────────────────


@pytest.mark.asyncio
async def test_partial_match_one_match_one_orphan(db_url):
    _insert_live_row(db_url, "ord-btc", symbol="BTCUSDT", side="buy", qty=0.001)
    broker = _make_broker_stub([
        _broker_pos("BTCUSDT", qty=0.001),     # match
        _broker_pos("ETHUSDT", qty=0.05),      # orphan
    ])
    result = await reconcile_position_state(broker, db_url)
    assert len(result.matches) == 1
    assert len(result.orphan_on_broker) == 1
    assert result.orphan_on_broker[0].symbol == "ETHUSDT"
    assert result.has_divergence is True


# ─── audit row written on every tick ────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_kind_differs_on_clean_vs_divergence(db_url):
    # Clean tick
    broker_clean = _make_broker_stub([])
    await reconcile_position_state(broker_clean, db_url)
    # Divergent tick
    broker_div = _make_broker_stub([_broker_pos("BTCUSDT", qty=0.001)])
    await reconcile_position_state(broker_div, db_url)

    with db.connect(db_url) as conn:
        kinds = [r["kind"] for r in conn.execute(
            "SELECT kind FROM audit_event "
            "WHERE actor=? ORDER BY id",
            (RECONCILER_ACTOR,),
        ).fetchall()]
    assert kinds == [
        POSITION_STATE_RECONCILED_KIND,
        POSITION_STATE_DIVERGENCE_KIND,
    ]


# ─── halt_on_divergence=False bypass ────────────────────────────────────


@pytest.mark.asyncio
async def test_halt_on_divergence_false_skips_latch(db_url):
    """Caller can opt out of the halt — useful for diagnostic / dashboard
    reads where the operator wants to see the diff without halting the bot."""
    _insert_live_row(db_url, "ord-x", side="buy", qty=0.001)
    broker = _make_broker_stub([])

    result = await reconcile_position_state(
        broker, db_url, halt_on_divergence=False,
    )

    assert result.has_divergence is True
    # Latch UNTOUCHED despite divergence
    assert broker._halt_new_orders is False


# ─── broker.get_pending_positions raises ────────────────────────────────


@pytest.mark.asyncio
async def test_broker_call_failure_treated_as_no_positions(db_url):
    """Transient broker failure → treat as 'no broker positions known';
    any tracked row becomes missing_on_broker → halt + audit. The next
    tick recovers automatically."""
    _insert_live_row(db_url, "ord-y", side="buy", qty=0.001)
    broker = MagicMock()
    broker.get_pending_positions = AsyncMock(
        side_effect=RuntimeError("network down"),
    )
    broker._halt_new_orders = False
    broker._halt_reason = None

    result = await reconcile_position_state(broker, db_url)

    assert len(result.missing_on_broker) == 1
    assert result.has_divergence is True
    assert broker._halt_new_orders is True
