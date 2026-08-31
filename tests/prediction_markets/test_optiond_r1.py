"""Option D (whale-exit) R-D1 -- the PURE detection adapters + the read-only net-open helper, fork-agnostic.

Proves: /activity -> SELL dicts (TRADE+SELL only; REDEEM/BUY excluded); /positions snapshot + reduction diff
(partial, full-VANISH via prior slug/outcome, no-change, scale-IN, new leg); the end-to-end detect_exit_signals
pairing INCLUDING the settlement-vanish FILTER (a reduction with a REDEEM, not a SELL, produces NO exit); the
read-only journal_net_open_contracts (the holding guard + Fork-B1 exit size); and the NO-LEG exit through the
chokepoint (sell NO -> side=bid, reduce_only). Fixtures use the REAL ActivityRow/PositionRow (from_api) so the
adapters are exercised against the true field shapes (standing lens: the fixture must mirror the real object)."""
import pytest

from trading_corp.prediction_markets import db, execution as ex, live_driver as ld
from trading_corp.data import mlb_poly_kalshi_match as M
from trading_corp.data.polymarket_data_api_client import ActivityRow, PositionRow

W = "0x16bb9951a36fce71e2ef57890b786145e0ba8492"
CID = "0xcond_sea_tor"
SLUG = "mlb-sea-tor-2026-08-28"


def _act(side, ts, tx, *, cid=CID, oidx=0, typ="TRADE", outcome="Toronto Blue Jays"):
    return ActivityRow.from_api({"proxyWallet": W, "conditionId": cid, "outcomeIndex": oidx, "side": side,
                                 "timestamp": ts, "transactionHash": tx, "type": typ, "size": 5.0,
                                 "slug": SLUG, "outcome": outcome})


def _pos(size, *, cid=CID, oidx=0, slug=SLUG, outcome="Toronto Blue Jays", cur=0.55, redeemable=False):
    return PositionRow.from_api({"proxyWallet": W, "conditionId": cid, "size": size, "slug": slug,
                                 "outcome": outcome, "outcomeIndex": oidx, "curPrice": cur,
                                 "redeemable": redeemable, "avgPrice": 0.5})


# ── /activity -> SELL dicts ───────────────────────────────────────────────────
def test_activity_sells_keeps_only_trade_sells():
    rows = [_act("SELL", 1000, "0xa"), _act("BUY", 1001, "0xb"),
            _act("SELL", 1002, "0xc", typ="REDEEM"),          # a redemption, NOT a discretionary sell
            _act("SELL", 1003, "0xd", cid="")]                # missing condition_id
    sells = ld.activity_sells_from_activity(rows, W)
    assert len(sells) == 1
    s = sells[0]
    assert s == {"wallet": W, "condition_id": CID, "outcome_index": 0, "ts": 1000, "tx_hash": "0xa"}


def test_activity_sells_uses_passed_wallet_not_row_echo():
    # the tracked-attachment identity is authoritative; must match the reduction side's wallet
    sells = ld.activity_sells_from_activity([_act("SELL", 1, "0xa")], "0xTRACKED")
    assert sells[0]["wallet"] == "0xTRACKED"


# ── /positions snapshot + reduction diff ──────────────────────────────────────
def test_snapshot_open_positions_genuinely_open_only_carries_meta():
    rows = [_pos(10.0), _pos(3.0, cid="0xother", slug="mlb-x", outcome="Over"),
            _pos(7.0, cid="0xsettled", redeemable=True)]     # settled -> excluded
    snap = ld.snapshot_open_positions(rows)
    assert snap[(CID, 0)] == (10.0, SLUG, "Toronto Blue Jays")
    assert snap[("0xother", 0)] == (3.0, "mlb-x", "Over")
    assert ("0xsettled", 0) not in snap                       # redeemable dropped


