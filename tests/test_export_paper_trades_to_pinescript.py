"""Tests for scripts/export_paper_trades_to_pinescript.py.

Hermetic: every test stands up an in-memory SQLite with the relevant
DDL, inserts shaped rows, and asserts on the produced Pine paste block.

Cases:
  - Multi-leg v2 row (flat tp1/tp2/tp3_price keys in extra_json) →
    populates g_tp1/g_tp2/g_tp3_price arrays.
  - Multi-leg v2 row via tp_plan list (flat keys absent) → same outcome.
  - Pre-v2 single-leg row (only top-level tp_price column) →
    g_tp1_price gets it, g_tp2/g_tp3 = 0.0.
  - Open trade (result IS NULL in DB) → result='open', result_ts=0
    sentinel, result_price=0.0.
  - Validator-pair join: matching decision row inside window → "V+VOL/-S"
    tag. No match → trigger_signal fallback. Stale-window decision
    (outside the 600s lookback) → trigger_signal fallback.
  - iso_to_unix_ms round-trip on known UTC ts.
  - Empty corpus → header lines + array.new placeholders + count=0.
  - Header trade-count breakdown (W/L/expired/open).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.export_paper_trades_to_pinescript import (
    Trade,
    _extract_tp_prices,
    build_validator_pair_tag,
    fetch_trades,
    fetch_validator_pair,
    format_pine_paste_block,
    iso_to_unix_ms,
)


PT_DDL = """
CREATE TABLE paper_trade_record (
    order_id              TEXT PRIMARY KEY,
    ts                    TEXT NOT NULL,
    strategy              TEXT NOT NULL,
    division              TEXT NOT NULL,
    symbol                TEXT NOT NULL,
    side                  TEXT NOT NULL,
    qty                   REAL NOT NULL,
    tier                  TEXT,
    source_signal         TEXT,
    entry_reference_price REAL,
    stop_price            REAL,
    tp_price              REAL,
    tp_r_multiple         REAL,
    expected_loss         REAL,
    expected_gain         REAL,
    rr_ratio              REAL,
    max_hold_seconds      INTEGER,
    result                TEXT,
    result_ts             TEXT,
    result_price          REAL,
    actual_pnl_dollars    REAL,
    actual_r_multiple     REAL,
    bars_to_resolution    INTEGER,
    extra_json            TEXT
);
CREATE TABLE audit_event (
    id           INTEGER PRIMARY KEY,
    ts           TEXT    NOT NULL,
    actor        TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    payload_json TEXT    NOT NULL
);
"""


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.executescript(PT_DDL)
    yield c
    c.close()


def _insert_trade(
    conn: sqlite3.Connection,
    *,
    order_id: str,
    ts: str,
    side: str = "sell",
    entry: float = 73000.0,
    sl: float = 73200.0,
    tp_price: float | None = None,
    result: str | None = "win",
    result_ts: str | None = None,
    result_price: float | None = None,
    actual_r: float | None = 1.0,
    actual_pnl: float | None = 0.5,
    extra: dict | None = None,
    division: str = "bitunix_futures",
) -> None:
    conn.execute(
        "INSERT INTO paper_trade_record (order_id, ts, strategy, division, symbol, "
        "side, qty, entry_reference_price, stop_price, tp_price, result, result_ts, "
        "result_price, actual_r_multiple, actual_pnl_dollars, extra_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            order_id, ts, "bitunix_confluence", division, "BTCUSDT.P",
            side, 0.001, entry, sl, tp_price, result, result_ts,
            result_price, actual_r, actual_pnl,
            json.dumps(extra or {}),
        ),
    )
    conn.commit()


def _insert_decision(
    conn: sqlite3.Connection,
    *,
    ts: str,
    trigger_signal: str,
    passed: list[str],
    failed: list[str],
) -> None:
    payload = {
        "trigger_signal": trigger_signal,
        "passed": passed,
        "failed": failed,
        "decision": "pass" if passed and not failed else "partial",
    }
    conn.execute(
        "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?, ?, ?, ?)",
        (ts, "bitunix_futures", "pa_validation_decision", json.dumps(payload)),
    )
    conn.commit()


# ── pure helpers ─────────────────────────────────────────────────────────


def test_iso_to_unix_ms_round_trip():
    # 2026-05-29T17:51:02Z → known epoch ms
    assert iso_to_unix_ms("2026-05-29T17:51:02+00:00") == 1780077062000
    # Trailing Z variant
    assert iso_to_unix_ms("2026-05-29T17:51:02Z") == 1780077062000
    # Empty / open-trade sentinel
    assert iso_to_unix_ms("") == 0


def test_build_validator_pair_tag():
    assert build_validator_pair_tag(
        ["vwap_alignment", "volume_confirmation"], ["structure_alignment"]
    ) == "V+VOL/-S"
    assert build_validator_pair_tag(
        ["vwap_alignment", "volume_confirmation", "structure_alignment"], []
    ) == "V+VOL+S"
    assert build_validator_pair_tag([], ["volume_confirmation"]) == "-VOL"
    assert build_validator_pair_tag(None, None) == ""


def test_extract_tp_prices_prefers_flat_keys_over_tp_plan():
    extra = {
        "tp1_price": 100.0,
        "tp2_price": 110.0,
        "tp3_price": 120.0,
        "tp_plan": [
            {"leg": "tp1", "price": 999.0},  # should be ignored — flat keys win
            {"leg": "tp2", "price": 999.0},
            {"leg": "tp3", "price": 999.0},
        ],
    }
    assert _extract_tp_prices(extra) == (100.0, 110.0, 120.0)


def test_extract_tp_prices_falls_back_to_tp_plan():
    extra = {
        "tp_plan": [
            {"leg": "tp1", "price": 50.0},
            {"leg": "tp2", "price": 55.0},
            {"leg": "tp3", "price": 60.0},
        ],
    }
    assert _extract_tp_prices(extra) == (50.0, 55.0, 60.0)


def test_extract_tp_prices_missing_legs_yield_zero_sentinels():
    extra = {"tp_plan": [{"leg": "tp1", "price": 50.0}]}
    assert _extract_tp_prices(extra) == (50.0, 0.0, 0.0)


def test_extract_tp_prices_empty_extra_returns_zeros():
    assert _extract_tp_prices({}) == (0.0, 0.0, 0.0)


# ── validator-pair join ──────────────────────────────────────────────────


def test_fetch_validator_pair_hits_decision_within_window(conn):
    _insert_decision(
        conn,
        ts="2026-05-29T17:50:30+00:00",
        trigger_signal="mc_a_redx",
        passed=["vwap_alignment", "volume_confirmation"],
        failed=["structure_alignment"],
    )
    tag = fetch_validator_pair(conn, "mc_a_redx", "2026-05-29T17:51:02+00:00")
    assert tag == "V+VOL/-S"


def test_fetch_validator_pair_outside_window_falls_back_to_trigger(conn):
    # Decision is 1 hour earlier than pt.ts → outside 600s window
    _insert_decision(
        conn,
        ts="2026-05-29T16:51:00+00:00",
        trigger_signal="mc_a_redx",
        passed=["vwap_alignment"],
        failed=[],
    )
    tag = fetch_validator_pair(conn, "mc_a_redx", "2026-05-29T17:51:02+00:00")
    assert tag == "mc_a_redx"  # fallback


def test_fetch_validator_pair_no_decision_row_falls_back_to_trigger(conn):
    tag = fetch_validator_pair(conn, "mc_a_redx", "2026-05-29T17:51:02+00:00")
    assert tag == "mc_a_redx"


def test_fetch_validator_pair_takes_most_recent_in_window(conn):
    # Two decisions both inside window → exporter must take the latest.
    _insert_decision(
        conn, ts="2026-05-29T17:45:00+00:00",
        trigger_signal="mc_a_redx",
        passed=["vwap_alignment"], failed=["structure_alignment"],
    )
    _insert_decision(
        conn, ts="2026-05-29T17:50:30+00:00",
        trigger_signal="mc_a_redx",
        passed=["vwap_alignment", "volume_confirmation"],
        failed=["structure_alignment"],
    )
    tag = fetch_validator_pair(conn, "mc_a_redx", "2026-05-29T17:51:02+00:00")
    assert tag == "V+VOL/-S"  # took the later (more validators passed)


# ── fetch_trades end-to-end ──────────────────────────────────────────────


def test_fetch_trades_v2_multi_leg(conn):
    _insert_trade(
        conn,
        order_id="abcd1234-...",
        ts="2026-05-29T17:51:02+00:00",
        result="win",
        result_ts="2026-05-29T18:23:00+00:00",
        result_price=73826.872,
        actual_r=0.8866,
        actual_pnl=0.06667,
        extra={
            "trigger_signal": "mc_a_redx",
            "tp_plan_version": "v2",
            "tp1_price": 73826.872,
            "tp2_price": 73557.9,
            "tp3_price": 73085.95,
            "tp_plan": [
                {"leg": "tp1", "price": 73826.872},
                {"leg": "tp2", "price": 73557.9},
                {"leg": "tp3", "price": 73085.95},
            ],
        },
    )
    _insert_decision(
        conn, ts="2026-05-29T17:50:00+00:00",
        trigger_signal="mc_a_redx",
        passed=["vwap_alignment", "volume_confirmation"],
        failed=["structure_alignment"],
    )
    trades = fetch_trades(
        conn,
        division="bitunix_futures",
        since_iso="2026-05-01T00:00:00+00:00",
    )
    assert len(trades) == 1
    t = trades[0]
    assert t.tp1_price == 73826.872
    assert t.tp2_price == 73557.9
    assert t.tp3_price == 73085.95
    assert t.result == "win"
    assert t.validator_pair == "V+VOL/-S"


def test_fetch_trades_pre_v2_single_leg_uses_top_level_tp_price(conn):
    _insert_trade(
        conn,
        order_id="legacy01",
        ts="2026-04-15T12:00:00+00:00",
        result="loss",
        result_ts="2026-04-15T12:30:00+00:00",
        result_price=73200.0,
        actual_r=-1.0,
        actual_pnl=-0.1,
        tp_price=72500.0,  # only top-level tp_price; no extra tp_plan / flat keys
        extra={"trigger_signal": "spoon_bear"},
    )
    trades = fetch_trades(
        conn,
        division="bitunix_futures",
        since_iso="2026-04-01T00:00:00+00:00",
    )
    assert len(trades) == 1
    t = trades[0]
    assert t.tp1_price == 72500.0
    assert t.tp2_price == 0.0
    assert t.tp3_price == 0.0
    assert t.validator_pair == "spoon_bear"  # no decision row → trigger fallback


def test_fetch_trades_open_trade_has_sentinels(conn):
    _insert_trade(
        conn,
        order_id="openrow1",
        ts="2026-05-30T10:00:00+00:00",
        result=None,         # open
        result_ts=None,
        result_price=None,
        actual_r=None,
        actual_pnl=None,
        extra={"trigger_signal": "cvd_bull_flip", "tp1_price": 100.0, "tp2_price": 110.0, "tp3_price": 120.0},
    )
    trades = fetch_trades(
        conn,
        division="bitunix_futures",
        since_iso="2026-05-01T00:00:00+00:00",
    )
    assert len(trades) == 1
    t = trades[0]
    assert t.result == "open"
    assert t.result_ts == ""
    assert t.result_price == 0.0
    assert t.actual_r_multiple == 0.0
    assert t.actual_pnl_dollars == 0.0


def test_fetch_trades_filters_by_division(conn):
    _insert_trade(
        conn, order_id="bit01", ts="2026-05-29T10:00:00+00:00",
        division="bitunix_futures", extra={"trigger_signal": "x"},
    )
    _insert_trade(
        conn, order_id="otr01", ts="2026-05-29T10:00:00+00:00",
        division="coinbase_spot", extra={"trigger_signal": "x"},
    )
    trades = fetch_trades(
        conn, division="bitunix_futures",
        since_iso="2026-05-01T00:00:00+00:00",
    )
    assert [t.order_id for t in trades] == ["bit01"]


# ── format_pine_paste_block ──────────────────────────────────────────────


def _trade(**kw) -> Trade:
    defaults = dict(
        order_id="5150fa62-abcd",
        entry_ts="2026-05-29T17:51:02+00:00",
        side="sell",
        entry_price=73960.0,
        sl_price=74132.13546,
        tp1_price=73826.872,
        tp2_price=73557.9,
        tp3_price=73085.95,
        result="win",
        result_ts="2026-05-29T18:23:00+00:00",
        result_price=73826.872,
        actual_r_multiple=0.8866,
        actual_pnl_dollars=0.06667,
        trigger_signal="mc_a_redx",
        validator_pair="V+VOL/-S",
    )
    defaults.update(kw)
    return Trade(**defaults)


def test_format_pine_block_renders_array_declarations():
    block = format_pine_paste_block(
        [_trade()],
        division="bitunix_futures",
        since_iso="2026-04-30T00:00:00+00:00",
        until_iso="2026-05-30T12:00:00+00:00",
        generated_at="2026-05-30T12:00:00+00:00",
    )
    assert "// === BEGIN GENERATED PASTE BLOCK ===" in block
    assert "// === END GENERATED PASTE BLOCK ===" in block
    assert "// Division : bitunix_futures" in block
    assert "// Trades   : 1 (1 win / 0 loss / 0 expired / 0 open)" in block
    assert "var int g_count = 1" in block
    # 2026-05-29T17:51:02 UTC == 1780077062000 ms
    assert "var array<int> g_entry_ts = array.from(1780077062000)" in block
    assert 'var array<string> g_side = array.from("sell")' in block
    assert 'var array<string> g_result = array.from("win")' in block
    assert 'var array<string> g_validator_pair = array.from("V+VOL/-S")' in block
    assert 'var array<string> g_order_id_short = array.from("5150fa62")' in block


def test_format_pine_block_open_trade_emits_zero_result_ts():
    open_t = _trade(
        result="open", result_ts="", result_price=0.0,
        actual_r_multiple=0.0, actual_pnl_dollars=0.0,
    )
    block = format_pine_paste_block(
        [open_t],
        division="bitunix_futures",
        since_iso="2026-05-01T00:00:00+00:00",
        until_iso="2026-05-30T12:00:00+00:00",
        generated_at="2026-05-30T12:00:00+00:00",
    )
    assert "var array<int> g_result_ts = array.from(0)" in block
    assert "// Trades   : 1 (0 win / 0 loss / 0 expired / 1 open)" in block


def test_format_pine_block_empty_corpus_uses_array_new():
    block = format_pine_paste_block(
        [],
        division="bitunix_futures",
        since_iso="2026-05-01T00:00:00+00:00",
        until_iso="2026-05-30T12:00:00+00:00",
        generated_at="2026-05-30T12:00:00+00:00",
    )
    assert "var int g_count = 0" in block
    assert "var array<int> g_entry_ts = array.new<int>()" in block
    assert "var array<string> g_validator_pair = array.new<string>()" in block
    assert "// Trades   : 0 (0 win / 0 loss / 0 expired / 0 open)" in block


def test_format_pine_block_breakdown_counts_each_result():
    trades = [
        _trade(order_id="w1", result="win"),
        _trade(order_id="w2", result="win"),
        _trade(order_id="l1", result="loss"),
        _trade(order_id="x1", result="expired"),
        _trade(order_id="o1", result="open"),
    ]
    block = format_pine_paste_block(
        trades,
        division="bitunix_futures",
        since_iso="2026-05-01T00:00:00+00:00",
        until_iso="2026-05-30T12:00:00+00:00",
        generated_at="2026-05-30T12:00:00+00:00",
    )
    assert "// Trades   : 5 (2 win / 1 loss / 1 expired / 1 open)" in block


def test_format_pine_block_escapes_quotes_in_strings():
    t = _trade(validator_pair='V+"weird"', order_id='abc"def')
    block = format_pine_paste_block(
        [t],
        division="bitunix_futures",
        since_iso="2026-05-01T00:00:00+00:00",
        until_iso="2026-05-30T12:00:00+00:00",
        generated_at="2026-05-30T12:00:00+00:00",
    )
    assert '"V+\\"weird\\""' in block
    assert '"abc\\"def"' in block
