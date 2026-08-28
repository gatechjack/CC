"""Stage 3 R2 -- three-dimension MLB matcher (moneyline + total + spread).

Real slugs (PM-DB data-gather 2026-08-28) and real Kalshi tickers (live catalog 2026-08-28) -- NOT invented.
The Kalshi tickers below are the actual SEA@TOR 2026-08-28 19:15 markets (stem 26AUG281915SEATOR); Poly slugs
use the verified live formats for the same game so the (date, teams) join is exercised end-to-end.

Established from live data:
  Kalshi total : KXMLBTOTAL-{stem}-{N}      YES = Over,   floor_strike = N-0.5
  Kalshi spread: KXMLBSPREAD-{stem}-{TEAM}{N}  YES = "{TEAM} wins by over (N-0.5)"
  Poly  total  : mlb-{a}-{h}-{date}-total-{W}pt{F}      outcome Over|Under
  Poly  spread : mlb-{a}-{h}-{date}-spread-{home|away}-1pt5  outcome = a team (anchor -> the -1.5 side)
"""
from trading_corp.data import mlb_poly_kalshi_match as M

# --- real Kalshi tickers, SEA@TOR 2026-08-28 19:15 (verbatim from the live catalog) ---
GAME_TICKERS = ["KXMLBGAME-26AUG281915SEATOR-SEA", "KXMLBGAME-26AUG281915SEATOR-TOR"]
TOTAL_TICKERS = [  # -N -> strike N-0.5 : 9->8.5 8->7.5 7->6.5 6->5.5 5->4.5 4->3.5
    "KXMLBTOTAL-26AUG281915SEATOR-9", "KXMLBTOTAL-26AUG281915SEATOR-8",
    "KXMLBTOTAL-26AUG281915SEATOR-7", "KXMLBTOTAL-26AUG281915SEATOR-6",
    "KXMLBTOTAL-26AUG281915SEATOR-5", "KXMLBTOTAL-26AUG281915SEATOR-4",
]
SPREAD_TICKERS = [  # {TEAM}{N} -> "{TEAM} wins by over N-0.5" : 2->1.5 3->2.5 4->3.5
    "KXMLBSPREAD-26AUG281915SEATOR-TOR2", "KXMLBSPREAD-26AUG281915SEATOR-TOR3",
    "KXMLBSPREAD-26AUG281915SEATOR-TOR4", "KXMLBSPREAD-26AUG281915SEATOR-SEA2",
    "KXMLBSPREAD-26AUG281915SEATOR-SEA3", "KXMLBSPREAD-26AUG281915SEATOR-SEA4",
]
DATE = "2026-08-28"


def _bundle():
    return (M.build_kalshi_game_index(GAME_TICKERS),
            M.build_kalshi_total_index(TOTAL_TICKERS),
            M.build_kalshi_spread_index(SPREAD_TICKERS),
            frozenset({DATE}))


def _match(slug, outcome, allowed=M.COPYABLE_MARKET_TYPES):
    mi, ti, si, dates = _bundle()
    p = M.parse_poly_mlb_bet(slug, outcome)
    return p, M.match_bet(p, mi, ti, si, dates, allowed_market_types=allowed)


# ── Kalshi ticker parsers: the N-0.5 strike convention ──────────────────────
def test_kalshi_total_ticker_strike_convention():
    assert M.parse_kalshi_total_ticker("KXMLBTOTAL-26AUG281915SEATOR-9") == ("26AUG281915SEATOR", 8.5)
    assert M.parse_kalshi_total_ticker("KXMLBTOTAL-26AUG281915SEATOR-4") == ("26AUG281915SEATOR", 3.5)
    assert M.parse_kalshi_total_ticker("KXMLBGAME-26AUG281915SEATOR-SEA") is None  # wrong series


def test_kalshi_spread_ticker_strike_and_team():
    assert M.parse_kalshi_spread_ticker("KXMLBSPREAD-26AUG281915SEATOR-TOR2") == ("26AUG281915SEATOR", "TOR", 1.5)
    assert M.parse_kalshi_spread_ticker("KXMLBSPREAD-26AUG281915SEATOR-SEA4") == ("26AUG281915SEATOR", "SEA", 3.5)
    assert M.parse_kalshi_spread_ticker("KXMLBTOTAL-26AUG281915SEATOR-9") is None  # wrong series


