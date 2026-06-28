"""Signed-fetch accurate auto-book (#1, 2026-06-15): book a server-side (B1)
stop close from the REAL exchange fill(s) — VWAP price, summed REAL per-fill
fees, real PnL — replacing P2's optimistic known-level estimate, with the
estimate kept as the fallback safety net.

Covers:
  * `_aggregate_close_fills` — VWAP / summed-fee / summed-qty over N fills (pure);
  * `_autobook_missing_close_real` — single-fill + multi-fill (VWAP) real book;
    real per-fill fee summed (NOT an assumed rate); slippage_unreconciled cleared;
    observed slippage recorded;
  * fallback to the known-level estimate when the signed fetch raises / returns
    no identifiable close fill (the safety net — never leaves a close unbooked);
  * ambiguous (filled_legs) → defers, real fetch NOT attempted;
  * `BitunixBroker.get_recent_close_fills` — filters to exit-side fills, excludes
    side-less / wrong-side / pre-entry fills, returns [] on history error;
  * full-flow integration via `reconcile_position_state` (the call-site wiring).

Mocked + fundless — the signed fetch is always mocked; NO live API call.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions.bitunix_position_reconciler import (
    AUTO_BOOK_SERVER_SIDE_CLOSE_KIND,
    POSITION_STATE_DIVERGENCE_KIND,
    RECONCILER_ACTOR,
    _aggregate_close_fills,
    _autobook_missing_close_real,
    reconcile_position_state,
)
from trading_corp.brokers.bitunix import BitunixBroker
from trading_corp.persistence import db


# ─── fixtures / helpers ──────────────────────────────────────────────────


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'sf.db'}"
    db.init_db(url)
    return url


def _seed_short(db_url, order_id="ord", *, filled_legs=None, stop=66291.075,
                entry=66150.1, qty=0.0007528, symbol="BTC/USDT.P",
                entry_fee=0.0185, mdr=0.212) -> None:
    extra = {
        "execution_mode": "live", "broker_order_id": order_id,
        "filled_legs": filled_legs or [], "stop_price": stop,
        "max_dollar_risk": mdr, "entry_reference_price": entry,
        "entry_fee_usd": entry_fee,
    }
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record (order_id, ts, strategy, division, "
            "symbol, side, qty, entry_reference_price, stop_price, tp_price, "
            "max_hold_seconds, result, extra_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, "2026-06-15T23:49:24+00:00", "bitunix_futures",
             "bitunix_futures", symbol, "sell", qty, entry, stop, 65797.66,
             86400, None, json.dumps(extra)),
        )


def _seed_prior_divergence(db_url, order_id="ord") -> None:
    payload = {"match_count": 0, "missing_on_broker_count": 1,
               "orphan_on_broker_count": 0,
               "missing_on_broker": [{"order_id": order_id, "symbol": "BTC/USDT.P",
                                      "side": "sell", "bot_qty": 0.0007528}],
               "orphan_on_broker": []}
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?,?,?,?)",
            ("2026-06-15T23:50:30+00:00", RECONCILER_ACTOR,
             POSITION_STATE_DIVERGENCE_KIND, json.dumps(payload)),
        )


def _row(db_url, order_id="ord"):
    with db.connect(db_url) as conn:
        r = conn.execute(
            "SELECT result, result_price, actual_pnl_dollars, extra_json "
            "FROM paper_trade_record WHERE order_id=?", (order_id,)).fetchone()
    extra = json.loads(r["extra_json"]) if r and r["extra_json"] else {}
    return r, extra


class _FillBroker:
    """Fake broker exposing only get_recent_close_fills (the signed fetch),
    mocked — NEVER hits a live API."""
    def __init__(self, fills=None, *, raise_=False):
        self._fills = [] if fills is None else fills
        self._raise = raise_
        self.calls = 0

    async def get_recent_close_fills(self, *, symbol, exit_side, since_ms=None):
        self.calls += 1
        if self._raise:
            raise RuntimeError("signed fetch boom")
        return list(self._fills)


_NOW = "2026-06-15T23:51:30+00:00"


# ─── _aggregate_close_fills (pure) ───────────────────────────────────────


def test_aggregate_single_fill():
    a = _aggregate_close_fills([{"price": 100.0, "qty": 2.0, "fee": 0.1}])
    # D3 role-fix (deployed) ADDED keys to the aggregate (close_order_ids,
    # exit_role, fee_implied_role, role_fee_mismatch, maker_taker_mix). Assert the
    # 4 economic keys rather than exact-dict equality so the deployed superset
    # stays green; the role keys are covered by the D3 tests.
    assert (a["vwap_price"], a["total_fee"], a["total_qty"], a["n_fills"]) \
        == (100.0, 0.1, 2.0, 1)


def test_aggregate_multi_fill_vwap():
    a = _aggregate_close_fills([
        {"price": 100.0, "qty": 1.0, "fee": 0.1},
        {"price": 102.0, "qty": 3.0, "fee": 0.3},
    ])
    assert a["vwap_price"] == pytest.approx((100.0 + 306.0) / 4.0)  # 101.5
    assert a["total_fee"] == pytest.approx(0.4)
    assert a["total_qty"] == pytest.approx(4.0)
    assert a["n_fills"] == 2


def test_aggregate_empty_and_skips_malformed():
    assert _aggregate_close_fills([])["n_fills"] == 0
    a = _aggregate_close_fills([
        {"price": 0.0, "qty": 1.0, "fee": 0.1},   # bad price
        {"price": 100.0, "qty": 0.0, "fee": 0.1},  # bad qty
        {"price": 100.0, "qty": 2.0, "fee": 0.2},  # good
    ])
    assert a["n_fills"] == 1 and a["vwap_price"] == pytest.approx(100.0)


# ─── real auto-book (single / multi / fee / slippage) ────────────────────


@pytest.mark.asyncio
async def test_real_book_single_fill(db_url):
    _seed_short(db_url)
    broker = _FillBroker([{"price": 66310.0, "qty": 0.0007528, "fee": 0.0052}])
    assert await _autobook_missing_close_real(broker, db_url, "ord", _NOW) == "booked"
    r, extra = _row(db_url)
    assert r["result"] == "loss"
    assert r["result_price"] == pytest.approx(66310.0)            # REAL fill, not the 66291 stop
    assert r["actual_pnl_dollars"] == pytest.approx((66150.1 - 66310.0) * 0.0007528)
    assert extra["result_source"] == "auto_booked_from_real_fill"
    assert extra["pnl_basis"] == "real_fill"
    assert extra["slippage_unreconciled"] is False               # CLEARED (reconciled)
    assert extra["exit_fee_usd"] == pytest.approx(0.0052)        # real exit fee (was unset in estimate)
    assert extra["exit_side"] == "buy"
    assert extra["close_fill_count"] == 1
    assert extra["observed_slippage_pts"] == pytest.approx(66310.0 - 66291.075)  # +18.9pt adverse
    assert extra["net_realized_usd"] == pytest.approx(
        (66150.1 - 66310.0) * 0.0007528 - 0.0185 - 0.0052)


@pytest.mark.asyncio
async def test_real_book_multi_fill_vwap(db_url):
    _seed_short(db_url)
    broker = _FillBroker([
        {"price": 66300.0, "qty": 0.0003, "fee": 0.002},
        {"price": 66320.0, "qty": 0.0004528, "fee": 0.003},
    ])
    assert await _autobook_missing_close_real(broker, db_url, "ord", _NOW) == "booked"
    r, extra = _row(db_url)
    vwap = (66300.0 * 0.0003 + 66320.0 * 0.0004528) / 0.0007528
    assert r["result_price"] == pytest.approx(vwap)              # VWAP, not a single fill
    assert extra["exit_fee_usd"] == pytest.approx(0.005)         # summed
    assert extra["close_fill_count"] == 2
    assert r["actual_pnl_dollars"] == pytest.approx((66150.1 - vwap) * 0.0007528)


@pytest.mark.asyncio
async def test_real_per_fill_fee_is_summed_not_assumed_rate(db_url):
    """A maker-fee fill + a taker-fee fill → the booked exit fee is the SUM of
    the two REAL per-fill fees, never a single assumed rate × notional."""
    _seed_short(db_url)
    # Close fills sum to the tracked position qty (0.0007528) so the deployed D1
    # netted-close guard (exit_fee = Σfee × closed_qty/q_close) leaves the ratio
    # at 1.0 — isolating THIS test's concern (real summed fee, not assumed rate)
    # from D1's proration, which has its own coverage.
    broker = _FillBroker([
        {"price": 66300.0, "qty": 0.0003764, "fee": 0.001},   # maker-ish (small fee)
        {"price": 66320.0, "qty": 0.0003764, "fee": 0.005},   # taker-ish (larger fee)
    ])
    await _autobook_missing_close_real(broker, db_url, "ord", _NOW)
    _, extra = _row(db_url)
    assert extra["exit_fee_usd"] == pytest.approx(0.006)        # 0.001 + 0.005, the REAL mix
    # sanity: NOT an assumed-rate compute (qty*price*rate would be ~0.02 at 0.04%)
    assert extra["exit_fee_usd"] != pytest.approx(0.0008 * 66310 * 0.0004, rel=1e-6)


# ─── fallback safety net ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_raises_falls_back_to_estimate(db_url):
    _seed_short(db_url)
    broker = _FillBroker(raise_=True)
    assert await _autobook_missing_close_real(broker, db_url, "ord", _NOW) == "booked"
    r, extra = _row(db_url)
    assert r["result"] == "loss"
    assert r["result_price"] == pytest.approx(66291.075)         # the STOP LEVEL estimate
    assert extra["result_source"] == "auto_booked_from_stop_level"
    assert extra["slippage_unreconciled"] is True               # still unreconciled (estimate)


@pytest.mark.asyncio
async def test_fetch_empty_falls_back_to_estimate(db_url):
    _seed_short(db_url)
    broker = _FillBroker([])                                     # no identifiable close fill
    assert await _autobook_missing_close_real(broker, db_url, "ord", _NOW) == "booked"
    r, extra = _row(db_url)
    assert r["result_price"] == pytest.approx(66291.075)
    assert extra["result_source"] == "auto_booked_from_stop_level"


@pytest.mark.asyncio
async def test_ambiguous_filled_legs_defers_without_fetch(db_url):
    _seed_short(db_url, filled_legs=["tp1"])
    broker = _FillBroker([{"price": 66310.0, "qty": 0.0007528, "fee": 0.0052}])
    assert await _autobook_missing_close_real(broker, db_url, "ord", _NOW) == "deferred"
    r, extra = _row(db_url)
    assert r["result"] is None                                  # DEFERRED, not booked
    assert extra.get("autobook_deferred") == "partial_tp_ambiguous"
    assert broker.calls == 0                                    # real fetch NOT attempted


# ─── broker method: get_recent_close_fills filtering ─────────────────────


def _broker_with_history(trades, *, raise_=False):
    b = BitunixBroker(api_key="k", api_secret="s")
    b._client = MagicMock()
    if raise_:
        b.get_history_trades = AsyncMock(side_effect=RuntimeError("hist boom"))
    else:
        b.get_history_trades = AsyncMock(return_value=trades)
    return b


@pytest.mark.asyncio
async def test_get_recent_close_fills_filters_to_exit_side():
    b = _broker_with_history([
        {"side": "BUY", "price": 66310.0, "qty": 0.0004, "fee": 0.002},   # close (exit)
        {"side": "SELL", "price": 66150.0, "qty": 0.0007, "fee": 0.003},  # entry — excluded
        {"side": "BUY", "price": 66320.0, "qty": 0.0003, "fee": 0.001},   # close (exit)
        {"price": 1.0, "qty": 1.0, "fee": 1.0},                           # no side — excluded
    ])
    fills = await b.get_recent_close_fills(symbol="BTC/USDT.P", exit_side="buy")
    assert len(fills) == 2
    assert {f["price"] for f in fills} == {66310.0, 66320.0}
    assert all("fee" in f and "qty" in f for f in fills)


@pytest.mark.asyncio
async def test_get_recent_close_fills_since_ms_excludes_pre_entry():
    b = _broker_with_history([
        {"side": "BUY", "price": 66310.0, "qty": 0.0004, "fee": 0.002, "ctime": 2000},
        {"side": "BUY", "price": 60000.0, "qty": 0.0004, "fee": 0.002, "ctime": 500},  # old close
    ])
    fills = await b.get_recent_close_fills(symbol="BTC/USDT.P", exit_side="buy",
                                           since_ms=1000)
    assert len(fills) == 1 and fills[0]["price"] == 66310.0


@pytest.mark.asyncio
async def test_get_recent_close_fills_history_error_returns_empty():
    b = _broker_with_history([], raise_=True)
    assert await b.get_recent_close_fills(symbol="BTC/USDT.P", exit_side="buy") == []


# ─── full-flow integration (call-site wiring) ────────────────────────────


@pytest.mark.asyncio
async def test_full_flow_books_real_fill_via_reconciler(db_url):
    _seed_short(db_url)
    _seed_prior_divergence(db_url)                              # 2nd consecutive missing → confirm
    broker = BitunixBroker(api_key="k", api_secret="s")
    broker._client = MagicMock()
    broker._request = AsyncMock(return_value=[])               # broker flat → bot short missing
    broker.get_recent_close_fills = AsyncMock(
        return_value=[{"price": 66310.0, "qty": 0.0007528, "fee": 0.0052}])
    result = await reconcile_position_state(broker, db_url)
    r, extra = _row(db_url)
    assert r["result"] == "loss"
    assert r["result_price"] == pytest.approx(66310.0)         # REAL fill booked via the reconciler
    assert extra["result_source"] == "auto_booked_from_real_fill"
    assert not result.has_divergence                           # missing resolved this tick
    broker.get_recent_close_fills.assert_awaited_once()
    with db.connect(db_url) as conn:
        kinds = [row["kind"] for row in conn.execute(
            "SELECT kind FROM audit_event WHERE actor=?", (RECONCILER_ACTOR,)).fetchall()]
    assert AUTO_BOOK_SERVER_SIDE_CLOSE_KIND in kinds
