"""Stage 3 R7.f-prep -- the SCALING liquidity floor (Jack RULED 2026-08-29). Gate-3 required book depth =
`liquidity_ratio * THIS order's notional`, NOT a fixed $ and NOT per_order_usd_cap. The ratio is CONFIG
(pm_subdivision.liquidity_ratio, migration 012), READ PER CYCLE (execution.sub_config_from_row), DEFAULTED
0.75 in CONFIG_DEFAULTS (NULL column -> 0.75). Proves, per Jack's list:
  - a match that PASSES at 0.75x and would have FAILED at the old fixed $25 floor;
  - a match that FAILS even at 0.75x;
  - the boundary (liq == ratio*notional passes; just below skips);
  - the ratio is READ FROM CONFIG, not a constant (change it, the decision FLIPS) + the NULL->0.75 default;
  - ★ the floor scales with the CORRECT-LEG notional -- a NO leg uses (1-yes_price), never the yes side (the
    NO-leg lens, bitten 6x).
Offline; tmp DBs; SubConfig drives the ratio."""
import pytest
from trading_corp.prediction_markets import db, execution as ex
from trading_corp.data import mlb_poly_kalshi_match as M

NOW = 1787900000
GAME = ["KXMLBGAME-26AUG281915SEATOR-SEA", "KXMLBGAME-26AUG281915SEATOR-TOR"]
TOTAL = ["KXMLBTOTAL-26AUG281915SEATOR-9"]
SPREAD = ["KXMLBSPREAD-26AUG281915SEATOR-TOR2"]
T_TOR = "KXMLBGAME-26AUG281915SEATOR-TOR"
T_TOT = "KXMLBTOTAL-26AUG281915SEATOR-9"
ACCT, CAT = "kalshi_jack", "mlb"
ML_SLUG = "mlb-sea-tor-2026-08-28"
TOT_SLUG = "mlb-sea-tor-2026-08-28-total-8pt5"


def _mk(liq_tor=500.0, liq_tot=500.0):
    return {
        T_TOR: {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.53, "no_ask_dollars": 0.47, "liquidity_dollars": liq_tor},
        "KXMLBGAME-26AUG281915SEATOR-SEA": {"yes_ask_dollars": 0.47, "yes_bid_dollars": 0.45, "no_ask_dollars": 0.55, "liquidity_dollars": 500},
        T_TOT: {"yes_ask_dollars": 0.82, "yes_bid_dollars": 0.80, "no_ask_dollars": 0.18, "liquidity_dollars": liq_tot},  # high yes-side -> cheap NO leg
    }


def _ctx(markets):
    return ex.MarketContext(M.build_kalshi_game_index(GAME), M.build_kalshi_total_index(TOTAL),
                            M.build_kalshi_spread_index(SPREAD), frozenset({"2026-08-28"}), markets)


def _sub(**over):
    base = dict(account_id=ACCT, category=CAT, market_types=("moneyline", "total", "spread"), sizing_mode="fixed",
                fixed_stake_usd=0.01, per_order_usd_cap=25.0, daily_usd_cap=50.0, max_open_usd=100.0,
                max_orders_per_day=25, max_slippage_cents=2, liquidity_ratio=0.75)
    base.update(over)
    return ex.SubConfig(**base)


def _sig(slug, outcome, sid="s1"):
    return ex.CopySignal(wallet="0xWHALE", slug=slug, outcome=outcome, condition_id="0xc_" + sid, outcome_index=0, signal_id=sid)


def _eval(tmp_path, sub, sig, markets):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        return ex.evaluate(sig, sub, _ctx(markets), j, conn, NOW, legacy_db_path=str(tmp_path / "noleg.db"))


# ── the schema column exists (migration 012) ──
def test_migration_012_adds_liquidity_ratio_and_schema_head_is_12(tmp_path):
    # 012 landed (proven by presence in the chain + the column below). SCHEMA_HEAD is NO LONGER pinned to
    # a literal here: Stage 4 added migration 013, so the head advanced past 12 -- track the chain, not a
    # number (the codebase's is-at-head discipline; the '..._is_12' name is now stale, kept for git history).
    assert (12, db.MIGRATION_012) in db.MIGRATIONS
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(pm_subdivision)")]
        assert "liquidity_ratio" in cols


