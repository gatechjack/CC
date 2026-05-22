"""Tests for scripts/audit_reality_reconciler.py — B7 guard + B9 window fix.

Covers:
  - B7: no_bars guard — a trade reconciled against zero bars must never
    produce a `match` verdict, even when the recorded result happens to
    equal the empty-bars classifier output ("expired").
  - Existing match behavior is preserved: trades with real bars and a
    matching recorded result still produce `matches=True`.
  - Summary roll-up: any `no_bars` row must change the fire status away
    from "match" so the dashboard/alarm sees it as attention-worthy.
  - B9: inverted-window fix — when result_ts < ts (finalizing-tick
    attribution artifact, documented in B5 / runbooks/2026-05-21_post_funding_diagnostics.md),
    the absolute bar window must still be queried correctly so bars that
    exist in [min(ts,result_ts), max(ts,result_ts)] are returned.
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
    _load_bars_for_trade,
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


# ── B9: inverted-window tests ─────────────────────────────────────────────


def test_load_bars_normalizes_inverted_window(tmp_db: str) -> None:
    """B9 unit test: _load_bars_for_trade must return bars even when
    trade['ts'] > trade['result_ts'] (inverted window — finalizing-tick
    attribution artifact documented in B5 and runbooks/2026-05-21_post_funding_diagnostics.md).

    The absolute window [min(ts,result_ts), max(ts,result_ts)] contains real
    bars; before the B9 fix the SQL window is inverted (start > end) and
    returns 0 rows. After the fix, the bars are returned.
    """
    init_db(tmp_db)
    _ensure_bar_history_table(tmp_db)

    # T0 (bar open, earlier) and T1 (trade entry, later).
    # The trade has ts=T1, result_ts=T0 — inverted by 12 seconds
    # (mirroring 2942ff8e: ts=14:00:12, result_ts=14:00:00).
    t0_ms = 1577836800_000  # 2020-01-01T00:00:00Z in ms
    t1_ms = 1577836812_000  # 2020-01-01T00:00:12Z in ms  (12 s later)

    # Insert one bar at T0 — it's inside the absolute window [T0, T1].
    _insert_bar(tmp_db, ts_ms=t0_ms, timeframe="3m",
                o=100.0, h=101.0, low=99.0, c=100.5)

    # Trade with inverted window: ts=T1 > result_ts=T0.
    trade = {
        "ts": "2020-01-01T00:00:12+00:00",       # T1 — later
        "result_ts": "2020-01-01T00:00:00+00:00", # T0 — earlier (inverted)
    }

    with connect(tmp_db) as conn:
        bars = _load_bars_for_trade(conn, trade, timeframe="3m")

    assert len(bars) == 1, (
        f"B9: expected 1 bar in absolute window [T0, T1] but got {len(bars)}. "
        f"Inverted window bug — ts > result_ts causes SQL to return 0 rows."
    )
    assert bars[0][0] == t0_ms, f"Expected bar ts_ms={t0_ms}, got {bars[0][0]}"


def test_reconcile_one_inverted_window_2942ff8e_shape_matches(tmp_db: str) -> None:
    """B9 integration test: a trade with result_ts < ts (inverted window,
    mirroring prod trade 2942ff8e from 2026-05-21) must reconcile to
    matches=True with bar_count > 0 and simulated_result='win'.

    Trade shape: SHORT BTC/USDT.P, entry 77089.4, SL 77324.2447,
    TP1 76950.639 (fill at bar 14:15, low=76888.0 <= 76950.639),
    TP2 76854.555 (fill at bar 14:18, low=76780.3 <= 76854.555),
    runner SL at TP1 price (76950.639), hit at bar 14:27 (high=77026.1 >= 76950.639).
    Actual R = 0.7955.

    OHLC data from runbooks/2026-05-21_post_funding_diagnostics.md § 1.

    Before the B9 fix: result_ts < ts → SQL window inverted → 0 bars →
    B7 guard fires → simulated_result='no_bars', matches=False.
    After the fix: bars returned, classifier runs, matches=True.
    """
    init_db(tmp_db)
    _ensure_bar_history_table(tmp_db)

    # Use 2020-01-01 as the base date (avoids strftime('%s', ...) issues
    # with 2026 timestamps on some sqlite builds). The inversion shape is
    # identical: ts is 12 s after result_ts.
    #
    # Trade entry: 2020-01-01T14:00:12Z (ts — the live-system ts column)
    # result_ts:   2020-01-01T14:00:00Z (bar-open — the v2 cosmetic artifact, B5)
    # Inverted by 12 s, exactly mirroring 2942ff8e.
    #
    # Bars at 3m intervals: 14:00 through 14:27 (10 bars).
    # Bar date: 2020-01-01. Base epoch: 1577836800 (2020-01-01T00:00:00Z).

    _BASE_SEC = 1577836800  # 2020-01-01T00:00:00Z

    def bar_ms(h: int, m: int) -> int:
        return (_BASE_SEC + h * 3600 + m * 60) * 1000

    # Bar table from runbook § 1 (actual OHLC from the prod bar history):
    #   bar  | O        | H        | L        | C
    #   14:00 | 77090.1  | 77193.9  | 77073.0  | 77166.8
    #   14:03 | 77166.8  | 77215.5  | 77112.4  | 77126.1
    #   14:06 | 77126.1  | 77131.8  | 77008.0  | 77065.0
    #   14:09 | 77065.0  | 77174.2  | 77055.0  | 77055.6
    #   14:12 | 77055.6  | 77120.7  | 77008.7  | 77054.6
    #   14:15 | 77054.6  | 77061.8  | 76888.0  | 76956.4  ← TP1 fill (low ≤ 76950.639)
    #   14:18 | 76956.4  | 76966.9  | 76780.3  | 76780.6  ← TP2 fill (low ≤ 76854.555)
    #   14:21 | 76780.6  | 76914.0  | 76747.1  | 76872.0
    #   14:24 | 76872.0  | 76943.1  | 76867.5  | 76907.8
    #   14:27 | 76907.8  | 77026.1  | 76907.8  | 77023.1  ← runner SL hit (high ≥ 76950.639)
    bars_data = [
        (bar_ms(14,  0), 77090.1, 77193.9, 77073.0, 77166.8),
        (bar_ms(14,  3), 77166.8, 77215.5, 77112.4, 77126.1),
        (bar_ms(14,  6), 77126.1, 77131.8, 77008.0, 77065.0),
        (bar_ms(14,  9), 77065.0, 77174.2, 77055.0, 77055.6),
        (bar_ms(14, 12), 77055.6, 77120.7, 77008.7, 77054.6),
        (bar_ms(14, 15), 77054.6, 77061.8, 76888.0, 76956.4),
        (bar_ms(14, 18), 76956.4, 76966.9, 76780.3, 76780.6),
        (bar_ms(14, 21), 76780.6, 76914.0, 76747.1, 76872.0),
        (bar_ms(14, 24), 76872.0, 76943.1, 76867.5, 76907.8),
        (bar_ms(14, 27), 76907.8, 77026.1, 76907.8, 77023.1),
    ]
    for ts_ms, o, h, low, c in bars_data:
        _insert_bar(tmp_db, ts_ms=ts_ms, timeframe="3m", o=o, h=h, low=low, c=c)

    # Trade: SHORT BTC/USDT.P, entry=77089.4, SL=77324.2447 (above entry for short),
    # TP1=76950.639, TP2=76854.555, TP3=76502.288 (progressively lower for short win).
    # Recorded result: win, R=0.7955.
    entry = 77089.4
    sl    = 77324.2447   # above entry — short SL
    tp1   = 76950.639
    tp2   = 76854.555
    tp3   = 76502.288
    tp_r_multiple = 2.5  # TP3 full-fill would be 2.5R
    expected_loss = -(sl - entry) * 0.01   # qty=0.01, loss if SL hit
    expected_gain = (entry - tp3) * 0.01   # full gain at TP3

    extra = {
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
        "current_sl": sl,
    }

    # ts=14:00:12Z (the live entry ts), result_ts=14:00:00Z (bar-open cosmetic B5).
    # This is the INVERTED shape: ts > result_ts by 12 seconds.
    trade_ts    = f"2020-01-01T14:00:12+00:00"
    result_ts   = f"2020-01-01T14:00:00+00:00"

    with connect(tmp_db) as conn:
        conn.execute("""
            INSERT INTO paper_trade_record (
                order_id, ts, strategy, division, symbol, side, qty,
                tier, source_signal,
                entry_reference_price, stop_price, tp_price, tp_r_multiple,
                expected_loss, expected_gain, rr_ratio, max_hold_seconds,
                result, result_ts, result_price,
                actual_r_multiple, extra_json
            ) VALUES (
                '2942ff8e-synthetic', ?, 'bitunix_futures', 'bitunix_futures',
                'BTC/USDT.P', 'sell', 0.01,
                'PREMIUM', 'test',
                ?, ?, ?, ?,
                ?, ?, 2.5, 86400,
                'win', ?, ?,
                0.7955, ?
            )
        """, (
            trade_ts, entry, sl, tp1, tp_r_multiple,
            expected_loss, expected_gain,
            result_ts, tp1,
            json.dumps(extra),
        ))

        row = dict(conn.execute(
            "SELECT * FROM paper_trade_record WHERE order_id = '2942ff8e-synthetic'"
        ).fetchone())
        result = _reconcile_one(conn, row)

    # Before B9 fix: result_ts < ts → inverted window → 0 bars → B7 guard →
    # simulated_result='no_bars', matches=False.
    # After B9 fix: absolute window [14:00:00, 14:27:00+] → 10 bars returned →
    # classifier replays the price path → win, R≈0.7955 → matches=True.
    assert result.bar_count > 0, (
        f"B9: expected bars but got bar_count=0. "
        f"simulated_result={result.simulated_result!r}. "
        f"Inverted window (ts=14:00:12 > result_ts=14:00:00) caused SQL to return 0 rows."
    )
    assert result.simulated_result != "no_bars", (
        f"B9: simulated_result='no_bars' means B7 guard fired on an inverted window "
        f"that actually has bars. bar_count={result.bar_count}"
    )
    assert result.simulated_result == "win", (
        f"B9: expected simulated_result='win' for the 2942ff8e price path, "
        f"got {result.simulated_result!r} (bar_count={result.bar_count}, "
        f"simulated_r={result.simulated_r})"
    )
    assert result.matches is True, (
        f"B9: expected matches=True but got matches=False. "
        f"simulated_result={result.simulated_result!r}, recorded_result={result.recorded_result!r}, "
        f"simulated_r={result.simulated_r}, recorded_r={result.recorded_r}, "
        f"discrepancy={result.discrepancy!r}"
    )
