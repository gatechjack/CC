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
    OrderPlacementError,
    TokenIdResolutionError,
    _native_order_type,
    _poll_order_to_fill,
    build_clob_order_args,
    cancel_order,
    create_signed_order,
    map_proposed_to_clob,
    place_order,
    place_order_fak_synth,
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


# ── E1·3: place/poll -> FillEvent (poll logic is pure; place_order needs SDK) ─

def _poll_client(*order_dicts):
    """MagicMock client whose get_order returns the given dict(s) in order (the
    last repeats indefinitely)."""
    c = MagicMock()
    seq = list(order_dicts)

    def _get(_order_id):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    c.get_order.side_effect = _get
    return c


async def test_poll_filled_full():
    c = _poll_client({"status": "matched", "size_matched": "5", "original_size": "5", "price": "0.5"})
    fill = await _poll_order_to_fill(c, "0xOID", symbol="0xC:Yes", side="buy",
                                     price=0.5, original_size=5.0, timeout=0.0, interval=0.0)
    assert fill.order_id == "0xOID"
    assert fill.symbol == "0xC:Yes"
    assert fill.side == "buy"
    assert fill.qty == 5.0
    assert fill.price == 0.5
    assert fill.venue == "polymarket"


async def test_poll_status_casing_normalized():
    # GET-order status casing isn't doc-pinned; "MATCHED" must work like "matched".
    c = _poll_client({"status": "MATCHED", "size_matched": "5", "original_size": "5", "price": "0.5"})
    fill = await _poll_order_to_fill(c, "0xO", symbol="s", side="buy", price=0.5,
                                     original_size=5.0, timeout=0.0, interval=0.0)
    assert fill.qty == 5.0


async def test_poll_partial_terminal_records_filled_portion():
    # cancelled with a partial fill -> FillEvent for the FILLED portion only.
    c = _poll_client({"status": "cancelled", "size_matched": "3", "original_size": "5", "price": "0.5"})
    fill = await _poll_order_to_fill(c, "0xO", symbol="s", side="buy", price=0.5,
                                     original_size=5.0, timeout=0.0, interval=0.0)
    assert fill.qty == 3.0  # partial, not 5


async def test_poll_partial_at_timeout_records_partial():
    # still 'live' with a partial fill at the deadline -> record the partial.
    c = _poll_client({"status": "live", "size_matched": "2", "original_size": "5", "price": "0.5"})
    fill = await _poll_order_to_fill(c, "0xO", symbol="s", side="buy", price=0.5,
                                     original_size=5.0, timeout=0.0, interval=0.0)
    assert fill.qty == 2.0


async def test_poll_zero_fill_terminal_raises():
    # cancelled with nothing matched -> no phantom FillEvent.
    c = _poll_client({"status": "cancelled", "size_matched": "0", "original_size": "5"})
    with pytest.raises(OrderPlacementError):
        await _poll_order_to_fill(c, "0xO", symbol="s", side="buy", price=0.5,
                                  original_size=5.0, timeout=0.0, interval=0.0)


async def test_poll_timeout_zero_fill_raises():
    # resting (live) with no fill at the deadline -> TimeoutError, no phantom fill.
    c = _poll_client({"status": "live", "size_matched": "0", "original_size": "5"})
    with pytest.raises(TimeoutError):
        await _poll_order_to_fill(c, "0xO", symbol="s", side="buy", price=0.5,
                                  original_size=5.0, timeout=0.0, interval=0.0)


async def test_poll_waits_through_live_then_fills():
    c = _poll_client(
        {"status": "live", "size_matched": "0", "original_size": "5"},
        {"status": "live", "size_matched": "0", "original_size": "5"},
        {"status": "matched", "size_matched": "5", "original_size": "5", "price": "0.5"},
    )
    fill = await _poll_order_to_fill(c, "0xO", symbol="s", side="sell", price=0.5,
                                     original_size=5.0, timeout=5.0, interval=0.0)
    assert fill.qty == 5.0
    assert fill.side == "sell"
    assert c.get_order.call_count == 3


# place_order needs OrderType + builds a real OrderArgs -> importorskip (py3.12 venv).
# The mocked client guarantees the real CLOB post_order is never hit.

async def test_place_order_success_full_fill():
    pytest.importorskip("py_clob_client")
    from py_clob_client.clob_types import OrderType

    c = MagicMock()
    c.create_order.return_value = "SIGNED"
    c.post_order.return_value = {"success": True, "orderID": "0xOID", "status": "live"}
    c.get_order.return_value = {"status": "matched", "size_matched": "5", "original_size": "5", "price": "0.5"}
    order = _order(side="buy", qty=5.0, price=0.5, extra={"token_id": "T"})

    fill = await place_order(c, order, timeout=0.0, interval=0.0)

    assert fill.order_id == "0xOID"
    assert fill.qty == 5.0
    assert fill.price == 0.5
    assert fill.venue == "polymarket"
    c.create_order.assert_called_once()                 # signed
    c.post_order.assert_called_once()
    assert c.post_order.call_args.args[1] == OrderType.GTC


