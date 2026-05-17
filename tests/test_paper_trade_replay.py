"""Phase C of the would_have_placed enrichment (BACKLOG.md 2026-05-01).

Pins the walk-forward classifier and the DB-side update path:

- For 'buy' rows: high>=tp wins; low<=stop loses.
- For 'sell' rows: low<=tp wins; high>=stop loses.
- Same-bar both-hit → conservative LOSS (we cannot tell intra-bar order
  from OHLC alone, so we bias toward the worse outcome — see module
  docstring in paper_trade_replay.py).
- No hit by max_hold_seconds → 'expired'.
- Missing tp or stop (legacy pre-Phase-A row) → 'pre_phase_a'.
- mark_pre_phase_a_rows + replay tick are idempotent across re-runs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trading_corp.agents.paper_trade_replay import (
    _aggregate_multi_leg_r,
    _classify,
    _classify_v2_multi_leg,
    _PendingRow,
    mark_pre_phase_a_rows,
    replay_pending_paper_trades_async,
)
from trading_corp.persistence.db import connect, init_db, insert_paper_trade_record
from trading_corp.persistence.models import PaperTradeRecord, ProposedOrder


# ── synthetic bar helpers ─────────────────────────────────────────────


def _bar(ts_ms: int, o: float, h: float, l: float, c: float) -> list[float]:
    return [ts_ms, o, h, l, c, 0.0]


def _row(side: str, *, tp: float | None = 100.0, stop: float | None = 90.0,
         max_hold: int = 86400, expected_loss: float = -10.0,
         expected_gain: float = 30.0, tp_r: float = 3.0,
         division: str = "coinbase_spot", entry: float | None = None,
         extra_json: str | None = None) -> _PendingRow:
    return _PendingRow(
        order_id="o1", ts="2026-05-02T00:00:00+00:00",
        strategy="lord_otter", division=division, symbol="BTC/USD",
        side=side, qty=0.01, stop_price=stop, tp_price=tp,
        tp_r_multiple=tp_r,
        entry_reference_price=entry,
        expected_loss=expected_loss,
        expected_gain=expected_gain, max_hold_seconds=max_hold,
        extra_json=extra_json,
    )


# ── classifier: buy/long ──────────────────────────────────────────────


def test_buy_tp_hit_first():
    bars = [
        _bar(0, 95, 96, 93, 95),       # neither
        _bar(60_000, 95, 102, 94, 99),  # high reaches 102 → TP hit
    ]
    v = _classify(_row("buy"), bars)
    assert v.result == "win"
    assert v.result_price == 100.0          # tp_price
    assert v.bars_to_resolution == 2
    assert v.actual_pnl_dollars == 30.0     # expected_gain
    assert v.actual_r_multiple == 3.0


def test_buy_sl_hit_first():
    bars = [
        _bar(0, 95, 96, 89, 91),  # low 89 → SL hit
    ]
    v = _classify(_row("buy"), bars)
    assert v.result == "loss"
    assert v.result_price == 90.0           # stop_price
    assert v.actual_pnl_dollars == -10.0    # expected_loss
    assert v.actual_r_multiple == -1.0
    assert v.bars_to_resolution == 1


def test_buy_expired_no_hit():
    bars = [
        _bar(0, 95, 96, 93, 95),
        _bar(60_000, 95, 97, 92, 96),
        _bar(120_000, 96, 98, 93, 97),
    ]
    v = _classify(_row("buy"), bars)
    assert v.result == "expired"
    assert v.actual_pnl_dollars == 0.0
    assert v.bars_to_resolution == 3
    assert v.result_price == 97.0  # last close


def test_buy_same_bar_both_hit_resolves_to_loss():
    """Conservative tie-rule: when a single bar's high reaches TP and
    its low reaches SL, we cannot tell intra-bar order — assume the
    worse outcome (loss). Documented in module docstring."""
    bars = [
        _bar(0, 95, 105, 89, 99),  # high 105 ≥ tp 100, low 89 ≤ stop 90
    ]
    v = _classify(_row("buy"), bars)
    assert v.result == "loss"
    assert v.result_price == 90.0
    assert v.actual_pnl_dollars == -10.0


# ── classifier: sell/short ────────────────────────────────────────────


def test_sell_tp_hit_first():
    """For shorts (sell), TP is BELOW entry — so price moving DOWN to tp."""
    # Short setup: tp=80 (below entry 90), stop=95 (above entry 90)
    row = _row("sell", tp=80.0, stop=95.0)
    bars = [
        _bar(0, 90, 92, 79, 81),  # low 79 ≤ tp 80 → TP hit
    ]
    v = _classify(row, bars)
    assert v.result == "win"
    assert v.result_price == 80.0


def test_sell_sl_hit_first():
    row = _row("sell", tp=80.0, stop=95.0)
    bars = [
        _bar(0, 90, 96, 88, 95),  # high 96 ≥ stop 95 → SL hit
    ]
    v = _classify(row, bars)
    assert v.result == "loss"
    assert v.result_price == 95.0


def test_sell_same_bar_both_hit_resolves_to_loss():
    row = _row("sell", tp=80.0, stop=95.0)
    bars = [
        _bar(0, 90, 96, 79, 88),  # high 96 ≥ stop 95 AND low 79 ≤ tp 80
    ]
    v = _classify(row, bars)
    assert v.result == "loss"


# ── classifier: pre-Phase-A handling ──────────────────────────────────


def test_missing_tp_marks_pre_phase_a():
    row = _row("buy", tp=None)
    v = _classify(row, [_bar(0, 95, 110, 80, 100)])
    assert v.result == "pre_phase_a"
    assert v.result_price is None


def test_missing_stop_marks_pre_phase_a():
    row = _row("buy", stop=None)
    v = _classify(row, [_bar(0, 95, 110, 80, 100)])
    assert v.result == "pre_phase_a"


# ── empty-bars edge case ──────────────────────────────────────────────


def test_no_bars_returns_expired_with_no_price():
    """No bars to walk (e.g. fetcher returned empty) → expired with
    NULL price, bars_to_resolution=0. Replay can re-pick it up next
    tick if more bars are available later."""
    v = _classify(_row("buy"), [])
    assert v.result == "expired"
    assert v.result_price is None
    assert v.bars_to_resolution == 0


# ── DB-side: mark_pre_phase_a_rows ────────────────────────────────────


def _insert_full_row(db_url: str, *, order_id: str, side: str = "buy",
                    tp: float | None = 100.0, stop: float | None = 90.0,
                    ts: str = "2026-05-02T00:00:00+00:00",
                    strategy: str = "lord_otter") -> None:
    """Helper: build a paper_trade_record row directly via the DB API
    (bypassing the from_order factory so we can inject NULLs explicitly)."""
    rec = PaperTradeRecord(
        order_id=order_id, ts=ts, strategy=strategy, division="coinbase_spot",
        symbol="BTC/USD", side=side, qty=0.01,
        tier="diamond", source_signal="x",
        entry_reference_price=95.0,
        stop_price=stop, tp_price=tp, tp_r_multiple=3.0,
        expected_loss=-10.0 if stop else None,
        expected_gain=30.0 if tp else None,
        rr_ratio=3.0 if (tp and stop) else None,
        max_hold_seconds=86400,
    )
    insert_paper_trade_record(rec.to_db_row(), db_url=db_url)


def test_mark_pre_phase_a_rows_marks_missing_specs(tmp_db):
    init_db(tmp_db)
    _insert_full_row(tmp_db, order_id="legacy", tp=None, stop=None)
    _insert_full_row(tmp_db, order_id="ok")  # full Phase A spec

    n = mark_pre_phase_a_rows(tmp_db)

    assert n == 1
    with connect(tmp_db) as conn:
        rows = {r["order_id"]: r["result"] for r in
                conn.execute("SELECT order_id, result FROM paper_trade_record").fetchall()}
    assert rows == {"legacy": "pre_phase_a", "ok": None}


def test_mark_pre_phase_a_rows_is_idempotent(tmp_db):
    init_db(tmp_db)
    _insert_full_row(tmp_db, order_id="legacy", tp=None, stop=None)

    n1 = mark_pre_phase_a_rows(tmp_db)
    n2 = mark_pre_phase_a_rows(tmp_db)

    assert n1 == 1
    assert n2 == 0   # already marked, second pass is a no-op


# ── DB-side: full async tick with mock fetcher ────────────────────────


@pytest.mark.asyncio
async def test_replay_tick_resolves_pending_rows(tmp_db):
    """End-to-end: insert a Phase-A row, invoke a mock fetcher that
    returns TP-hit bars, verify the row's result_* columns get UPDATEd."""
    init_db(tmp_db)
    _insert_full_row(tmp_db, order_id="o-tp")
    _insert_full_row(tmp_db, order_id="o-legacy", tp=None, stop=None)

    async def mock_fetcher(symbol, timeframe, since_ms, limit):
        # Return one TP-hit bar at since_ms+0.
        return [_bar(since_ms, 95, 105, 93, 100)]

    counts = await replay_pending_paper_trades_async(
        tmp_db, ohlcv_fetcher=mock_fetcher,
    )

    assert counts["resolved_win"] == 1
    assert counts["resolved_loss"] == 0
    assert counts["marked_pre_phase_a"] == 1   # legacy row caught upfront
    assert counts["errors"] == 0

    with connect(tmp_db) as conn:
        rows = {r["order_id"]: dict(r) for r in
                conn.execute("SELECT * FROM paper_trade_record").fetchall()}
    assert rows["o-tp"]["result"] == "win"
    assert rows["o-tp"]["result_price"] == 100.0
    assert rows["o-tp"]["actual_pnl_dollars"] == 30.0
    assert rows["o-legacy"]["result"] == "pre_phase_a"