# ── TOTALS: exact-strike match + leg orientation (Over=YES, Under=NO) ────────
def test_total_over_matches_exact_strike_yes_leg():
    p, r = _match("mlb-sea-tor-2026-08-28-total-8pt5", "Over")
    assert p.market_type == "total" and p.line == 8.5 and p.leg == "yes"
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBTOTAL-26AUG281915SEATOR-9"
    assert r.leg == "yes" and r.strike == 8.5 and r.market_type == "total"


def test_total_under_is_the_NO_leg_same_ticker():
    """Under = the NO leg of the SAME KXMLBTOTAL strike ticker (explicit orientation)."""
    p, r = _match("mlb-sea-tor-2026-08-28-total-8pt5", "Under")
    assert p.leg == "no"
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBTOTAL-26AUG281915SEATOR-9" and r.leg == "no"


def test_total_far_tail_missing_strike_is_labelled_skip_not_rounded():
    p, r = _match("mlb-sea-tor-2026-08-28-total-15pt5", "Over")   # 15.5 is above the ladder
    assert p.line == 15.5
    assert r.status == "no_kalshi_strike" and r.kalshi_ticker is None   # NEVER rounded to 12.5/8.5
    assert r.strike == 15.5


# ── SPREADS: exact-strike + leg orientation (anchor=YES, other=NO) ───────────
def test_spread_anchor_is_yes_leg():
    # anchor = home = tor (Toronto); outcome = Toronto -> "Toronto -1.5" -> TOR2 YES
    p, r = _match("mlb-sea-tor-2026-08-28-spread-home-1pt5", "Toronto Blue Jays")
    assert p.market_type == "spread" and p.line == 1.5 and p.anchor_side == "home" and p.leg == "yes"
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBSPREAD-26AUG281915SEATOR-TOR2" and r.leg == "yes"


def test_spread_other_side_is_the_NO_leg_of_the_anchor_market():
    # anchor = home = tor; outcome = Seattle -> "Seattle +1.5" == NOT(TOR wins by >1.5) -> TOR2 NO
    p, r = _match("mlb-sea-tor-2026-08-28-spread-home-1pt5", "Seattle Mariners")
    assert p.leg == "no"
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBSPREAD-26AUG281915SEATOR-TOR2" and r.leg == "no"


def test_spread_away_anchor_targets_that_teams_market():
    # anchor = away = sea; outcome = Seattle -> "Seattle -1.5" -> SEA2 YES
    p, r = _match("mlb-sea-tor-2026-08-28-spread-away-1pt5", "Seattle Mariners")
    assert p.anchor_side == "away" and p.leg == "yes"
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBSPREAD-26AUG281915SEATOR-SEA2" and r.leg == "yes"


def test_spread_missing_strike_is_labelled_skip():
    p, r = _match("mlb-sea-tor-2026-08-28-spread-home-4pt5", "Toronto Blue Jays")  # no TOR5 in the ladder
    assert p.line == 4.5
    assert r.status == "no_kalshi_strike" and r.kalshi_ticker is None


# ── liquidity floor (K4) ────────────────────────────────────────────────────
def test_liquidity_ok_accepts_liquid_two_sided_tight_book():
    assert M.liquidity_ok({"liquidity_dollars": 500, "yes_bid_dollars": 0.55, "yes_ask_dollars": 0.57})


def test_liquidity_reject_thin_onesided_or_wide():
    assert not M.liquidity_ok({"liquidity_dollars": 5, "yes_bid_dollars": 0.55, "yes_ask_dollars": 0.57})   # thin
    assert not M.liquidity_ok({"liquidity_dollars": 500, "yes_bid_dollars": 0.0, "yes_ask_dollars": 0.57})  # one-sided
    assert not M.liquidity_ok({"liquidity_dollars": 500, "yes_bid_dollars": 0.30, "yes_ask_dollars": 0.60}) # 30c spread
    assert not M.liquidity_ok(None)