# ── (1) passes at 0.75x where the old fixed $25 floor would have SKIPPED ──
def test_liquidity_passes_at_ratio_where_fixed_25_would_skip(tmp_path):
    d = _eval(tmp_path, _sub(liquidity_ratio=0.75), _sig(ML_SLUG, "Toronto Blue Jays"), _mk(liq_tor=5.0))
    assert d.status == "dry_run_would_place"                 # $5 book, 1-contract ~$0.57 order, floor 0.75*0.57~=$0.43
    assert d.count == 1 and d.notional_usd < 1.0
    assert 5.0 < 25.0 and 0.75 * d.notional_usd <= 5.0       # the OLD $25 floor would have skipped 5<25; the new one passes


# ── (2) fails even at 0.75x ──
def test_liquidity_fails_even_at_ratio(tmp_path):
    d = _eval(tmp_path, _sub(liquidity_ratio=0.75), _sig(ML_SLUG, "Toronto Blue Jays"), _mk(liq_tor=0.20))
    assert d.status == "skip:illiquid" and "liquidity_floor" in d.reason
    assert 0.75 * d.notional_usd > 0.20                      # the floor exceeds the $0.20 book -> correctly skipped


# ── (3) the boundary: liq == ratio*notional passes; just below skips (liquidity_ok uses liq < floor) ──
def test_liquidity_boundary_at_exactly_ratio_times_notional(tmp_path):
    d0 = _eval(tmp_path, _sub(), _sig(ML_SLUG, "Toronto Blue Jays"), _mk(liq_tor=500.0))
    assert d0.status == "dry_run_would_place"
    floor = 0.75 * d0.notional_usd
    assert _eval(tmp_path, _sub(), _sig(ML_SLUG, "Toronto Blue Jays"), _mk(liq_tor=floor)).status == "dry_run_would_place"
    assert _eval(tmp_path, _sub(), _sig(ML_SLUG, "Toronto Blue Jays"), _mk(liq_tor=floor - 0.01)).status == "skip:illiquid"


# ── (4) the ratio is READ FROM CONFIG, not a constant: change ONLY it, the decision flips + NULL->0.75 ──
def test_liquidity_ratio_is_read_from_config_not_a_constant(tmp_path):
    mk = _mk(liq_tor=1.0)                                    # a $1 book
    d_lo = _eval(tmp_path, _sub(liquidity_ratio=0.75), _sig(ML_SLUG, "Toronto Blue Jays"), mk)
    d_hi = _eval(tmp_path, _sub(liquidity_ratio=2.0), _sig(ML_SLUG, "Toronto Blue Jays"), mk)
    assert d_lo.status == "dry_run_would_place"              # 1.0 >= 0.75*notional
    assert d_hi.status == "skip:illiquid"                    # 1.0 < 2.0*notional -- ONLY the ratio changed; decision FLIPPED
    # a NULL liquidity_ratio column reads as CONFIG_DEFAULTS 0.75 (real schema-12 DB, real Row):
    p = str(tmp_path / "pm2.db"); db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_subdivision(account_id,category,market_types,sizing_mode,fixed_stake_usd,active,created_ts) "
                     "VALUES(?,?,?,?,?,1,?)", (ACCT, CAT, "moneyline", "fixed", 0.01, NOW))   # liquidity_ratio left NULL
        conn.commit()
        r = conn.execute("SELECT * FROM pm_subdivision WHERE account_id=? AND category=?", (ACCT, CAT)).fetchone()
    assert r["liquidity_ratio"] is None                     # column is NULL on disk
    assert ex.sub_config_from_row(r).liquidity_ratio == 0.75  # -> falls back to the code default, not 0.0/error


