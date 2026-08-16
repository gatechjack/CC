"""Unit tests for the duplicated Poly->Kalshi executor (CP2). Pure + dry-run."""
from __future__ import annotations

import asyncio

import pytest

from trading_corp.agents.strategies.poly_kalshi_executor import (
    PolyKalshiExecutor, translate_whale_action,
)

NYY = "KXMLBGAME-26AUG161337NYYTOR-NYY"   # YES side = NYY
TEXATH_TEX = "KXMLBGAME-26AUG161605TEXATH-TEX"  # away club (TEX) YES ticker
COLSF_SF = "KXMLBGAME-26AUG161605COLSF-SF"      # home club (SF) YES ticker


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_entry_buy_is_yes_bid():
    o = translate_whale_action(whale="SDTrading", kalshi_ticker=NYY, confidence=1.0,
                               whale_side="BUY", base_price=0.55, stake_usd=2.0)
    assert o.action == "entry" and o.outcome == "yes" and o.v2_side == "bid"
    assert o.reduce_only is False
    assert o.count == 3               # floor(2.00 / 0.55)
    assert o.body["price"] == "0.5700"  # 0.55 + 2c slippage
    assert o.body["time_in_force"] == "immediate_or_cancel"


def test_exit_sell_is_yes_ask_reduce_only():
    o = translate_whale_action(whale="SDTrading", kalshi_ticker=NYY, confidence=1.0,
                               whale_side="SELL", base_price=0.55, stake_usd=2.0)
    assert o.action == "exit" and o.v2_side == "ask" and o.reduce_only is True
    assert o.body["price"] == "0.5300"  # 0.55 - 2c slippage
    assert o.body.get("reduce_only") is True


def test_side_mapping_away_and_home_both_yes():
    # bet AWAY club -> away club's own YES ticker; bet HOME club -> home club's YES ticker.
    away = translate_whale_action(whale="w", kalshi_ticker=TEXATH_TEX, confidence=1.0,
                                  whale_side="BUY", base_price=0.5, stake_usd=2.0)
    home = translate_whale_action(whale="w", kalshi_ticker=COLSF_SF, confidence=1.0,
                                  whale_side="BUY", base_price=0.5, stake_usd=2.0)
    # never NO; the club is encoded in the ticker suffix, always the YES leg.
    assert away.outcome == "yes" and away.ticker.endswith("-TEX") and away.v2_side == "bid"
    assert home.outcome == "yes" and home.ticker.endswith("-SF") and home.v2_side == "bid"


def test_idempotency_replay_suppressed():
    ex = PolyKalshiExecutor(dry_run=True)
    o = translate_whale_action(whale="SDTrading", kalshi_ticker=NYY, confidence=1.0,
                               whale_side="BUY", base_price=0.55, stake_usd=2.0)
    r1 = _run(ex.submit(o))
    r2 = _run(ex.submit(o))   # replay the identical action
    assert r1["status"] == "DRY_RUN_would_place"
    assert r2["status"] == "suppressed_duplicate"
    assert r1["idempotency_key"] == r2["idempotency_key"]
    assert len(ex._placed) == 1   # exactly one order retained


def test_entry_and_exit_are_distinct_keys():
    ex = PolyKalshiExecutor(dry_run=True)
    entry = translate_whale_action(whale="w", kalshi_ticker=NYY, confidence=1.0,
                                   whale_side="BUY", base_price=0.5, stake_usd=2.0)
    exit_ = translate_whale_action(whale="w", kalshi_ticker=NYY, confidence=1.0,
                                   whale_side="SELL", base_price=0.5, stake_usd=2.0)
    assert entry.idempotency_key != exit_.idempotency_key
    assert _run(ex.submit(entry))["status"] == "DRY_RUN_would_place"
    assert _run(ex.submit(exit_))["status"] == "DRY_RUN_would_place"


def test_below_threshold_skipped_never_placed():
    ex = PolyKalshiExecutor(dry_run=True)
    dh = translate_whale_action(whale="w", kalshi_ticker=NYY, confidence=0.50,
                                whale_side="BUY", base_price=0.5, stake_usd=2.0)
    r = _run(ex.submit(dh))
    assert r["status"] == "skip_below_threshold"
    assert len(ex._placed) == 0


def test_non_trade_side_rejected_not_treated_as_exit():
    # REDEEM/rebate rows (empty side) must NOT become an exit-sell.
    for bad in ("", "REDEEM", "redeem", "TAKER_REBATE", None):
        with pytest.raises(ValueError):
            translate_whale_action(whale="w", kalshi_ticker=NYY, confidence=1.0,
                                   whale_side=bad, base_price=0.5, stake_usd=2.0)


def test_dry_run_default_needs_no_broker():
    ex = PolyKalshiExecutor()   # dry_run defaults True, broker None
    assert ex._dry_run is True
    o = translate_whale_action(whale="w", kalshi_ticker=NYY, confidence=1.0,
                               whale_side="BUY", base_price=0.5, stake_usd=2.0)
    assert _run(ex.submit(o))["status"] == "DRY_RUN_would_place"
