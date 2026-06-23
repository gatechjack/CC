"""D3 role-recording fix (2026-06-23) — booking-honesty.

The bot previously recorded exit_role / entry_role from BitUnix's trade-history
`roleType` field, which is UNRELIABLE: it reports "MAKER" for fills that are
economically TAKER (proven on 2 live trades whose known-taker market entry AND B1
stop exit were both charged the taker rate 0.00019 yet both recorded role=maker).

D3 derives role from ORDER SEMANTICS — which order the bot placed:
  * EXIT  (_aggregate_close_fills): a close fill whose order-id is a bracket TP
    leg = maker (resting POST_ONLY limit); the position SL order-id = taker (B1
    stop / market reduce); no match = corroborate by fee rate, NEVER default
    maker; no fee either = 'unknown'.
  * ENTRY (place_order): role = 'maker' iff the placed order body's effect is
    POST_ONLY (the B2 maker clone), else 'taker' (market / non-POST_ONLY, incl.
    the maker->taker fallback).

role and fee stay INDEPENDENT signals: a fee-model error remains detectable via
the `role_fee_mismatch` flag (order-semantics role vs aggregate-fee-implied role).

Pure / unit — no live API call. Sacred path (PnL, D1 min(qty,q_close), ref-vs-fill,
B1 placement, risk) is untouched: these tests assert role attribution ONLY.
"""
from __future__ import annotations

import pytest

from trading_corp.agents.divisions.bitunix_position_reconciler import (
    D3_MAKER_FEE_REF,
    D3_TAKER_FEE_REF,
    _aggregate_close_fills,
    _role_summary,
)
from trading_corp.brokers.bitunix import BitunixBroker
from trading_corp.persistence.models import ProposedOrder


# ════════════════════════════════════════════════════════════════════════════
# _role_summary — positive-evidence-only (no maker default)
# ════════════════════════════════════════════════════════════════════════════

def test_role_summary_positive_evidence_only():
    assert _role_summary(0, 0) == "unknown"   # the KILLED maker default
    assert _role_summary(1, 0) == "maker"
    assert _role_summary(0, 1) == "taker"
    assert _role_summary(1, 1) == "mixed"


# ════════════════════════════════════════════════════════════════════════════
# _aggregate_close_fills — role from ORDER SEMANTICS (order-id), not roleType
# ════════════════════════════════════════════════════════════════════════════

def test_tp_order_id_fill_is_maker():
    # A close fill whose order-id is a bracket TP leg → maker (resting POST_ONLY).
    agg = _aggregate_close_fills(
        [{"price": 64000.0, "qty": 0.001, "fee": 0.001, "order_id": "TP1"}],
        tp_order_ids=["TP1", "TP2"], sl_order_id="SL9",
    )
    assert agg["exit_role"] == "maker"
    assert agg["maker_taker_mix"]["maker_qty"] == pytest.approx(0.001)
    assert agg["maker_taker_mix"]["taker_qty"] == 0.0


def test_sl_order_id_fill_is_taker():
    # A close fill whose order-id is the position SL → taker (B1 stop / reduce).
    agg = _aggregate_close_fills(
        [{"price": 64200.0, "qty": 0.001, "fee": 0.005, "order_id": "SL9"}],
        tp_order_ids=["TP1"], sl_order_id="SL9",
    )
    assert agg["exit_role"] == "taker"
    assert agg["maker_taker_mix"]["taker_qty"] == pytest.approx(0.001)
    assert agg["maker_taker_mix"]["maker_qty"] == 0.0


def test_tp_and_sl_fill_is_mixed_with_fraction():
    # One TP fill (maker) + one SL fill (taker) → 'mixed', maker_fraction by qty.
    agg = _aggregate_close_fills(
        [
            {"price": 64000.0, "qty": 1.0, "fee": 0.1, "order_id": "TP1"},
            {"price": 64100.0, "qty": 3.0, "fee": 0.3, "order_id": "SL9"},
        ],
        tp_order_ids=["TP1"], sl_order_id="SL9",
    )
    assert agg["exit_role"] == "mixed"
    assert agg["maker_taker_mix"]["maker_qty"] == 1.0
    assert agg["maker_taker_mix"]["taker_qty"] == 3.0
    assert agg["maker_taker_mix"]["maker_fraction"] == pytest.approx(0.25)