def test_reduction_partial():
    prior = {(CID, 0): (10.0, SLUG, "Toronto Blue Jays")}
    reds = ld.detect_position_reductions(prior, [_pos(4.0)], W, now_ts=2000)
    assert reds == [{"wallet": W, "condition_id": CID, "outcome_index": 0, "ts": 2000,
                     "slug": SLUG, "outcome": "Toronto Blue Jays"}]


def test_reduction_full_vanish_uses_prior_slug_outcome():
    """The whale FULLY sold -> the leg is absent from the current book, so slug/outcome are unreadable there.
    The reduction MUST still carry them (from the prior snapshot) or the exit can never match its Kalshi ticker --
    the most complete exit would silently miss."""
    prior = {(CID, 0): (10.0, SLUG, "Toronto Blue Jays")}
    reds = ld.detect_position_reductions(prior, [], W, now_ts=2000)   # current book EMPTY
    assert len(reds) == 1 and reds[0]["slug"] == SLUG and reds[0]["outcome"] == "Toronto Blue Jays"
    assert reds[0]["condition_id"] == CID


def test_reduction_none_on_unchanged_or_scale_in_or_new():
    prior = {(CID, 0): (10.0, SLUG, "Toronto Blue Jays")}
    assert ld.detect_position_reductions(prior, [_pos(10.0)], W, now_ts=1) == []      # unchanged
    assert ld.detect_position_reductions(prior, [_pos(15.0)], W, now_ts=1) == []      # scale IN
    # a leg NOT in prior (a fresh open) is not a reduction -- it is an ENTRY (the entry path handles it)
    assert ld.detect_position_reductions({}, [_pos(4.0)], W, now_ts=1) == []


# ── end-to-end pairing + the settlement FILTER ────────────────────────────────
def test_exit_pairs_sell_with_reduction():
    prior = {(CID, 0): (10.0, SLUG, "Toronto Blue Jays")}
    sells = ld.activity_sells_from_activity([_act("SELL", 1900, "0xtx1")], W)
    reds = ld.detect_position_reductions(prior, [_pos(4.0)], W, now_ts=2000)          # detected 100s after the sell
    sigs = ex.detect_exit_signals(sells, reds, window_sec=300)
    assert len(sigs) == 1 and sigs[0].is_exit is True
    assert sigs[0].condition_id == CID and sigs[0].slug == SLUG and sigs[0].outcome == "Toronto Blue Jays"


def test_exit_settlement_vanish_is_filtered_no_sell():
    """A position vanished (reduction detected) but the ONLY activity is a REDEEM, not a SELL -> NO exit. This is
    the settlement/redemption case: it must NOT copy-exit (there is nothing to follow -- the market resolved)."""
    prior = {(CID, 0): (10.0, SLUG, "Toronto Blue Jays")}
    sells = ld.activity_sells_from_activity([_act("SELL", 1900, "0xtx1", typ="REDEEM")], W)   # REDEEM filtered out
    reds = ld.detect_position_reductions(prior, [], W, now_ts=2000)
    assert sells == [] and len(reds) == 1
    assert ex.detect_exit_signals(sells, reds, window_sec=300) == []


def test_two_sells_distinct_signal_ids():
    """Per-SELL identity: two different sells on the same leg derive DIFFERENT signal_ids (tx_hash-keyed) so the
    second is a distinct order, not a gate-4 dedup collision (the exit-side of the Finding-5 property)."""
    prior = {(CID, 0): (10.0, SLUG, "Toronto Blue Jays")}
    reds = ld.detect_position_reductions(prior, [_pos(4.0)], W, now_ts=2000)
    a = ex.detect_exit_signals(ld.activity_sells_from_activity([_act("SELL", 1900, "0xtxA")], W), reds, window_sec=300)
    b = ex.detect_exit_signals(ld.activity_sells_from_activity([_act("SELL", 1950, "0xtxB")], W), reds, window_sec=300)
    assert a[0].signal_id != b[0].signal_id


