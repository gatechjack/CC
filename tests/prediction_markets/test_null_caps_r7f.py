"""R7.f pre-arm -- NULL sub-division CAPS resolve to CONFIG_DEFAULTS, not to unbounded / crash (Jack: "prove it
with a test, not by reading it", 2026-08-30). The live `kalshi_jack/mlb` sub-division (observed 2026-08-30T17:18Z)
has fixed_stake_usd=0.01 and EVERY OTHER cap NULL on disk: per_order_usd_cap, daily_usd_cap, max_open_usd,
max_orders_per_day, max_slippage_cents, liquidity_ratio. Arming means UNATTENDED operation, so these caps are the
only thing standing between "one order" and "every order SDTrading makes." This proves, for ALL caps (not just
liquidity_ratio, which was already guarded):
  - every cap field has a CONFIG_DEFAULTS entry -> `cap(k)` can never KeyError on a NULL column;
  - the LIVE row shape (all caps NULL but fixed_stake=0.01) resolves each NULL to the code default via
    sub_config_from_row -- the SOLE SubConfig( builder, sole callers live_driver.py:332/353 (no raw-row bypass);
  - an all-None row does NOT crash and yields finite, typed values (no None reaches gate arithmetic);
  - gate 6 ENFORCES the resolved cap (a real bound, NOT "present but constraining nothing");
  - the NULL-derived config is fully functional (a 1-contract order reaches dry_run_would_place, bounded).
Offline; tmp DBs; mirrors test_liquidity_floor_r7f's harness."""
import math
from trading_corp.prediction_markets import db, execution as ex
from trading_corp.data import mlb_poly_kalshi_match as M

NOW = 1787900000
ACCT, CAT = "kalshi_jack", "mlb"
GAME = ["KXMLBGAME-26AUG281915SEATOR-SEA", "KXMLBGAME-26AUG281915SEATOR-TOR"]
TOTAL = ["KXMLBTOTAL-26AUG281915SEATOR-9"]
SPREAD = ["KXMLBSPREAD-26AUG281915SEATOR-TOR2"]
T_TOR = "KXMLBGAME-26AUG281915SEATOR-TOR"
ML_SLUG = "mlb-sea-tor-2026-08-28"

# What each NULL column MUST resolve to (the EXPECTED contract; asserted to come out of the LOADER, re-listed here
# rather than imported so a silent change to CONFIG_DEFAULTS is caught, not mirrored):
EXPECT_DEFAULT = {"per_order_usd_cap": 25.0, "daily_usd_cap": 50.0, "max_open_usd": 100.0,
                  "max_orders_per_day": 25, "max_slippage_cents": 2, "liquidity_ratio": 0.75}
CAP_FIELDS = ("fixed_stake_usd", "per_order_usd_cap", "daily_usd_cap", "max_open_usd",
              "max_orders_per_day", "max_slippage_cents", "liquidity_ratio")


def _markets(liq=500.0):
    return {T_TOR: {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.53, "no_ask_dollars": 0.47, "liquidity_dollars": liq},
            "KXMLBGAME-26AUG281915SEATOR-SEA": {"yes_ask_dollars": 0.47, "yes_bid_dollars": 0.45, "no_ask_dollars": 0.55, "liquidity_dollars": 500}}


def _ctx(markets):
    return ex.MarketContext(M.build_kalshi_game_index(GAME), M.build_kalshi_total_index(TOTAL),
                            M.build_kalshi_spread_index(SPREAD), frozenset({"2026-08-28"}), markets)


def _sig(sid="s1"):
    return ex.CopySignal(wallet="0xWHALE", slug=ML_SLUG, outcome="Toronto Blue Jays",
                         condition_id="0xc_" + sid, outcome_index=0, signal_id=sid)


def _insert_live_shaped_row(conn):
    # EXACTLY the live kalshi_jack/mlb shape: fixed_stake_usd set, every other cap left NULL.
    conn.execute("INSERT INTO pm_subdivision(account_id,category,market_types,sizing_mode,fixed_stake_usd,active,created_ts) "
                 "VALUES(?,?,?,?,?,1,?)", (ACCT, CAT, "moneyline,total,spread", "fixed", 0.01, NOW))
    conn.commit()
    return conn.execute("SELECT * FROM pm_subdivision WHERE account_id=? AND category=?", (ACCT, CAT)).fetchone()


# (0) every cap has a CONFIG_DEFAULT -> a NULL column can NEVER KeyError inside cap(k). Proves (c): NULL is SAFE,
#     not open, for ALL caps -- there is no cap without a default.
def test_every_cap_has_a_config_default():
    for k in CAP_FIELDS:
        assert k in ex.CONFIG_DEFAULTS, "cap %r has NO CONFIG_DEFAULT -> a NULL column would be unsafe/open" % k


