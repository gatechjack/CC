"""Tests for `comms/position_context.py` — Phase B.2 of HITL-in-app.

Pins the structured-dict shape the web `/approvals/{order_id}` template
consumes. Built from `ApprovalRequest.detail` produced by graph/ceo_graph.py.
"""
from __future__ import annotations

import json

import pytest

from trading_corp.comms.position_context import (
    build_approval_view,
    coalesce_paired_view,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _option_order_row(
    *, action: str = "open_short_call",
    side: str = "sell", qty: float = 1.0,
    underlying: str = "NVDA", strike: float = 150.0,
    expiration: str = "2026-05-09", dte: int = 7, delta: float = 0.30,
    mark: float = 5.50, bid: float | None = 5.45, ask: float | None = 5.55,
    pair_id: str | None = None, position_context: dict | None = None,
) -> dict:
    extra = {
        "is_option": True,
        "underlying": underlying,
        "strike": strike,
        "option_type": "call",
        "expiration": expiration,
        "dte": dte, "delta": delta,
        "action": action,
        "position_effect": "close" if "close" in action else "open",
        "mark_per_share": mark,
    }
    if bid is not None: extra["bid"] = bid
    if ask is not None: extra["ask"] = ask
    if pair_id: extra["pmcc_pair_id"] = pair_id
    if position_context: extra["position_context"] = position_context
    return {
        "id": f"ord-{action}",
        "symbol": underlying,
        "side": side,
        "qty": qty,
        "limit_price": mark,
        "rationale": f"test {action}",
        "extra_json": json.dumps(extra),
    }


def _detail(order_row: dict, *, division: str = "robinhood_pmcc",
            risk: dict | None = None) -> dict:
    return {
        "order": order_row,
        "risk_verdict": risk or {"verdict": "approve", "reason": "ok"},
        "division": division,
    }


# ── Headline + asset classification ──────────────────────────────────────


def test_view_option_open_short_call_headline():
    v = build_approval_view(_detail(_option_order_row()))
    assert v["headline"]["label"] == "SELL CALL TO OPEN"
    assert v["headline"]["symbol"] == "NVDA"
    assert v["headline"]["division"] == "robinhood_pmcc"
    assert v["trade"]["asset_class"] == "option"


def test_view_roll_close_action_label():
    v = build_approval_view(_detail(_option_order_row(
        action="roll_short_call_close", side="buy",
    )))
    assert v["headline"]["label"] == "ROLL: BUY TO CLOSE"
    assert v["headline"]["emoji"] == "🔄"


def test_view_roll_open_action_label():
    v = build_approval_view(_detail(_option_order_row(
        action="roll_short_call_open", side="sell",
    )))
    assert v["headline"]["label"] == "ROLL: SELL TO OPEN"


def test_view_stock_classification():
    order = {
        "id": "ord-stk", "symbol": "AAPL", "side": "buy",
        "qty": 10.0, "limit_price": 200.0, "order_type": "limit",
        "extra_json": "{}",
    }
    v = build_approval_view(_detail(order))
    assert v["trade"]["asset_class"] == "stock"
    assert v["trade"]["legs"][0]["gross_dollars"] == pytest.approx(2000.0)
    assert v["trade"]["legs"][0]["side_sign"] == -1   # buy → debit


def test_view_crypto_classification():
    order = {
        "id": "ord-crypto", "symbol": "BTC/USD", "side": "buy",
        "qty": 0.05, "limit_price": 70000.0,
        "extra_json": json.dumps({
            "asset_type": "crypto",
            "tier": "premium",
            "notional_target": 3500.0,
        }),
    }
    v = build_approval_view(_detail(order))
    assert v["trade"]["asset_class"] == "crypto"
    assert v["trade"]["legs"][0]["gross_dollars"] == pytest.approx(3500.0)
    assert v["trade"]["legs"][0]["tier"] == "premium"


# ── Leg dollar math + side sign ──────────────────────────────────────────


def test_view_option_sell_is_credit_signed_positive():
    v = build_approval_view(_detail(_option_order_row(side="sell", mark=5.0, qty=2)))
    leg = v["trade"]["legs"][0]
    assert leg["side_sign"] == +1
    assert leg["gross_dollars"] == pytest.approx(5.0 * 2 * 100)
    assert v["trade"]["net_dollars"] == pytest.approx(+1000.0)


def test_view_option_buy_is_debit_signed_negative():
    v = build_approval_view(_detail(_option_order_row(side="buy", mark=4.0, qty=1)))
    leg = v["trade"]["legs"][0]
    assert leg["side_sign"] == -1
    assert leg["gross_dollars"] == pytest.approx(4.0 * 1 * 100)
    assert v["trade"]["net_dollars"] == pytest.approx(-400.0)


def test_view_option_bid_ask_extracted():
    v = build_approval_view(_detail(_option_order_row(bid=5.40, ask=5.60)))
    leg = v["trade"]["legs"][0]
    assert leg["bid"] == 5.40
    assert leg["ask"] == 5.60


# ── Position context block ──────────────────────────────────────────────


def test_view_context_omitted_when_no_position_context():
    v = build_approval_view(_detail(_option_order_row()))
    assert v["context"] is None


def test_view_context_leap_pnl_pct_computed():
    ctx = {
        "leap": {
            "underlying": "NVDA", "strike": 100.0, "expiration": "2027-01-15",
            "cost_basis": 25.0, "mark": 50.0, "dte": 365, "days_held": 90,
        },
        "unrealized_pnl_dollars": 2500.0,
        "unrealized_pnl_pct": 1.0,
        "roll_count": 3,
        "prior_credit_total": 450.0,
    }
    v = build_approval_view(_detail(
        _option_order_row(position_context=ctx),
    ))
    cb = v["context"]
    assert cb["leap"]["pnl_pct"] == pytest.approx(100.0)
    assert cb["leap"]["dte"] == 365
    assert cb["unrealized_pnl_dollars"] == 2500.0
    assert cb["roll_count"] == 3
    assert cb["prior_credit_total"] == 450.0


# ── Risk verdict normalization ──────────────────────────────────────────


def test_view_risk_approve_color_gain():
    v = build_approval_view(_detail(
        _option_order_row(),
        risk={"verdict": "approve", "reason": "within all caps"},
    ))
    assert v["risk"]["color"] == "gain"
    assert v["risk"]["icon"] == "✓"
    assert v["risk"]["reason"] == "within all caps"


def test_view_risk_resize_color_warn():
    v = build_approval_view(_detail(
        _option_order_row(),
        risk={"verdict": "resize", "reason": "scaled to caps"},
    ))
    assert v["risk"]["color"] == "warn"


def test_view_risk_reject_color_loss():
    v = build_approval_view(_detail(
        _option_order_row(),
        risk={"verdict": "reject", "reason": "halted"},
    ))
    assert v["risk"]["color"] == "loss"


# ── Pair ID extraction ──────────────────────────────────────────────────


def test_view_pmcc_pair_id_extracted():
    v = build_approval_view(_detail(
        _option_order_row(pair_id="abcd1234"),
    ))
    assert v["pmcc_pair_id"] == "abcd1234"


def test_view_pmcc_pair_id_none_when_solo():
    v = build_approval_view(_detail(_option_order_row()))
    assert v["pmcc_pair_id"] is None


# ── Defensive fallbacks ─────────────────────────────────────────────────


def test_view_handles_missing_extra_json():
    detail = {
        "order": {"id": "ord-x", "symbol": "AAPL", "side": "buy", "qty": 1},
        "risk_verdict": {},
        "division": "default",
    }
    v = build_approval_view(detail)
    # Doesn't crash; falls back to stock classification.
    assert v["trade"]["asset_class"] == "stock"


def test_view_handles_malformed_extra_json():
    detail = {
        "order": {
            "id": "ord-x", "symbol": "AAPL", "side": "buy", "qty": 1,
            "extra_json": "not-json-{",
        },
        "risk_verdict": {},
        "division": "default",
    }
    v = build_approval_view(detail)
    assert v["trade"]["asset_class"] == "stock"   # safe fallback
    assert v["raw_extra"] == {}


# ── coalesce_paired_view ────────────────────────────────────────────────


def test_coalesce_paired_view_close_first():
    pid = "pair-1"
    close_v = build_approval_view(_detail(_option_order_row(
        action="roll_short_call_close", side="buy", pair_id=pid,
        mark=3.0, qty=1,
    )))
    open_v = build_approval_view(_detail(_option_order_row(
        action="roll_short_call_open", side="sell", pair_id=pid,
        mark=4.0, qty=1,
    )))
    coalesced = coalesce_paired_view([open_v, close_v])  # any order
    legs = coalesced["trade"]["legs"]
    assert len(legs) == 2
    # Close (buy) leg first, open (sell) second.
    assert legs[0]["side"] == "buy"
    assert legs[1]["side"] == "sell"
    # Net: -300 (close debit) + 400 (open credit) = +100 credit
    assert coalesced["trade"]["net_dollars"] == pytest.approx(+100.0)
    assert coalesced["headline"]["label"] == "ROLL · CLOSE + OPEN"
    assert coalesced["headline"]["emoji"] == "🔄"
    assert coalesced.get("is_paired") is True


def test_coalesce_singleton_passes_through():
    v = build_approval_view(_detail(_option_order_row()))
    out = coalesce_paired_view([v])
    assert out is v


def test_coalesce_empty_list_raises():
    with pytest.raises(ValueError):
        coalesce_paired_view([])
