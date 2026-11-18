"""Gate F -- the COLLAPSED-POSITION FLOOR (2026-11-18): never copy an ENTRY priced < 0.25x the whale's entry.

    ratio = ours / whale_avg_price, ours = base_price (the SAME leg-correct ask gate C prices against,
    FEE EXCLUDED -- the fail-closed direction for a too-CHEAP gate). STRICT boundary: blocks ratio < 0.25,
    exact equality PASSES. ENTRY-only; exits exempt. Missing whale price fails closed loudly even with
    gate C disabled. ALWAYS ON (code constant E.FLOOR_RATIO, no config column, no migration).

Evidence: 2026-11-18 bucket study over the chase-study backtest set (3,681 priced fills, M4 cut 2026-10-16):
the ratio<0.25 bucket is 0-for-22 LIFETIME (train 0/20 -$8.57, holdout 0/2 -$0.29). The motivating live case
is order 10663: EMU-WMU Under FILLED at 0.01 vs whale entry 0.64 (ratio 0.016) -> settled a loser at 0.0.

Harness mirrors tests/prediction_markets/test_chase_gate.py (the REAL execution.evaluate, a real NBA-spread
matcher path -- the market-type Jack enabled live 2026-11-18). Expected tickers/legs derived independently
from the ticker's own -CODE + the whale-buys-the-named-side convention, never from the matcher's output.
"""
import asyncio
import logging
import os
import sqlite3
import tempfile

from trading_corp.prediction_markets import db as DB
from trading_corp.prediction_markets import execution as E
from trading_corp.prediction_markets import heartbeat as HB
from trading_corp.prediction_markets import live_driver as LD


def _mk(yes_ask, yes_bid, shard=0):
    return {"yes_ask_dollars": yes_ask, "yes_bid_dollars": yes_bid,
            "no_ask_dollars": round(1.0 - yes_bid, 4), "no_bid_dollars": round(1.0 - yes_ask, 4),
            "liquidity_dollars": 0.0, "yes_ask_size_fp": 1000.0, "yes_bid_size_fp": 1000.0,
            "exchange_index": shard}


def _sub(**over):
    kw = dict(account_id="kalshi_test", category="nba", market_types=("moneyline", "total", "spread"),
              sizing_mode="contracts", fixed_stake_usd=0.0, per_order_usd_cap=100.0, daily_usd_cap=100.0,
              max_open_usd=100.0, max_orders_per_day=10, max_slippage_cents=2, liquidity_ratio=0.75, contracts=1)
    kw.update(over)
    return E.SubConfig(**kw)


# NBA spread: whale holds "Mavericks" on the home -5.5 -> KXNBASPREAD ...GSWDAL-DAL6 (integer = N+0.5 -> 6),
# YES leg (whale's team == the ticker's own anchor) -- the exact shape of live fill 10687 (2026-11-18).
_TK = "KXNBASPREAD-26NOV18GSWDAL-DAL6"


def _nba_ctx(yes_ask, yes_bid):
    from trading_corp.data import sports_structural_match as SS
    cfg = SS.LEAGUES["nba"]
    gi = SS.build_game_index(["KXNBAGAME-26NOV18GSWDAL-GSW", "KXNBAGAME-26NOV18GSWDAL-DAL"], cfg)
    si = SS.build_spread_index([_TK], cfg)
    return E.MarketContext({}, {}, si, frozenset(k[0] for k in gi.keys()), {_TK: _mk(yes_ask, yes_bid)},
                           structural_index=gi)


def _sig(whale, sid="sigfloor", is_exit=False):
    return E.CopySignal(wallet="0xabc", slug="nba-gsw-dal-2026-11-18-spread-home-5pt5", outcome="Mavericks",
                        condition_id="0xcondfloor", outcome_index=0, signal_id=sid, is_exit=is_exit,
                        title="Spread: Mavericks (-5.5)", whale_avg_price=whale)


def _eval(sig, sub, ctx):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "pm.db")
    DB.init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    jrnl = E.Journal(conn, [sub.account_id], 1_800_000_000)
    dec = E.evaluate(sig, sub, ctx, jrnl, conn, 1_800_000_000, shard_balances=None, venue_exposure=None,
                     legacy_db_path=os.path.join(d, "no_legacy.db"))
    conn.close()
    return dec, jrnl


def test_floor_blocks_the_10663_case():
    # the REAL numbers of order 10663 (fill 0.01 vs whale 0.64, ratio 0.016 -> settled 0.0), through the
    # real NBA-spread path. chase passes (-62.9c -- a collapse is the OPPOSITE of a chase); the floor blocks.
    dec, _ = _eval(_sig(whale=0.64), _sub(), _nba_ctx(0.01, 0.005))
    assert dec.status == "skip:floor", (dec.status, dec.reason)
    assert dec.kalshi_ticker == _TK and dec.leg == "yes"
    assert dec.reason == "floor_0.016=ours_0.0100/whale_0.6400 lt_0.25", dec.reason


def test_floor_boundary_exactly_025_is_strict():
    # STRICT boundary: ours == 0.25*whale exactly PASSES (0.15 == 0.25*0.60); one tick below BLOCKS.
    dec, _ = _eval(_sig(whale=0.60), _sub(), _nba_ctx(0.15, 0.13))
    assert dec.status == "dry_run_would_place", (dec.status, dec.reason)
    dec2, _ = _eval(_sig(whale=0.60), _sub(), _nba_ctx(0.14, 0.12))
    assert dec2.status == "skip:floor", (dec2.status, dec2.reason)
    assert "floor_0.233=ours_0.1400/whale_0.6000 lt_0.25" == dec2.reason, dec2.reason


