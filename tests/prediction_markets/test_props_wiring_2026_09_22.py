"""Phase B/C wiring: props route through the real matcher entrypoints (SS.match_bet for NFL, M.match_bet for MLB),
gated PER-STAT and INERT without the token. Proves parse->prop, skip-without-token, matched-with-token+prop_index,
exact-strike safe miss, and the binary TD path."""
from trading_corp.data import sports_structural_match as SS
from trading_corp.data import player_props_match as P

NFL = SS.LEAGUES["nfl"]
GIDX = SS.build_game_index(["KXNFLGAME-26SEP24ATLGB-GB"], NFL)
PIDX = P.build_prop_index(["KXNFLRSHYDS-26SEP24ATLGB-GBKJOHNSON26-50", "KXNFLRSHYDS-26SEP24ATLGB-GBKJOHNSON26-90",
                           "KXNFLANYTD-26SEP24ATLGB-GBKJOHNSON26"])
DATES = {"2026-09-24"}


def _p(slug, outcome):
    return SS.parse_poly_bet(slug, outcome, NFL)


def test_nfl_slug_parses_to_prop():
    pb = _p("nfl-atl-gb-2026-09-24-ryd-kaleb-johnson-49pt5", "Over")
    assert pb.market_type == "prop" and pb.raw["prop"]["series"] == "KXNFLRSHYDS" and pb.raw["prop"]["strike"] == 50


def test_nfl_inert_without_token():
    pb = _p("nfl-atl-gb-2026-09-24-ryd-kaleb-johnson-49pt5", "Over")
    r = SS.match_bet(pb, GIDX, DATES, NFL, allowed_market_types=("moneyline", "total", "spread"), prop_index=PIDX)
    assert r.status == "skip_market_type_excluded"


def test_nfl_matched_with_token():
    pb = _p("nfl-atl-gb-2026-09-24-ryd-kaleb-johnson-49pt5", "Over")
    r = SS.match_bet(pb, GIDX, DATES, NFL, allowed_market_types=("moneyline", "ryd"), prop_index=PIDX)
    assert r.status == "matched" and r.kalshi_ticker == "KXNFLRSHYDS-26SEP24ATLGB-GBKJOHNSON26-50" and r.leg == "yes"


def test_nfl_exact_strike_safe_miss():
    pb = _p("nfl-atl-gb-2026-09-24-ryd-kaleb-johnson-69pt5", "Over")   # 70; Kalshi has only 50/90
    r = SS.match_bet(pb, GIDX, DATES, NFL, allowed_market_types=("ryd",), prop_index=PIDX)
    assert r.status == "no_kalshi_strike"


def test_nfl_binary_anytd():
    pb = _p("nfl-atl-gb-2026-09-24-anytime-td-kaleb-johnson", "Yes")
    r = SS.match_bet(pb, GIDX, DATES, NFL, allowed_market_types=("anytime-td",), prop_index=PIDX)
    assert r.status == "matched" and r.kalshi_ticker == "KXNFLANYTD-26SEP24ATLGB-GBKJOHNSON26" and r.leg == "yes"
