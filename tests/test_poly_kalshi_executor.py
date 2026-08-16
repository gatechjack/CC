"""Unit tests for the duplicated Poly->Kalshi executor (CP2 + CP3 guardrails).
Pure + dry-run; the [G-halt] tests use a real temp DB to prove the shared
StrategyState halt mechanism (same one the other divisions read)."""
from __future__ import annotations

import asyncio

import pytest

from trading_corp.persistence import db as _db
from trading_corp.persistence.models import StrategyState
from trading_corp.agents.strategies.poly_kalshi_executor import (
    PolyKalshiExecutor, translate_whale_action,
)

NYY = "KXMLBGAME-26AUG161337NYYTOR-NYY"
COLSF_SF = "KXMLBGAME-26AUG161605COLSF-SF"
TEXATH_TEX = "KXMLBGAME-26AUG161605TEXATH-TEX"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def hdb(tmp_path):
    p = tmp_path / "guard.db"
    _db.init_db(f"sqlite:///{p}")
    return f"sqlite:///{p}"


def _order(*, conf=1.0, side="BUY", stake=2.0, base=0.55, ticker=NYY, whale="w"):
    return translate_whale_action(whale=whale, kalshi_ticker=ticker, confidence=conf,
                                  whale_side=side, base_price=base, stake_usd=stake)


# ── CP2: translation + side mapping (pure) ──────────────────────────────────
def test_entry_buy_is_yes_bid():
    o = _order(base=0.55, stake=2.0)
    assert o.action == "entry" and o.outcome == "yes" and o.v2_side == "bid"
    assert o.reduce_only is False and o.count == 3           # floor(2.00/0.55)
    assert o.body["price"] == "0.5700" and o.body["time_in_force"] == "immediate_or_cancel"


def test_exit_sell_is_yes_ask_reduce_only():
    o = _order(side="SELL", base=0.55, stake=2.0)
    assert o.action == "exit" and o.v2_side == "ask" and o.reduce_only is True
    assert o.body["price"] == "0.5300" and o.body.get("reduce_only") is True


def test_side_mapping_away_and_home_both_yes():
    away = _order(ticker=TEXATH_TEX, base=0.5)
    home = _order(ticker=COLSF_SF, base=0.5)
    assert away.outcome == "yes" and away.ticker.endswith("-TEX") and away.v2_side == "bid"
    assert home.outcome == "yes" and home.ticker.endswith("-SF") and home.v2_side == "bid"


def test_non_trade_side_rejected_not_treated_as_exit():
    for bad in ("", "REDEEM", "redeem", "TAKER_REBATE", None):
        with pytest.raises(ValueError):
            translate_whale_action(whale="w", kalshi_ticker=NYY, confidence=1.0,
                                   whale_side=bad, base_price=0.5, stake_usd=2.0)


# ── CP2: idempotency + threshold (still active after CP3 wiring) ─────────────
def test_gidem_replay_suppressed(hdb):
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb)
    o = _order()
    r1 = _run(ex.submit(o))
    r2 = _run(ex.submit(o))
    assert r1["status"] == "DRY_RUN_would_place" and r2["status"] == "suppressed_duplicate"
    assert r1["idempotency_key"] == r2["idempotency_key"] and len(ex._placed) == 1


def test_gconf_below_threshold_skipped(hdb):
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb)
    r = _run(ex.submit(_order(conf=0.50)))     # e.g. a doubleheader_ambiguous
    assert r["status"] == "skip_below_threshold" and len(ex._placed) == 0


def test_entry_and_exit_are_distinct_keys(hdb):
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb)
    entry, exit_ = _order(base=0.5), _order(side="SELL", base=0.5)
    assert entry.idempotency_key != exit_.idempotency_key
    assert _run(ex.submit(entry))["status"] == "DRY_RUN_would_place"
    assert _run(ex.submit(exit_))["status"] == "DRY_RUN_would_place"


# ── CP3: each guardrail BLOCKS (order stopped, placed=0) ─────────────────────
def test_gsize_blocks_over_cap(hdb):
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb, per_trade_cap_usd=5.0)
    r = _run(ex.submit(_order(stake=6.0)))
    assert r["status"] == "blocked_size_cap"
    assert ex._deployed_usd == 0.0 and len(ex._placed) == 0