async def test_place_order_rejected_raises_and_does_not_poll():
    pytest.importorskip("py_clob_client")
    c = MagicMock()
    c.create_order.return_value = "SIGNED"
    c.post_order.return_value = {"success": False, "errorMsg": "insufficient balance"}
    with pytest.raises(OrderPlacementError):
        await place_order(c, _order(extra={"token_id": "T"}), timeout=0.0, interval=0.0)
    c.get_order.assert_not_called()


async def test_place_order_unmatched_raises():
    pytest.importorskip("py_clob_client")
    c = MagicMock()
    c.create_order.return_value = "SIGNED"
    c.post_order.return_value = {"success": True, "orderID": "0xO", "status": "unmatched"}
    with pytest.raises(OrderPlacementError):
        await place_order(c, _order(extra={"token_id": "T"}), timeout=0.0, interval=0.0)


# ── E1·4: cancel_order -> bool (defensive; no SDK import needed) ─────────────

async def test_cancel_success_returns_true():
    c = MagicMock()
    c.cancel.return_value = {"canceled": ["0xOID"], "not_canceled": {}}
    assert await cancel_order(c, "0xOID") is True
    c.cancel.assert_called_once_with("0xOID")  # CLOB orderID passed through, no mapping


async def test_cancel_not_canceled_returns_false():
    c = MagicMock()
    c.cancel.return_value = {"canceled": [], "not_canceled": {"0xOID": "order already matched"}}
    assert await cancel_order(c, "0xOID") is False


async def test_cancel_order_id_absent_from_canceled_returns_false():
    c = MagicMock()
    c.cancel.return_value = {"canceled": ["0xOTHER"], "not_canceled": {}}
    assert await cancel_order(c, "0xOID") is False


async def test_cancel_exception_returns_false_never_raises():
    c = MagicMock()
    c.cancel.side_effect = RuntimeError("network down")
    assert await cancel_order(c, "0xOID") is False


async def test_cancel_unrecognized_or_empty_response_returns_false():
    c = MagicMock()
    c.cancel.return_value = {"weird": True}
    assert await cancel_order(c, "0xOID") is False
    c2 = MagicMock()
    c2.cancel.return_value = None
    assert await cancel_order(c2, "0xOID") is False


async def test_cancel_empty_order_id_returns_false_without_calling():
    c = MagicMock()
    assert await cancel_order(c, "") is False
    c.cancel.assert_not_called()


# ── E2·2: synthesized FAK + native tif pass-through ─────────────────────────
# py_clob_client 0.17.5 has no native FAK, so place_order_fak_synth posts GTC,
# polls <= poll_seconds, cancels the unfilled remainder, and fills ONLY the
# matched portion. These exercise build_clob_order_args/OrderType -> importorskip
# (py3.12 venv), like the E1·3 place_order tests. The mocked client guarantees the
# real CLOB is never hit.


def _fak_client(*order_dicts, post_status="live", order_id="0xOID"):
    """Mock client for the FAK-synth path: create_order/post_order succeed and
    get_order returns the given dict(s) in sequence (last repeats). cancel returns
    a canceled-confirming response by default."""
    c = MagicMock()
    c.create_order.return_value = "SIGNED"
    c.post_order.return_value = {"success": True, "orderID": order_id, "status": post_status}
    seq = list(order_dicts)

    def _get(_oid):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    c.get_order.side_effect = _get
    c.cancel.return_value = {"canceled": [order_id], "not_canceled": {}}
    return c


async def test_fak_synth_posts_gtc_full_fill_no_cancel():
    # GTC is the order actually posted; a full fill cancels nothing.
    pytest.importorskip("py_clob_client")
    from py_clob_client.clob_types import OrderType

    c = _fak_client({"status": "matched", "size_matched": "5", "original_size": "5", "price": "0.5"})
    order = _order(side="buy", qty=5.0, price=0.5, extra={"token_id": "T"})
    fill = await place_order_fak_synth(c, order, poll_seconds=0.0, interval=0.0)
    assert fill.qty == 5.0
    assert fill.venue == "polymarket"
    assert c.post_order.call_args.args[1] == OrderType.GTC  # synth posts GTC
    c.cancel.assert_not_called()                            # full fill -> no remainder