def test_no_match_fill_with_taker_fee_is_taker_not_maker():
    # NO order-id match (manual close / unknown order) WITH a fee at the taker
    # rate → corroborated TAKER (never the killed maker default).
    p, q = 64239.2, 0.0005
    fee = D3_TAKER_FEE_REF * p * q          # exactly the taker rate on this notional
    agg = _aggregate_close_fills(
        [{"price": p, "qty": q, "fee": fee, "order_id": "UNRELATED"}],
        tp_order_ids=["TP1"], sl_order_id="SL9",
    )
    assert agg["exit_role"] == "taker"
    assert agg["maker_taker_mix"]["taker_qty"] == pytest.approx(q)


def test_no_match_fill_with_maker_fee_is_maker():
    # NO order-id match WITH a fee at the maker rate → corroborated MAKER.
    p, q = 64239.2, 0.0005
    fee = D3_MAKER_FEE_REF * p * q
    agg = _aggregate_close_fills(
        [{"price": p, "qty": q, "fee": fee, "order_id": "UNRELATED"}],
        tp_order_ids=["TP1"], sl_order_id="SL9",
    )
    assert agg["exit_role"] == "maker"
    assert agg["maker_taker_mix"]["maker_qty"] == pytest.approx(q)


def test_no_match_fill_with_no_fee_contributes_to_neither():
    # NO order-id match AND no fee → no positive evidence → neither bucket.
    agg = _aggregate_close_fills(
        [{"price": 64000.0, "qty": 0.001, "fee": 0.0, "order_id": "UNRELATED"}],
        tp_order_ids=["TP1"], sl_order_id="SL9",
    )
    assert agg["exit_role"] == "unknown"
    assert agg["maker_taker_mix"]["maker_qty"] == 0.0
    assert agg["maker_taker_mix"]["taker_qty"] == 0.0
    assert agg["maker_taker_mix"]["maker_fraction"] is None


def test_all_fills_role_less_is_unknown_not_maker():
    # No tp/sl ids passed AND no fee on any fill → 'unknown' (the killed default;
    # roleType, even if present, is no longer consulted).
    agg = _aggregate_close_fills([
        {"price": 100.0, "qty": 1.0, "fee": 0.0, "role": "MAKER", "order_id": "x"},
        {"price": 100.0, "qty": 2.0, "fee": 0.0, "role": "TAKER", "order_id": "y"},
    ])
    assert agg["exit_role"] == "unknown"


# ════════════════════════════════════════════════════════════════════════════
# fee corroboration — role + fee independent → mismatch flag
# ════════════════════════════════════════════════════════════════════════════

def test_taker_semantics_with_taker_fee_no_mismatch():
    # Order-semantics taker (SL order-id) + aggregate fee rate at the taker ref
    # → fee_implied_role 'taker', NO mismatch.
    p, q = 64239.2, 0.0005
    fee = D3_TAKER_FEE_REF * p * q
    agg = _aggregate_close_fills(
        [{"price": p, "qty": q, "fee": fee, "order_id": "SL9"}],
        tp_order_ids=["TP1"], sl_order_id="SL9",
    )
    assert agg["exit_role"] == "taker"
    assert agg["fee_implied_role"] == "taker"
    assert agg["role_fee_mismatch"] is False


def test_maker_semantics_but_taker_fee_flags_mismatch():
    # Order-semantics says MAKER (TP order-id) but the REAL fee was charged at the
    # taker rate → role_fee_mismatch True (a fee-model error stays detectable
    # BECAUSE role is NOT derived from the fee).
    p, q = 64239.2, 0.0005
    fee = D3_TAKER_FEE_REF * p * q          # taker-rate fee on a "maker" leg
    agg = _aggregate_close_fills(
        [{"price": p, "qty": q, "fee": fee, "order_id": "TP1"}],
        tp_order_ids=["TP1"], sl_order_id="SL9",
    )
    assert agg["exit_role"] == "maker"          # order semantics (independent)
    assert agg["fee_implied_role"] == "taker"   # fee corroboration disagrees
    assert agg["role_fee_mismatch"] is True


def test_unknown_role_never_flags_mismatch():
    # exit_role 'unknown' (no evidence) is not decisive → never a mismatch.
    agg = _aggregate_close_fills(
        [{"price": 64000.0, "qty": 0.001, "fee": 0.0, "order_id": "UNRELATED"}],
        tp_order_ids=["TP1"], sl_order_id="SL9",
    )
    assert agg["exit_role"] == "unknown"
    assert agg["role_fee_mismatch"] is False


