"""D1 — netted-close PnL double-booking fix (2026-06-21).

The real-fill auto-book (`_autobook_missing_close_real`) must attribute only
THIS record's share of a server-side netted close, capped at the close qty:

    closed_qty   = min(record_qty, netted_close_qty)
    pnl_i        = (entry_i - vwap) * closed_qty      (sign by side)
    exit_fee_i   = total_fee * (closed_qty / netted_close_qty)

so that when several stacked records share ONE netted close, the per-record
PnL/fee sum to the netted close ONCE — not N times over.

Covers the 4 required cases:
  1. BYTE-UNCHANGED single record incl. a normal fill gap (regression guard):
     replay the e9c35907 geometry (recorded 0.00095, closed 0.0009) — D1 books
     +0.2389 (on the CLOSED qty, == prior behavior), NOT +0.2522 (raw record qty).
  2. STACKED / netted: two records share one netted close → per-record qty-
     weighted PnL/fee, summing to the single netted close, NOT 2x.
  3. IDEMPOTENCY: a second auto-book of an already-booked record is a no-op
     (WHERE result IS NULL) — PnL not doubled, only one audit row.
  4. FLAG threshold: a normal (~5%) fill gap does NOT flag; a record qty that
     grossly exceeds the netted close (>= 1.5x) FLAGS (log.warning) but still
     books safely (capped at the close qty) — never defers / crashes.

Mocked + fundless — the signed fetch is always mocked; NO live API call.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from trading_corp.agents.divisions.bitunix_position_reconciler import (
    AUTO_BOOK_SERVER_SIDE_CLOSE_KIND,
    D1_QTY_ANOMALY_RATIO,
    RECONCILER_ACTOR,
    _autobook_missing_close_real,
)
from trading_corp.persistence import db

RECONCILER_LOGGER = "trading_corp.agents.divisions.bitunix_position_reconciler"
_NOW = "2026-06-21T23:51:30+00:00"


# ─── fixtures / helpers ──────────────────────────────────────────────────


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'd1.db'}"
    db.init_db(url)
    return url


def _seed_short(db_url, order_id, *, qty, entry, stop, symbol="BTC/USDT.P",
                entry_fee=0.0, mdr=0.212) -> None:
    """A live, unbooked short awaiting a server-side close (no filled_legs)."""
    extra = {
        "execution_mode": "live", "broker_order_id": order_id,
        "filled_legs": [], "stop_price": stop,
        "max_dollar_risk": mdr, "entry_reference_price": entry,
        "entry_fee_usd": entry_fee,
    }
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record (order_id, ts, strategy, division, "
            "symbol, side, qty, entry_reference_price, stop_price, tp_price, "
            "max_hold_seconds, result, extra_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, "2026-06-21T23:49:24+00:00", "bitunix_futures",
             "bitunix_futures", symbol, "sell", qty, entry, stop, None,
             86400, None, json.dumps(extra)),
        )


class _FillBroker:
    """Fake broker returning ONE netted close (the same fills to every record
    that shares it) — mocked, NEVER hits a live API."""
    def __init__(self, fills):
        self._fills = list(fills)
        self.calls = 0

    async def get_recent_close_fills(self, *, symbol, exit_side, since_ms=None):
        self.calls += 1
        return list(self._fills)


def _row(db_url, order_id):
    with db.connect(db_url) as conn:
        r = conn.execute(
            "SELECT result, result_price, actual_pnl_dollars, extra_json "
            "FROM paper_trade_record WHERE order_id=?", (order_id,)).fetchone()
    extra = json.loads(r["extra_json"]) if r and r["extra_json"] else {}
    return r, extra


def _autobook_audits(db_url, order_id):
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event WHERE actor=? AND kind=? "
            "ORDER BY id ASC", (RECONCILER_ACTOR, AUTO_BOOK_SERVER_SIDE_CLOSE_KIND),
        ).fetchall()
    out = []
    for row in rows:
        p = json.loads(row["payload_json"])
        if p.get("order_id") == order_id:
            out.append(p)
    return out


# ─── 1. BYTE-UNCHANGED single record incl. fill gap (regression guard) ───
#
# e9c35907 geometry: the record asked for 0.00095 but the venue netted-closed
# 0.0009. Price delta chosen so the booked PnL on the CLOSED qty is +0.2389
# (what the deployed full-close-qty book produced) — NOT +0.2522, the value the
# naive `(entry-vwap)*record_qty` attribution would wrongly produce. min() caps
# the recorded qty at the closed qty, so this stays byte-unchanged.

_ENTRY = 64265.444444444445   # delta to VWAP = 265.4444.. → *0.0009 == 0.2389
_VWAP = 64000.0
_REQ_QTY = 0.00095            # recorded / requested
_CLOSE_QTY = 0.0009           # actually closed (netted)


@pytest.mark.asyncio
async def test_byte_unchanged_single_record_with_fill_gap(db_url):
    _seed_short(db_url, "e9c35907", qty=_REQ_QTY, entry=_ENTRY, stop=64600.0)
    broker = _FillBroker([{"price": _VWAP, "qty": _CLOSE_QTY, "fee": 0.003}])

    assert await _autobook_missing_close_real(broker, db_url, "e9c35907", _NOW) \
        == "booked"
    r, extra = _row(db_url, "e9c35907")

    expected_min = (_ENTRY - _VWAP) * _CLOSE_QTY   # +0.2389  (CLOSED qty)
    expected_raw = (_ENTRY - _VWAP) * _REQ_QTY     # +0.2522  (record qty — WRONG)

    # books on the CLOSED qty (min) == prior deployed behavior — byte-unchanged.
    assert r["actual_pnl_dollars"] == pytest.approx(expected_min)
    assert r["actual_pnl_dollars"] == pytest.approx(0.2389, abs=5e-5)
    # and emphatically NOT the raw-record-qty over-book.
    assert r["actual_pnl_dollars"] != pytest.approx(expected_raw, abs=1e-4)
    assert r["actual_pnl_dollars"] != pytest.approx(0.2522, abs=5e-5)
    # full fee booked (closed_qty == netted close qty → fee share == 1.0).
    assert extra["exit_fee_usd"] == pytest.approx(0.003)
    # audit records the attributed (capped) qty + the netted close qty.
    (audit,) = _autobook_audits(db_url, "e9c35907")
    assert audit["qty"] == pytest.approx(_CLOSE_QTY)
    assert audit["netted_close_qty"] == pytest.approx(_CLOSE_QTY)


# ─── 2. STACKED / netted: per-record qty-weighted, sum == close once ─────


@pytest.mark.asyncio
async def test_stacked_records_sum_to_netted_close_not_n_times(db_url):
    # Two bot positions netted into ONE server-side close of 0.0009 @ 64000,
    # total exit fee 0.006. Each has its OWN entry + qty (sum == close qty).
    entry_a, qty_a = 64265.444444444445, 0.0005
    entry_b, qty_b = 64300.0, 0.0004
    close_qty, total_fee, vwap = 0.0009, 0.006, 64000.0
    _seed_short(db_url, "stackA", qty=qty_a, entry=entry_a, stop=64600.0)
    _seed_short(db_url, "stackB", qty=qty_b, entry=entry_b, stop=64600.0)
    # The SAME netted close is what the signed fetch returns to each record.
    broker = _FillBroker([{"price": vwap, "qty": close_qty, "fee": total_fee}])

    assert await _autobook_missing_close_real(broker, db_url, "stackA", _NOW) \
        == "booked"
    assert await _autobook_missing_close_real(broker, db_url, "stackB", _NOW) \
        == "booked"
    ra, ea = _row(db_url, "stackA")
    rb, eb = _row(db_url, "stackB")

    # Per-record PnL is qty-weighted by each record's OWN entry + OWN qty.
    pnl_a = (entry_a - vwap) * qty_a
    pnl_b = (entry_b - vwap) * qty_b
    assert ra["actual_pnl_dollars"] == pytest.approx(pnl_a)
    assert rb["actual_pnl_dollars"] == pytest.approx(pnl_b)

    # Each record is NOT booked on the full netted close qty (the old double-book).
    assert ra["actual_pnl_dollars"] != pytest.approx((entry_a - vwap) * close_qty)

    # Exit fee is split by qty share and sums to the single netted fee — not 2x.
    assert ea["exit_fee_usd"] == pytest.approx(total_fee * qty_a / close_qty)
    assert eb["exit_fee_usd"] == pytest.approx(total_fee * qty_b / close_qty)
    assert ea["exit_fee_usd"] + eb["exit_fee_usd"] == pytest.approx(total_fee)

    # The attributed qty across the stack sums to the netted close ONCE.
    (aud_a,) = _autobook_audits(db_url, "stackA")
    (aud_b,) = _autobook_audits(db_url, "stackB")
    assert aud_a["qty"] + aud_b["qty"] == pytest.approx(close_qty)
    assert aud_a["netted_close_qty"] == pytest.approx(close_qty)


# ─── 3. IDEMPOTENCY: re-run does not double-correct ──────────────────────


@pytest.mark.asyncio
async def test_rerun_does_not_double_book(db_url):
    _seed_short(db_url, "idem", qty=_REQ_QTY, entry=_ENTRY, stop=64600.0)
    broker = _FillBroker([{"price": _VWAP, "qty": _CLOSE_QTY, "fee": 0.003}])

    assert await _autobook_missing_close_real(broker, db_url, "idem", _NOW) \
        == "booked"
    r1, _ = _row(db_url, "idem")
    pnl_first = r1["actual_pnl_dollars"]
    assert r1["result"] is not None  # booked → result set, no longer NULL

    # Second pass: the row's result is no longer NULL → SELECT ... result IS NULL
    # returns nothing → 'skipped', PnL unchanged, NO second audit row.
    assert await _autobook_missing_close_real(broker, db_url, "idem", _NOW) \
        == "skipped"
    r2, _ = _row(db_url, "idem")
    assert r2["actual_pnl_dollars"] == pytest.approx(pnl_first)  # not doubled
    assert len(_autobook_audits(db_url, "idem")) == 1            # booked exactly once


# ─── 4. FLAG threshold: normal gap quiet, gross qty flags but still books ─


@pytest.mark.asyncio
async def test_normal_fill_gap_does_not_flag(db_url, caplog):
    # ~5.5% gap (0.00095 vs 0.0009, ratio 1.056) — well under the 1.5x flag.
    assert _REQ_QTY / _CLOSE_QTY < D1_QTY_ANOMALY_RATIO
    _seed_short(db_url, "normgap", qty=_REQ_QTY, entry=_ENTRY, stop=64600.0)
    broker = _FillBroker([{"price": _VWAP, "qty": _CLOSE_QTY, "fee": 0.003}])
    with caplog.at_level(logging.WARNING, logger=RECONCILER_LOGGER):
        assert await _autobook_missing_close_real(broker, db_url, "normgap", _NOW) \
            == "booked"
    assert "grossly exceeds netted close" not in caplog.text


@pytest.mark.asyncio
async def test_gross_qty_excess_flags_but_still_books_capped(db_url, caplog):
    # Record qty 0.002 vs netted close 0.0009 (ratio 2.22 >= 1.5) — a data error.
    gross_qty, close_qty, vwap = 0.002, 0.0009, 64000.0
    assert gross_qty / close_qty >= D1_QTY_ANOMALY_RATIO
    _seed_short(db_url, "grossqty", qty=gross_qty, entry=_ENTRY, stop=64600.0)
    broker = _FillBroker([{"price": vwap, "qty": close_qty, "fee": 0.003}])

    with caplog.at_level(logging.WARNING, logger=RECONCILER_LOGGER):
        # FLAGS but still BOOKS (does not defer / crash) — capped at the close qty.
        assert await _autobook_missing_close_real(broker, db_url, "grossqty", _NOW) \
            == "booked"
    assert "grossly exceeds netted close" in caplog.text

    r, extra = _row(db_url, "grossqty")
    # PnL booked on the CAPPED qty (close qty), NOT the oversized record qty.
    assert r["actual_pnl_dollars"] == pytest.approx((_ENTRY - vwap) * close_qty)
    assert r["actual_pnl_dollars"] != pytest.approx((_ENTRY - vwap) * gross_qty)
    assert extra["exit_fee_usd"] == pytest.approx(0.003)  # full fee (capped == close)
    (audit,) = _autobook_audits(db_url, "grossqty")
    assert audit["qty"] == pytest.approx(close_qty)        # attributed = capped
    assert audit["netted_close_qty"] == pytest.approx(close_qty)


# ─── 5. OVER-FETCH: netted close EXCEEDS the record qty (Q_close > q_i) ───
#
# REACHABILITY — proven by code trace, NOT assumed:
#   brokers/bitunix.py get_recent_close_fills() is scoped by symbol + exit_side
#   + since_ms (a LOWER time bound only) — never by record qty / position id /
#   the record's own order ids.  _aggregate_close_fills() sums total_qty over ALL
#   returned fills with no cap. reconcile_position_state()'s missing-loop calls
#   the auto-book ONCE PER missing record. So when N stacked same-side records
#   net into ONE venue close, every record's fetch returns the FULL netted close
#   → Q_close > that record's q_i. Same unscoped-fetch root cause as the double-
#   book, in the qty direction; it ALREADY occurred live (125b6f9e/81f5427a:
#   q_i ~0.0014 each, Q_close 0.0028). Reachable, not a dead path.
#
# Intended economics: a record is NEVER credited more PnL than its OWN size
# traded. D1 caps at q_i. The OLD code booked the full Q_close → over-credited.


@pytest.mark.asyncio
async def test_overfetch_close_exceeds_record_caps_at_record_qty(db_url, caplog):
    # One record of 0.0005, but the (unscoped) close fetch returns 0.0009 of
    # exit-side fills — e.g. the netted close of a stacked pair, or an over-fetch
    # pulling an unrelated same-side close inside the since_ms window.
    rec_qty, q_close, vwap, total_fee = 0.0005, 0.0009, 64000.0, 0.006
    _seed_short(db_url, "overfetch", qty=rec_qty, entry=_ENTRY, stop=64600.0)
    broker = _FillBroker([{"price": vwap, "qty": q_close, "fee": total_fee}])

    with caplog.at_level(logging.WARNING, logger=RECONCILER_LOGGER):
        assert await _autobook_missing_close_real(broker, db_url, "overfetch", _NOW) \
            == "booked"
    r, extra = _row(db_url, "overfetch")

    d1_pnl = (_ENTRY - vwap) * rec_qty     # D1: capped at the record's OWN qty
    old_pnl = (_ENTRY - vwap) * q_close    # OLD: booked the full netted close

    # D1 books on q_i, NOT Q_close — never credits more PnL than its own size.
    assert r["actual_pnl_dollars"] == pytest.approx(d1_pnl)
    assert r["actual_pnl_dollars"] != pytest.approx(old_pnl)
    # strictly more conservative/correct than OLD: |D1| < |OLD over-credit|,
    # and exactly the record's qty share of it.
    assert abs(r["actual_pnl_dollars"]) < abs(old_pnl)
    assert r["actual_pnl_dollars"] == pytest.approx(old_pnl * rec_qty / q_close)
    # exit fee scaled to the record's share, < the full netted fee.
    assert extra["exit_fee_usd"] == pytest.approx(total_fee * rec_qty / q_close)
    assert extra["exit_fee_usd"] < total_fee
    # over-fetch (record < close) is the NORMAL stacked direction → NOT flagged
    # (the >1.5x flag only catches record >> close, the stale/duplicate case).
    assert "grossly exceeds netted close" not in caplog.text
    # audit records the attributed (capped) qty AND the larger netted close qty,
    # so the over-fetch is visible in the trail.
    (audit,) = _autobook_audits(db_url, "overfetch")
    assert audit["qty"] == pytest.approx(rec_qty)
    assert audit["netted_close_qty"] == pytest.approx(q_close)
    assert audit["netted_close_qty"] > audit["qty"]


@pytest.mark.asyncio
async def test_overfetch_extreme_ratio_still_not_flagged(db_url, caplog):
    # A tiny record against a huge netted close (0.0001 vs 0.0028 = 28x) — the
    # real 125b6f9e/81f5427a shape pushed to an extreme. Still the normal stacked
    # direction (record < close): capped at q_i, NOT flagged (record is not the
    # one claiming to be oversized).
    rec_qty, q_close, vwap, total_fee = 0.0001, 0.0028, 63386.63, 0.02
    _seed_short(db_url, "tinyrec", qty=rec_qty, entry=_ENTRY, stop=64600.0)
    broker = _FillBroker([{"price": vwap, "qty": q_close, "fee": total_fee}])
    with caplog.at_level(logging.WARNING, logger=RECONCILER_LOGGER):
        assert await _autobook_missing_close_real(broker, db_url, "tinyrec", _NOW) \
            == "booked"
    r, extra = _row(db_url, "tinyrec")
    assert r["actual_pnl_dollars"] == pytest.approx((_ENTRY - vwap) * rec_qty)
    assert extra["exit_fee_usd"] == pytest.approx(total_fee * rec_qty / q_close)
    assert "grossly exceeds netted close" not in caplog.text