@pytest.mark.asyncio
async def test_replay_tick_is_idempotent(tmp_db):
    """Second tick on already-resolved rows is a no-op (filter is
    `result IS NULL`). Counts come back zero on the second pass."""
    init_db(tmp_db)
    _insert_full_row(tmp_db, order_id="o-tp")

    async def mock_fetcher(symbol, timeframe, since_ms, limit):
        return [_bar(since_ms, 95, 105, 93, 100)]

    await replay_pending_paper_trades_async(tmp_db, ohlcv_fetcher=mock_fetcher)
    counts2 = await replay_pending_paper_trades_async(
        tmp_db, ohlcv_fetcher=mock_fetcher,
    )

    assert counts2["scanned"] == 0
    assert counts2["resolved_win"] == 0


@pytest.mark.asyncio
async def test_replay_tick_records_error_count_and_continues(tmp_db):
    """Fetcher exception on one row should NOT crash the tick — count
    it as an error and move on. (Important: a single broken symbol
    shouldn't stop replay of all other rows.)"""
    init_db(tmp_db)
    _insert_full_row(tmp_db, order_id="o-broken")
    _insert_full_row(tmp_db, order_id="o-fine")

    call_count = {"n": 0}

    async def flaky_fetcher(symbol, timeframe, since_ms, limit):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("api blew up on first call")
        return [_bar(since_ms, 95, 105, 93, 100)]

    counts = await replay_pending_paper_trades_async(
        tmp_db, ohlcv_fetcher=flaky_fetcher,
    )

    assert counts["errors"] == 1
    assert counts["resolved_win"] == 1


