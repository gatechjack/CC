"""B2 (2026-09-03) -- per-category matcher DISPATCH at the chokepoint. Proves:
  * the MLB adapter is BYTE-IDENTICAL to the direct M.* calls evaluate used inline before B2 (the equivalence proof
    Jack asked for) -- so the MLB path cannot regress from the seam change;
  * an UNKNOWN category fail-SAFE skips (never matched with the wrong matcher);
  * a UFC ctx + ufc SubConfig routes to the UFC matcher and produces the right Kalshi ticker/leg for BOTH ufc types
    (moneyline per-fighter YES, go-the-distance) through the SAME gates/sizing/body as MLB;
  * MarketContext stays byte-identical for MLB construction (fight_index defaults None).
Real UFC ticker/title shapes are from the 2026-09-05 live card (probe pm_ufc_shape_probe_ro)."""
from trading_corp.prediction_markets import db, execution as ex
from trading_corp.data import mlb_poly_kalshi_match as M
from trading_corp.data import ufc_poly_kalshi_match as U

# ── MLB fixture (same tickers as test_execution_r4) ─────────────────────────────
GAME = ["KXMLBGAME-26AUG281915SEATOR-SEA", "KXMLBGAME-26AUG281915SEATOR-TOR"]
TOTAL = ["KXMLBTOTAL-26AUG281915SEATOR-9", "KXMLBTOTAL-26AUG281915SEATOR-8"]
SPREAD = ["KXMLBSPREAD-26AUG281915SEATOR-TOR2", "KXMLBSPREAD-26AUG281915SEATOR-SEA2"]
MLB_MARKETS = {
    "KXMLBGAME-26AUG281915SEATOR-TOR": {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.53, "no_ask_dollars": 0.47, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"},
    "KXMLBGAME-26AUG281915SEATOR-SEA": {"yes_ask_dollars": 0.47, "yes_bid_dollars": 0.45, "no_ask_dollars": 0.55, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"},
    "KXMLBTOTAL-26AUG281915SEATOR-9":  {"yes_ask_dollars": 0.52, "yes_bid_dollars": 0.50, "no_ask_dollars": 0.50, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"},
    "KXMLBSPREAD-26AUG281915SEATOR-TOR2": {"yes_ask_dollars": 0.40, "yes_bid_dollars": 0.38, "no_ask_dollars": 0.62, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"},
}


def _mlb_ctx():
    return ex.MarketContext(M.build_kalshi_game_index(GAME), M.build_kalshi_total_index(TOTAL),
                            M.build_kalshi_spread_index(SPREAD), frozenset({"2026-08-28"}), MLB_MARKETS)


# ── UFC fixture (real shapes from the 2026-09-05 card) ──────────────────────────
UFC_FIGHT = [
    {"ticker": "KXUFCFIGHT-26SEP05HOOPAR-HOO", "title": "Daniel Hooker wins"},
    {"ticker": "KXUFCFIGHT-26SEP05HOOPAR-PAR", "title": "Salahdine Parnasse wins"},
]
UFC_DISTANCE = [{"ticker": "KXUFCDISTANCE-26SEP05HOOPAR-DIST", "title": "Fight goes the distance?"}]
UFC_MARKETS = {
    "KXUFCFIGHT-26SEP05HOOPAR-HOO":     {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.53, "no_ask_dollars": 0.47, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00", "exchange_index": 0},
    "KXUFCFIGHT-26SEP05HOOPAR-PAR":     {"yes_ask_dollars": 0.47, "yes_bid_dollars": 0.45, "no_ask_dollars": 0.55, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00", "exchange_index": 0},
    "KXUFCDISTANCE-26SEP05HOOPAR-DIST": {"yes_ask_dollars": 0.20, "yes_bid_dollars": 0.17, "no_ask_dollars": 0.83, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00", "exchange_index": 0},
}


def _ufc_ctx():
    idx = U.build_kalshi_fight_index(UFC_FIGHT)
    idx = U.attach_distance_tickers(idx, UFC_DISTANCE)
    dates = frozenset(k[0] for k in idx)
    return ex.MarketContext({}, {}, {}, dates, UFC_MARKETS, fight_index=idx)


def _sub(**over):
    base = dict(account_id="kalshi_jack", category="mlb", market_types=("moneyline", "total", "spread"),
                sizing_mode="fixed", fixed_stake_usd=5.0, per_order_usd_cap=25.0, daily_usd_cap=50.0,
                max_open_usd=100.0, max_orders_per_day=25, max_slippage_cents=2)
    base.update(over)
    return ex.SubConfig(**base)


def _sig(slug, outcome, sid="s1", is_exit=False):
    return ex.CopySignal(wallet="0x16bb9951a36fce71e2ef57890b786145e0ba8492", slug=slug, outcome=outcome,
                         condition_id="0xcond_" + sid, outcome_index=0, signal_id=sid, is_exit=is_exit)


# ── MLB ADAPTER EQUIVALENCE (the byte-identical proof) ──────────────────────────
def test_mlb_adapter_equivalence_to_direct_match():
    """The mlb adapter's (parse, match) must equal the DIRECT M.parse_poly_mlb_bet + M.match_bet call evaluate used
    inline before B2 -- for matches AND honest misses. MatchResult is a frozen dataclass, so `==` is value equality."""
    ctx = _mlb_ctx()
    parse, match = ex.MATCHER_ADAPTERS["mlb"]
    mts = ("moneyline", "total", "spread")
    cases = [("mlb-sea-tor-2026-08-28", "Toronto Blue Jays"),          # moneyline yes
             ("mlb-sea-tor-2026-08-28", "Seattle Mariners"),           # moneyline other
             ("mlb-sea-tor-2026-08-28-total-8pt5", "Over"),            # total yes
             ("mlb-sea-tor-2026-08-28-total-8pt5", "Under"),           # total NO leg
             ("mlb-sea-tor-2026-08-28-spread-home-1pt5", "Toronto Blue Jays"),
             ("mlb-nope-nope-2026-08-28", "Nobody"),                   # honest miss
             ("nba-lal-bos-2026-08-28", "Los Angeles Lakers")]         # non-mlb slug
    for slug, outcome in cases:
        direct = M.match_bet(M.parse_poly_mlb_bet(slug, outcome), ctx.moneyline_index, ctx.total_index,
                             ctx.spread_index, ctx.kalshi_dates, allowed_market_types=mts)
        via = match(parse(slug, outcome), ctx, mts)
        assert via == direct, "adapter diverged from direct M.* for %r/%r: %r vs %r" % (slug, outcome, via, direct)


def test_marketcontext_mlb_construction_unchanged():
    """The 5-positional MLB MarketContext still constructs and fight_index defaults None (byte-identical shape)."""
    ctx = _mlb_ctx()
    assert ctx.fight_index is None
    assert ctx.moneyline_index and ctx.markets   # the MLB dims are populated as before


# ── unknown category fail-safe ──────────────────────────────────────────────────
def test_unknown_category_failsafe_skip(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        j = ex.Journal(conn, ["kalshi_jack"], 1787900000)
        d = ex.evaluate(_sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays"), _sub(category="xyz"),
                        _mlb_ctx(), j, conn, 1787900000)
    assert d.status == "skip:no_matcher_for_category"
    assert "xyz" in (d.reason or "")


# ── UFC dispatch: moneyline + go-the-distance route to the ufc matcher ──────────
def _eval(conn, sig, sub, ctx):
    j = ex.Journal(conn, [sub.account_id], 1787900000)
    return ex.evaluate(sig, sub, ctx, j, conn, 1787900000)


def test_ufc_moneyline_dispatch_matches_fighter(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    sub = _sub(category="ufc", market_types=("moneyline", "go_the_distance"))
    with db.connect(p) as conn:
        d = _eval(conn, _sig("ufc-dan6-salpar-2026-09-05", "Daniel Hooker", sid="mh"), sub, _ufc_ctx())
    assert d.status == "dry_run_would_place" and d.market_type == "moneyline"
    assert d.kalshi_ticker == "KXUFCFIGHT-26SEP05HOOPAR-HOO" and d.leg == "yes"


def test_ufc_distance_dispatch_matches(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    sub = _sub(category="ufc", market_types=("moneyline", "go_the_distance"))
    with db.connect(p) as conn:
        d = _eval(conn, _sig("ufc-dan6-salpar-2026-09-05-go-the-distance", "Yes", sid="gd"), sub, _ufc_ctx())
    assert d.status == "dry_run_would_place" and d.market_type == "go_the_distance"
    assert d.kalshi_ticker == "KXUFCDISTANCE-26SEP05HOOPAR-DIST" and d.leg == "yes"


def test_ufc_unknown_fighter_is_a_miss_not_a_wrong_pick(tmp_path):
    """An outcome name that is on the card's date but NOT a fighter in any fight -> a labelled skip, never a
    nearest-neighbour guess (the MLB exact-strike discipline, re-expressed for fighter identity)."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    sub = _sub(category="ufc", market_types=("moneyline", "go_the_distance"))
    with db.connect(p) as conn:
        d = _eval(conn, _sig("ufc-dan6-salpar-2026-09-05", "Some Nobody", sid="miss"), sub, _ufc_ctx())
    assert d.status.startswith("skip:") and d.kalshi_ticker is None


def test_ufc_market_type_excluded_when_not_configured(tmp_path):
    """A ufc sub configured moneyline-only skips a go-the-distance signal (scope gate), never mis-routes it."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    sub = _sub(category="ufc", market_types=("moneyline",))
    with db.connect(p) as conn:
        d = _eval(conn, _sig("ufc-dan6-salpar-2026-09-05-go-the-distance", "Yes", sid="ex"), sub, _ufc_ctx())
    assert d.status.startswith("skip:") and d.kalshi_ticker is None
