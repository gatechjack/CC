"""phantom-legs fix (2026-06-22) — the paper bar-walk replay must SKIP
bracket-managed LIVE rows. Those are owned by the venue bracket + the
reconciler (venue-truth fills, SL trail, auto-book). Running the bar-walk on
them simulated TP fills and wrote PHANTOM `filled_legs`, which stalled the
auto-book (`partial_tp_ambiguous`) → hours-long stuck/halted engine
(89966d01 & 2a53de19, 2026-06-22), plus false `would_call_broker:false` SL
telemetry.

Covers:
  1. a bracket-managed live row is SKIPPED — no classify, no phantom
     `filled_legs`, no `position_sl_update`, `result` stays NULL for the
     reconciler; counted as `skipped_bracket_managed_live`.
  2. trade-2/3 geometry (bars that WOULD simulate tp1+tp2) → still no phantom
     `filled_legs` on the live row.
  3. a PAPER v2 row is still bar-walked (filled_legs / result populated) —
     paper behavior unchanged.
  4. a LIVE row WITHOUT bracket markers is NOT skipped (the guard is precise).

Mocked + fundless — synthetic bars; NO live API.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_corp.agents.paper_trade_replay import (
    replay_pending_paper_trades_async,
)
from trading_corp.persistence.db import connect, init_db, insert_paper_trade_record
from trading_corp.persistence.models import PaperTradeRecord

_TS = "2026-06-22T00:00:00+00:00"
# short geometry: entry 64000, tps below, stop above
_ENTRY, _STOP = 64000.0, 64200.0
_TP1, _TP2, _TP3 = 63900.0, 63800.0, 63700.0


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'pl.db'}"
    init_db(url)
    return url


def _tp_plan():
    return [
        {"leg": "tp1", "fraction": 0.25, "target_r": 0.5, "price": _TP1,
         "stop_action": "move_to_breakeven"},
        {"leg": "tp2", "fraction": 0.5, "target_r": 1.0, "price": _TP2,
         "stop_action": "move_to_tp1"},
        {"leg": "tp3", "fraction": 0.25, "target_r": 2.0, "price": _TP3,
         "stop_action": "trail_atr"},
    ]


def _seed(db_url, oid, *, is_live: bool, has_bracket: bool):
    extra = {
        "tp_plan": _tp_plan(), "tp_plan_version": "v2",
        "tp1_price": _TP1, "tp2_price": _TP2, "tp3_price": _TP3,
        "stop_price": _STOP, "filled_legs": [],
    }
    if is_live:
        extra["execution_mode"] = "live"
        extra["broker_order_id"] = oid
        extra["bracket_entry_qty"] = 0.0009
    if has_bracket:
        extra["bracket_tp_order_ids"] = {"tp3": "5731"}
        extra["bracket_position_sl_order_id"] = "7722"
    rec = PaperTradeRecord(
        order_id=oid, ts=_TS, strategy="bitunix_futures",
        division="bitunix_futures", symbol="BTC/USDT.P", side="sell", qty=0.0009,
        tier="STANDARD", source_signal="mc_a_redx",
        entry_reference_price=_ENTRY, stop_price=_STOP, tp_price=_TP3,
        tp_r_multiple=2.0, max_hold_seconds=86400, extra=extra,
    )
    insert_paper_trade_record(rec.to_db_row(), db_url=db_url)


async def _tp1_tp2_fetcher(symbol, timeframe, since_ms, limit):
    # one bar whose LOW reaches tp2 (63800) — would fill tp1+tp2 for a short if
    # the bar-walk ran — but not tp3 (63700) or the stop (64200).
    return [[since_ms, 63950.0, 63990.0, 63780.0, 63850.0, 0.0]]


def _row(db_url, oid):
    with connect(db_url) as conn:
        r = conn.execute(
            "SELECT result, extra_json FROM paper_trade_record WHERE order_id=?",
            (oid,)).fetchone()
    extra = json.loads(r["extra_json"]) if r and r["extra_json"] else {}
    return r, extra


def _sl_update_count(db_url, oid):
    with connect(db_url) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM audit_event WHERE kind='position_sl_update' "
            "AND payload_json LIKE ?", (f"%{oid}%",)).fetchone()[0]


# ─── 1 + 2. bracket-managed live row is skipped, no phantom legs ──────────


@pytest.mark.asyncio
async def test_bracket_managed_live_row_is_skipped(db_url):
    _seed(db_url, "live_bracket", is_live=True, has_bracket=True)
    counts = await replay_pending_paper_trades_async(
        db_url, ohlcv_fetcher=_tp1_tp2_fetcher)

    assert counts.get("skipped_bracket_managed_live") == 1
    assert counts.get("resolved_win", 0) == 0 and counts.get("resolved_loss", 0) == 0
    r, extra = _row(db_url, "live_bracket")
    # result left NULL for the reconciler to auto-book from venue truth.
    assert r["result"] is None
    # NO phantom filled_legs written by the bar-walk (the bug).
    assert extra.get("filled_legs") == []
    # NO false SL telemetry emitted.
    assert _sl_update_count(db_url, "live_bracket") == 0


@pytest.mark.asyncio
async def test_live_bracket_no_phantom_even_with_tp_hitting_bars(db_url):
    # the trade-2/3 geometry: bars that WOULD have produced filled_legs=[tp1,tp2].
    _seed(db_url, "t3", is_live=True, has_bracket=True)
    await replay_pending_paper_trades_async(db_url, ohlcv_fetcher=_tp1_tp2_fetcher)
    _, extra = _row(db_url, "t3")
    assert extra.get("filled_legs") == []          # NOT [tp1, tp2]
    assert "current_sl" not in extra               # no phantom SL trail persisted


# ─── 3. paper v2 row still walked (unchanged) ────────────────────────────


@pytest.mark.asyncio
async def test_paper_v2_row_still_bar_walked(db_url):
    # paper (no execution_mode=live, no bracket) — the bar-walk MUST still run.
    _seed(db_url, "paper", is_live=False, has_bracket=False)
    counts = await replay_pending_paper_trades_async(
        db_url, ohlcv_fetcher=_tp1_tp2_fetcher)

    assert counts.get("skipped_bracket_managed_live", 0) == 0   # not skipped
    _, extra = _row(db_url, "paper")
    # the bar-walk simulated tp1+tp2 fills (paper behavior preserved).
    assert extra.get("filled_legs") == ["tp1", "tp2"]


# ─── 4. live row WITHOUT bracket markers is NOT skipped ───────────────────


@pytest.mark.asyncio
async def test_live_row_without_bracket_markers_not_skipped(db_url):
    # live but no bracket order ids → the precise guard must NOT skip it
    # (it falls through to the existing live-row handling).
    _seed(db_url, "live_nobracket", is_live=True, has_bracket=False)
    counts = await replay_pending_paper_trades_async(
        db_url, ohlcv_fetcher=_tp1_tp2_fetcher)
    assert counts.get("skipped_bracket_managed_live", 0) == 0
