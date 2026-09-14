"""Sub-game families: MLB F5 (first-5-innings) + structural first-half (1H) matcher tests (2026-09-14).

ONE PATTERN, TWO MATCHERS -- F5 in the MLB matcher's prop branch, first-half in the structural matcher's
non-moneyline branch; both delegate to the SHARED route-only core (subgame_match). These tests pin the FOUR
load-bearing safety properties Jack named:

  (1) `-1h-` MATCHED EXACTLY; `-2h-` STAYS A NON-MONEYLINE SKIP (a 2H bet binding a 1H ticker = right game, right
      teams, WRONG market = real money on the wrong contract). 2H is a KNOWN UNSUPPORTED FAMILY, deliberately.
  (2) ROUTE-ONLY BY CONSTRUCTION -- a 1H/F5 bet CANNOT REACH A FULL-GAME TICKER. The *_TEETH tests DELIBERATELY
      SHARE the full-game index into the sub-game slot and assert the WRONG full-game bind DOES occur -- so the
      companion route-only test (which passes the correct sub-game-only index and asserts a MISS) has teeth: the
      miss is caused by WHICH INDEX IS PASSED, not by the ticker being absent everywhere. If the production wiring
      ever shared the indices, the *_TEETH scenario is exactly what would happen in prod.
  (3) `-draw`/tie binds ONLY the TIE ticker; a tie CANNOT reach a two-way team ticker (the side_key differs).
  (4) inert until enabled -- the tokens are `f5` (all three f5_* sub-types) and `first_half` (all three
      first_half_* sub-types); absent from a sub's market_types -> skip_market_type_excluded, never a fill.
"""
from trading_corp.data import mlb_poly_kalshi_match as M
from trading_corp.data import sports_structural_match as SS
from trading_corp.data import subgame_match as SG
from trading_corp.prediction_markets import execution
from trading_corp.prediction_markets import live_driver


# ══════════════════════════════════════════════════════════════════════════════════════════
# MLB F5 (first-five-innings): winner (3-way) + total + spread
# ══════════════════════════════════════════════════════════════════════════════════════════
# real game shape (RFI probe): SF @ STL, 2026-09-14, stem 26SEP141945SFSTL
MLB_GAME_TK = ["KXMLBGAME-26SEP141945SFSTL-SF", "KXMLBGAME-26SEP141945SFSTL-STL"]
F5_WIN_TK = ["KXMLBF5-26SEP141945SFSTL-SF", "KXMLBF5-26SEP141945SFSTL-STL", "KXMLBF5-26SEP141945SFSTL-TIE"]
F5_TOT_TK = ["KXMLBF5TOTAL-26SEP141945SFSTL-5"]        # strike 4.5
F5_SPR_TK = ["KXMLBF5SPREAD-26SEP141945SFSTL-SF2"]     # SF -1.5


def _mlb_idx():
    ml = M.build_kalshi_game_index(MLB_GAME_TK)
    w = M.build_kalshi_f5_win_index(F5_WIN_TK)
    t = M.build_kalshi_f5_total_index(F5_TOT_TK)
    s = M.build_kalshi_f5_spread_index(F5_SPR_TK)
    return ml, w, t, s, frozenset(MLB_GAME_TK)


def _f5_match(slug, outcome, *, w=None, t=None, s=None, allowed=("f5",)):
    ml, wi, ti, si, dates = _mlb_idx()
    p = M.parse_poly_mlb_bet(slug, outcome, "")
    return p, M.match_bet(p, ml, {}, {}, dates, allowed_market_types=allowed,
                          f5_win_index=(wi if w is None else w),
                          f5_total_index=(ti if t is None else t),
                          f5_spread_index=(si if s is None else s))