# (1) THE LIVE ROW SHAPE: caps NULL on disk -> each resolves to the code default; every resolved value is finite.
def test_live_null_caps_resolve_to_config_defaults(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        r = _insert_live_shaped_row(conn)
    for k in EXPECT_DEFAULT:                       # every non-fixed_stake cap is NULL ON DISK (mirrors live)
        assert r[k] is None, "%s expected NULL on disk (live-row shape)" % k
    sub = ex.sub_config_from_row(r)
    assert sub.fixed_stake_usd == 0.01             # the ONLY explicitly-set cap
    assert sub.per_order_usd_cap == 25.0
    assert sub.daily_usd_cap == 50.0
    assert sub.max_open_usd == 100.0               # gate 6 compares against THIS finite number, never None
    assert sub.max_orders_per_day == 25 and isinstance(sub.max_orders_per_day, int)
    assert sub.max_slippage_cents == 2 and isinstance(sub.max_slippage_cents, int)
    assert sub.liquidity_ratio == 0.75
    for k in ("fixed_stake_usd", "per_order_usd_cap", "daily_usd_cap", "max_open_usd", "liquidity_ratio"):
        v = getattr(sub, k); assert isinstance(v, float) and math.isfinite(v), "%s not finite float: %r" % (k, v)


# (2) an all-None row does NOT crash and yields typed, finite, defaulted values (crash-proof; no None reaches gates).
def test_all_none_row_does_not_crash():
    row = {"account_id": ACCT, "category": CAT, "market_types": "moneyline", "sizing_mode": None,
           "fixed_stake_usd": None, "per_order_usd_cap": None, "daily_usd_cap": None, "max_open_usd": None,
           "max_orders_per_day": None, "max_slippage_cents": None, "liquidity_ratio": None}
    sub = ex.sub_config_from_row(row)              # must NOT raise
    assert sub.fixed_stake_usd == 5.0              # fixed_stake NULL -> CONFIG default 5.0 (live row overrides to 0.01)
    assert sub.max_open_usd == 100.0
    assert sub.max_orders_per_day == 25 and isinstance(sub.max_orders_per_day, int)
    assert sub.max_slippage_cents == 2 and isinstance(sub.max_slippage_cents, int)
    assert sub.sizing_mode == "fixed"              # None -> 'fixed'


# (3) gate 6 ENFORCES the resolved exposure cap -> the NULL-derived 100.0 is a REAL bound, refuting the
#     "present but constraining nothing" failure class. max_open_usd=0 -> even a 1-contract order rejects.
def test_gate6_enforces_resolved_exposure_cap(tmp_path):
    sub = ex.SubConfig(account_id=ACCT, category=CAT, market_types=("moneyline", "total", "spread"), sizing_mode="fixed",
                       fixed_stake_usd=0.01, per_order_usd_cap=25.0, daily_usd_cap=50.0, max_open_usd=0.0,
                       max_orders_per_day=25, max_slippage_cents=2, liquidity_ratio=0.75)
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        d = ex.evaluate(_sig(), sub, _ctx(_markets()), ex.Journal(conn, [ACCT], NOW), conn, NOW,
                        legacy_db_path=str(tmp_path / "noleg.db"))
    assert d.status == "reject:exposure_cap", "gate 6 did not enforce max_open_usd -> got %s" % d.status


# (4) gate 8 ENFORCES the resolved order-count ceiling (the cap that turns "one order" into a hard stop when set to 1).
def test_gate8_enforces_resolved_order_count(tmp_path):
    sub = ex.SubConfig(account_id=ACCT, category=CAT, market_types=("moneyline", "total", "spread"), sizing_mode="fixed",
                       fixed_stake_usd=0.01, per_order_usd_cap=25.0, daily_usd_cap=50.0, max_open_usd=100.0,
                       max_orders_per_day=1, max_slippage_cents=2, liquidity_ratio=0.75)
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        d1 = ex.evaluate(_sig("a"), sub, _ctx(_markets()), j, conn, NOW, legacy_db_path=str(tmp_path / "noleg.db"))
        assert d1.status == "dry_run_would_place"                 # 1st order allowed (0+1 not > 1); evaluate itself
        # increments orders_today via commit_would_place (execution.py:270) -- no manual journal poke needed:
        d2 = ex.evaluate(_sig("b"), sub, _ctx(_markets()), j, conn, NOW, legacy_db_path=str(tmp_path / "noleg.db"))
    assert d2.status == "reject:count_ceiling", "gate 8 did not enforce max_orders_per_day=1 -> got %s" % d2.status


# (5) the NULL-derived live config is fully FUNCTIONAL: a 1-contract order reaches dry_run_would_place, bounded.
def test_null_derived_config_reaches_would_place(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        sub = ex.sub_config_from_row(_insert_live_shaped_row(conn))
        d = ex.evaluate(_sig(), sub, _ctx(_markets()), ex.Journal(conn, [ACCT], NOW), conn, NOW,
                        legacy_db_path=str(tmp_path / "noleg.db"))
    assert d.status == "dry_run_would_place"
    assert d.count == 1 and d.notional_usd < 1.0
