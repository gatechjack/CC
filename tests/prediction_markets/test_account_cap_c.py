"""C (2026-09-03): the ACCOUNT-LEVEL aggregate cap -- gate 5b (daily) + gate 8b (count). The account total stays
$150/day + 50 orders ACROSS ALL its categories (not per-category, which would double to $300/100). Race-free under
Option C's SHARED Journal (the account aggregate sees every category's in-cycle commit, the same mechanism as gate 6).
Proves: the aggregate binds across categories; BYTE-IDENTICAL with one category at $150 (gate 5/8 fire first); headroom
FLOWS to the active category and the total never doubles. The sibling category's spend is a DIRECT journal commit (the
matcher is category-specific; the cap is category-agnostic) while the evaluated signal is a matching mlb bet."""
from trading_corp.prediction_markets import db, execution as ex
from trading_corp.data import mlb_poly_kalshi_match as M

GAME = ["KXMLBGAME-26AUG281915SEATOR-SEA", "KXMLBGAME-26AUG281915SEATOR-TOR"]
MARKETS = {
    "KXMLBGAME-26AUG281915SEATOR-TOR": {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.53, "no_ask_dollars": 0.47, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"},
    "KXMLBGAME-26AUG281915SEATOR-SEA": {"yes_ask_dollars": 0.47, "yes_bid_dollars": 0.45, "no_ask_dollars": 0.55, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"},
}
NOW = 1787900000
ACCT = "kalshi_jack"


def _ctx():
    return ex.MarketContext(M.build_kalshi_game_index(GAME), {}, {}, frozenset({"2026-08-28"}), MARKETS)


def _sub(**over):
    base = dict(account_id=ACCT, category="mlb", market_types=("moneyline",), sizing_mode="fixed",
                fixed_stake_usd=5.0, per_order_usd_cap=25.0, daily_usd_cap=150.0, max_open_usd=100000.0,
                max_orders_per_day=50, max_slippage_cents=2)
    base.update(over); return ex.SubConfig(**base)


def _sig(sid):
    return ex.CopySignal(wallet="0xW", slug="mlb-sea-tor-2026-08-28", outcome="Toronto Blue Jays",
                         condition_id="0xc_" + sid, outcome_index=0, signal_id=sid)


def _ev(conn, j, sid="e", sub=None):
    return ex.evaluate(_sig(sid), sub or _sub(), _ctx(), j, conn, NOW)


def test_c_journal_account_aggregates_sum_across_categories(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        j.commit_would_place(ACCT, "mlb", 100.0); j.commit_would_place(ACCT, "ufc", 30.0); j.commit_would_place(ACCT, "ufc", 5.0)
    assert j.daily_usd_account(ACCT) == 135.0                         # sum ACROSS categories
    assert j.orders_today_account(ACCT) == 3
    assert j.daily_usd(ACCT, "mlb") == 100.0 and j.daily_usd(ACCT, "ufc") == 35.0   # per-category still separate


def test_c_gate5b_account_daily_cap_binds_across_categories(tmp_path):
    """A sibling category (ufc) consumed $148 of the account cap; an mlb entry (~$5) pushes the ACCOUNT past $150 ->
    reject:account_daily_cap, even though mlb's OWN per-category daily is $0. This is the invisible failure mode
    (per-category caps each look fine while the total doubles) -- the aggregate is the number that holds."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        j.commit_would_place(ACCT, "ufc", 148.0)                     # sibling category's spend (direct)
        d = _ev(conn, j)
    assert d.status == "reject:account_daily_cap"


def test_c_byte_identical_one_category_gate5_fires_first(tmp_path):
    """ONE category at the ruled $150: gate 5 (per-cat) and gate 5b (account) share the threshold; gate 5 is checked
    FIRST, so an over-cap mlb entry rejects:daily_cap (NOT account_daily_cap). BYTE-IDENTICAL to pre-C."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        j.commit_would_place(ACCT, "mlb", 148.0)                     # same category
        d = _ev(conn, j)
    assert d.status == "reject:daily_cap"                            # gate 5 binds first, not gate 5b


def test_c_gate8b_account_count_ceiling_binds_across_categories(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        for _ in range(50):
            j.commit_would_place(ACCT, "ufc", 1.0)                   # 50 sibling orders (account count = 50)
        d = _ev(conn, j)
    assert d.status == "reject:account_count_ceiling"                # the 51st account order, though mlb's own count is 0


def test_c_headroom_flows_to_active_category_and_total_holds(tmp_path):
    """Headroom FLOWS: a quiet-ish account (ufc took $145) lets mlb use the REMAINING ~$5 (the deliberate reason C was
    chosen over the 75/75 divide -- a quiet co-category does not strand its share). Then the account total HOLDS at
    $150: the next mlb entry rejects:account_daily_cap. Whichever category is active consumes the shared cap; the total
    never doubles."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        j.commit_would_place(ACCT, "ufc", 140.0)                     # sibling used most of the cap ($10 headroom left)
        d1 = _ev(conn, j, sid="h1")                                  # mlb uses the remaining headroom (~$5.13 fits in $10)
        assert d1.status == "dry_run_would_place"                    # evaluate committed mlb's entry -> account ~$145.13
        d2 = _ev(conn, j, sid="h2")                                  # the next mlb entry would push the ACCOUNT past $150
    assert d2.status == "reject:account_daily_cap"                   # total HOLDS at $150 (never doubles); headroom flowed