# ── F5 index builders (delegate to SG; winner keyed by side OR TIE) ───────────
def test_f5_index_builders():
    w = M.build_kalshi_f5_win_index(F5_WIN_TK)
    assert w == {"26SEP141945SFSTL": {"SF": "KXMLBF5-26SEP141945SFSTL-SF",
                                      "STL": "KXMLBF5-26SEP141945SFSTL-STL",
                                      "TIE": "KXMLBF5-26SEP141945SFSTL-TIE"}}
    assert M.build_kalshi_f5_total_index(F5_TOT_TK) == {"26SEP141945SFSTL": {4.5: "KXMLBF5TOTAL-26SEP141945SFSTL-5"}}
    assert M.build_kalshi_f5_spread_index(F5_SPR_TK) == {"26SEP141945SFSTL": {("SF", 1.5): "KXMLBF5SPREAD-26SEP141945SFSTL-SF2"}}


# ── F5 winner: away/home/draw, both legs ─────────────────────────────────────
def test_f5_winner_away_yes():
    p, r = _f5_match("mlb-sf-stl-2026-09-14-first-five-winner-away", "Yes")
    assert p.market_type == "f5_winner"
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBF5-26SEP141945SFSTL-SF" and r.leg == "yes"


def test_f5_winner_home_no_leg():
    p, r = _f5_match("mlb-sf-stl-2026-09-14-first-five-winner-home", "No")
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBF5-26SEP141945SFSTL-STL" and r.leg == "no"


def test_f5_winner_draw_binds_tie_only():
    p, r = _f5_match("mlb-sf-stl-2026-09-14-first-five-winner-draw", "Yes")
    assert p.side == "draw"
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBF5-26SEP141945SFSTL-TIE" and r.leg == "yes"


def test_f5_winner_draw_CANNOT_reach_team_ticker():
    """A draw with NO tie ticker present must MISS -- never fall back to a two-way team ticker (side_key='TIE')."""
    w_no_tie = {"26SEP141945SFSTL": {"SF": "KXMLBF5-26SEP141945SFSTL-SF", "STL": "KXMLBF5-26SEP141945SFSTL-STL"}}
    p, r = _f5_match("mlb-sf-stl-2026-09-14-first-five-winner-draw", "Yes", w=w_no_tie)
    assert r.status == "no_kalshi_contract" and r.kalshi_ticker is None


def test_f5_total_over_exact_strike():
    p, r = _f5_match("mlb-sf-stl-2026-09-14-f5-total-4pt5", "Over")
    assert p.market_type == "f5_total"
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBF5TOTAL-26SEP141945SFSTL-5" and r.leg == "yes" and r.strike == 4.5


def test_f5_total_under_is_no():
    p, r = _f5_match("mlb-sf-stl-2026-09-14-f5-total-4pt5", "Under")
    assert r.status == "matched" and r.leg == "no"


def test_f5_total_wrong_strike_safe_miss():
    p, r = _f5_match("mlb-sf-stl-2026-09-14-f5-total-5pt5", "Over")   # 5.5, ticker is 4.5
    assert r.status != "matched"


def test_f5_spread_anchor_yes():
    p, r = _f5_match("mlb-sf-stl-2026-09-14-f5-spread-away-1pt5", "San Francisco Giants")
    assert p.market_type == "f5_spread"
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBF5SPREAD-26SEP141945SFSTL-SF2" and r.leg == "yes" and r.strike == 1.5


def test_f5_spread_other_side_is_no():
    p, r = _f5_match("mlb-sf-stl-2026-09-14-f5-spread-away-1pt5", "St. Louis Cardinals")
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBF5SPREAD-26SEP141945SFSTL-SF2" and r.leg == "no"


# ── F5 inert until enabled ───────────────────────────────────────────────────
def test_f5_inert_when_not_enabled():
    p, r = _f5_match("mlb-sf-stl-2026-09-14-first-five-winner-away", "Yes",
                     allowed=("moneyline", "total", "spread", "first_inning_run"))
    assert r.status == "skip_market_type_excluded"