def test_paper_trade_summary_buckets_by_window_and_excludes_pre_phase_a(tmp_db):
    """Phase C dashboard panel feed: counts wins/losses/expired per
    7d/30d/all window, includes sim P&L, excludes pre_phase_a rows from
    win-rate math but still surfaces their count via n_pre_phase_a."""
    from trading_corp.web.data import paper_trade_summary

    init_db(tmp_db)
    now = datetime.now(timezone.utc)

    def insert(order_id: str, *, result: str | None, pnl: float, days_ago: int,
               tier: str = "diamond"):
        ts = (now - timedelta(days=days_ago)).isoformat(timespec="seconds")
        rec = PaperTradeRecord(
            order_id=order_id, ts=ts, strategy="lord_otter",
            division="coinbase_spot", symbol="BTC/USD", side="buy", qty=0.01,
            tier=tier, entry_reference_price=95.0,
            stop_price=90.0, tp_price=100.0, tp_r_multiple=3.0,
            expected_loss=-10.0, expected_gain=30.0, rr_ratio=3.0,
            max_hold_seconds=86400,
            result=result, actual_pnl_dollars=pnl,
        )
        insert_paper_trade_record(rec.to_db_row(), db_url=tmp_db)

    # 7d window: 2 wins, 1 loss
    insert("a", result="win", pnl=30.0, days_ago=1)
    insert("b", result="win", pnl=30.0, days_ago=3)
    insert("c", result="loss", pnl=-10.0, days_ago=5)
    # 30d window adds: 1 expired, 1 pre_phase_a
    insert("d", result="expired", pnl=0.0, days_ago=20)
    insert("e", result="pre_phase_a", pnl=0.0, days_ago=15)
    # all-time adds: 1 win 60d ago
    insert("f", result="win", pnl=30.0, days_ago=60)

    summary = paper_trade_summary(tmp_db, "coinbase_spot")

    t7 = summary["totals"]["7d"]
    assert t7["wins"] == 2 and t7["losses"] == 1
    assert t7["win_rate_pct"] == pytest.approx(2 / 3 * 100, rel=1e-3)
    assert t7["sim_pnl"] == 50.0
    assert t7["n_pre_phase_a"] == 0

    t30 = summary["totals"]["30d"]
    assert t30["wins"] == 2 and t30["losses"] == 1 and t30["expired"] == 1
    assert t30["n_pre_phase_a"] == 1
    # pre_phase_a NOT counted toward win-rate denominator
    assert t30["win_rate_pct"] == pytest.approx(2 / 3 * 100, rel=1e-3)

    t_all = summary["totals"]["all"]
    assert t_all["wins"] == 3
    assert t_all["sim_pnl"] == 80.0


