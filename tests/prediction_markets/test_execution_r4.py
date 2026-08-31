"""Stage 3 R4 -- the central execution CHOKEPOINT, DRY-RUN. Real Kalshi tickers + real slug formats (SEA@TOR
2026-08-28). Proves: well-formed V2 bodies for moneyline/total/spread INCLUDING a NO-leg (the $163.84-class
inversion), stable idempotent client_order_id, every gate, Option-D exit detection (both signals or MISSED), caps as
in-memory counters, and -- structurally -- ZERO outbound POSTs (the module holds no broker)."""
import inspect

import pytest

from trading_corp.prediction_markets import db, execution as ex
from trading_corp.data import mlb_poly_kalshi_match as M

GAME_TICKERS = ["KXMLBGAME-26AUG281915SEATOR-SEA", "KXMLBGAME-26AUG281915SEATOR-TOR"]
TOTAL_TICKERS = ["KXMLBTOTAL-26AUG281915SEATOR-9", "KXMLBTOTAL-26AUG281915SEATOR-8"]     # 8.5, 7.5
SPREAD_TICKERS = ["KXMLBSPREAD-26AUG281915SEATOR-TOR2", "KXMLBSPREAD-26AUG281915SEATOR-SEA2"]  # 1.5
MARKETS = {
    "KXMLBGAME-26AUG281915SEATOR-TOR": {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.53, "no_ask_dollars": 0.47, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"},
    "KXMLBGAME-26AUG281915SEATOR-SEA": {"yes_ask_dollars": 0.47, "yes_bid_dollars": 0.45, "no_ask_dollars": 0.55, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"},
    "KXMLBTOTAL-26AUG281915SEATOR-9":  {"yes_ask_dollars": 0.52, "yes_bid_dollars": 0.50, "no_ask_dollars": 0.50, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"},
    "KXMLBSPREAD-26AUG281915SEATOR-TOR2": {"yes_ask_dollars": 0.40, "yes_bid_dollars": 0.38, "no_ask_dollars": 0.62, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"},
}


def _ctx(markets=None):
    return ex.MarketContext(
        M.build_kalshi_game_index(GAME_TICKERS), M.build_kalshi_total_index(TOTAL_TICKERS),
        M.build_kalshi_spread_index(SPREAD_TICKERS), frozenset({"2026-08-28"}),
        MARKETS if markets is None else markets)


def _sub(**over):
    base = dict(account_id="kalshi_jack", category="mlb", market_types=("moneyline", "total", "spread"),
                sizing_mode="fixed", fixed_stake_usd=5.0, per_order_usd_cap=25.0, daily_usd_cap=50.0,
                max_open_usd=100.0, max_orders_per_day=25, max_slippage_cents=2)
    base.update(over)
    return ex.SubConfig(**base)


def _sig(slug, outcome, sid="s1", is_exit=False, wallet="0x16bb9951a36fce71e2ef57890b786145e0ba8492"):
    return ex.CopySignal(wallet=wallet, slug=slug, outcome=outcome, condition_id="0xcond_" + sid,
                         outcome_index=0, signal_id=sid, is_exit=is_exit)


def _one(conn, sig, sub=None):
    sub = sub or _sub()
    j = ex.Journal(conn, [sub.account_id], 1787900000)
    return ex.evaluate(sig, sub, _ctx(), j, conn, 1787900000)


# ── structural: the module cannot place ─────────────────────────────────────
def test_structurally_unable_to_place():
    assert "KalshiLiveBroker" not in dir(ex)       # broker never imported into the namespace
    assert "place_order" not in dir(ex)
    assert "asyncio" not in dir(ex) and "httpx" not in dir(ex) and "requests" not in dir(ex)  # no network
    assert "broker" not in inspect.signature(ex.dry_run_subdivision).parameters                # takes no broker
    assert "broker" not in inspect.signature(ex.evaluate).parameters


# ── moneyline / total / spread bodies -- correct side & price, incl. the NO leg ─────────────
def test_moneyline_yes_body(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        d = _one(conn, _sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays"))
    assert d.status == "dry_run_would_place" and d.market_type == "moneyline"
    assert d.kalshi_ticker == "KXMLBGAME-26AUG281915SEATOR-TOR" and d.leg == "yes"
    assert d.body["side"] == "bid" and d.body["price"] == "0.5700" and d.body["count"] == "9"   # 0.55+0.02; floor(5/.55)


def test_total_over_yes_and_under_NO_leg_inversion(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        over = _one(conn, _sig("mlb-sea-tor-2026-08-28-total-8pt5", "Over", sid="ov"))
        under = _one(conn, _sig("mlb-sea-tor-2026-08-28-total-8pt5", "Under", sid="un"))
    assert over.leg == "yes" and over.body["side"] == "bid"                         # Over -> buy YES
    # ★ Under = the NO leg: body priced from the YES side (1 - no_ask); no_ask 0.50 -> yes_equiv 0.50 -> ask 0.48.
    assert under.leg == "no" and under.body["side"] == "ask" and under.body["price"] == "0.4800"
    assert under.kalshi_ticker == over.kalshi_ticker == "KXMLBTOTAL-26AUG281915SEATOR-9"
    # ★★ CRITICAL: the NO-leg NOTIONAL/cap gates on the OUTCOME-leg cost (1 - 0.48 = 0.52), NOT the yes-side 0.48
    # (the $163.84-class fix). count=floor(5/0.50)=10 -> notional = 10*0.52 = 5.20, never 10*0.48 = 4.80.
    assert abs(under.notional_usd - 5.20) < 1e-6
    assert abs(over.notional_usd - (9 * 0.54)) < 1e-6                                # YES leg: 9 * limit 0.54


def test_spread_anchor_yes_other_NO(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        anchor = _one(conn, _sig("mlb-sea-tor-2026-08-28-spread-home-1pt5", "Toronto Blue Jays", sid="an"))  # home=tor anchor
        other = _one(conn, _sig("mlb-sea-tor-2026-08-28-spread-home-1pt5", "Seattle Mariners", sid="ot"))
    assert anchor.leg == "yes" and anchor.kalshi_ticker == "KXMLBSPREAD-26AUG281915SEATOR-TOR2"
    assert anchor.body["side"] == "bid" and anchor.body["price"] == "0.4200"        # 0.40+0.02
    assert other.leg == "no" and other.kalshi_ticker == "KXMLBSPREAD-26AUG281915SEATOR-TOR2"
    assert other.body["side"] == "ask" and other.body["price"] == "0.3600"          # 1-0.62=0.38 -> ask 0.36


# ── gates ───────────────────────────────────────────────────────────────────
def test_gate_disarm_recorded_but_dry_run_still_computes(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        d = _one(conn, _sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays"))
    assert d.disarm_armed is False and d.status == "dry_run_would_place"   # absent arm state -> disarmed; dry-run computes


def test_gate2a_fixed_stake_over_cap_rejects(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        d = _one(conn, _sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays"), sub=_sub(per_order_usd_cap=1.0))
    assert d.status == "reject:per_order_cap"


def test_gate2b_notional_on_usd_catches_wrong_low_price(tmp_path):
    """gate 2b is on USD (count * LIMIT price), not a raw contract count. A wrong-LOW leg price inflates the count
    AND (via slippage on a tiny price) the committed USD -> rejected. A range check on price alone would miss it."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    mk = dict(MARKETS)
    mk["KXMLBGAME-26AUG281915SEATOR-TOR"] = {"yes_ask_dollars": 0.02, "yes_bid_dollars": 0.019, "no_ask_dollars": 0.97, "yes_bid_size_fp": "500000.00", "yes_ask_size_fp": "500000.00"}
    sub = _sub(fixed_stake_usd=20.0, per_order_usd_cap=25.0)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [sub.account_id], 1787900000)
        d = ex.evaluate(_sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays"), sub, _ctx(mk), j, conn, 1787900000)
    # count=floor(20/0.02)=1000; limit=0.02+0.02=0.04; notional=1000*0.04=$40 > $25 cap -> reject on USD, not count
    assert d.status == "reject:per_order_cap" and d.count == 1000 and abs(d.notional_usd - 40.0) < 1e-6
    # a normal-priced order of the same stake is fine (USD basis, not contract count)
    with db.connect(p) as conn:
        d2 = _one(conn, _sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays"), sub=_sub(fixed_stake_usd=5.0))
    assert d2.status == "dry_run_would_place"


def test_gate_market_type_excluded_is_a_skip(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        d = _one(conn, _sig("mlb-sea-tor-2026-08-28-total-8pt5", "Over"), sub=_sub(market_types=("moneyline",)))
    assert d.status == "skip:skip_market_type_excluded" and d.market_type == "total"


def test_gate_no_match_and_far_tail_are_skips(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        far = _one(conn, _sig("mlb-sea-tor-2026-08-28-total-15pt5", "Over"))        # far-tail strike absent
        oow = _one(conn, _sig("mlb-sea-tor-2020-01-01", "Toronto Blue Jays"))       # game not in window
    assert far.status == "skip:no_kalshi_strike" and far.kalshi_ticker is None
    assert oow.status == "skip:out_of_window"


def test_gate_illiquid_and_no_quote_skip(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    thin = {"KXMLBGAME-26AUG281915SEATOR-TOR": {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.20, "no_ask_dollars": 0.47, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"}}
    noq = {"KXMLBGAME-26AUG281915SEATOR-TOR": {"yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"}}           # no yes_ask -> no quote
    with db.connect(p) as conn:
        j = ex.Journal(conn, ["kalshi_jack"], 1787900000)
        d_thin = ex.evaluate(_sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays"), _sub(), _ctx(thin), j, conn, 1787900000)
        d_noq = ex.evaluate(_sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays"), _sub(), _ctx(noq), j, conn, 1787900000)
    assert d_thin.status == "skip:illiquid"        # 35c spread
    assert d_noq.status == "skip:no_quote"


def test_idempotency_key_stable_and_wallet_based(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        a = _one(conn, _sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays", sid="X"))
        b = _one(conn, _sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays", sid="X"))  # same signal -> same coid
        c = _one(conn, _sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays", sid="Y"))  # different signal_id -> different
    assert a.client_order_id == b.client_order_id and a.client_order_id != c.client_order_id


def test_idempotency_skips_already_placed_via_journal(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    sub = _sub()
    with db.connect(p) as conn:
        d = _one(conn, _sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays", sid="Z"), sub=sub)
        assert d.status == "dry_run_would_place"
        # a PLACED (live) order with that coid already exists in the durable journal
        conn.execute("INSERT INTO pm_subdivision_order (account_id,category,client_order_id,dry_run,outcome_status,"
                     "submitted_count,submitted_price,response_ts) VALUES (?,?,?,0,'filled',1,0.5,?)",
                     (sub.account_id, sub.category, d.client_order_id, 1787900000))
        conn.commit()
        d2 = _one(conn, _sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays", sid="Z"), sub=sub)   # fresh journal seeds it
    assert d2.status == "skip:duplicate" and d2.client_order_id == d.client_order_id


# ── dry-run runner: writes dry_run rows, ZERO posts ─────────────────────────
def test_dry_run_writes_rows_and_zero_posts(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    sigs = [_sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays", sid="m"),
            _sig("mlb-sea-tor-2026-08-28-total-8pt5", "Under", sid="t"),
            _sig("mlb-sea-tor-2026-08-28-spread-home-1pt5", "Seattle Mariners", sid="s"),
            _sig("mlb-sea-tor-2020-01-01", "Toronto Blue Jays", sid="oow")]          # 3 would-place, 1 skip
    with db.connect(p) as conn:
        summ = ex.dry_run_subdivision(conn, _sub(), sigs, _ctx(), 1787900000)
        rows = conn.execute("SELECT COUNT(*) n, SUM(dry_run) d FROM pm_subdivision_order").fetchone()
        placed_live = conn.execute("SELECT COUNT(*) FROM pm_subdivision_order WHERE dry_run=0").fetchone()[0]
    assert summ["posts_sent"] == 0                          # ★ assert the zero
    assert summ["n_would_place"] == 3 and summ["n_skip"] == 1
    assert rows["n"] == 3 and rows["d"] == 3                # 3 rows, all dry_run=1
    assert placed_live == 0                                 # NOTHING placed


def test_daily_cap_trips_within_a_run(tmp_path):
    """The daily cap is an in-memory counter accumulating across a run -- a stream of copies trips it (proves the
    cap is enforced without a per-order scan)."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    sub = _sub(daily_usd_cap=8.0)                            # ~1 copy of ~$5 fits; the 2nd trips it
    sigs = [_sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays", sid="a"),
            _sig("mlb-sea-tor-2026-08-28-total-8pt5", "Over", sid="b")]
    with db.connect(p) as conn:
        summ = ex.dry_run_subdivision(conn, sub, sigs, _ctx(), 1787900000)
    assert summ["n_would_place"] == 1 and summ["n_reject"] == 1
    assert any(d.status == "reject:daily_cap" for d in summ["decisions"])


# ── whale-exit: Option D (both signals or MISSED), and an exit is reduce_only ────────────────
def test_exit_needs_both_signals_else_missed():
    sell = {"wallet": "0xw", "condition_id": "0xc", "outcome_index": 0, "ts": 1000, "tx_hash": "0xtx"}
    red = {"wallet": "0xw", "condition_id": "0xc", "outcome_index": 0, "ts": 1030, "slug": "mlb-sea-tor-2026-08-28", "outcome": "Toronto Blue Jays"}
    both = ex.detect_exit_signals([sell], [red], window_sec=120)
    assert len(both) == 1 and both[0].is_exit is True and both[0].wallet == "0xw"
    assert ex.detect_exit_signals([sell], [], window_sec=120) == []          # SELL only -> MISSED, no fallback
    assert ex.detect_exit_signals([], [red], window_sec=120) == []           # reduction only -> MISSED
    far = {"wallet": "0xw", "condition_id": "0xc", "outcome_index": 0, "ts": 5000, "slug": "mlb-sea-tor-2026-08-28", "outcome": "Toronto Blue Jays"}
    assert ex.detect_exit_signals([sell], [far], window_sec=120) == []       # out of window -> MISSED


def test_exit_transits_chokepoint_as_reduce_only(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        # seed a FILLED holding (net-open 5 YES) on the SIGNAL'S wallet so the EXIT has a position to close (per-wallet)
        conn.execute("INSERT INTO pm_subdivision_order (account_id,category,wallet,ticker,outcome_leg,is_exit,"
                     "fill_count,outcome_status,dry_run,submitted_ts,response_ts) VALUES "
                     "('kalshi_jack','mlb','0x16bb9951a36fce71e2ef57890b786145e0ba8492',"
                     "'KXMLBGAME-26AUG281915SEATOR-TOR','yes',0,5,'filled',0,1,1)"); conn.commit()
        d = _one(conn, _sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays", is_exit=True))
    assert d.status == "dry_run_would_place" and d.is_exit is True
    assert d.body.get("reduce_only") is True and d.body["side"] == "ask"      # sell YES to reduce, reduce_only set
    assert int(d.body["count"]) == 5                                          # ★ B1 FULL close = journal net-open (5)
    assert d.body["price"] == "0.5100"                                        # ★ marketable: yes_bid 0.53 - slip 0.02 (NOT ask-based)