def test_f5_one_token_gates_all_three():
    for slug, oc in (("mlb-sf-stl-2026-09-14-first-five-winner-away", "Yes"),
                     ("mlb-sf-stl-2026-09-14-f5-total-4pt5", "Over"),
                     ("mlb-sf-stl-2026-09-14-f5-spread-away-1pt5", "San Francisco Giants")):
        _, r = _f5_match(slug, oc, allowed=("f5",))
        assert r.status == "matched", (slug, r)
        _, r2 = _f5_match(slug, oc, allowed=("moneyline",))
        assert r2.status == "skip_market_type_excluded", (slug, r2)


# ── F5 ROUTE-ONLY: teeth (shared -> wrong bind) + correct wiring (miss) ───────
def test_f5_winner_shared_fullgame_index_binds_fullgame_TEETH():
    """DELIBERATELY share the FULL-GAME moneyline index into the F5 winner slot -> it (WRONGLY) binds the full-game
    moneyline ticker. Proves the join reaches whatever index it is handed; the ONLY thing keeping a F5 bet off a
    full-game ticker in production is that the caller passes the F5 index here, never the full-game index."""
    fg_win = SG.build_win_index(MLB_GAME_TK, "KXMLBGAME")   # full-game moneyline index, shaped like a winner index
    _, r = _f5_match("mlb-sf-stl-2026-09-14-first-five-winner-away", "Yes", w=fg_win)
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBGAME-26SEP141945SFSTL-SF"   # WRONG bind -> teeth confirmed


def test_f5_winner_route_only_correct_wiring_misses():
    """Correct wiring: NO F5 winner ticker exists (empty F5 index), only the full-game moneyline ticker exists in the
    world -- but the F5 matcher is handed ONLY the (empty) F5 index -> MISS, never the full-game ticker."""
    _, r = _f5_match("mlb-sf-stl-2026-09-14-first-five-winner-away", "Yes", w={})
    assert r.status == "no_kalshi_contract" and r.kalshi_ticker is None


def test_f5_total_route_only_vs_teeth():
    fg_total = {"26SEP141945SFSTL": {4.5: "KXMLBTOTAL-26SEP141945SFSTL-5"}}   # a FULL-GAME total at the same strike
    # teeth: sharing the full-game total index into the F5 total slot binds the full-game total ticker (WRONG)
    _, r_teeth = _f5_match("mlb-sf-stl-2026-09-14-f5-total-4pt5", "Over", t=fg_total)
    assert r_teeth.status == "matched" and r_teeth.kalshi_ticker == "KXMLBTOTAL-26SEP141945SFSTL-5"
    # correct: F5 total index empty -> MISS (the F5 bet never reaches the full-game total ticker)
    _, r_ok = _f5_match("mlb-sf-stl-2026-09-14-f5-total-4pt5", "Over", t={})
    assert r_ok.status != "matched"


# ── F5 non-f5 slugs untouched (RFI + full-game regression) ────────────────────
def test_f5_types_in_copyable():
    for mt in ("f5_winner", "f5_total", "f5_spread"):
        assert mt in M.COPYABLE_MARKET_TYPES


def test_f5_regression_moneyline_unchanged():
    ml, wi, ti, si, dates = _mlb_idx()
    p = M.parse_poly_mlb_bet("mlb-sf-stl-2026-09-14", "San Francisco Giants", "")
    assert p.market_type == "moneyline"
    r = M.match_bet(p, ml, {}, {}, dates, allowed_market_types=("moneyline",),
                    f5_win_index=wi, f5_total_index=ti, f5_spread_index=si)
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBGAME-26SEP141945SFSTL-SF" and r.leg == "yes"


# ── F5 winner leg-audit (Yes/No DIRECT; independent of the matcher transform) ─
def test_audit_f5_winner_direct_ok_and_inversion():
    assert live_driver._audit_leg_independent("mlb", "Yes", "KXMLBF5-26SEP141945SFSTL-SF", "yes") == "ok"
    assert live_driver._audit_leg_independent("mlb", "No", "KXMLBF5-26SEP141945SFSTL-SF", "no") == "ok"
    assert live_driver._audit_leg_independent("mlb", "Yes", "KXMLBF5-26SEP141945SFSTL-SF", "no").startswith("REVIEW")