# ── market-type discrimination: real slugs, unmatched -> skip (never moneyline) ──
def test_market_type_discrimination_real_slugs():
    assert M.parse_poly_mlb_bet("mlb-cle-tor-2026-04-25", "Toronto Blue Jays").market_type == "moneyline"
    assert M.parse_poly_mlb_bet("mlb-nym-ari-2026-05-09-total-9pt5", "Under").market_type == "total"
    assert M.parse_poly_mlb_bet("mlb-bos-tor-2026-08-10-spread-away-1pt5", "Toronto Blue Jays").market_type == "spread"
    # real prop slugs -> 'prop', NEVER moneyline
    assert M.parse_poly_mlb_bet("mlb-cws-min-2025-09-04-nrfi", "No").market_type == "prop"
    assert M.parse_poly_mlb_bet("mlb-hou-tex-2025-09-06-349", "Astros").market_type == "prop"
    # a non-mlb slug is never moneyline
    assert M.parse_poly_mlb_bet("nba-lal-bos-2026-01-01", "Boston Celtics").market_type == "non_mlb"


def test_unmatched_suffix_never_classified_moneyline_and_skips():
    p, r = _match("mlb-hou-tex-2025-09-06-349", "Astros")
    assert p.market_type == "prop"                       # NOT moneyline
    assert r.status == "skip_non_ml"                     # labelled skip, not an error, not a copy
    p2, r2 = _match("nba-lal-bos-2026-01-01", "Boston Celtics")
    assert p2.market_type == "non_mlb" and r2.status == "skip_non_game"


def test_market_type_excluded_by_subdivision_config_is_a_skip():
    # a total bet, but the sub-division only allows moneyline -> labelled skip, not an error
    _, r = _match("mlb-sea-tor-2026-08-28-total-8pt5", "Over", allowed=("moneyline",))
    assert r.status == "skip_market_type_excluded" and r.market_type == "total"
    # a spread bet, sub-division allows moneyline+total only
    _, r2 = _match("mlb-sea-tor-2026-08-28-spread-home-1pt5", "Toronto Blue Jays", allowed=("moneyline", "total"))
    assert r2.status == "skip_market_type_excluded" and r2.market_type == "spread"


# ── moneyline regression: existing behaviour UNCHANGED ──────────────────────
def test_moneyline_regression_match_bet_and_legacy_path():
    mi, ti, si, dates = _bundle()
    # via the legacy direct path (poly_kalshi consumes THIS) -- unchanged: matched to the bet team's YES ticker
    p_home = M.parse_poly_mlb_bet("mlb-sea-tor-2026-08-28", "Toronto Blue Jays")
    r_legacy = M.match_poly_to_kalshi(p_home, mi, dates)
    assert r_legacy.status == "matched" and r_legacy.kalshi_ticker == "KXMLBGAME-26AUG281915SEATOR-TOR"
    # via the unified match_bet: same ticker, leg forced 'yes' (buy YES on the bet team)
    r_home = M.match_bet(p_home, mi, ti, si, dates)
    assert r_home.status == "matched" and r_home.kalshi_ticker == "KXMLBGAME-26AUG281915SEATOR-TOR"
    assert r_home.leg == "yes" and r_home.market_type == "moneyline"
    # away side resolves to the away YES ticker
    p_away = M.parse_poly_mlb_bet("mlb-sea-tor-2026-08-28", "Seattle Mariners")
    r_away = M.match_bet(p_away, mi, ti, si, dates)
    assert r_away.status == "matched" and r_away.kalshi_ticker == "KXMLBGAME-26AUG281915SEATOR-SEA"


def test_total_out_of_window_and_no_contract_mirror_moneyline():
    mi, ti, si, dates = _bundle()
    # a total for a game whose date isn't in the Kalshi window -> out_of_window (not a false skip)
    p = M.parse_poly_mlb_bet("mlb-sea-tor-2020-01-01-total-8pt5", "Over")
    r = M.match_bet(p, mi, ti, si, dates)
    assert r.status == "out_of_window"