def test_gdaily_blocks_breach_and_counter_is_in_memory(hdb):
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb, per_trade_cap_usd=5.0,
                            daily_deployment_cap_usd=5.0)
    assert _run(ex.submit(_order(ticker=NYY, stake=2.0)))["status"] == "DRY_RUN_would_place"       # 2
    assert _run(ex.submit(_order(ticker=COLSF_SF, stake=2.0)))["status"] == "DRY_RUN_would_place"  # 4
    r = _run(ex.submit(_order(ticker=TEXATH_TEX, stake=2.0)))                                      # 4+2>5
    assert r["status"] == "blocked_daily_cap"
    assert ex._deployed_usd == 4.0                    # the breaching order was NOT counted
    assert isinstance(ex._deployed_usd, float)        # plain in-process counter, not a DB query


def test_gslip_blocks_thin_book(hdb):
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb, max_slippage_cents=2)
    o = _order(base=0.55)
    assert _run(ex.submit(o, market_quote={"yes_ask": 0.70, "yes_bid": 0.68}))["status"] == "blocked_slippage"
    assert ex._deployed_usd == 0.0
    # a healthy book (1c from base) passes
    assert _run(ex.submit(o, market_quote={"yes_ask": 0.56, "yes_bid": 0.54}))["status"] == "DRY_RUN_would_place"


def test_ghalt_blocks_all_until_cleared_same_mechanism(hdb):
    strat = "poly_kalshi_mlb_guardtest"
    StrategyState.persist_halt(strat, "daily-loss cap breached", db_url=hdb)
    # proves it is the SAME primitive the other divisions read:
    assert StrategyState.from_persistence(strat, db_url=hdb).halted is True
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb, strategy=strat)
    assert _run(ex.submit(_order(ticker=NYY)))["status"] == "blocked_halt"
    assert _run(ex.submit(_order(ticker=COLSF_SF)))["status"] == "blocked_halt"
    assert ex._deployed_usd == 0.0 and len(ex._placed) == 0
    StrategyState.clear_halt(strat, db_url=hdb)
    assert _run(ex.submit(_order(ticker=NYY)))["status"] == "DRY_RUN_would_place"


# ── CP3: order-of-operations + interaction ──────────────────────────────────
def test_halt_short_circuits_before_counter_and_key(hdb):
    strat = "poly_kalshi_mlb_ooptest"
    StrategyState.persist_halt(strat, "halt", db_url=hdb)
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb, strategy=strat)
    o = _order()
    assert _run(ex.submit(o))["status"] == "blocked_halt"
    assert ex._deployed_usd == 0.0            # no daily-cap budget consumed
    assert o.idempotency_key not in ex._placed  # no idempotency key burned


def test_rejected_order_does_not_increment_daily_counter(hdb):
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb, per_trade_cap_usd=5.0)
    _run(ex.submit(_order(stake=6.0)))        # size-cap reject
    assert ex._deployed_usd == 0.0


def test_daily_counter_counts_only_would_place(hdb):
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb)
    _run(ex.submit(_order(conf=0.5)))          # below threshold -> skip
    assert ex._deployed_usd == 0.0
    _run(ex.submit(_order(conf=1.0)))          # passes -> counts
    assert ex._deployed_usd == 2.0


def test_dedup_replay_does_not_double_count_daily(hdb):
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb)
    o = _order()
    assert _run(ex.submit(o))["status"] == "DRY_RUN_would_place" and ex._deployed_usd == 2.0
    assert _run(ex.submit(o))["status"] == "suppressed_duplicate"
    assert ex._deployed_usd == 2.0             # replay did NOT re-consume budget


def test_dry_run_default_needs_no_broker(hdb):
    ex = PolyKalshiExecutor(db_url=hdb)         # dry_run defaults True, broker None
    assert ex._dry_run is True
    assert _run(ex.submit(_order()))["status"] == "DRY_RUN_would_place"
