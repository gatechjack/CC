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


def _order(*, conf=1.0, side="BUY", stake=2.0, base=0.55, ticker=NYY, whale="w", wallet="0xWALLET"):
    return translate_whale_action(whale=whale, whale_wallet=wallet, kalshi_ticker=ticker,
                                  confidence=conf, whale_side=side, base_price=base, stake_usd=stake)


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
            translate_whale_action(whale="w", whale_wallet="0xW", kalshi_ticker=NYY, confidence=1.0,
                                   whale_side=bad, base_price=0.5, stake_usd=2.0)


def test_idempotency_keyed_on_wallet_not_display_name(hdb):
    # Same wallet + different display user_name -> SAME key (immune to name edits).
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb)
    o1 = _order(whale="monkeymashingke", wallet="0x684baa57c338c2549aec0aa3f034f695d72a8409")
    o2 = _order(whale="monkeymashingkeyboard", wallet="0x684baa57c338c2549aec0aa3f034f695d72a8409")
    assert o1.idempotency_key == o2.idempotency_key
    assert _run(ex.submit(o1))["status"] == "DRY_RUN_would_place"
    assert _run(ex.submit(o2))["status"] == "suppressed_duplicate"   # one whale action -> <=1 order
    # a different wallet is a different action -> different key
    o3 = _order(whale="monkeymashingke", wallet="0x0000000000000000000000000000000000000000")
    assert o3.idempotency_key != o1.idempotency_key


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


def test_caps_none_disables_size_and_daily_gates(hdb):
    # launch config: no per-trade cap, no daily-deployment cap (halt is the backstop)
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb, per_trade_cap_usd=None,
                            daily_deployment_cap_usd=None)
    assert _run(ex.submit(_order(stake=1000.0, ticker=NYY)))["status"] == "DRY_RUN_would_place"
    assert _run(ex.submit(_order(stake=1000.0, ticker=COLSF_SF)))["status"] == "DRY_RUN_would_place"
    assert ex._deployed_usd == 2000.0        # neither gate blocked


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


# ── fix (b): trade-count ceiling (real-time, count-only) ────────────────────
def test_gcount_ceiling_25_pass_26th_trips_halt_27th_blocked(hdb):
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb, per_trade_cap_usd=None,
                            daily_deployment_cap_usd=None, max_orders_per_day=25,
                            strategy="poly_kalshi_mlb_counttest")
    for i in range(25):                                   # 25 distinct orders all place
        o = _order(ticker=f"KXMLBGAME-26AUG16{i:04d}AAABBB-AAA")
        assert _run(ex.submit(o))["status"] == "DRY_RUN_would_place"
    assert ex._orders_today == 25
    o26 = _order(ticker="KXMLBGAME-26AUG160026CCCDDD-CCC")
    assert _run(ex.submit(o26))["status"] == "blocked_count_ceiling"   # 26th trips
    assert StrategyState.from_persistence("poly_kalshi_mlb_counttest", db_url=hdb).halted is True
    o27 = _order(ticker="KXMLBGAME-26AUG160027EEEFFF-EEE")
    assert _run(ex.submit(o27))["status"] == "blocked_halt"            # 27th blocked by [G-halt]
    assert ex._orders_today == 25                                      # blocked orders don't count


# ── fix (c): every outcome is journaled ─────────────────────────────────────
class _FakeLogger:
    def __init__(self):
        self.events = []

    def log_event(self, strategy, kind, payload):
        self.events.append((strategy, kind, dict(payload)))


def test_placed_and_rejected_both_write_journal_rows(hdb):
    lg = _FakeLogger()
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb, per_trade_cap_usd=5.0, logger=lg,
                            strategy="poly_kalshi_mlb_journaltest")
    _run(ex.submit(_order(stake=2.0, ticker=NYY)))          # would-place
    _run(ex.submit(_order(stake=6.0, ticker=COLSF_SF)))     # blocked_size_cap
    assert [k for _s, k, _p in lg.events] == ["poly_kalshi_order", "poly_kalshi_order"]
    by_status = {p["status"]: p for _s, _k, p in lg.events}
    assert "DRY_RUN_would_place" in by_status and "blocked_size_cap" in by_status
    # deployed_usd + count queryable from the journal, not just in-memory:
    assert by_status["DRY_RUN_would_place"]["deployed_usd_after"] == 2.0
    assert by_status["DRY_RUN_would_place"]["orders_today_after"] == 1
    assert by_status["blocked_size_cap"]["deployed_usd_after"] == 2.0   # reject added nothing