# ── read-only net-open helper (holding guard + Fork-B1 size) ──────────────────
def _order(conn, ticker, leg, is_exit, fill, *, status="filled", dry=0):
    conn.execute(
        "INSERT INTO pm_subdivision_order (account_id, category, ticker, outcome_leg, is_exit, fill_count, "
        " outcome_status, dry_run, submitted_ts, response_ts) VALUES ('kalshi_jack','mlb',?,?,?,?,?,?,1,1)",
        (ticker, leg, is_exit, fill, status, dry))
    conn.commit()


def test_net_open_entry_minus_exits(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    T = "KXMLBGAME-26AUG281915SEATOR-TOR"
    with db.connect(p) as conn:
        assert ex.journal_net_open_contracts(conn, "kalshi_jack", "mlb", T, "yes") == 0.0   # nothing yet
        _order(conn, T, "yes", 0, 5.0)                                   # entry 5
        assert ex.journal_net_open_contracts(conn, "kalshi_jack", "mlb", T, "yes") == 5.0
        _order(conn, T, "yes", 1, 2.0)                                   # exit 2 -> net 3
        assert ex.journal_net_open_contracts(conn, "kalshi_jack", "mlb", T, "yes") == 3.0
        _order(conn, T, "yes", 1, 3.0)                                   # exit 3 -> net 0 (flat)
        assert ex.journal_net_open_contracts(conn, "kalshi_jack", "mlb", T, "yes") == 0.0
        # a dry_run row and a non-filled row must NOT count
        _order(conn, T, "yes", 0, 9.0, dry=1)
        _order(conn, T, "yes", 0, 9.0, status="no_fill")
        assert ex.journal_net_open_contracts(conn, "kalshi_jack", "mlb", T, "yes") == 0.0
        # case-insensitive ticker + leg isolation
        assert ex.journal_net_open_contracts(conn, "kalshi_jack", "mlb", T.lower(), "yes") == 0.0
        assert ex.journal_net_open_contracts(conn, "kalshi_jack", "mlb", T, "no") == 0.0


# ── the NO-leg exit through the chokepoint (sell NO -> side=bid, reduce_only) ──
_TOTAL_TICKERS = ["KXMLBTOTAL-26AUG281915SEATOR-9", "KXMLBTOTAL-26AUG281915SEATOR-8"]
_MARKETS = {"KXMLBTOTAL-26AUG281915SEATOR-9": {"yes_ask_dollars": 0.52, "yes_bid_dollars": 0.50,
            "no_ask_dollars": 0.50, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"}}


def test_no_leg_exit_reduce_only_side_bid(tmp_path):
    """An exit of a NO-leg holding (Under) -> sell NO = side 'bid', reduce_only (the mirror of the NO-leg entry
    which is side 'ask'). The NO-leg-inversion lens on the EXIT path -- only reachable after a NO entry (which
    trips the standing NO-leg STOP), but proven correct here regardless."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    ctx = ex.MarketContext({}, M.build_kalshi_total_index(_TOTAL_TICKERS), {}, frozenset({"2026-08-28"}), _MARKETS)
    sub = ex.SubConfig(account_id="kalshi_jack", category="mlb", market_types=("total",), sizing_mode="fixed",
                       fixed_stake_usd=5.0, per_order_usd_cap=25.0, daily_usd_cap=50.0, max_open_usd=100.0,
                       max_orders_per_day=25, max_slippage_cents=2)
    sig = ex.CopySignal(wallet=W, slug="mlb-sea-tor-2026-08-28-total-8pt5", outcome="Under",
                        condition_id=CID, outcome_index=1, signal_id="ex_no", is_exit=True)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [sub.account_id], 1787900000)
        d = ex.evaluate(sig, sub, ctx, j, conn, 1787900000)
    assert d.status == "dry_run_would_place" and d.is_exit is True and d.leg == "no"
    assert d.body.get("reduce_only") is True and d.body["side"] == "bid"