# ════════════════════════════════════════════════════════════════════════════
# ENTRY role (bitunix.py place_order) — placed_role from the order body effect
# ════════════════════════════════════════════════════════════════════════════

def _placed_role_from_body(body: dict) -> str:
    """The exact derivation used in place_order (EDIT 2): role from the placed
    order TYPE (POST_ONLY effect = maker), NOT the venue roleType."""
    return (
        "maker"
        if str(body.get("effect") or "").upper() == "POST_ONLY"
        else "taker"
    )


def _entry_order(side="buy", *, ref=65000.0, stop=64000.0, qty=0.001):
    return ProposedOrder(
        strategy="bitunix_futures", symbol="BTC/USDT.P", side=side, qty=qty,
        order_type="market",
        extra={
            "maker_entry": True, "maker_offset_pct": 0.0005,
            "entry_reference_price": ref, "stop_price": stop, "leverage": 8,
        },
    )


def test_entry_post_only_body_is_maker():
    # A POST_ONLY maker-clone body → placed_role 'maker'.
    b = BitunixBroker(api_key="k", api_secret="s")
    b._client = object()  # not used; _build_order_body / _maker_clone are pure
    maker = b._maker_clone(_entry_order(side="sell", ref=65000.0, stop=66000.0))
    body = b._build_order_body(maker, "BTCUSDT", reduce_only=False)
    assert body["effect"] == "POST_ONLY"
    assert _placed_role_from_body(body) == "maker"


def test_entry_market_body_is_taker():
    # A plain market entry (no POST_ONLY tif) → effect GTC → placed_role 'taker'.
    b = BitunixBroker(api_key="k", api_secret="s")
    b._client = object()
    order = _entry_order(side="sell", ref=65000.0, stop=66000.0)
    body = b._build_order_body(order, "BTCUSDT", reduce_only=False)
    assert body["effect"] == "GTC"            # NOT POST_ONLY
    assert _placed_role_from_body(body) == "taker"


def test_entry_taker_fallback_body_is_taker():
    # The maker->taker fallback re-enters place_order with the taker clone (tif
    # stripped) → non-POST_ONLY body → 'taker'.
    b = BitunixBroker(api_key="k", api_secret="s")
    b._client = object()
    taker = b._taker_clone(_entry_order(side="sell"))
    body = b._build_order_body(taker, "BTCUSDT", reduce_only=False)
    assert body.get("effect", "").upper() != "POST_ONLY"
    assert _placed_role_from_body(body) == "taker"


# ════════════════════════════════════════════════════════════════════════════
# REGRESSION — re-derive the 2 post-epoch live trades' geometry under D3
# ════════════════════════════════════════════════════════════════════════════

def test_regression_b1_stop_close_is_taker_no_mismatch():
    # A single B1-stop close fill (order-id == the position SL) at the real fill
    # price / a taker-rate fee → exit_role 'taker', and the aggregate fee agrees
    # (no mismatch). This is the trade whose roleType WRONGLY said 'maker'.
    p, q = 64239.2, 0.0009475
    fee = 0.011563                            # taker-charged (rate ~0.00019/notional)
    agg = _aggregate_close_fills(
        [{"price": p, "qty": q, "fee": fee, "order_id": "SL9"}],
        tp_order_ids=["TP1", "TP2", "TP3"], sl_order_id="SL9",
    )
    # sanity: the real charged rate is at/near the taker ref, not the maker ref
    rate = fee / (p * q)
    assert abs(rate - D3_TAKER_FEE_REF) <= abs(rate - D3_MAKER_FEE_REF)
    assert agg["exit_role"] == "taker"        # was wrongly 'maker' under roleType
    assert agg["fee_implied_role"] == "taker"
    assert agg["role_fee_mismatch"] is False


def test_regression_market_entry_is_taker():
    # The known-taker market entry: a non-POST_ONLY body → placed_role 'taker'
    # (it too was wrongly recorded 'maker' from roleType pre-D3).
    b = BitunixBroker(api_key="k", api_secret="s")
    b._client = object()
    order = _entry_order(side="sell", ref=64595.1, stop=64754.2, qty=0.000377)
    body = b._build_order_body(order, "BTCUSDT", reduce_only=False)
    assert _placed_role_from_body(body) == "taker"
