"""Rung 3 (soccer) wiring: the per-league soccer MATCHER_ADAPTERS + CATEGORY_CTX_BUILDERS, the
MarketContext.soccer_index field (all prior constructions byte-identical), dispatch + fail-safe."""
from trading_corp.prediction_markets import execution, live_driver
from trading_corp.data import soccer_poly_kalshi_match as SO

SOCCER_CATS = ("epl", "lal", "fl1", "sea", "bun", "mls", "bra", "mex", "ucl", "uel")


def test_soccer_registered_and_prior_intact():
    for cat in SOCCER_CATS:
        assert cat in execution.MATCHER_ADAPTERS, cat
        assert cat in live_driver.CATEGORY_CTX_BUILDERS, cat
    for cat in ("mlb", "ufc", "atp", "wta", "cs2", "nfl", "nba", "nhl", "wnba", "cfb"):
        assert cat in execution.MATCHER_ADAPTERS and cat in live_driver.CATEGORY_CTX_BUILDERS


def test_marketcontext_soccer_default_byte_identical():
    ctx = execution.MarketContext({}, {}, {}, frozenset(), {})
    assert ctx.soccer_index is None
    assert ctx.cs2_index is None and ctx.structural_index is None and ctx.fight_index is None and ctx.match_index is None


def test_soccer_dispatch_matches_and_gates():
    parse, match = execution.MATCHER_ADAPTERS["epl"]
    mkts = [{"ticker": "KXEPLGAME-26SEP05ARSCHE-ARS", "title": "Arsenal wins", "yes_sub_title": "Arsenal"},
            {"ticker": "KXEPLGAME-26SEP05ARSCHE-CHE", "title": "Chelsea wins", "yes_sub_title": "Chelsea"},
            {"ticker": "KXEPLGAME-26SEP05ARSCHE-TIE", "title": "Tie is the result", "yes_sub_title": "Tie"}]
    idx = SO.build_game_index(mkts, SO.LEAGUES["epl"])
    ctx = execution.MarketContext({}, {}, {}, frozenset({"2026-09-05"}), {}, soccer_index=idx)
    r = match(parse("epl-ars-che-2026-09-05-ars", "No", "Will Arsenal FC win on 2026-09-05?"), ctx, ("moneyline",))
    assert r.status == "matched" and r.kalshi_ticker == "KXEPLGAME-26SEP05ARSCHE-ARS" and r.leg == "no", r
    # fail-safe: a non-soccer ctx (soccer_index None) never crashes
    r2 = match(parse("epl-ars-che-2026-09-05-ars", "Yes", "Will Arsenal FC win on 2026-09-05?"),
               execution.MarketContext({}, {}, {}, frozenset(), {}), ("moneyline",))
    assert r2.status in ("out_of_window", "no_kalshi_contract") and r2.kalshi_ticker is None, r2