def test_audit_f5_total_uses_total_branch():
    assert live_driver._audit_leg_independent("mlb", "Over", "KXMLBF5TOTAL-26SEP141945SFSTL-5", "yes") == "ok"
    assert live_driver._audit_leg_independent("mlb", "Under", "KXMLBF5TOTAL-26SEP141945SFSTL-5", "no") == "ok"
    assert live_driver._audit_leg_independent("mlb", "Over", "KXMLBF5TOTAL-26SEP141945SFSTL-5", "no").startswith("REVIEW")


def test_audit_f5_spread_is_na_like_fullgame():
    # F5 spread stays 'na' (code-anchored like full-game spread); startswith('KXMLBF5-') must NOT catch the SPREAD series
    assert live_driver._audit_leg_independent("mlb", "San Francisco Giants", "KXMLBF5SPREAD-26SEP141945SFSTL-SF2", "yes") == "na"


# ══════════════════════════════════════════════════════════════════════════════════════════
# structural first-half (1H): winner (3-way) + total + spread. nfl is in-season.
# ══════════════════════════════════════════════════════════════════════════════════════════
NFL = SS.LEAGUES["nfl"]
NFL_GAME_TK = ["KXNFLGAME-26SEP13NODET-NO", "KXNFLGAME-26SEP13NODET-DET"]
H1_WIN_TK = ["KXNFL1H-26SEP13NODET-NO", "KXNFL1H-26SEP13NODET-DET", "KXNFL1H-26SEP13NODET-TIE"]
H1_TOT_TK = ["KXNFL1HTOTAL-26SEP13NODET-24"]        # strike 23.5
H1_SPR_TK = ["KXNFL1HSPREAD-26SEP13NODET-DET4"]     # DET -3.5


def _nfl_idx():
    gi = SS.build_game_index(NFL_GAME_TK, NFL)
    w = SS.build_h1_win_index(H1_WIN_TK, NFL)
    t = SS.build_h1_total_index(H1_TOT_TK, NFL)
    s = SS.build_h1_spread_index(H1_SPR_TK, NFL)
    return gi, w, t, s, frozenset(k[0] for k in gi.keys())


def _fh_match(slug, outcome, *, w=None, t=None, s=None, allowed=("first_half",)):
    gi, wi, ti, si, dates = _nfl_idx()
    p = SS.parse_poly_bet(slug, outcome, NFL)
    return p, SS.match_bet(p, gi, dates, NFL, allowed_market_types=allowed,
                           total_index={}, spread_index={},
                           h1_win_index=(wi if w is None else w),
                           h1_total_index=(ti if t is None else t),
                           h1_spread_index=(si if s is None else s))


# ── 1H index builders (config-driven series) ─────────────────────────────────
def test_h1_index_builders_use_cfg_series():
    assert SS.build_h1_win_index(H1_WIN_TK, NFL) == {
        "26SEP13NODET": {"NO": "KXNFL1H-26SEP13NODET-NO", "DET": "KXNFL1H-26SEP13NODET-DET",
                         "TIE": "KXNFL1H-26SEP13NODET-TIE"}}
    assert SS.build_h1_total_index(H1_TOT_TK, NFL) == {"26SEP13NODET": {23.5: "KXNFL1HTOTAL-26SEP13NODET-24"}}
    assert SS.build_h1_spread_index(H1_SPR_TK, NFL) == {"26SEP13NODET": {("DET", 3.5): "KXNFL1HSPREAD-26SEP13NODET-DET4"}}


def test_h1_builders_empty_when_cfg_lacks_series():
    # a league cfg with no h1_* series -> {} (safe/inert), regardless of tickers handed in
    bare = SS.LEAGUES.get("nhl")
    if bare is not None and not getattr(bare, "h1_win_series", None):
        assert SS.build_h1_win_index(H1_WIN_TK, bare) == {}