# ── FLAG 1 (CP3 prerequisite): the REAL fill is persisted WITH the journal row ─
from trading_corp.agents.strategies.poly_kalshi_executor import _fill_fields_from_v2_resp


class _FakeClient:
    """Stands in for pykalshi's client — the executor calls _client().post()."""
    def __init__(self, resp):
        self._resp = resp
        self.posted = []

    async def post(self, path, body):
        self.posted.append((path, body))
        return self._resp


class _FakeBroker:
    def __init__(self, resp):
        self._c = _FakeClient(resp)

    def _client(self):
        return self._c


def test_flag1_extractor_maps_v2_response_fields_always_yes():
    # same field reads as kalshi_live.fill_event_from_v2_response (YES leg).
    f = _fill_fields_from_v2_resp(
        {"order_id": "7000441c", "fill_count": 9, "remaining_count": 0,
         "average_fill_price": "0.54", "average_fee_paid": "0.01"}, outcome="yes")
    assert f == {"order_id": "7000441c", "fill_count": 9,
                 "fill_price": 0.54, "fill_fee": pytest.approx(0.09)}


def test_flag1_extractor_no_leg_is_book_side_converted():
    # defensive parity with kalshi_live.py:211 (the $163.84 book-side bug); this
    # strategy is always-YES, but the extractor stays honest for a NO leg.
    f = _fill_fields_from_v2_resp(
        {"order_id": "x", "fill_count": 2, "average_fill_price": 0.987}, outcome="no")
    assert f["fill_price"] == pytest.approx(0.013)


def test_flag1_extractor_missing_or_zero_fill_never_raises():
    assert _fill_fields_from_v2_resp({"order_id": "z"}, outcome="yes") == {
        "order_id": "z", "fill_count": 0, "fill_price": None, "fill_fee": 0.0}
    assert _fill_fields_from_v2_resp(None, outcome="yes")["order_id"] == ""


def test_flag1_live_submit_journals_real_fill_not_limit(hdb):
    """A LIVE placement journals the REAL fill (order_id/fill_count/fill_price)
    IN the audit row. Pre-CP3 this data was lost: rec['resp'] was set AFTER
    _record had already written the row, so only the limit price persisted."""
    lg = _FakeLogger()
    resp = {"order_id": "7000441c-aaaa", "fill_count": 9, "remaining_count": 0,
            "average_fill_price": "0.55", "average_fee_paid": "0.01"}  # filled @0.55; limit was 0.56
    ex = PolyKalshiExecutor(
        dry_run=False, broker=_FakeBroker(resp), db_url=hdb, logger=lg,
        per_trade_cap_usd=None, daily_deployment_cap_usd=None,
        strategy="poly_kalshi_mlb_filltest")
    r = _run(ex.submit(_order(base=0.54, stake=5.0, ticker=NYY),
                       market_quote={"yes_ask": 0.55, "yes_bid": 0.53}))
    assert r["status"] == "placed"
    payload = lg.events[-1][2]                       # exactly what log_event journaled
    assert payload["division"] == "poly_kalshi_mlb_filltest"
    assert payload["order_id"] == "7000441c-aaaa"
    assert payload["fill_count"] == 9
    assert payload["fill_price"] == 0.55             # REAL fill, not the 0.56 limit
    assert payload["fill_fee"] == pytest.approx(0.09)
    assert payload["price"] == "0.5600"              # limit still kept for reference
    assert "resp" not in payload                     # fill is IN the row, not a post-hoc mutation


def test_flag1_dry_run_row_has_division_but_no_fill(hdb):
    lg = _FakeLogger()
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb, logger=lg,
                            strategy="poly_kalshi_mlb_drydiv")
    _run(ex.submit(_order(ticker=NYY)))
    payload = lg.events[-1][2]
    assert payload["status"] == "DRY_RUN_would_place"
    assert payload["division"] == "poly_kalshi_mlb_drydiv"
    assert "order_id" not in payload and "fill_price" not in payload   # no live fill
