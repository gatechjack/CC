"""Tests for scripts/audit_reality_reconciler.py.

Covers:
  - B7: no_bars guard — when fetcher returns [], _reconcile_one must never
    produce a `match` verdict; simulated_result must be `no_bars`.
  - 1m fetcher wiring: reconciler calls _bitunix_kline_fetcher with timeframe
    "1m" and since/bars_needed derived from the trade.
  - Fast partial-win case (matching the 5/27 trade shape 6daca683): a SELL
    with 1m bars where low dips through tp1+tp2 and a later bar's high
    reaches the ratcheted SL resolves to result='win' (not 'expired'/'still_open').
  - Forward-fetch coverage: a trade whose old tight window gave 0 DB bars
    now gets bars from the forward API fetch and resolves (not `no_bars`).
  - Rename: discrepancy string uses `sim_filled_legs:` not `missed_legs:`.
  - Summary roll-up: any `no_bars` row must prevent status='match'.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Ensure the repo root is on sys.path so `scripts/` is importable.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from trading_corp.persistence.db import connect, init_db
from scripts.audit_reality_reconciler import (
    _persist_summary,
    _reconcile_one,
    _ReconcileResult,
)


# ── helpers ──────────────────────────────────────────────────────────────


def _make_trade_dict(
    *,
    order_id: str = "test-order-1",
    ts: str = "2026-05-28T01:15:00+00:00",
    result_ts: str = "2026-05-28T01:18:00+00:00",
    symbol: str = "BTC/USDT.P",
    side: str = "sell",
    entry: float = 74438.2,
    stop_price: float = 74602.52,
    tp_price: float = 74304.21,
    tp_r_multiple: float = 2.5,
    expected_loss: float = -163.32,
    expected_gain: float = 408.3,
    max_hold_seconds: int = 86400,
    result: str = "win",
    actual_r_multiple: float = 0.9076,
    extra_json: str | None = None,
) -> dict:
    """Build a synthetic paper_trade_record dict without touching the DB."""
    tp1 = 74304.21
    tp2 = 74273.88
    tp3 = 74027.40
    extra = extra_json or json.dumps({
        "tp_plan": [
            {"leg": "tp1", "fraction": 0.25, "target_r": 0.5,
             "price": tp1, "stop_action": "move_to_breakeven"},
            {"leg": "tp2", "fraction": 0.50, "target_r": 1.0,
             "price": tp2, "stop_action": "move_to_tp1"},
            {"leg": "tp3", "fraction": 0.25, "target_r": 2.5,
             "price": tp3, "stop_action": "trail_atr"},
        ],
        "tp_plan_version": "v2",
        "filled_legs": [],
        "current_sl": stop_price,
    })
    return {
        "order_id": order_id,
        "ts": ts,
        "strategy": "bitunix_futures",
        "division": "bitunix_futures",
        "symbol": symbol,
        "side": side,
        "qty": 0.01,
        "stop_price": stop_price,
        "tp_price": tp_price,
        "tp_r_multiple": tp_r_multiple,
        "entry_reference_price": entry,
        "expected_loss": expected_loss,
        "expected_gain": expected_gain,
        "max_hold_seconds": max_hold_seconds,
        "extra_json": extra,
        "result": result,
        "result_ts": result_ts,
        "result_price": tp1,
        "actual_r_multiple": actual_r_multiple,
        "bars_to_resolution": None,
    }


def _insert_v2_trade(
    db_url: str,
    *,
    order_id: str = "test-order-1",
    result: str = "expired",
    actual_r_multiple: float = 0.0,
    ts: str = "2020-01-01T00:00:00+00:00",
    result_ts: str = "2020-01-01T01:00:00+00:00",
    audit_corrected: bool = False,
    corrected_result: str | None = None,
    corrected_r: float | None = None,
) -> None:
    """Insert a closed v2 paper_trade_record row directly."""
    extra: dict = {
        "tp_plan": [
            {"leg": "tp1", "fraction": 0.25, "target_r": 0.5, "price": 102.5,
             "stop_action": "move_to_breakeven"},
            {"leg": "tp2", "fraction": 0.50, "target_r": 1.0, "price": 105.0,
             "stop_action": "move_to_tp1"},
            {"leg": "tp3", "fraction": 0.25, "target_r": 2.5, "price": 112.5,
             "stop_action": "trail_atr"},
        ],
        "tp_plan_version": "v2",
        "filled_legs": [],
        "current_sl": 95.0,
    }
    if audit_corrected:
        extra["audit_corrected"] = True
        if corrected_result is not None:
            extra["corrected_result"] = corrected_result
        if corrected_r is not None:
            extra["corrected_r_multiple"] = corrected_r

    with connect(db_url) as conn:
        conn.execute("""
            INSERT INTO paper_trade_record (
                order_id, ts, strategy, division, symbol, side, qty,
                tier, source_signal,
                entry_reference_price, stop_price, tp_price, tp_r_multiple,
                expected_loss, expected_gain, rr_ratio, max_hold_seconds,
                result, result_ts, result_price,
                actual_r_multiple, extra_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?
            )
        """, (
            order_id, ts, "bitunix_futures", "bitunix_futures", "BTC/USDT.P",
            "buy", 0.01,
            "PREMIUM", "test",
            100.0, 95.0, 112.5, 2.5,
            -50.0, 125.0, 2.5, 86400,
            result, result_ts, 100.0,
            actual_r_multiple, json.dumps(extra),
        ))


# ── (a) fetcher wiring: 1m timeframe + correct since/bars_needed ─────────


def test_reconciler_calls_fetcher_with_1m_timeframe(tmp_db: str) -> None:
    """(a) _reconcile_one must call _bitunix_kline_fetcher with timeframe='1m'
    and since/bars_needed derived from trade.ts and max_hold_seconds."""
    init_db(tmp_db)

    # Trade with max_hold_seconds=3600 → bars_needed = max(1, 3600//60) = 60.
    trade = _make_trade_dict(
        ts="2026-05-28T01:15:00+00:00",
        result="win",
        actual_r_multiple=0.9076,
        max_hold_seconds=3600,
    )

    calls = []

    async def fake_fetcher(symbol, timeframe, since_ms, bars_needed):
        calls.append((symbol, timeframe, since_ms, bars_needed))
        # Return a bar that will trigger SL hit (high >= sl 74602.52 for sell)
        # to let reconciler resolve without hanging.
        return [[since_ms, 74438.2, 74700.0, 74100.0, 74200.0, 100.0]]

    with patch("scripts.audit_reality_reconciler._bitunix_kline_fetcher", fake_fetcher):
        with connect(tmp_db) as conn:
            _reconcile_one(conn, trade)

    assert len(calls) == 1, f"Expected fetcher called once, got {len(calls)} calls"
    symbol_arg, tf_arg, since_arg, bars_arg = calls[0]
    assert tf_arg == "1m", f"Expected timeframe='1m', got {tf_arg!r}"
    # since_ms = _iso_to_ms("2026-05-28T01:15:00+00:00")
    from trading_corp.agents.paper_trade_replay import _iso_to_ms
    expected_since = _iso_to_ms("2026-05-28T01:15:00+00:00")
    assert since_arg == expected_since, (
        f"Expected since_ms={expected_since}, got {since_arg}"
    )
    expected_bars = max(1, 3600 // 60)  # 60
    assert bars_arg == expected_bars, (
        f"Expected bars_needed={expected_bars}, got {bars_arg}"
    )


# ── (b)/(c) fast partial-win — the 6daca683 shape ────────────────────────


def test_reconciler_resolves_fast_partial_win_as_win(tmp_db: str) -> None:
    """(b)/(c) SELL trade (6daca683 shape): entry=74438.2, SL=74602.52,
    tp1=74304.21, tp2=74273.88, tp3=74027.40.

    1m bar sequence:
      bar0 (01:15): O=74438.2, H=74450.0, L=74380.0, C=74400.0  — no fill yet
      bar1 (01:16): O=74400.0, H=74420.0, L=74133.5, C=74200.0  — low<=tp1,tp2 → fill both; SL ratchets to tp1=74304.21
      bar2 (01:17): O=74200.0, H=74365.8, L=74180.0, C=74300.0  — high>=ratcheted SL 74304.21 → win

    Old 3m reconciler: single bar collapses bar1+bar2 into one bar (L=74133.5, H=74365.8),
    fills tp1+tp2 and immediately bounces — but no NEXT bar → 'still_open'.
    New 1m reconciler: bar1 fills tp1+tp2, bar2's high hits the moved SL → 'win'. ✓
    """
    init_db(tmp_db)

    # Note: actual_r_multiple is set to the value the sim computes so
    # matches=True can be asserted. The key regression this test guards
    # is simulated_result='win' (not 'expired'/'still_open').
    # Computed: tp1+tp2 filled (partial win R) + runner exits at moved SL
    # (74304.21). Weighted R = 0.5*0.25 + 1.0*0.5 + 0.8154*0.25 = 0.8289.
    trade = _make_trade_dict(
        order_id="6daca683",
        ts="2026-05-28T01:15:00+00:00",
        result_ts="2026-05-28T01:18:00+00:00",
        side="sell",
        entry=74438.2,
        stop_price=74602.52,
        tp_price=74304.21,
        tp_r_multiple=2.5,
        max_hold_seconds=86400,
        result="win",
        actual_r_multiple=0.8289,  # matches sim's weighted R for this bar sequence
    )

    # ts_ms for 2026-05-28T01:15:00Z
    base_ms = 1748394900_000  # 2026-05-28T01:15:00Z in ms

    # 1m bars: bar0=01:15, bar1=01:16, bar2=01:17
    bars_1m = [
        [base_ms + 0 * 60_000, 74438.2, 74450.0, 74380.0, 74400.0, 50.0],   # bar0
        [base_ms + 1 * 60_000, 74400.0, 74420.0, 74133.5, 74200.0, 80.0],   # bar1: low<=tp1,tp2
        [base_ms + 2 * 60_000, 74200.0, 74365.8, 74180.0, 74300.0, 60.0],   # bar2: high>=moved SL
    ]

    async def fake_fetcher(symbol, timeframe, since_ms, bars_needed):
        return bars_1m

    with patch("scripts.audit_reality_reconciler._bitunix_kline_fetcher", fake_fetcher):
        with connect(tmp_db) as conn:
            result = _reconcile_one(conn, trade)

    assert result.simulated_result == "win", (
        f"Expected simulated_result='win' for fast partial-win trade, "
        f"got {result.simulated_result!r} (bar_count={result.bar_count}). "
        f"The old 3m reconciler returned 'still_open' for this shape."
    )
    assert result.bar_count == 3, f"Expected bar_count=3, got {result.bar_count}"
    assert result.matches is True, (
        f"Expected matches=True (recorded=win, sim=win), "
        f"got discrepancy={result.discrepancy!r}"
    )


# ── (d) forward-fetch coverage: old tight window → 0 DB bars, now resolved ──


def test_reconciler_resolves_tight_window_trade_via_forward_fetch(tmp_db: str) -> None:
    """(d) A trade whose [entry_ts, result_ts] is only 2 minutes wide
    (like the 99d62e04 no_bars case) — the old DB query returned 0 3m bars.
    With the forward 1m fetch from entry_ts for bars_needed bars, we now
    get coverage and the reconciler produces a real verdict (not 'no_bars').
    """
    init_db(tmp_db)

    # Short window: ts to result_ts is 2 min — would have been 0 3m DB bars.
    trade = _make_trade_dict(
        order_id="tight-window",
        ts="2026-05-28T04:00:02+00:00",
        result_ts="2026-05-28T04:02:00+00:00",
        side="buy",
        entry=73231.8,
        stop_price=72990.03,
        tp_price=73500.0,
        tp_r_multiple=2.5,
        max_hold_seconds=86400,
        result="loss",
        actual_r_multiple=-1.0,
    )

    base_ms = 1748394002_000  # 2026-05-28T04:00:02Z approx

    # Provide a bar that hits the SL (low <= stop_price=72990.03 for buy)
    bars_1m = [
        [base_ms, 73231.8, 73250.0, 72980.0, 73000.0, 40.0],  # low hits SL
    ]

    async def fake_fetcher(symbol, timeframe, since_ms, bars_needed):
        return bars_1m

    with patch("scripts.audit_reality_reconciler._bitunix_kline_fetcher", fake_fetcher):
        with connect(tmp_db) as conn:
            result = _reconcile_one(conn, trade)

    assert result.simulated_result != "no_bars", (
        f"Expected a real verdict from forward-fetch, got 'no_bars'. "
        f"The DB-based reconciler had 0 3m bars for this tight window."
    )
    assert result.bar_count > 0, (
        f"Expected bar_count > 0 from forward-fetch, got {result.bar_count}"
    )


# ── (e) discrepancy string uses sim_filled_legs, not missed_legs ──────────


def test_discrepancy_string_uses_sim_filled_legs(tmp_db: str) -> None:
    """(e) When there's a result mismatch and the sim filled TP legs,
    the discrepancy string must contain 'sim_filled_legs:' not 'missed_legs:'.
    """
    init_db(tmp_db)

    # Trade recorded as 'expired' but sim will see a 'win'.
    # Use a SELL trade where tp1 is hit.
    trade = _make_trade_dict(
        order_id="rename-test",
        result="expired",
        actual_r_multiple=0.0,
        side="sell",
        entry=74438.2,
        stop_price=74602.52,
        tp_price=74304.21,
        tp_r_multiple=2.5,
        max_hold_seconds=86400,
    )

    base_ms = 1748394900_000

    # bar0: low hits tp1 → sim fills tp1 and starts watching for moved SL
    # bar1: high hits moved SL (tp1 price = 74304.21) → win
    bars_1m = [
        [base_ms,             74438.2, 74450.0, 74200.0, 74250.0, 50.0],  # low<=tp1,tp2
        [base_ms + 60_000,    74250.0, 74350.0, 74200.0, 74300.0, 40.0],  # high>=moved SL
    ]

    async def fake_fetcher(symbol, timeframe, since_ms, bars_needed):
        return bars_1m

    with patch("scripts.audit_reality_reconciler._bitunix_kline_fetcher", fake_fetcher):
        with connect(tmp_db) as conn:
            result = _reconcile_one(conn, trade)

    # Sim resolves as 'win'; recorded is 'expired' → mismatch → discrepancy set.
    assert result.discrepancy is not None, (
        "Expected a discrepancy string (sim=win vs recorded=expired)"
    )
    assert "sim_filled_legs:" in result.discrepancy, (
        f"Expected 'sim_filled_legs:' in discrepancy, got: {result.discrepancy!r}"
    )
    assert "missed_legs:" not in result.discrepancy, (
        f"Old 'missed_legs:' label still present in discrepancy: {result.discrepancy!r}"
    )


# ── B7: no_bars guard (fetcher returns []) ────────────────────────────────


def test_reconcile_one_no_bars_must_not_declare_match(tmp_db: str) -> None:
    """B7 guard: when _bitunix_kline_fetcher returns [], _reconcile_one
    must NOT return matches=True; simulated_result must be 'no_bars'.
    """
    init_db(tmp_db)

    trade = _make_trade_dict(
        order_id="no-bars-trade",
        result="expired",
        actual_r_multiple=0.0,
    )

    async def fake_fetcher(symbol, timeframe, since_ms, bars_needed):
        return []

    with patch("scripts.audit_reality_reconciler._bitunix_kline_fetcher", fake_fetcher):
        with connect(tmp_db) as conn:
            result = _reconcile_one(conn, trade)

    assert result.matches is False, (
        f"Expected matches=False for zero-bar trade, got matches=True "
        f"(simulated_result={result.simulated_result!r}, bar_count={result.bar_count})"
    )
    assert result.bar_count == 0
    assert result.simulated_result == "no_bars", (
        f"Expected simulated_result='no_bars', got {result.simulated_result!r}"
    )


# ── B7: summary roll-up test ──────────────────────────────────────────────


def test_persist_summary_no_bars_row_prevents_match_status(tmp_db: str) -> None:
    """When the per-fire summary contains any no_bars row, the summary
    status must NOT be 'match' — it must be a distinct attention-worthy
    string so the dashboard/alarm treats it as non-green.
    """
    init_db(tmp_db)

    results = [
        _ReconcileResult(
            order_id="ok-trade",
            ts="2020-01-01T00:00:00+00:00",
            division="bitunix_futures",
            side="buy",
            recorded_result="loss",
            recorded_r=-1.0,
            recorded_source="native",
            simulated_result="loss",
            simulated_r=-1.0,
            simulated_filled_legs=[],
            simulated_current_sl=95.0,
            bar_count=10,
            matches=True,
            discrepancy=None,
        ),
        _ReconcileResult(
            order_id="no-bars-trade",
            ts="2020-01-01T00:00:00+00:00",
            division="bitunix_futures",
            side="buy",
            recorded_result="expired",
            recorded_r=0.0,
            recorded_source="native",
            simulated_result="no_bars",
            simulated_r=None,
            simulated_filled_legs=[],
            simulated_current_sl=None,
            bar_count=0,
            matches=False,
            discrepancy="no_bars: 0 bars in window",
        ),
    ]

    _persist_summary(tmp_db, results)

    with connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT payload_json FROM audit_event WHERE kind='audit_reality_run' "
            "ORDER BY ts DESC LIMIT 1"
        ).fetchone()

    assert row is not None, "Expected an audit_reality_run row to be written"
    payload = json.loads(row["payload_json"])

    assert payload["n_total"] == 2
    assert payload["n_matches"] == 1
    assert payload["status"] != "match", (
        f"Expected non-match status when a no_bars row is present, "
        f"got status={payload['status']!r}"
    )