# ── 1H winner: team / team / tie ─────────────────────────────────────────────
def test_1h_winner_home_team_yes():
    p, r = _fh_match("nfl-no-det-2026-09-13-1h-moneyline", "Lions")
    assert p.market_type == "first_half_winner"
    assert r.status == "matched" and r.kalshi_ticker == "KXNFL1H-26SEP13NODET-DET" and r.leg == "yes"


def test_1h_winner_tie_binds_tie_only():
    p, r = _fh_match("nfl-no-det-2026-09-13-1h-moneyline", "Tie")
    assert p.side == "draw"
    assert r.status == "matched" and r.kalshi_ticker == "KXNFL1H-26SEP13NODET-TIE" and r.leg == "yes"


def test_1h_winner_tie_CANNOT_reach_team_ticker():
    w_no_tie = {"26SEP13NODET": {"NO": "KXNFL1H-26SEP13NODET-NO", "DET": "KXNFL1H-26SEP13NODET-DET"}}
    p, r = _fh_match("nfl-no-det-2026-09-13-1h-moneyline", "Tie", w=w_no_tie)
    assert r.status == "no_kalshi_contract" and r.kalshi_ticker is None


def test_1h_total_over_exact_strike():
    p, r = _fh_match("nfl-no-det-2026-09-13-1h-total-23pt5", "Over")
    assert p.market_type == "first_half_total"
    assert r.status == "matched" and r.kalshi_ticker == "KXNFL1HTOTAL-26SEP13NODET-24" and r.leg == "yes" and r.strike == 23.5


def test_1h_total_under_is_no():
    p, r = _fh_match("nfl-no-det-2026-09-13-1h-total-23pt5", "Under")
    assert r.status == "matched" and r.leg == "no"


def test_1h_total_wrong_strike_safe_miss():
    p, r = _fh_match("nfl-no-det-2026-09-13-1h-total-27pt5", "Over")   # 27.5, ticker is 23.5
    assert r.status != "matched"


def test_1h_spread_anchor_yes():
    p, r = _fh_match("nfl-no-det-2026-09-13-1h-spread-home-3pt5", "Lions")
    assert p.market_type == "first_half_spread"
    assert r.status == "matched" and r.kalshi_ticker == "KXNFL1HSPREAD-26SEP13NODET-DET4" and r.leg == "yes" and r.strike == 3.5


def test_1h_spread_other_side_is_no():
    p, r = _fh_match("nfl-no-det-2026-09-13-1h-spread-home-3pt5", "Saints")
    assert r.status == "matched" and r.kalshi_ticker == "KXNFL1HSPREAD-26SEP13NODET-DET4" and r.leg == "no"


# ── 1H inert until enabled ───────────────────────────────────────────────────
def test_1h_inert_when_not_enabled():
    p, r = _fh_match("nfl-no-det-2026-09-13-1h-moneyline", "Lions",
                     allowed=("moneyline", "total", "spread"))
    assert r.status == "skip_market_type_excluded"


def test_1h_one_token_gates_all_three():
    for slug, oc in (("nfl-no-det-2026-09-13-1h-moneyline", "Lions"),
                     ("nfl-no-det-2026-09-13-1h-total-23pt5", "Over"),
                     ("nfl-no-det-2026-09-13-1h-spread-home-3pt5", "Lions")):
        _, r = _fh_match(slug, oc, allowed=("first_half",))
        assert r.status == "matched", (slug, r)
        _, r2 = _fh_match(slug, oc, allowed=("moneyline",))
        assert r2.status == "skip_market_type_excluded", (slug, r2)


# ── THE -2h- FINDING: second half is a KNOWN UNSUPPORTED FAMILY -> skip, NEVER a 1H bind ──
def test_2h_moneyline_is_non_moneyline_skip():
    p, r = _fh_match("nfl-no-det-2026-09-13-2h-moneyline", "Lions", allowed=("first_half", "moneyline"))
    assert p.market_type == "non_moneyline"          # -2h- falls through to the labelled skip, NOT first_half_winner
    assert r.status == "skip_non_moneyline" and r.kalshi_ticker is None