async def test_fak_synth_partial_at_timeout_fills_partial_and_cancels_remainder():
    # Window expires with a partial fill: FillEvent reflects the FILLED qty (not the
    # posted size) and the unfilled remainder is cancelled.
    pytest.importorskip("py_clob_client")
    c = _fak_client({"status": "live", "size_matched": "2", "original_size": "5", "price": "0.5"})
    order = _order(side="buy", qty=5.0, price=0.5, extra={"token_id": "T"})
    fill = await place_order_fak_synth(c, order, poll_seconds=0.0, interval=0.0)
    assert fill.qty == 2.0                       # partial, NOT the posted 5
    c.cancel.assert_called_once_with("0xOID")    # remainder cancelled


async def test_fak_synth_zero_fill_at_timeout_cancels_and_raises_no_phantom():
    # Resting with nothing matched at the window's expiry: cancel + raise, no fill.
    pytest.importorskip("py_clob_client")
    c = _fak_client({"status": "live", "size_matched": "0", "original_size": "5"})
    order = _order(side="buy", qty=5.0, price=0.5, extra={"token_id": "T"})
    with pytest.raises(OrderPlacementError):
        await place_order_fak_synth(c, order, poll_seconds=0.0, interval=0.0)
    c.cancel.assert_called_once_with("0xOID")    # remainder cancelled, no phantom fill


async def test_fak_synth_terminal_zero_fill_raises_without_extra_cancel():
    # Venue-terminal (cancelled) with 0 matched: _poll raises OrderPlacementError
    # before the timeout branch — already terminal, so no extra cancel is issued.
    pytest.importorskip("py_clob_client")
    c = _fak_client({"status": "cancelled", "size_matched": "0", "original_size": "5"})
    order = _order(side="buy", qty=5.0, price=0.5, extra={"token_id": "T"})
    with pytest.raises(OrderPlacementError):
        await place_order_fak_synth(c, order, poll_seconds=0.0, interval=0.0)
    c.cancel.assert_not_called()


async def test_fak_synth_polls_within_window_then_full_fill():
    # Poll runs across the window (respects poll_seconds): rests live twice, then
    # fully fills on the third poll -> no remainder to cancel.
    pytest.importorskip("py_clob_client")
    c = _fak_client(
        {"status": "live", "size_matched": "0", "original_size": "5"},
        {"status": "live", "size_matched": "0", "original_size": "5"},
        {"status": "matched", "size_matched": "5", "original_size": "5", "price": "0.5"},
    )
    order = _order(side="buy", qty=5.0, price=0.5, extra={"token_id": "T"})
    fill = await place_order_fak_synth(c, order, poll_seconds=5.0, interval=0.0)
    assert fill.qty == 5.0
    assert c.get_order.call_count == 3           # polled through the window
    c.cancel.assert_not_called()


async def test_fak_synth_rejected_placement_raises_before_poll():
    # A rejected post_order never polls or cancels (no order to manage).
    pytest.importorskip("py_clob_client")
    c = MagicMock()
    c.create_order.return_value = "SIGNED"
    c.post_order.return_value = {"success": False, "errorMsg": "insufficient balance"}
    with pytest.raises(OrderPlacementError):
        await place_order_fak_synth(c, _order(extra={"token_id": "T"}), poll_seconds=0.0, interval=0.0)
    c.get_order.assert_not_called()
    c.cancel.assert_not_called()


@pytest.mark.parametrize("ot,enum_name", [("gtc", "GTC"), ("fok", "FOK"), ("gtd", "GTD")])
async def test_place_order_native_posts_requested_tif_and_never_synthesizes(ot, enum_name):
    # gtc/fok/gtd pass straight through to the native path: the requested tif is
    # posted and the synth-only cancel is never invoked.
    pytest.importorskip("py_clob_client")
    from py_clob_client.clob_types import OrderType

    c = _fak_client({"status": "matched", "size_matched": "5", "original_size": "5", "price": "0.5"})
    order = _order(side="buy", qty=5.0, price=0.5, extra={"token_id": "T"})
    fill = await place_order(c, order, order_type=ot, timeout=0.0, interval=0.0)
    assert fill.qty == 5.0
    assert c.post_order.call_args.args[1] == getattr(OrderType, enum_name)
    c.cancel.assert_not_called()                 # native path never cancels-as-synth


def test_native_order_type_maps_and_rejects_synth_and_unknown():
    pytest.importorskip("py_clob_client")
    from py_clob_client.clob_types import OrderType

    assert _native_order_type("gtc") == OrderType.GTC
    assert _native_order_type("FOK") == OrderType.FOK     # case-insensitive
    assert _native_order_type("gtd") == OrderType.GTD
    with pytest.raises(ValueError):
        _native_order_type("fak_synth")                  # synth is not a native tif
    with pytest.raises(ValueError):
        _native_order_type("bogus")
