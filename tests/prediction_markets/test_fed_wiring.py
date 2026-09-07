"""Rung 4 (fed) wiring: the fed MATCHER_ADAPTERS + CATEGORY_CTX_BUILDERS registration, the
MarketContext.fed_index field (all prior constructions byte-identical), dispatch + fail-safe."""
from trading_corp.prediction_markets import execution, live_driver
from trading_corp.data import fed_poly_kalshi_match as FED


def test_fed_registered_and_prior_intact():
    assert "fed" in execution.MATCHER_ADAPTERS
    assert live_driver.CATEGORY_CTX_BUILDERS["fed"] is live_driver.fetch_fed_market_context
    assert live_driver.FED_SERIES == "KXFEDDECISION"
    for cat in ("mlb", "ufc", "atp", "wta", "cs2", "nfl", "nba", "nhl", "wnba", "cfb",
                "epl", "ucl", "lal", "mls"):
        assert cat in execution.MATCHER_ADAPTERS and cat in live_driver.CATEGORY_CTX_BUILDERS


def test_marketcontext_fed_default_byte_identical():
    ctx = execution.MarketContext({}, {}, {}, frozenset(), {})
    assert ctx.fed_index is None
    assert ctx.soccer_index is None and ctx.cs2_index is None and ctx.structural_index is None
    assert ctx.fight_index is None and ctx.match_index is None


def test_fed_dispatch_matches_and_gates():
    parse, match = execution.MATCHER_ADAPTERS["fed"]
    idx = FED.build_bucket_index([{"ticker": "KXFEDDECISION-26SEP-C25", "yes_sub_title": "Cut 25bps"},
                                  {"ticker": "KXFEDDECISION-26SEP-H0", "yes_sub_title": "Fed maintains rate"}])
    ctx = execution.MarketContext({}, {}, {}, frozenset(idx), {}, fed_index=idx)
    r = match(parse("fed-x", "No", "Fed decreases interest rates by 25 bps after September 2026 meeting?"),
              ctx, ("bucket",))
    assert r.status == "matched" and r.kalshi_ticker == "KXFEDDECISION-26SEP-C25" and r.leg == "no", r
    # coarse gated even through dispatch
    r2 = match(parse("fed-x", "Yes", "Fed increases interest rates by 25+ bps after September 2026 meeting?"),
               ctx, ("bucket",))
    assert r2.status == "skip_coarse", r2
    # fail-safe: non-fed ctx (fed_index None) never crashes
    r3 = match(parse("fed-x", "Yes", "Will there be no change in Fed interest rates after the September 2026 meeting?"),
               execution.MarketContext({}, {}, {}, frozenset(), {}), ("bucket",))
    assert r3.status in ("out_of_window", "no_kalshi_contract") and r3.kalshi_ticker is None, r3