def test_paper_trade_summary_other_division_returns_zero_n(tmp_db):
    """A division with no paper_trade_record rows returns a well-shaped
    summary with all zero counts — the template uses `totals.all.n` to
    decide whether to render the panel at all."""
    from trading_corp.web.data import paper_trade_summary

    init_db(tmp_db)
    summary = paper_trade_summary(tmp_db, "robinhood_pmcc")
    assert summary["totals"]["all"]["n"] == 0
    assert summary["totals"]["all"]["win_rate_pct"] is None


@pytest.mark.asyncio
async def test_replay_tick_skips_rows_with_zero_max_hold(tmp_db):
    """A row with max_hold_seconds = 0 / NULL has no bounded window —
    skip it and count as error. Sanity guard so we don't trigger an
    unbounded fetch."""
    init_db(tmp_db)
    rec = PaperTradeRecord(
        order_id="bad", ts="2026-05-02T00:00:00+00:00",
        strategy="lord_otter", division="coinbase_spot",
        symbol="BTC/USD", side="buy", qty=0.01,
        tier="diamond", entry_reference_price=95.0,
        stop_price=90.0, tp_price=100.0, tp_r_multiple=3.0,
        expected_loss=-10.0, expected_gain=30.0, rr_ratio=3.0,
        max_hold_seconds=None,
    )
    insert_paper_trade_record(rec.to_db_row(), db_url=tmp_db)

    async def fetcher_should_not_be_called(*args, **kw):
        raise AssertionError("fetcher invoked despite NULL max_hold")

    counts = await replay_pending_paper_trades_async(
        tmp_db, ohlcv_fetcher=fetcher_should_not_be_called,
    )

    assert counts["errors"] == 1
    assert counts["resolved_win"] == 0


# ── multi-leg (trade-plan v2) classifier ───────────────────────────────


def _v2_row(side: str, *, entry: float = 100.0, stop: float = 95.0,
            tp1: float = 102.5, tp2: float = 105.0, tp3: float = 112.5,
            max_hold: int = 86400) -> _PendingRow:
    """Construct a v2 _PendingRow with a 3-leg tp_plan in extra_json.
    Defaults are a long: entry=100, stop=95 (R=5), tp1=+0.5R, tp2=+1R, tp3=+2.5R."""
    tp_plan = [
        {"leg": "tp1", "fraction": 0.25, "target_r": 0.5, "price": tp1,
         "stop_action": "move_to_breakeven"},
        {"leg": "tp2", "fraction": 0.50, "target_r": 1.0, "price": tp2,
         "stop_action": "move_to_tp1"},
        {"leg": "tp3", "fraction": 0.25, "target_r": 2.5, "price": tp3,
         "stop_action": "trail_atr"},
    ]
    return _PendingRow(
        order_id="v2-o1", ts="2026-05-02T00:00:00+00:00",
        strategy="bitunix_futures", division="bitunix_futures",
        symbol="BTCUSDT.P", side=side, qty=0.01,
        stop_price=stop, tp_price=tp3, tp_r_multiple=2.5,
        entry_reference_price=entry,
        expected_loss=-50.0, expected_gain=125.0,
        max_hold_seconds=max_hold,
        extra_json=__import__("json").dumps({
            "tp_plan": tp_plan,
            "tp_plan_version": "v2",
        }),
    )


def test_v2_buy_all_three_legs_fill_yields_1_25R():
    """tp1+tp2+tp3 hit in three sequential bars → weighted R = 0.125 + 0.5 + 0.625 = 1.25R."""
    row = _v2_row("buy")
    bars = [
        _bar(0, 100, 102.7, 99.5, 102.5),  # tp1 hit
        _bar(60_000, 102.5, 105.5, 102.0, 105.0),  # tp2 hit
        _bar(120_000, 105, 113.0, 104.5, 112.5),   # tp3 hit
    ]
    import json as _json
    extra = _json.loads(row.extra_json)
    verdict = _classify_v2_multi_leg(row, bars, extra)
    assert verdict.result == "win"
    assert verdict.actual_r_multiple == 1.25
    assert verdict.bars_to_resolution == 3
    assert verdict.extra_json_updates["filled_legs"] == ["tp1", "tp2", "tp3"]


