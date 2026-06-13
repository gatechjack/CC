"""E1·2 — ProposedOrder -> OrderArgs mapping + sign-only create_order.

Two test groups:
  * PURE (no py_clob_client): token_id resolution (direct + gamma-fallback incl.
    the correct-outcome / ordering-mismatch cases) and the price/size/side mapping.
    These run in the normal pytest gate (the SDK is not installed there).
  * SDK (`importorskip("py_clob_client")`): OrderArgs construction + the sign-only
    create_order wiring (mocked client; post_order must never be called). These run
    on the pinned py3.12 venv where py_clob_client is installed.

Network-free. `post_order` is never invoked.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trading_corp.brokers.polymarket_live import (
    TokenIdResolutionError,
    build_clob_order_args,
    create_signed_order,
    map_proposed_to_clob,
    resolve_token_id,
    resolve_token_id_from_market,
)
from trading_corp.persistence.models import ProposedOrder


def _order(*, side="buy", qty=4.0, price=0.5, extra=None) -> ProposedOrder:
    return ProposedOrder(
        strategy="polymarket_copy_trader",
        symbol="0xCOND:Yes",
        side=side,
        qty=qty,
        order_type="market",
        limit_price=price,
        extra=extra if extra is not None else {},
    )


# ── token_id: direct path (authoritative, no lookup) ───────────────────────

def test_resolve_token_id_direct_token_id_key():
    assert resolve_token_id({"token_id": "TID_DIRECT", "condition_id": "0xC"}) == "TID_DIRECT"


def test_resolve_token_id_direct_asset_key():
    # ActivityRow.asset IS the ERC-1155 token id (E2 will propagate it here).
    assert resolve_token_id({"asset": "TID_FROM_ASSET"}) == "TID_FROM_ASSET"


def test_resolve_token_id_no_source_raises():
    with pytest.raises(TokenIdResolutionError):
        resolve_token_id({"outcome": "Yes"})  # no token_id/asset, no condition_id


def test_resolve_token_id_lookup_needed_but_no_fetcher_raises():
    with pytest.raises(TokenIdResolutionError):
        resolve_token_id({"condition_id": "0xC", "outcome": "Yes", "outcome_index": 1})


def test_resolve_token_id_fetcher_returns_none_raises():
    with pytest.raises(TokenIdResolutionError):
        resolve_token_id(
            {"condition_id": "0xC", "outcome": "Yes", "outcome_index": 1},
            market_fetcher=lambda cid: None,
        )


# ── token_id: gamma lookup — CORRECT outcome + ordering safety ──────────────

# outcomes are deliberately ordered No,Yes (NOT a "Yes is always index 0" layout)
# so a naive index assumption would pick the wrong token.
_MKT = {"conditionId": "0xC", "clobTokenIds": ["TID_NO", "TID_YES"], "outcomes": ["No", "Yes"]}


def test_lookup_picks_correct_outcome_by_label():
    tid = resolve_token_id_from_market(_MKT, outcome_index=1, outcome="Yes")
    assert tid == "TID_YES"  # the Yes token, not TID_NO
    assert resolve_token_id_from_market(_MKT, outcome_index=0, outcome="No") == "TID_NO"


def test_lookup_label_match_does_not_depend_on_index():
    # outcome_index absent: label match alone must still pick the right token
    # (proves we don't fall back to a default index-0).
    assert resolve_token_id_from_market(_MKT, outcome_index=None, outcome="Yes") == "TID_YES"


def test_lookup_ordering_mismatch_raises():
    # Label "Yes" is at index 1, but outcome_index claims 0 — refuse to guess.
    with pytest.raises(TokenIdResolutionError, match="ordering mismatch"):
        resolve_token_id_from_market(_MKT, outcome_index=0, outcome="Yes")


def test_lookup_outcome_not_found_raises():
    with pytest.raises(TokenIdResolutionError):
        resolve_token_id_from_market(_MKT, outcome_index=None, outcome="Maybe")


def test_lookup_length_mismatch_raises():
    bad = {"conditionId": "0xC", "clobTokenIds": ["A", "B"], "outcomes": ["No", "Yes", "Draw"]}
    with pytest.raises(TokenIdResolutionError):
        resolve_token_id_from_market(bad, outcome_index=1, outcome="Yes")


def test_lookup_ambiguous_label_raises():
    bad = {"conditionId": "0xC", "clobTokenIds": ["A", "B"], "outcomes": ["Yes", "Yes"]}
    with pytest.raises(TokenIdResolutionError):
        resolve_token_id_from_market(bad, outcome_index=0, outcome="Yes")


def test_lookup_json_encoded_fields_parse():
    # gamma returns clobTokenIds/outcomes as JSON-encoded strings.
    mkt = {"conditionId": "0xC", "clobTokenIds": '["TID_NO", "TID_YES"]', "outcomes": '["No", "Yes"]'}
    assert resolve_token_id_from_market(mkt, outcome_index=1, outcome="Yes") == "TID_YES"


def test_lookup_index_out_of_range_raises():
    with pytest.raises(TokenIdResolutionError):
        resolve_token_id_from_market(_MKT, outcome_index=5, outcome=None)


def test_resolve_token_id_via_fetcher_end_to_end():
    captured = {}

    def fetcher(cid):
        captured["cid"] = cid
        return _MKT

    tid = resolve_token_id(
        {"condition_id": "0xC", "outcome": "Yes", "outcome_index": 1},
        market_fetcher=fetcher,
    )
    assert tid == "TID_YES"
    assert captured["cid"] == "0xC"


# ── ProposedOrder -> CLOB field mapping (units/side) ────────────────────────

def test_map_buy_fields():
    m = map_proposed_to_clob(_order(side="buy", qty=4.0, price=0.5, extra={"token_id": "T"}))
    assert m == {"token_id": "T", "price": 0.5, "size": 4.0, "side": "buy"}


def test_map_sell_side():
    m = map_proposed_to_clob(_order(side="sell", extra={"token_id": "T"}))
    assert m["side"] == "sell"


@pytest.mark.parametrize("bad_price", [0.0, 1.0, 1.5, -0.1, None])
def test_map_rejects_non_probability_price(bad_price):
    with pytest.raises(ValueError):
        map_proposed_to_clob(_order(price=bad_price, extra={"token_id": "T"}))


def test_map_rejects_nonpositive_size():
    with pytest.raises(ValueError):
        map_proposed_to_clob(_order(qty=0.0, extra={"token_id": "T"}))


def test_map_rejects_bad_side():
    with pytest.raises(ValueError):
        map_proposed_to_clob(_order(side="hold", extra={"token_id": "T"}))


# ── SDK-dependent: OrderArgs build + sign-only wiring (py3.12 venv) ─────────

def test_build_clob_order_args_fields():
    pytest.importorskip("py_clob_client")
    from py_clob_client.order_builder.constants import BUY, SELL

    args = build_clob_order_args({"token_id": "T123", "price": 0.5, "size": 4.0, "side": "buy"})
    assert args.token_id == "T123"
    assert args.price == 0.5
    assert args.size == 4.0
    assert args.side == BUY

    sell_args = build_clob_order_args({"token_id": "T", "price": 0.3, "size": 2.0, "side": "sell"})
    assert sell_args.side == SELL


def test_create_signed_order_signs_and_never_posts():
    pytest.importorskip("py_clob_client")

    client = MagicMock()
    client.create_order.return_value = "SIGNED-SENTINEL"
    order = _order(side="buy", qty=4.0, price=0.5, extra={"token_id": "T123"})

    result = create_signed_order(client, order)

    assert result == "SIGNED-SENTINEL"
    client.create_order.assert_called_once()
    # The arg handed to create_order is an OrderArgs carrying the mapped fields.
    passed = client.create_order.call_args.args[0]
    assert passed.token_id == "T123"
    assert passed.price == 0.5
    assert passed.size == 4.0
    # SIGN-ONLY: post_order must never be invoked in this slice.
    client.post_order.assert_not_called()
