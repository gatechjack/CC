"""Shard money-mgmt RUNG 3 -- explicit `exchange_index` on the V2 order body (PM-only; the SHARED broker
`build_v2_event_order` is NOT edited -- the field is set in execution.evaluate after the body is built). Sets
`body["exchange_index"]` = the MARKET's OWN shard when KNOWN -> DETERMINISTIC routing (correction 4: bills only that
shard's Write budget + avoids the auto-route latency, vs auto-route billing the unscoped + every nonzero shard).
Unknown/None -> OMIT -> auto-route (byte-identical to the prior body). The body carrying it IS decision.body, so the
dry-run body and the POSTed body stay identical (option-b parity). Offline."""
import pytest

from trading_corp.prediction_markets import db, execution as ex, shard_balance as sb
from trading_corp.data import mlb_poly_kalshi_match as M

ACCT, CAT = "kalshi_jack", "mlb"
NOW = 1787900000
GAME = ["KXMLBGAME-26AUG281915SEATOR-SEA", "KXMLBGAME-26AUG281915SEATOR-TOR"]
TOTAL = ["KXMLBTOTAL-26AUG281915SEATOR-9"]
SPREAD = ["KXMLBSPREAD-26AUG281915SEATOR-TOR2"]
T_TOR = "KXMLBGAME-26AUG281915SEATOR-TOR"
SLUG = "mlb-sea-tor-2026-08-28"


def _markets(tor_shard=3, liq=500.0):
    m = {T_TOR: {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.53, "no_ask_dollars": 0.47, "no_bid_dollars": 0.45,
                 "yes_ask_size_fp": "500.00", "yes_bid_size_fp": "500.00"},
         "KXMLBGAME-26AUG281915SEATOR-SEA": {"yes_ask_dollars": 0.47, "yes_bid_dollars": 0.45, "no_ask_dollars": 0.55,
                                             "no_bid_dollars": 0.53, "yes_ask_size_fp": "500.00", "yes_bid_size_fp": "500.00"}}
    if tor_shard is not None:
        m[T_TOR]["exchange_index"] = tor_shard
        m["KXMLBGAME-26AUG281915SEATOR-SEA"]["exchange_index"] = tor_shard
    return m


def _ctx(markets):
    return ex.MarketContext(M.build_kalshi_game_index(GAME), M.build_kalshi_total_index(TOTAL),
                            M.build_kalshi_spread_index(SPREAD), frozenset({"2026-08-28"}), markets)


def _sub():
    return ex.SubConfig(account_id=ACCT, category=CAT, market_types=("moneyline", "total", "spread"), sizing_mode="fixed",
                        fixed_stake_usd=5.0, per_order_usd_cap=25.0, daily_usd_cap=50.0, max_open_usd=100.0,
                        max_orders_per_day=25, max_slippage_cents=2, liquidity_ratio=0.75)


def _sig(is_exit=False, sid="s1"):
    return ex.CopySignal(wallet="0xWHALE", slug=SLUG, outcome="Toronto Blue Jays", condition_id="0xc_" + sid,
                         outcome_index=0, signal_id=sid, is_exit=is_exit)


def _bal(shards):
    bd = [{"exchange_index": int(k), "balance": "%.4f" % v} for k, v in shards.items()]
    return sb.parse_balance({"balance_dollars": "%.4f" % sum(shards.values()), "balance_breakdown": bd})


def _eval(tmp_path, ctx, shard_balances, sig=None):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        return ex.evaluate(sig or _sig(), _sub(), ctx, ex.Journal(conn, [ACCT], NOW), conn, NOW,
                           shard_balances=shard_balances, legacy_db_path=str(tmp_path / "noleg.db"))


def test_body_carries_market_shard_when_known(tmp_path):
    d = _eval(tmp_path, _ctx(_markets(tor_shard=3)), _bal({3: 500.0}))
    assert d.status == "dry_run_would_place"
    assert d.body["exchange_index"] == 3                       # explicit -> deterministic routing to shard 3


def test_body_shard0_is_set_explicitly(tmp_path):
    # shard 0 is a valid shard and FALSY -- it must be set (int 0), not dropped
    d = _eval(tmp_path, _ctx(_markets(tor_shard=0)), _bal({0: 500.0}))
    assert d.status == "dry_run_would_place"
    assert d.body["exchange_index"] == 0 and isinstance(d.body["exchange_index"], int)


def test_body_omits_exchange_index_when_shard_unknown(tmp_path):
    # market without exchange_index + gate 6b OFF (shard_balances=None) -> body OMITS the field -> auto-route
    d = _eval(tmp_path, _ctx(_markets(tor_shard=None)), None)
    assert d.status == "dry_run_would_place"
    assert "exchange_index" not in d.body                      # byte-identical to the prior (auto-route) body


def test_body_regression_other_fields_intact(tmp_path):
    d = _eval(tmp_path, _ctx(_markets(tor_shard=3)), _bal({3: 500.0}))
    for k in ("ticker", "client_order_id", "side", "count", "price", "time_in_force", "self_trade_prevention_type",
              "post_only"):
        assert k in d.body, k                                  # the shared builder's fields are untouched
    assert d.body["ticker"] == T_TOR.upper() and d.body["count"].isdigit() and int(d.body["count"]) >= 1


def test_corrupt_shard_coerces_or_fails_closed(tmp_path):
    # defensive (fail-closed lens): a string-int coerces; a non-numeric shard -> None -> omit (auto-route), no crash
    m = _markets(tor_shard=3); m[T_TOR]["exchange_index"] = "3"
    d = _eval(tmp_path, _ctx(m), None)                        # gate 6b OFF -> reach the body
    assert d.status == "dry_run_would_place" and d.body["exchange_index"] == 3
    m2 = _markets(tor_shard=3); m2[T_TOR]["exchange_index"] = "bogus"
    d2 = _eval(tmp_path, _ctx(m2), None)
    assert d2.status == "dry_run_would_place" and "exchange_index" not in d2.body


def test_exit_body_also_routes_explicitly(tmp_path):
    # a reduce_only EXIT is NOT shard-gated, but its body should still route deterministically to the market's shard
    d = _eval(tmp_path, _ctx(_markets(tor_shard=3)), _bal({3: 0.0}), sig=_sig(is_exit=True, sid="ex"))
    assert d.status == "dry_run_would_place" and d.is_exit is True
    assert d.body["exchange_index"] == 3 and d.body.get("reduce_only") is True