def test_floor_entry_only_exit_lands_on_holding_guard():
    # an EXIT at a collapsed price must NEVER be floor-blocked (a reduce_only close at a collapsed price is
    # exactly the settlement-ward exit that MUST go through). With nothing held it lands on the holding
    # guard -- NOT on gate F -- mirroring test_exits_are_exempt_from_chase.
    dec, _ = _eval(_sig(whale=0.64, is_exit=True), _sub(), _nba_ctx(0.01, 0.005))
    assert dec.status == "skip:not_held", (dec.status, dec.reason)


def test_floor_unpriced_fails_closed_even_with_chase_off():
    # with gate C at its defaults the chase gate's own unpriced skip fires first (test_chase_gate pins that);
    # with gate C DISABLED the floor's self-check must keep the fail-closed semantics -- loudly.
    sub_off = _sub(max_chase_cents=0.0, chase_upside_pct=0.0)
    dec, _ = _eval(_sig(whale=None), sub_off, _nba_ctx(0.47, 0.45))
    assert dec.status == "skip:floor_unpriced", (dec.status, dec.reason)
    assert "fail_closed" in dec.reason
    # and the floor itself still blocks a collapse with gate C off (the gates are independent)
    dec2, _ = _eval(_sig(whale=0.64), sub_off, _nba_ctx(0.01, 0.005))
    assert dec2.status == "skip:floor", (dec2.status, dec2.reason)


def test_floor_skip_consumes_no_budget_and_journals_no_coid():
    dec, jrnl = _eval(_sig(whale=0.64), _sub(), _nba_ctx(0.01, 0.005))
    assert dec.status == "skip:floor"
    assert jrnl.daily_usd("kalshi_test", "nba") == 0.0 and jrnl.orders_today("kalshi_test", "nba") == 0
    assert jrnl.open_usd("kalshi_test") == 0.0
    # the SAME signal places once the ratio recovers: ask 0.01 vs whale 0.03 -> ratio 0.333, chase -1.93c
    dec2, _ = _eval(_sig(whale=0.03), _sub(), _nba_ctx(0.01, 0.005))
    assert dec2.status == "dry_run_would_place", (dec2.status, dec2.reason)


def _cycle(conn, sub, sigs, ctx, now_ts, log):
    return asyncio.run(LD.run_live_arm_gated_cycle(
        conn, sub, sigs, ctx, E.Journal(conn, [sub.account_id], now_ts), now_ts,
        place_fn=None, shard_balances=None, venue_exposure=None,
        legacy_db_path=os.path.join(tempfile.mkdtemp(), "no_legacy.db"), log=log))


def test_cycle_counts_floor_skips_and_rate_limits_the_log(caplog):
    LD._floor_log_last.clear()
    ctx = _nba_ctx(0.01, 0.005)
    d = tempfile.mkdtemp()
    path = os.path.join(d, "pm.db")
    DB.init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    log = logging.getLogger("test_floor_cycle")
    with caplog.at_level(logging.WARNING, logger="test_floor_cycle"):
        s1 = _cycle(conn, _sub(), [_sig(whale=0.64, sid="sigA"), _sig(whale=0.80, sid="sigB")],
                    ctx, 1_800_000_000, log)
        assert s1["floor_skips"] == 2 and s1["n_skip"] == 2 and s1["placed"] == 0
        assert s1["chase_skips"] == 0                      # a collapse is NOT a chase -- separate counters
        lines = [r.message for r in caplog.records if "gate-F FLOOR skip" in r.message]
        assert len(lines) == 1, lines                      # rate-limited: ONE line for the two skips
        assert _TK in lines[0] and "floor_0.016=ours_0.0100/whale_0.6400 lt_0.25" in lines[0], lines[0]
        # a second cycle inside the window: counted again, NOT logged again
        s2 = _cycle(conn, _sub(), [_sig(whale=0.64, sid="sigA")], ctx, 1_800_000_005, log)
        assert s2["floor_skips"] == 1
        assert len([r.message for r in caplog.records if "gate-F FLOOR skip" in r.message]) == 1
    conn.close()


def test_floor_is_log_only_no_heartbeat_column():
    # DOCUMENTED DECISION (2026-11-18): pm_driver_heartbeat has NO floor column (none fits; a new one needs a
    # migration, ruled out for this build) -- the upsert must IGNORE the summary's floor_skips key, not raise.
    d = tempfile.mkdtemp()
    path = os.path.join(d, "pm.db")
    DB.init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pm_driver_heartbeat)")]
    assert not any("floor" in c for c in cols), cols
    HB.upsert_evaluated(conn, "kalshi_test", "nba", 1_800_000_000,
                        {"n_signals": 6, "chase_skips": 3, "floor_skips": 2})
    r = conn.execute("SELECT chase_skips, n_signals FROM pm_driver_heartbeat "
                     "WHERE account_id='kalshi_test' AND category='nba'").fetchone()
    assert r["chase_skips"] == 3 and r["n_signals"] == 6
    conn.close()
