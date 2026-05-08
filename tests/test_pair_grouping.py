"""Tests for the pair-grouping helper in main.py — Phase B.3 of HITL.

`_group_orders_by_pair_id` is the orchestration glue that ensures
PMCC-roll siblings (close + open sharing pmcc_pair_id) launch in
parallel via asyncio.gather, so both ApprovalRequests land in the
PendingApprovalRegistry simultaneously and the web detail page can
coalesce them into one card.

Solo orders (no pair_id) remain singleton groups — sequential
processing preserved.
"""
from __future__ import annotations

from trading_corp.main import _group_orders_by_pair_id
from trading_corp.persistence.models import ProposedOrder


def _order(symbol: str, side: str = "buy", pair_id: str | None = None) -> ProposedOrder:
    extra = {"pmcc_pair_id": pair_id} if pair_id else {}
    return ProposedOrder(
        strategy="robinhood_pmcc", symbol=symbol, side=side,
        qty=1.0, order_type="limit", limit_price=5.0,
        rationale="test", extra=extra,
    )


def test_solo_orders_pass_through_as_singletons():
    orders = [_order("AAPL"), _order("NVDA"), _order("MSFT")]
    out = _group_orders_by_pair_id(orders)
    assert len(out) == 3
    assert all(len(g) == 1 for g in out)
    assert [g[0].symbol for g in out] == ["AAPL", "NVDA", "MSFT"]


def test_paired_orders_group_together():
    close_leg = _order("MSTR", "buy", pair_id="pair-1")
    open_leg = _order("MSTR", "sell", pair_id="pair-1")
    out = _group_orders_by_pair_id([close_leg, open_leg])
    assert len(out) == 1
    assert len(out[0]) == 2
    assert {o.side for o in out[0]} == {"buy", "sell"}


def test_mixed_solo_and_paired_orders():
    orders = [
        _order("AAPL"),
        _order("MSTR", "buy", pair_id="pair-X"),
        _order("NVDA"),
        _order("MSTR", "sell", pair_id="pair-X"),
    ]
    out = _group_orders_by_pair_id(orders)
    assert len(out) == 3
    sizes = [len(g) for g in out]
    # AAPL solo → 1, MSTR pair → 2, NVDA solo → 1
    assert sorted(sizes) == [1, 1, 2]


def test_two_separate_pairs_are_independent_groups():
    orders = [
        _order("MSTR", "buy",  pair_id="pair-A"),
        _order("RKLB", "buy",  pair_id="pair-B"),
        _order("MSTR", "sell", pair_id="pair-A"),
        _order("RKLB", "sell", pair_id="pair-B"),
    ]
    out = _group_orders_by_pair_id(orders)
    assert len(out) == 2
    pairs = {tuple(sorted({o.symbol for o in g})): g for g in out}
    assert ("MSTR",) in pairs
    assert ("RKLB",) in pairs
    assert all(len(g) == 2 for g in out)


def test_empty_list_returns_empty():
    assert _group_orders_by_pair_id([]) == []


def test_pair_groups_appear_at_first_legs_position():
    """Group ordering preserves where the first leg of each pair was
    seen — solo orders interleave naturally."""
    orders = [
        _order("AAPL"),
        _order("MSTR", "buy", pair_id="pair-1"),
        _order("NVDA"),
        _order("MSTR", "sell", pair_id="pair-1"),
        _order("TSLA"),
    ]
    out = _group_orders_by_pair_id(orders)
    # AAPL, MSTR-pair (at position of close leg), NVDA, TSLA
    # = 4 groups; pair shows up between AAPL and NVDA
    assert len(out) == 4
    assert out[0][0].symbol == "AAPL"
    assert len(out[1]) == 2 and out[1][0].symbol == "MSTR"
    assert out[2][0].symbol == "NVDA"
    assert out[3][0].symbol == "TSLA"
