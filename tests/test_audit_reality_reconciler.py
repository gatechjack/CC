"""Tests for scripts/audit_reality_reconciler.py — B7 guard.

Covers:
  - B7: no_bars guard — a trade reconciled against zero bars must never
    produce a `match` verdict, even when the recorded result happens to
    equal the empty-bars classifier output ("expired").
  - Existing match behavior is preserved: trades with real bars and a
    matching recorded result still produce `matches=True`.
  - Summary roll-up: any `no_bars` row must change the fire status away
    from "match" so the dashboard/alarm sees it as attention-worthy.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

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


# ── fixtures ─────────────────────────────────────────────────────────────


def _ensure_bar_history_table(db_url: str) -> None:
    """Create bitunix_bar_history if init_db doesn't include it yet."""
    ddl = """
    CREATE TABLE IF NOT EXISTS bitunix_bar_history (
        ts_ms        INTEGER NOT NULL,
        timeframe    TEXT NOT NULL,
        open         REAL NOT NULL,
        high         REAL NOT NULL,
        low          REAL NOT NULL,
        close        REAL NOT NULL,
        volume       REAL NOT NULL,
        inserted_at  TEXT NOT NULL,
        PRIMARY KEY (ts_ms, timeframe)
    )
    """
    with connect(db_url) as conn:
        conn.execute(ddl)


def _insert_v2_trade(
    db_url: str,
    *,
    order_id: str = "test-order-1",
    result: str = "expired",
    actual_r_multiple: float = 0.0,
    ts: str = "2020-01-01T00:00:00+00:00",    # old timestamp → fully elapsed
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


def _insert_bar(
    db_url: str,
    *,
    ts_ms: int,
    timeframe: str = "3m",
    o: float = 100.0,
    h: float = 101.0,
    low: float = 99.0,
    c: float = 100.5,
) -> None:
    with connect(db_url) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO bitunix_bar_history
                (ts_ms, timeframe, open, high, low, close, volume, inserted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, '2020-01-01T00:00:00+00:00')
        """, (ts_ms, timeframe, o, h, low, c, 0.0))


# ── B7: failing test (RED before fix) ────────────────────────────────────


def test_reconcile_one_no_bars_must_not_declare_match(tmp_db: str) -> None:
    """B7 guard: when bitunix_bar_history has zero bars for a trade's
    window, _reconcile_one must NOT return matches=True.

    This is the false-match path described in BACKLOG.md B7:
      bars=[]  →  _classify_v2_multi_leg returns result="expired"
      rec_result="expired"
      sim_result == rec_result AND R tolerance satisfied
      → matches=True  (WRONG — this is the bug being fixed)

    After the fix the verdict must be `no_bars` (or equivalent) and
    matches must be False.
    """
    init_db(tmp_db)
    _ensure_bar_history_table(tmp_db)

    # Trade recorded as "expired" with R=0 — the outcome that coincides
    # with the empty-bars classifier output (verified in paper_trade_replay.py
    # lines 624-640: empty bars + fully_elapsed → result="expired", R=0.0).
    _insert_v2_trade(tmp_db, order_id="no-bars-trade",
                     result="expired", actual_r_multiple=0.0)

    with connect(tmp_db) as conn:
        row = dict(conn.execute(
            "SELECT * FROM paper_trade_record WHERE order_id = 'no-bars-trade'"
        ).fetchone())
        result = _reconcile_one(conn, row)

    # The guard must prevent a match against zero bars.
    assert result.matches is False, (
        f"Expected matches=False for zero-bar trade, got matches=True "
        f"(simulated_result={result.simulated_result!r}, bar_count={result.bar_count})"
    )
    # bar_count must be 0 so the caller can confirm why.
    assert result.bar_count == 0


# ── B7: verify existing match behavior is preserved (must stay GREEN) ────


def test_reconcile_one_with_bars_and_matching_result_is_match(tmp_db: str) -> None:
    """Regression: a trade with real bars covering its window and a
    genuinely matching recorded result must still produce matches=True.

    This ensures the guard doesn't break the 3/3 clean track record
    from 2026-05-21 06:03 UTC.
    """
    init_db(tmp_db)
    _ensure_bar_history_table(tmp_db)

    # A trade that was recorded as a loss: original SL hit on bar 1.
    # We provide a bar where low <= stop_price (95) so the classifier
    # returns result="loss" and the recorded result matches.
    trade_ts = "2020-01-01T00:00:00+00:00"
    result_ts = "2020-01-01T01:00:00+00:00"

    # Insert the trade with result="loss" and R=-1.0.
    with connect(tmp_db) as conn:
        extra = {
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
        conn.execute("""
            INSERT INTO paper_trade_record (
                order_id, ts, strategy, division, symbol, side, qty,
                tier, source_signal,
                entry_reference_price, stop_price, tp_price, tp_r_multiple,
                expected_loss, expected_gain, rr_ratio, max_hold_seconds,
                result, result_ts, result_price,
                actual_r_multiple, extra_json
            ) VALUES (
                'bars-trade', ?, 'bitunix_futures', 'bitunix_futures', 'BTC/USDT.P',
                'buy', 0.01, 'PREMIUM', 'test',
                100.0, 95.0, 112.5, 2.5,
                -50.0, 125.0, 2.5, 86400,
                'loss', ?, 95.0,
                -1.0, ?
            )
        """, (trade_ts, result_ts, json.dumps(extra)))

    # Provide bars: one bar where price action hits the SL (low=94.8 <= stop=95).
    # ts_ms must be within [ts, result_ts] for the SQL query in _load_bars_for_trade.
    bar_ts_ms = 1577836860_000  # 2020-01-01 00:01:00 UTC in ms
    _insert_bar(tmp_db, ts_ms=bar_ts_ms, timeframe="3m",
                o=100.0, h=100.5, low=94.8, c=95.0)

    with connect(tmp_db) as conn:
        row = dict(conn.execute(
            "SELECT * FROM paper_trade_record WHERE order_id = 'bars-trade'"
        ).fetchone())
        result = _reconcile_one(conn, row)

    # With bars present and matching result → must still match.
    assert result.bar_count > 0, f"Expected bars but got bar_count={result.bar_count}"
    assert result.simulated_result == "loss", (
        f"Expected sim=loss, got {result.simulated_result!r}"
    )
    assert result.matches is True, (
        f"Expected matches=True for valid loss match, got {result.matches} "
        f"(simulated={result.simulated_result!r}, recorded={result.recorded_result!r})"
    )


# ── B7: summary roll-up test ──────────────────────────────────────────────


def test_persist_summary_no_bars_row_prevents_match_status(tmp_db: str) -> None:
    """When the per-fire summary contains any no_bars row, the summary
    status must NOT be 'match' — it must be a distinct attention-worthy
    string so the dashboard/alarm treats it as non-green.
    """
    init_db(tmp_db)
    _ensure_bar_history_table(tmp_db)

    # Simulate a mix: one legitimate match + one no_bars row.
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
    assert payload["n_matches"] == 1  # the no_bars row does NOT count as match
    assert payload["status"] != "match", (
        f"Expected non-match status when a no_bars row is present, "
        f"got status={payload['status']!r}"
    )