def test_v2_buy_tp1_only_then_sl_at_breakeven_yields_0_125R():
    """tp1 hits, SL moves to entry, price retraces, SL hit at entry."""
    row = _v2_row("buy")
    bars = [
        _bar(0, 100, 103, 99.5, 102.5),  # tp1 hit
        _bar(60_000, 102.5, 102.6, 99.9, 100.0),  # touches entry (=BE SL); SL HIT
    ]
    import json as _json
    extra = _json.loads(row.extra_json)
    verdict = _classify_v2_multi_leg(row, bars, extra)
    # tp1 filled = 0.5R × 0.25 = 0.125; remainder (0.75) exits at entry = 0R
    assert verdict.actual_r_multiple == 0.125
    assert verdict.result == "win"  # net positive
    assert verdict.extra_json_updates["filled_legs"] == ["tp1"]
    assert verdict.extra_json_updates["current_sl"] == 100.0  # BE


def test_v2_buy_tp1_tp2_then_sl_at_tp1_floor_yields_0_75R():
    """tp1+tp2 fill → SL moves to tp1 price → retraces → SL hit at tp1."""
    row = _v2_row("buy")
    bars = [
        _bar(0, 100, 102.7, 99.5, 102.5),  # tp1 hit
        _bar(60_000, 102.5, 105.5, 102.0, 105.0),  # tp2 hit
        _bar(120_000, 105, 105.1, 102.4, 102.5),  # SL hit at tp1=102.5
    ]
    import json as _json
    extra = _json.loads(row.extra_json)
    verdict = _classify_v2_multi_leg(row, bars, extra)
    # tp1: 0.5R × 0.25 = 0.125; tp2: 1.0R × 0.50 = 0.5; tp3 remainder
    # (0.25) exits at tp1=102.5, which is +0.5R → 0.5 × 0.25 = 0.125
    # Total = 0.75R
    assert verdict.actual_r_multiple == 0.75
    assert verdict.result == "win"
    assert verdict.extra_json_updates["filled_legs"] == ["tp1", "tp2"]
    assert verdict.extra_json_updates["current_sl"] == 102.5  # tp1 floor


def test_v2_buy_no_legs_then_sl_yields_minus_1R():
    """No tp hit, original SL hit → full -1R loss (mirrors single-leg)."""
    row = _v2_row("buy")
    bars = [
        _bar(0, 100, 100.5, 94.9, 95.0),  # SL=95 hit
    ]
    import json as _json
    extra = _json.loads(row.extra_json)
    verdict = _classify_v2_multi_leg(row, bars, extra)
    assert verdict.result == "loss"
    assert verdict.actual_r_multiple == -1.0
    assert verdict.extra_json_updates["filled_legs"] == []


def test_v2_sell_all_three_legs_mirror_buy():
    """Symmetric: short with entry=100, stop=105, tp1=97.5, tp2=95, tp3=87.5."""
    row = _v2_row("sell", entry=100.0, stop=105.0,
                  tp1=97.5, tp2=95.0, tp3=87.5)
    bars = [
        _bar(0, 100, 100.5, 97.3, 97.5),    # tp1 hit (low <= 97.5)
        _bar(60_000, 97.5, 98.0, 94.9, 95.0),  # tp2 hit
        _bar(120_000, 95, 96, 87.0, 87.5),  # tp3 hit
    ]
    import json as _json
    extra = _json.loads(row.extra_json)
    verdict = _classify_v2_multi_leg(row, bars, extra)
    assert verdict.result == "win"
    assert verdict.actual_r_multiple == 1.25
    assert verdict.extra_json_updates["filled_legs"] == ["tp1", "tp2", "tp3"]


def test_v2_resume_from_partial_state_in_extra_json():
    """Replay should resume from extra_json.filled_legs / current_sl —
    simulates a second replay tick where tp1 already filled on a prior pass."""
    row = _v2_row("buy")
    import json as _json
    extra = _json.loads(row.extra_json)
    # Pretend tp1 already filled on a prior tick + SL moved to BE
    extra["filled_legs"] = ["tp1"]
    extra["current_sl"] = 100.0
    bars = [
        _bar(0, 100, 100.5, 99.9, 100.0),  # SL HIT at BE → close
    ]
    verdict = _classify_v2_multi_leg(row, bars, extra)
    assert verdict.actual_r_multiple == 0.125  # tp1 only, remainder at BE
    assert verdict.result == "win"


