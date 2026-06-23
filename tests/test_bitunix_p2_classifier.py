"""P2 classifier sign-bug fix + maker/taker recording + mc_a_yellow_x
declassification (report 2026-06-19_p2_classifier_signbug_diagnosis).

Covers:
  * classify_result — result from the NET (else gross) PnL sign, never a literal;
  * classify_exit_kind — tp/stop from the actual fill (order-id match → price
    inference), 'unknown' when ambiguous (NEVER defaulting to 'stop');
  * _aggregate_close_fills — maker/taker role mix + close order-ids;
  * _autobook_missing_close_real — a positive-PnL close books result='win',
    exit_kind='tp', exit_role recorded, PnL VALUE unchanged;
  * _fill_price_from_history — entry maker/taker role threaded onto the fill;
  * mc_a_yellow_x — no longer a directional factor (config), scorer gives an
    unlisted signal 0 directional points.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from trading_corp.agents.divisions.bitunix_bracket import (
    classify_exit_kind,
    classify_result,
)
from trading_corp.agents.divisions.bitunix_position_reconciler import (
    _aggregate_close_fills,
    _autobook_missing_close_real,
    _role_summary,
)
from trading_corp.brokers.bitunix import BitunixBroker
from trading_corp.persistence import db


# ════════════════════════════════════════════════════════════════════════════
# classify_result — NET basis, never a literal
# ════════════════════════════════════════════════════════════════════════════

def test_classify_result_net_basis_and_zero():
    assert classify_result(net_pnl=0.024, gross_pnl=0.03) == "win"
    assert classify_result(net_pnl=-0.06, gross_pnl=0.01) == "loss"   # net<0 beats gross>0
    assert classify_result(net_pnl=0.0, gross_pnl=0.5) == "loss"      # zero net → loss
    # net absent (estimate path) → gross basis
    assert classify_result(net_pnl=None, gross_pnl=0.5) == "win"
    assert classify_result(net_pnl=None, gross_pnl=-0.5) == "loss"


def test_classify_result_the_two_mis_signed_records_book_win():
    # The exact records the bug mis-signed (PnL values are correct/positive).
    assert classify_result(net_pnl=0.02443, gross_pnl=0.03489) == "win"   # e1758fc9
    assert classify_result(net_pnl=0.26776, gross_pnl=0.29822) == "win"   # 7d1a78dc


# ════════════════════════════════════════════════════════════════════════════
# classify_exit_kind — from the actual fill; ambiguous → 'unknown' not 'stop'
# ════════════════════════════════════════════════════════════════════════════

def test_exit_kind_order_id_match_wins():
    assert classify_exit_kind(
        side="sell", vwap_fill=0, stop_level=0, tp_prices=[],
        close_order_ids=["A", "B"], tp_order_ids=["B"], sl_order_id="Z") == "tp"
    assert classify_exit_kind(
        side="sell", vwap_fill=0, stop_level=0, tp_prices=[],
        close_order_ids=["Z"], tp_order_ids=["B"], sl_order_id="Z") == "stop"


def test_exit_kind_price_inference_short_records():
    # e1758fc9: fill sits on tp1
    assert classify_exit_kind(side="sell", vwap_fill=64478.8, stop_level=64754.20,
                              tp_prices=[64478.83, 64456.99, 64197.36]) == "tp"
    # 7d1a78dc: favorable past tp1/tp2
    assert classify_exit_kind(side="sell", vwap_fill=63094.97, stop_level=63747.76,
                              tp_prices=[63406.66, 63294.24, 62954.10]) == "tp"
    # cb6b4d4a: at the stop
    assert classify_exit_kind(side="sell", vwap_fill=62858.2, stop_level=62858.02,
                              tp_prices=[62532.04, 62431.58, 62111.74]) == "stop"


def test_exit_kind_ambiguous_is_unknown_never_stop():
    # short, favorable (below entry) but short of tp1 and not near the stop
    assert classify_exit_kind(side="sell", vwap_fill=63450.0, stop_level=63747.76,
                              tp_prices=[63406.66, 63294.24, 62954.10]) == "unknown"


def test_exit_kind_stop_without_tp_prices():
    # No TP levels available, but the fill is at/beyond the stop → 'stop'
    # (the stop check is independent of TP presence).
    assert classify_exit_kind(side="sell", vwap_fill=66310.0, stop_level=66291.075,
                              tp_prices=[]) == "stop"
    # favorable, no levels at all → 'unknown' (never default 'stop')
    assert classify_exit_kind(side="sell", vwap_fill=66000.0, stop_level=0,
                              tp_prices=[]) == "unknown"


def test_exit_kind_long_side():
    assert classify_exit_kind(side="buy", vwap_fill=101.0, stop_level=95.0,
                              tp_prices=[100.0, 102.0]) == "tp"
    assert classify_exit_kind(side="buy", vwap_fill=95.0, stop_level=95.0,
                              tp_prices=[100.0, 102.0]) == "stop"
    assert classify_exit_kind(side="buy", vwap_fill=98.0, stop_level=95.0,
                              tp_prices=[100.0, 102.0]) == "unknown"


# ════════════════════════════════════════════════════════════════════════════
# _aggregate_close_fills — maker/taker mix + order ids
# ════════════════════════════════════════════════════════════════════════════

def test_role_summary():
    assert _role_summary(0, 0) == "unknown"
    assert _role_summary(1, 0) == "maker"
    assert _role_summary(0, 1) == "taker"
    assert _role_summary(1, 1) == "mixed"


def test_aggregate_records_role_mix_and_order_ids():
    # D3: role now from ORDER SEMANTICS — the TP-leg order-id is maker, the SL
    # order-id is taker (the venue `role`/roleType field is ignored).
    agg = _aggregate_close_fills(
        [
            {"price": 100.0, "qty": 1.0, "fee": 0.1, "order_id": "o1"},
            {"price": 100.0, "qty": 3.0, "fee": 0.3, "order_id": "o2"},
        ],
        tp_order_ids=["o1"], sl_order_id="o2",
    )
    assert agg["n_fills"] == 2
    assert agg["close_order_ids"] == ["o1", "o2"]
    assert agg["exit_role"] == "mixed"
    assert agg["maker_taker_mix"]["maker_qty"] == 1.0
    assert agg["maker_taker_mix"]["taker_qty"] == 3.0
    assert agg["maker_taker_mix"]["maker_fraction"] == pytest.approx(0.25)
    # the fee is still the REAL summed per-fill fee (unchanged)
    assert agg["total_fee"] == pytest.approx(0.4)


def test_aggregate_no_role_and_no_fee_degrades():
    # D3: no order-id match AND no fee → no positive role evidence → 'unknown'
    # (the killed maker-default; roleType is no longer consulted).
    agg = _aggregate_close_fills([{"price": 100.0, "qty": 1.0, "fee": 0.0}])
    assert agg["exit_role"] == "unknown"
    assert agg["close_order_ids"] == []
    assert agg["maker_taker_mix"]["maker_fraction"] is None


# ════════════════════════════════════════════════════════════════════════════
# _autobook_missing_close_real — the end-to-end fix (win/tp; loss/stop)
# ════════════════════════════════════════════════════════════════════════════

class _FillBroker:
    def __init__(self, fills):
        self._fills = fills

    async def get_recent_close_fills(self, *, symbol, exit_side, since_ms=None):
        return list(self._fills)


def _seed(db_url, order_id, *, side, entry, stop, qty, fill_legs=None):
    extra = {
        "execution_mode": "live", "broker_order_id": order_id,
        "filled_legs": fill_legs or [], "stop_price": stop,
        "max_dollar_risk": 0.12, "entry_reference_price": entry,
        "entry_fee_usd": 0.0185,
        "tp1_price": 64478.83, "tp2_price": 64456.99, "tp3_price": 64197.36,
        "bracket_tp_order_ids": {"tp1": "TPID1"},
        "bracket_position_sl_order_id": "SLID9",
    }
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record (order_id, ts, strategy, division, "
            "symbol, side, qty, entry_reference_price, stop_price, tp_price, "
            "max_hold_seconds, result, extra_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, "2026-06-18T01:39:57+00:00", "bitunix_futures",
             "bitunix_futures", "BTC/USDT.P", side, qty, entry, stop, 64197.36,
             86400, None, json.dumps(extra)),
        )


def _read(db_url, order_id):
    with db.connect(db_url) as conn:
        r = conn.execute(
            "SELECT result, result_price, actual_pnl_dollars, extra_json "
            "FROM paper_trade_record WHERE order_id=?", (order_id,)).fetchone()
    return r, (json.loads(r["extra_json"]) if r and r["extra_json"] else {})


@pytest.mark.asyncio
async def test_real_autobook_positive_pnl_books_win_tp(tmp_path):
    url = f"sqlite:///{tmp_path / 'p2.db'}"
    db.init_db(url)
    _seed(url, "winrec", side="sell", entry=64595.1, stop=64754.20, qty=0.000377)
    # favorable maker TP fill whose order-id matches the tp1 leg
    broker = _FillBroker([{"price": 64478.8, "qty": 0.000377, "fee": 0.001,
                           "role": "MAKER", "order_id": "TPID1"}])
    assert await _autobook_missing_close_real(
        broker, url, "winrec", "2026-06-18T01:52:17+00:00") == "booked"
    r, extra = _read(url, "winrec")
    assert r["result"] == "win"                       # was hard-coded 'loss'
    assert extra["exit_kind"] == "tp"                 # order-id match → tp
    assert extra["autobook_level_type"] == "tp"       # mirrored field
    assert extra["exit_role"] == "maker"              # real role recorded
    assert extra["maker_taker_mix"]["maker_fraction"] == pytest.approx(1.0)
    # PnL VALUE is the real fill economics — positive — and NEVER overridden
    assert r["actual_pnl_dollars"] == pytest.approx((64595.1 - 64478.8) * 0.000377)
    assert r["actual_pnl_dollars"] > 0
    assert extra["net_realized_usd"] > 0


@pytest.mark.asyncio
async def test_real_autobook_negative_pnl_books_loss_stop(tmp_path):
    url = f"sqlite:///{tmp_path / 'p2b.db'}"
    db.init_db(url)
    _seed(url, "lossrec", side="sell", entry=64595.1, stop=64754.20, qty=0.000377)
    # adverse fill above the stop, no order-id match → loss / stop
    broker = _FillBroker([{"price": 64760.0, "qty": 0.000377, "fee": 0.005,
                           "role": "TAKER", "order_id": "OTHER"}])
    await _autobook_missing_close_real(
        broker, url, "lossrec", "2026-06-18T02:00:00+00:00")
    r, extra = _read(url, "lossrec")
    assert r["result"] == "loss"
    assert extra["exit_kind"] == "stop"
    assert extra["exit_role"] == "taker"
    assert r["actual_pnl_dollars"] < 0


# ════════════════════════════════════════════════════════════════════════════
# entry maker/taker role threaded onto the fill
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_fill_price_from_history_returns_role():
    b = BitunixBroker(api_key="k", api_secret="s")
    b._client = MagicMock()
    b.get_history_trades = AsyncMock(return_value=[
        {"price": 100.0, "qty": 1.0, "fee": 0.1, "roleType": "MAKER"},
        {"price": 100.0, "qty": 1.0, "fee": 0.1, "roleType": "TAKER"},
    ])
    avg, fee, qty, role = await b._fill_price_from_history("oid")
    assert qty == pytest.approx(2.0) and role == "mixed"

    b.get_history_trades = AsyncMock(return_value=[
        {"price": 100.0, "qty": 1.0, "fee": 0.1, "roleType": "TAKER"}])
    _, _, _, role2 = await b._fill_price_from_history("oid")
    assert role2 == "taker"


# ════════════════════════════════════════════════════════════════════════════
# mc_a_yellow_x — declassified out of the directional factors
# ════════════════════════════════════════════════════════════════════════════

def _all_factor_maps(node):
    """Yield every dict that is the value of a 'factors:' key, recursively."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "factors" and isinstance(v, dict):
                yield v
            yield from _all_factor_maps(v)
    elif isinstance(node, list):
        for it in node:
            yield from _all_factor_maps(it)


def test_yellow_x_not_a_directional_factor_in_config():
    cfg = yaml.safe_load(
        open(Path(__file__).resolve().parents[1] / "config" / "strategies.yaml"))
    maps = list(_all_factor_maps(cfg))
    assert maps, "expected at least one factors: block"
    # declassified — present in NO factors block
    assert all("mc_a_yellow_x" not in m for m in maps)
    # control: a real bear factor in the same family is still present
    assert any("mc_a_redx" in m for m in maps)