def test_2h_total_is_non_moneyline_skip():
    p, r = _fh_match("nfl-no-det-2026-09-13-2h-total-23pt5", "Over", allowed=("first_half", "total"))
    assert p.market_type == "non_moneyline" and r.status == "skip_non_moneyline"


def test_1h_moneyline_with_extra_segment_falls_through():
    # `-1h-moneyline-433` (an unexpected extra segment) is NOT exactly `-1h-moneyline` -> skip, never a guessed 1H bind
    p, r = _fh_match("nfl-no-det-2026-09-13-1h-moneyline-433", "Lions", allowed=("first_half", "moneyline"))
    assert p.market_type == "non_moneyline" and r.status == "skip_non_moneyline"


# ── 1H ROUTE-ONLY: teeth (shared -> wrong bind) + correct wiring (miss) ───────
def test_1h_winner_shared_fullgame_index_binds_fullgame_TEETH():
    """DELIBERATELY share the FULL-GAME moneyline index into the 1H winner slot -> it (WRONGLY) binds the full-game
    moneyline ticker (right game, right team, WRONG market). Proves the route-only miss below is caused by which
    index is passed, not by the ticker being absent. A production wiring that shared the indices would do THIS."""
    fg_win = SG.build_win_index(NFL_GAME_TK, "KXNFLGAME")
    _, r = _fh_match("nfl-no-det-2026-09-13-1h-moneyline", "Lions", w=fg_win)
    assert r.status == "matched" and r.kalshi_ticker == "KXNFLGAME-26SEP13NODET-DET"   # WRONG bind -> teeth confirmed


def test_1h_winner_route_only_correct_wiring_misses():
    _, r = _fh_match("nfl-no-det-2026-09-13-1h-moneyline", "Lions", w={})
    assert r.status == "no_kalshi_contract" and r.kalshi_ticker is None


def test_1h_total_route_only_vs_teeth():
    fg_total = {"26SEP13NODET": {23.5: "KXNFLTOTAL-26SEP13NODET-24"}}   # a FULL-GAME total at the same strike
    _, r_teeth = _fh_match("nfl-no-det-2026-09-13-1h-total-23pt5", "Over", t=fg_total)
    assert r_teeth.status == "matched" and r_teeth.kalshi_ticker == "KXNFLTOTAL-26SEP13NODET-24"
    _, r_ok = _fh_match("nfl-no-det-2026-09-13-1h-total-23pt5", "Over", t={})
    assert r_ok.status != "matched"


# ── 1H types in copyable + registry wiring for the in-season leagues ──────────
def test_first_half_types_in_copyable():
    for mt in ("first_half_winner", "first_half_total", "first_half_spread"):
        assert mt in SS.COPYABLE_MARKET_TYPES


def test_registry_h1_series_for_inseason_leagues():
    assert NFL.h1_win_series == "KXNFL1H" and NFL.h1_total_series == "KXNFL1HTOTAL" and NFL.h1_spread_series == "KXNFL1HSPREAD"
    cfb = SS.LEAGUES["cfb"]
    assert cfb.h1_win_series == "KXNCAAF1H" and cfb.h1_total_series == "KXNCAAF1HTOTAL" and cfb.h1_spread_series == "KXNCAAF1HSPREAD"


def test_registry_h1_series_for_nba_configured_but_offseason():
    # nba is INCONCLUSIVE at build time (offseason -> empty fetch), but the series ARE configured so it lights up at tip-off
    nba = SS.LEAGUES["nba"]
    assert nba.h1_win_series == "KXNBA1HWINNER" and nba.h1_total_series == "KXNBA1HTOTAL" and nba.h1_spread_series == "KXNBA1HSPREAD"