def test_v2_same_bar_sl_and_tp_resolves_to_sl_first_loss_bias():
    """Conservative tie-handling mirrors single-leg: if SL and TP hit in
    the same bar, assume SL first. Here the bar high reaches tp1 AND
    the bar low touches the original stop — should resolve to -1R loss."""
    row = _v2_row("buy")
    bars = [
        _bar(0, 100, 102.6, 94.9, 100),  # both tp1 (102.5) and SL (95) hit
    ]
    import json as _json
    extra = _json.loads(row.extra_json)
    verdict = _classify_v2_multi_leg(row, bars, extra)
    assert verdict.result == "loss"
    assert verdict.actual_r_multiple == -1.0


def test_v2_still_open_returns_transient_with_extra_updates():
    """Inside max_hold but no fills → still_open verdict, extra_json
    updates carry empty filled_legs / unchanged SL so the row doesn't
    get DB-thrashed every tick (delta detection in _replay_tick_async
    handles the no-op write)."""
    # Use a fresh timestamp so the window isn't elapsed
    from datetime import datetime as _dt, timezone as _tz
    row = _v2_row("buy")
    row = _PendingRow(
        order_id=row.order_id,
        ts=_dt.now(_tz.utc).isoformat(timespec="seconds"),
        strategy=row.strategy, division=row.division,
        symbol=row.symbol, side=row.side, qty=row.qty,
        stop_price=row.stop_price, tp_price=row.tp_price,
        tp_r_multiple=row.tp_r_multiple,
        entry_reference_price=row.entry_reference_price,
        expected_loss=row.expected_loss,
        expected_gain=row.expected_gain,
        max_hold_seconds=row.max_hold_seconds,
        extra_json=row.extra_json,
    )
    bars = [
        _bar(0, 100, 101, 99, 100.5),
        _bar(60_000, 100.5, 101, 100, 100.8),
    ]
    import json as _json
    extra = _json.loads(row.extra_json)
    verdict = _classify_v2_multi_leg(row, bars, extra)
    assert verdict.result == "still_open"
    assert verdict.extra_json_updates["filled_legs"] == []


def test_v2_aggregate_r_helper_matches_option_c_arithmetic():
    """Pure-function check on _aggregate_multi_leg_r matches the
    Option C worst-case scenarios in the strategy_gaps memo."""
    tp_plan = [
        {"leg": "tp1", "fraction": 0.25, "target_r": 0.5, "price": 102.5,
         "stop_action": "move_to_breakeven"},
        {"leg": "tp2", "fraction": 0.50, "target_r": 1.0, "price": 105.0,
         "stop_action": "move_to_tp1"},
        {"leg": "tp3", "fraction": 0.25, "target_r": 2.5, "price": 112.5,
         "stop_action": "trail_atr"},
    ]
    # tp1+tp2 + remainder at BE (entry) → 0.125 + 0.5 + 0 = 0.625
    r = _aggregate_multi_leg_r(
        side="buy", entry_price=100.0, original_sl=95.0,
        tp_plan=tp_plan, filled_legs=["tp1", "tp2"], exit_price=100.0,
    )
    assert r == 0.625
    # tp1+tp2 + remainder at tp1=102.5 → 0.125 + 0.5 + 0.125 = 0.75 (Option C floor)
    r = _aggregate_multi_leg_r(
        side="buy", entry_price=100.0, original_sl=95.0,
        tp_plan=tp_plan, filled_legs=["tp1", "tp2"], exit_price=102.5,
    )
    assert r == 0.75
    # All 3 fill → 0.125 + 0.5 + 0.625 = 1.25
    r = _aggregate_multi_leg_r(
        side="buy", entry_price=100.0, original_sl=95.0,
        tp_plan=tp_plan, filled_legs=["tp1", "tp2", "tp3"],
        exit_price=112.5,
    )
    assert r == 1.25
    # No fills, exit at original SL → -1R full
    r = _aggregate_multi_leg_r(
        side="buy", entry_price=100.0, original_sl=95.0,
        tp_plan=tp_plan, filled_legs=[], exit_price=95.0,
    )
    assert r == -1.0
