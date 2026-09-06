"""Rung 1 D/E wiring: the structural MATCHER_ADAPTERS + CATEGORY_CTX_BUILDERS registration, the
MarketContext.structural_index field (mlb/ufc/tennis constructions stay byte-identical), and the
volume-first cycle order. Pure -- no pykalshi/network (the ctx builder itself needs pykalshi -> box-scratch)."""
import sqlite3
from trading_corp.prediction_markets import execution, live_driver
from trading_corp.data import sports_structural_match as ssm


def test_structural_adapters_and_ctx_builders_registered():
    for cat in ("nfl", "nba", "nhl", "wnba", "cfb"):
        assert cat in execution.MATCHER_ADAPTERS, cat
        assert cat in live_driver.CATEGORY_CTX_BUILDERS, cat
    # the live 4 are untouched
    for cat in ("mlb", "ufc", "atp", "wta"):
        assert cat in execution.MATCHER_ADAPTERS and cat in live_driver.CATEGORY_CTX_BUILDERS


def test_marketcontext_structural_default_keeps_prior_byte_identical():
    # the mlb-shaped positional construction leaves ALL optional indexes None (byte-identical to pre-rung-1)
    ctx = execution.MarketContext({}, {}, {}, frozenset(), {})
    assert ctx.structural_index is None and ctx.fight_index is None and ctx.match_index is None


def test_structural_adapter_dispatch_matches_and_gates():
    parse, match = execution.MATCHER_ADAPTERS["nfl"]
    tk = ["KXNFLGAME-26SEP21NYGLAR-NYG", "KXNFLGAME-26SEP21NYGLAR-LAR"]
    idx = ssm.build_game_index(tk, ssm.LEAGUES["nfl"])
    ctx = execution.MarketContext({}, {}, {}, frozenset({"2026-09-21"}), {}, structural_index=idx)
    r = match(parse("nfl-nyg-lar-2026-09-21", "Giants", None), ctx, ("moneyline",))
    assert r.status == "matched" and r.kalshi_ticker == "KXNFLGAME-26SEP21NYGLAR-NYG" and r.leg == "yes", r
    # moneyline-only: a total-suffix bet is a labelled skip, never a match/ticker
    r2 = match(parse("nfl-nyg-lar-2026-09-21-total-44pt5", "Over", None), ctx, ("moneyline",))
    assert r2.status.startswith("skip") and r2.kalshi_ticker is None, r2
    # market-type gate honours the sub's allowed set
    r3 = match(parse("nfl-nyg-lar-2026-09-21", "Giants", None), ctx, ())
    assert r3.status == "skip_market_type_excluded", r3
    # a non-structural ctx (structural_index None) fails safe to no-contract, never crashes
    r4 = match(parse("nfl-nyg-lar-2026-09-21", "Giants", None),
               execution.MarketContext({}, {}, {}, frozenset(), {}), ("moneyline",))
    assert r4.status in ("out_of_window", "no_kalshi_contract") and r4.kalshi_ticker is None, r4


def _voldb():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE pm_subdivision_order (account_id TEXT, category TEXT, submitted_count INTEGER, "
                 "submitted_price REAL, outcome_leg TEXT, dry_run INTEGER, outcome_status TEXT, is_exit INTEGER, "
                 "response_ts INTEGER)")
    return conn


def test_category_volume_order_proven_first_new_last():
    conn = _voldb(); now = 1_000_000_000
    def ins(cat, n, price, off=0, leg="yes", dry=0, status="filled", ex=0):
        conn.execute("INSERT INTO pm_subdivision_order VALUES ('acc',?,?,?,?,?,?,?,?)",
                     (cat, n, price, leg, dry, status, ex, now - off))
    ins("mlb", 10, 0.5); ins("mlb", 10, 0.5)      # yes: 10*0.5 + 10*0.5 = $10
    ins("mlb", 10, 0.3, leg="no")                 # no: 10*(1-0.3) = $7 -> mlb $17
    ins("nba", 5, 0.4)                            # $2
    ins("wnba", 1, 0.5)                           # $0.5
    # noise that must NOT count toward volume:
    ins("nfl", 100, 0.9, dry=1)                   # dry-run
    ins("nfl", 100, 0.9, status="resting")        # unfilled
    ins("nfl", 100, 0.9, ex=1)                    # exit
    ins("nfl", 100, 0.9, off=40 * 86400)          # older than the 30d window
    conn.commit()
    cats = ["atp", "mlb", "nba", "nfl", "wnba"]   # alphabetical input
    out = live_driver.category_volume_order(conn, "acc", cats, now_ts=now, window_days=30)
    # mlb($17) > nba($2) > wnba($0.5) > new/quiet {atp, nfl}=0 -> alphabetical tiebreak
    assert out == ["mlb", "nba", "wnba", "atp", "nfl"], out


def test_category_volume_order_failsafe_alphabetical():
    # a broken/absent conn must NEVER stop the driver -> deterministic alphabetical
    assert live_driver.category_volume_order(None, "acc", ["wta", "mlb", "nfl"], now_ts=1) == ["mlb", "nfl", "wta"]
    conn = sqlite3.connect(":memory:")   # no pm_subdivision_order table -> query raises -> fallback
    assert live_driver.category_volume_order(conn, "acc", ["wta", "mlb"], now_ts=1) == ["mlb", "wta"]
