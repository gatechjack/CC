"""Rung 2 (cs2) wiring: the cs2 MATCHER_ADAPTERS + CATEGORY_CTX_BUILDERS registration, the
MarketContext.cs2_index field (all prior constructions stay byte-identical), and the adapter's
dispatch + fail-safe. Pure -- no pykalshi/network (the ctx builder itself needs pykalshi -> box-scratch)."""
from trading_corp.prediction_markets import execution, live_driver
from trading_corp.data import cs2_poly_kalshi_match as CS


def test_cs2_registered_and_live_four_untouched():
    assert "cs2" in execution.MATCHER_ADAPTERS
    assert "cs2" in live_driver.CATEGORY_CTX_BUILDERS
    assert live_driver.CATEGORY_CTX_BUILDERS["cs2"] is live_driver.fetch_cs2_market_context
    assert live_driver.CS2_SERIES == "KXCS2GAME"
    for cat in ("mlb", "ufc", "atp", "wta", "nfl", "nba", "nhl", "wnba", "cfb"):
        assert cat in execution.MATCHER_ADAPTERS and cat in live_driver.CATEGORY_CTX_BUILDERS


def test_marketcontext_cs2_default_keeps_prior_byte_identical():
    ctx = execution.MarketContext({}, {}, {}, frozenset(), {})
    assert ctx.cs2_index is None
    assert ctx.structural_index is None and ctx.fight_index is None and ctx.match_index is None


def test_cs2_adapter_dispatch_matches_and_gates():
    parse, match = execution.MATCHER_ADAPTERS["cs2"]
    mkts = [{"ticker": "KXCS2GAME-26SEP051300ENCVIT-ENC", "title": "ENCE wins", "yes_sub_title": "ENCE"},
            {"ticker": "KXCS2GAME-26SEP051300ENCVIT-VIT", "title": "Vitality wins", "yes_sub_title": "Vitality"}]
    idx = CS.build_kalshi_match_index(mkts)
    ctx = execution.MarketContext({}, {}, {}, frozenset({"2026-09-05"}), {}, cs2_index=idx)
    r = match(parse("cs2-ence-vit-2026-09-05", "ENCE", "Counter-Strike: ENCE vs Vitality (BO3) - E"),
              ctx, ("moneyline",))
    assert r.status == "matched" and r.kalshi_ticker == "KXCS2GAME-26SEP051300ENCVIT-ENC" and r.leg == "yes", r
    # market-type gate honours the sub's allowed set
    r2 = match(parse("cs2-ence-vit-2026-09-05", "ENCE", "Counter-Strike: ENCE vs Vitality (BO3) - E"), ctx, ())
    assert r2.status == "skip_market_type_excluded", r2
    # a non-cs2 ctx (cs2_index None) fails safe to no-contract, never crashes
    r3 = match(parse("cs2-ence-vit-2026-09-05", "ENCE", "Counter-Strike: ENCE vs Vitality (BO3) - E"),
               execution.MarketContext({}, {}, {}, frozenset(), {}), ("moneyline",))
    assert r3.status in ("out_of_window", "no_kalshi_contract") and r3.kalshi_ticker is None, r3