# ── (5) ★ the floor scales with the CORRECT-LEG notional: a NO leg uses (1-yes_price), never the yes side ──
def test_liquidity_floor_uses_the_no_leg_notional_not_the_yes_side(tmp_path):
    d = _eval(tmp_path, _sub(), _sig(TOT_SLUG, "Under", sid="tot"), _mk(liq_tot=500.0))
    assert d.status == "dry_run_would_place" and d.leg == "no"
    assert d.notional_usd < d.count * 0.5                   # NO cost (~$0.18/contract) -- NOT the yes side (~$0.82)
    floor_no = 0.75 * d.notional_usd
    assert _eval(tmp_path, _sub(), _sig(TOT_SLUG, "Under", sid="tot"), _mk(liq_tot=floor_no)).status == "dry_run_would_place"
    assert _eval(tmp_path, _sub(), _sig(TOT_SLUG, "Under", sid="tot"), _mk(liq_tot=floor_no - 0.001)).status == "skip:illiquid"
    # a book sized ABOVE the NO floor but far BELOW a (wrong) yes-side floor still WOULD-PLACE -> proves the NO leg drove it:
    yes_side_floor = 0.75 * d.count * 0.82
    if yes_side_floor - 0.02 > floor_no:
        assert _eval(tmp_path, _sub(), _sig(TOT_SLUG, "Under", sid="tot"), _mk(liq_tot=yes_side_floor - 0.02)).status == "dry_run_would_place"


# ── (6) ★ THE GUARD (Jack ruled 2026-08-29): a present 0/negative/NaN ratio CLAMPS to 0.75 + LOGS LOUDLY ──
def test_liquidity_ratio_clamps_invalid_to_default_and_logs(caplog):
    default = ex.CONFIG_DEFAULTS["liquidity_ratio"]
    for bad in (0.0, -1.0, float("nan")):
        row = {"account_id": ACCT, "category": CAT, "market_types": "moneyline", "sizing_mode": "fixed",
               "fixed_stake_usd": 0.01, "liquidity_ratio": bad}
        caplog.clear()
        with caplog.at_level("WARNING"):
            sub = ex.sub_config_from_row(row)
        assert sub.liquidity_ratio == default                                   # clamped to the code default
        assert any("liquidity_ratio" in rec.getMessage() for rec in caplog.records), "no LOUD log for %r" % bad
    # a VALID positive ratio passes through unchanged (no clamp, no spurious log):
    caplog.clear()
    with caplog.at_level("WARNING"):
        ok = ex.sub_config_from_row({"account_id": ACCT, "category": CAT, "market_types": "moneyline",
                                     "sizing_mode": "fixed", "fixed_stake_usd": 0.01, "liquidity_ratio": 1.5})
    assert ok.liquidity_ratio == 1.5 and not any("liquidity_ratio" in r.getMessage() for r in caplog.records)


def test_clamped_ratio_skips_a_book_that_would_pass_at_ratio_zero(tmp_path):
    thin = _mk(liq_tor=0.05)                                                     # a $0.05 near-empty book
    # UNGUARDED ratio 0 (direct SubConfig, bypasses the config guard) -> floor 0 -> the book PASSES (the footgun):
    assert _eval(tmp_path, _sub(liquidity_ratio=0.0), _sig(ML_SLUG, "Toronto Blue Jays"), thin).status == "dry_run_would_place"
    # but a DB liquidity_ratio of 0 is CLAMPED to 0.75 by sub_config_from_row -> the SAME book now SKIPS:
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_subdivision(account_id,category,market_types,sizing_mode,fixed_stake_usd,"
                     "liquidity_ratio,active,created_ts) VALUES(?,?,?,?,?,?,1,?)",
                     (ACCT, CAT, "moneyline,total,spread", "fixed", 0.01, 0.0, NOW))   # ratio 0 ON DISK
        conn.commit()
        sub = ex.sub_config_from_row(conn.execute("SELECT * FROM pm_subdivision WHERE account_id=? AND category=?", (ACCT, CAT)).fetchone())
        assert sub.liquidity_ratio == 0.75                                      # the on-disk 0 was clamped
        d = ex.evaluate(_sig(ML_SLUG, "Toronto Blue Jays"), sub, _ctx(thin), ex.Journal(conn, [ACCT], NOW),
                        conn, NOW, legacy_db_path=str(tmp_path / "noleg.db"))
    assert d.status == "skip:illiquid"                                          # clamp restored the gate