# ── 1H regression: full-game moneyline/total/spread parse+match unchanged ─────
def test_1h_regression_fullgame_moneyline_unchanged():
    gi, wi, ti, si, dates = _nfl_idx()
    p = SS.parse_poly_bet("nfl-no-det-2026-09-13", "Lions", NFL)
    assert p.market_type == "moneyline"
    r = SS.match_bet(p, gi, dates, NFL, allowed_market_types=("moneyline",), total_index={}, spread_index={},
                     h1_win_index=wi, h1_total_index=ti, h1_spread_index=si)
    assert r.status == "matched" and r.kalshi_ticker == "KXNFLGAME-26SEP13NODET-DET" and r.leg == "yes"


# ── 1H leg-audit: winner/spread stay 'na' (code-anchored); total uses TOTAL- branch ──
def test_audit_1h_winner_is_na():
    assert live_driver._audit_leg_independent("nfl", "Lions", "KXNFL1H-26SEP13NODET-DET", "yes") == "na"


def test_audit_1h_spread_is_na():
    assert live_driver._audit_leg_independent("nfl", "Lions", "KXNFL1HSPREAD-26SEP13NODET-DET4", "yes") == "na"


def test_audit_1h_total_uses_total_branch():
    assert live_driver._audit_leg_independent("nfl", "Over", "KXNFL1HTOTAL-26SEP13NODET-24", "yes") == "ok"
    assert live_driver._audit_leg_independent("nfl", "Under", "KXNFL1HTOTAL-26SEP13NODET-24", "no") == "ok"
    assert live_driver._audit_leg_independent("nfl", "Over", "KXNFL1HTOTAL-26SEP13NODET-24", "no").startswith("REVIEW")


# ══════════════════════════════════════════════════════════════════════════════════════════
# adapter wiring: the sub-game indices flow through the adapters via MarketContext
# ══════════════════════════════════════════════════════════════════════════════════════════
def test_mlb_adapter_passes_f5_indices():
    ml, wi, ti, si, dates = _mlb_idx()
    ctx = execution.MarketContext(ml, {}, {}, dates, {}, f5_win_index=wi, f5_total_index=ti, f5_spread_index=si)
    p = M.parse_poly_mlb_bet("mlb-sf-stl-2026-09-14-first-five-winner-away", "Yes", "")
    r = execution._mlb_match(p, ctx, ("f5",))
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBF5-26SEP141945SFSTL-SF" and r.leg == "yes"


def test_structural_adapter_passes_h1_indices():
    gi, wi, ti, si, dates = _nfl_idx()
    ctx = execution.MarketContext({}, {}, {}, dates, {}, structural_index=gi,
                                  h1_win_index=wi, h1_total_index=ti, h1_spread_index=si)
    _parse, _match = execution._structural_adapter(NFL)
    p = SS.parse_poly_bet("nfl-no-det-2026-09-13-1h-moneyline", "Lions", NFL)
    r = _match(p, ctx, ("first_half",))
    assert r.status == "matched" and r.kalshi_ticker == "KXNFL1H-26SEP13NODET-DET" and r.leg == "yes"


def test_structural_adapter_does_NOT_route_fullgame_into_1h():
    """The adapter reads ctx.h1_* for the 1H join; the full-game total/spread ride ctx.total_index/spread_index and are
    NOT reachable from a 1H bet. Populate ONLY the full-game total slot -> a 1H total bet MISSES (route-only at the
    adapter boundary, not just the join)."""
    gi, wi, ti, si, dates = _nfl_idx()
    # positional total_index = a FULL-GAME total at the same strike; 1H total slot is EMPTY
    ctx = execution.MarketContext({}, {"26SEP13NODET": {23.5: "KXNFLTOTAL-26SEP13NODET-24"}}, {}, dates, {},
                                  structural_index=gi,
                                  h1_win_index=wi, h1_total_index={}, h1_spread_index={})   # 1H total EMPTY
    _parse, _match = execution._structural_adapter(NFL)
    p = SS.parse_poly_bet("nfl-no-det-2026-09-13-1h-total-23pt5", "Over", NFL)
    r = _match(p, ctx, ("first_half",))
    assert r.status != "matched"   # the full-game total in ctx.total_index is NEVER consulted for a 1H bet
