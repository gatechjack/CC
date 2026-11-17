"""Polymarket Data API v2 BOUNDARY EQUIVALENCE for the category matchers (offline; no network, no DB, no client).

trading_corp/data/polymarket_data_api_client.py was migrated to Data API v2 (snake_case raw keys). The category
matchers consume ONLY (slug, outcome, title) strings (+ outcome_index in paper.py). These tests prove the migration
is invisible to them: a whale position arriving as a RAW V2 dict -> `PositionRow.from_api` -> the category parse +
match resolves to the SAME Kalshi (ticker, leg) the v1-era fixtures expect. Matcher code is untouched by the migration.

Per category: build a raw v2 position (current_price ~0.5, redeemable False -> genuinely open), run it through
PositionRow.from_api, assert paper.pos_outcome_index == the raw outcome_index and paper.is_genuinely_open is True
(the synthesized v1 "outcomeIndex"/"curPrice"/"redeemable" extra keys), then feed rp.slug / rp.outcome / rp.title
into the matcher against a tiny Kalshi index copied from the category's existing test file.

Independence rule (Jack's): every expected (ticker, leg) is derived from the Kalshi ticker's OWN -CODE + the
title "A vs B" + the whale-buys-YES-on-the-named-side convention (Yes->yes / No->no for binary markets,
Over->yes / Under->no for totals) -- NEVER from the matcher's output. Fixture + expectation sources are cited per test.

SKIPPED (no reusable fixture anywhere in tests/ -- not invented):
  * nba moneyline: the only NBA ticker in tests/ is KXNBAGAME-26AUG31LALBOS-LAL (test_account_rollup.py, a DB-row
    fixture, no Poly slug/outcome/title and no match fixture). nba shares the structural matcher with nfl (same
    SS.parse_poly_bet / SS.match_bet, config SS.LEAGUES["nba"]); the nfl case below exercises that shared code path.
"""
from trading_corp.data import boxing_poly_kalshi_match as BX
from trading_corp.data import cs2_poly_kalshi_match as CS2
from trading_corp.data import f1_poly_kalshi_match as F1X
from trading_corp.data import itf_poly_kalshi_match as ITF
from trading_corp.data import mlb_poly_kalshi_match as M
from trading_corp.data import soccer_poly_kalshi_match as SOC
from trading_corp.data import sports_structural_match as SS
from trading_corp.data import tennis_poly_kalshi_match as TEN
from trading_corp.data import ufc_poly_kalshi_match as UFC
from trading_corp.data.polymarket_data_api_client import PositionRow
from trading_corp.prediction_markets import paper


def _v2_position(slug, outcome, title, outcome_index):
    """A RAW Data API v2 /positions row (snake_case), genuinely open (0 < current_price < 1, redeemable False)."""
    return {
        "proxy_wallet": "0xFB07F48542000000000000000000000000000001",
        "condition_id": "0xcond%s" % slug[-12:],
        "token_id": "1234567890123456789012345678901234567890",
        "current_size": 100.0,
        "avg_price": 0.48,
        "entry_cost_usdc": 48.0,
        "current_value": 50.0,
        "unrealized_pnl": 2.0,
        "current_price": 0.50,
        "redeemable": False,
        "end_date": "2026-12-31",
        "title": title,
        "outcome": outcome,
        "outcome_index": outcome_index,
        "slug": slug,
        "event_slug": slug,
    }


def _rp(slug, outcome, title, outcome_index):
    """raw v2 -> PositionRow, asserting the paper.py extra-key contract on the way (step 3 of every test)."""
    raw = _v2_position(slug, outcome, title, outcome_index)
    rp = PositionRow.from_api(raw)
    assert (rp.slug, rp.outcome, rp.title) == (slug, outcome, title)
    assert paper.pos_outcome_index(rp) == raw["outcome_index"] == outcome_index
    assert paper.is_genuinely_open(rp) is True
    return rp


def _dates(idx):
    return frozenset(idx.keys())


# ── 1. itf ─ fixtures + expectation: test_itf_match.py::test_match_mens_winner / test_match_womens_winner_same_index
ITF_MEN = [{"ticker": "KXITFMATCH-26SEP16WENTOS-WEN", "title": "Jefferson Wendler Filho wins"},
           {"ticker": "KXITFMATCH-26SEP16WENTOS-TOS", "title": "Marco Tosetti wins"}]
ITF_WOMEN = [{"ticker": "KXITFWMATCH-26SEP16ZELPRE-ZEL", "title": "Darja Zeltina wins"},
             {"ticker": "KXITFWMATCH-26SEP16ZELPRE-PRE", "title": "Amarni Pree wins"}]
ITF_SLUG = "itf-wendler-tosetti-2026-09-16"
ITF_TITLE = "ITF: Jefferson Wendler Filho vs Marco Tosetti"


def _itf_resolve(slug, outcome, title):
    idx = ITF.build_kalshi_itf_index(ITF_MEN + ITF_WOMEN)
    parsed = ITF.parse_poly_itf_bet(slug, outcome, title)
    return ITF.match_bet(parsed, idx, _dates(idx), allowed_market_types=ITF.COPYABLE_MARKET_TYPES)


def test_v2_itf_mens_winner_via_position_row():
    # independent: "Marco Tosetti" -> the -TOS-code ticker, YES leg (test_itf_match.py::test_match_mens_winner)
    rp = _rp(ITF_SLUG, "Marco Tosetti", ITF_TITLE, 1)
    r = _itf_resolve(rp.slug, rp.outcome, rp.title)
    assert r.status == "matched" and r.kalshi_ticker == "KXITFMATCH-26SEP16WENTOS-TOS" and r.leg == "yes", r


def test_v2_itf_womens_winner_via_position_row():
    # independent: "Darja Zeltina" -> -ZEL-code ticker, YES (test_itf_match.py::test_match_womens_winner_same_index)
    rp = _rp("itf-zeltina-pree-2026-09-16", "Darja Zeltina", "ITF: Darja Zeltina vs Amarni Pree", 0)
    r = _itf_resolve(rp.slug, rp.outcome, rp.title)
    assert r.status == "matched" and r.kalshi_ticker == "KXITFWMATCH-26SEP16ZELPRE-ZEL" and r.leg == "yes", r


def test_v2_itf_control_plain_strings_identical_to_position_row():
    # CONTROL: the same inputs as plain strings (no PositionRow) resolve identically -> the client boundary
    # adds/loses nothing for the matcher.
    rp = _rp(ITF_SLUG, "Marco Tosetti", ITF_TITLE, 1)
    via_row = _itf_resolve(rp.slug, rp.outcome, rp.title)
    plain = _itf_resolve(ITF_SLUG, "Marco Tosetti", ITF_TITLE)
    assert (plain.status, plain.kalshi_ticker, plain.leg) == (via_row.status, via_row.kalshi_ticker, via_row.leg)
    assert plain.status == "matched" and plain.kalshi_ticker == "KXITFMATCH-26SEP16WENTOS-TOS" and plain.leg == "yes"


# ── 2. boxing ─ fixtures + expectation: test_boxing_match.py::test_match_surname_outcome_resolves_side / _other_side
BX_MARKETS = [
    {"ticker": "KXBOXING-26SEP12GARCIAMORALE-GARCIA", "title": "Sean Garcia wins", "yes_sub_title": "Sean Garcia"},
    {"ticker": "KXBOXING-26SEP12GARCIAMORALE-MORALE", "title": "Abraham Morales wins", "yes_sub_title": "Abraham Morales"},
]


def _boxing_resolve(slug, outcome, title):
    idx = BX.build_kalshi_boxing_index(BX_MARKETS)
    parsed = BX.parse_poly_boxing_bet(slug, outcome, title)
    return BX.match_bet(parsed, idx, frozenset(k[0] for k in idx))


def test_v2_boxing_surname_outcome_garcia():
    # independent: "Garcia" -> ticker whose OWN code is GARCIA, YES leg
    rp = _rp("zuffa-garci1-moral1-2026-09-12", "Garcia", "Sean Garcia vs. Abraham Morales", 0)
    r = _boxing_resolve(rp.slug, rp.outcome, rp.title)
    assert r.status == "matched" and r.kalshi_ticker == "KXBOXING-26SEP12GARCIAMORALE-GARCIA" and r.leg == "yes", r


def test_v2_boxing_surname_outcome_morales():
    # independent: "Morales" -> ticker whose OWN code is MORALE, YES leg
    rp = _rp("zuffa-garci1-moral1-2026-09-12", "Morales", "Sean Garcia vs. Abraham Morales", 1)
    r = _boxing_resolve(rp.slug, rp.outcome, rp.title)
    assert r.status == "matched" and r.kalshi_ticker == "KXBOXING-26SEP12GARCIAMORALE-MORALE" and r.leg == "yes", r


# ── 3. f1 ─ fixtures + expectation: test_f1_match.py::test_match_yes / test_match_no_leg_copies_kalshi_no
def _f1_mk(code, name, date="2026-09-06", event="ITAGP26"):
    return {"ticker": "KXF1RACE-%s-%s" % (event, code), "yes_sub_title": name, "close_date_iso": date}


F1_ITA = [_f1_mk("VER", "Max Verstappen"), _f1_mk("GAS", "Pierre Gasly"), _f1_mk("ALO", "Fernando Alonso")]


def _f1_resolve(slug, outcome, title):
    idx = F1X.build_kalshi_f1_index(F1_ITA)
    parsed = F1X.parse_poly_f1_bet(slug, outcome, title)
    return F1X.match_bet(parsed, idx, _dates(idx))


def test_v2_f1_race_winner_yes():
    # independent: Yes on driver "Pierre Gasly" -> -GAS code, YES leg
    rp = _rp("f1-italian-grand-prix-winner-gasly-2026-09-06", "Yes",
             "Will Pierre Gasly win the 2026 F1 Italian Grand Prix?", 0)
    r = _f1_resolve(rp.slug, rp.outcome, rp.title)
    assert r.status == "matched" and r.kalshi_ticker == "KXF1RACE-ITAGP26-GAS" and r.leg == "yes", r


def test_v2_f1_race_winner_no_leg():
    # independent: No on "Max Verstappen" -> -VER code, NO leg (outcome_index 1 = the "No" token)
    rp = _rp("f1-italian-grand-prix-winner-verstappen-2026-09-06", "No",
             "Will Max Verstappen win the 2026 F1 Italian Grand Prix?", 1)
    r = _f1_resolve(rp.slug, rp.outcome, rp.title)
    assert r.status == "matched" and r.kalshi_ticker == "KXF1RACE-ITAGP26-VER" and r.leg == "no", r


# ── 4. mlb ─ fixtures: test_mlb_team_total.py (STEM/_ctx/_run); expectation from the ticker's own -SD code
MLB_STEM = "26SEP222210SDLAD"   # SD @ LAD
MLB_DATE = "2026-09-22"


def _mlb_resolve(slug, outcome, title):
    gi = M.build_kalshi_game_index(["KXMLBGAME-%s-SD" % MLB_STEM, "KXMLBGAME-%s-LAD" % MLB_STEM])
    dates = frozenset([MLB_DATE, "KXMLBGAME-%s-SD" % MLB_STEM, "KXMLBGAME-%s-LAD" % MLB_STEM])
    tt = M.build_kalshi_team_total_index(["KXMLBTEAMTOTAL-%s-SD8" % MLB_STEM, "KXMLBTEAMTOTAL-%s-LAD5" % MLB_STEM])
    full_tot = M.build_kalshi_total_index(["KXMLBTOTAL-%s-8" % MLB_STEM])
    parsed = M.parse_poly_mlb_bet(slug, outcome, title or "")
    return M.match_bet(parsed, gi, full_tot, {}, dates,
                       allowed_market_types=("moneyline", "total", "spread", "team_total"), team_total_index=tt)


def test_v2_mlb_moneyline():
    # independent: "San Diego" -> the -SD-code KXMLBGAME ticker, YES leg (a moneyline copy BUYS YES on the bet team;
    # test_mlb_team_total.py::test_teamtotal_registered_and_moneyline_unchanged asserts matched for this slug/outcome)
    rp = _rp("mlb-sd-lad-%s" % MLB_DATE, "San Diego", "Padres vs. Dodgers", 0)
    r = _mlb_resolve(rp.slug, rp.outcome, rp.title)
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBGAME-%s-SD" % MLB_STEM and r.leg == "yes", r


def test_v2_mlb_team_total_over_and_under():
    # test_mlb_team_total.py::test_match_leg_from_outcome -- SD 7.5 -> SD8 strike (N-0.5); Over->yes, Under->no
    slug = "mlb-sd-lad-%s-team-total-sd-7pt5" % MLB_DATE
    over = _rp(slug, "Over", "Padres vs. Dodgers: Padres Team Total 7.5", 0)
    under = _rp(slug, "Under", "Padres vs. Dodgers: Padres Team Total 7.5", 1)
    ro = _mlb_resolve(over.slug, over.outcome, over.title)
    ru = _mlb_resolve(under.slug, under.outcome, under.title)
    assert ro.status == "matched" and ro.leg == "yes" and ro.kalshi_ticker == "KXMLBTEAMTOTAL-%s-SD8" % MLB_STEM, ro
    assert ru.status == "matched" and ru.leg == "no" and ru.kalshi_ticker == "KXMLBTEAMTOTAL-%s-SD8" % MLB_STEM, ru


# ── 5. nfl (structural matcher; nba shares the code path -- see module docstring for the nba skip)
#      fixtures: test_matcher_leg_independence.py::_nfl_ctx + test_subgame_f5_1h_2026_09_14.py::
#      test_1h_regression_fullgame_moneyline_unchanged ("Lions" -> -DET, yes) and
#      test_matcher_leg_independence.py::test_nfl_total_over_is_yes_at_exact_strike / _under_is_NO_leg
NFL = SS.LEAGUES["nfl"]
NFL_GAMES = ["KXNFLGAME-26SEP13NODET-NO", "KXNFLGAME-26SEP13NODET-DET"]


def _nfl_resolve(slug, outcome, title, total_tickers=()):
    gi = SS.build_game_index(list(NFL_GAMES), NFL)
    ti = SS.build_total_index(list(total_tickers), NFL)
    si = SS.build_spread_index([], NFL)
    parsed = SS.parse_poly_bet(slug, outcome, NFL)   # structural adapter ignores title (execution._structural_adapter)
    return SS.match_bet(parsed, gi, frozenset(k[0] for k in gi.keys()), NFL,
                        allowed_market_types=("moneyline", "total", "spread"), total_index=ti, spread_index=si)


def test_v2_nfl_moneyline():
    # independent: "Lions" -> the -DET-code KXNFLGAME ticker, YES leg
    rp = _rp("nfl-no-det-2026-09-13", "Lions", "Saints vs. Lions", 1)
    r = _nfl_resolve(rp.slug, rp.outcome, rp.title)
    assert r.status == "matched" and r.kalshi_ticker == "KXNFLGAME-26SEP13NODET-DET" and r.leg == "yes", r


def test_v2_nfl_total_over_yes_under_no():
    # independent: line 49.5 -> KXNFLTOTAL ...-50 (strike N-0.5); Over->yes, Under->no
    tot = ["KXNFLTOTAL-26SEP13NODET-50"]
    slug = "nfl-no-det-2026-09-13-total-49pt5"
    over = _rp(slug, "Over", "Saints vs. Lions: O/U 49.5", 0)
    under = _rp(slug, "Under", "Saints vs. Lions: O/U 49.5", 1)
    ro = _nfl_resolve(over.slug, over.outcome, over.title, tot)
    ru = _nfl_resolve(under.slug, under.outcome, under.title, tot)
    assert ro.status == "matched" and ro.kalshi_ticker == "KXNFLTOTAL-26SEP13NODET-50" and ro.leg == "yes", ro
    assert ru.status == "matched" and ru.kalshi_ticker == "KXNFLTOTAL-26SEP13NODET-50" and ru.leg == "no", ru


# ── 6. soccer (mls) ─ fixtures + expectation: test_matcher_leg_independence.py::test_soccer_win_yes_is_yes_leg... /
#      test_soccer_win_no_is_NO_leg_on_same_team_market
SOC_CFG = SOC.LEAGUES["mls"]
SOC_T_LAFC = "KXMLSGAME-26SEP09LAFCNYRB-LAFC"
SOC_MARKETS = [{"ticker": SOC_T_LAFC, "yes_sub_title": "Los Angeles F"},
               {"ticker": "KXMLSGAME-26SEP09LAFCNYRB-NYRB", "yes_sub_title": "New York RB"},
               {"ticker": "KXMLSGAME-26SEP09LAFCNYRB-TIE", "yes_sub_title": "Tie"}]
SOC_SLUG = "mls-laf-nyr-2026-09-09-laf"
SOC_TITLE = "Will Los Angeles F win on 2026-09-09?"


def _soc_resolve(slug, outcome, title):
    idx = SOC.build_game_index(SOC_MARKETS, SOC_CFG)
    parsed = SOC.parse_poly_bet(slug, outcome, SOC_CFG, title=title)
    return SOC.match_bet(parsed, idx, _dates(idx), SOC_CFG)


def test_v2_soccer_win_yes_is_yes_leg():
    # independent: Yes on "Los Angeles F win" -> the -LAFC-code team ticker, YES leg
    rp = _rp(SOC_SLUG, "Yes", SOC_TITLE, 0)
    r = _soc_resolve(rp.slug, rp.outcome, rp.title)
    assert r.status == "matched" and r.kalshi_ticker == SOC_T_LAFC and r.leg == "yes", r


def test_v2_soccer_win_no_is_no_leg():
    # independent: No on the SAME team market -> same -LAFC ticker, NO leg (outcome_index 1 = the "No" token)
    rp = _rp(SOC_SLUG, "No", SOC_TITLE, 1)
    r = _soc_resolve(rp.slug, rp.outcome, rp.title)
    assert r.status == "matched" and r.kalshi_ticker == SOC_T_LAFC and r.leg == "no", r


# ── 7. tennis (atp) ─ fixtures + expectation: test_matcher_leg_independence.py::test_tennis_correct_binding
TEN_MARKETS = [{"ticker": "KXATPMATCH-26SEP04MEDRIN-MED", "title": "Daniil Medvedev wins"},
               {"ticker": "KXATPMATCH-26SEP04MEDRIN-RIN", "title": "Arthur Rinderknech wins"}]


def test_v2_tennis_atp_winner():
    # independent: "Arthur Rinderknech" -> the -RIN-code ticker, YES leg
    rp = _rp("atp-med-rin-2026-09-04", "Arthur Rinderknech", "ATP: Daniil Medvedev vs Arthur Rinderknech", 1)
    idx = TEN.build_kalshi_match_index(TEN_MARKETS)
    parsed = TEN.parse_poly_tennis_bet(rp.slug, rp.outcome, rp.title)
    r = TEN.match_bet(parsed, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == "KXATPMATCH-26SEP04MEDRIN-RIN" and r.leg == "yes", r


# ── 8. ufc ─ fixtures + expectation: test_matcher_leg_independence.py::test_ufc_correct_binding
UFC_MARKETS = [{"ticker": "KXUFCFIGHT-26SEP05HOOPAR-HOO", "title": "Daniel Hooker wins"},
               {"ticker": "KXUFCFIGHT-26SEP05HOOPAR-PAR", "title": "Salahdine Parnasse wins"}]


def test_v2_ufc_moneyline():
    # independent: "Salahdine Parnasse" -> the -PAR-code ticker, YES leg
    rp = _rp("ufc-dan6-salpar-2026-09-05", "Salahdine Parnasse", "UFC Fight Night: Daniel Hooker vs. Salahdine Parnasse", 1)
    idx = UFC.build_kalshi_fight_index(UFC_MARKETS)
    parsed = UFC.parse_poly_ufc_bet(rp.slug, rp.outcome, rp.title)
    r = UFC.match_bet(parsed, idx, frozenset(k[0] for k in idx.keys()))
    assert r.status == "matched" and r.kalshi_ticker == "KXUFCFIGHT-26SEP05HOOPAR-PAR" and r.leg == "yes", r


# ── 9. cs2 ─ fixtures + expectation: test_matcher_leg_independence.py::test_cs2_correct_binding_magic_buys_MGC
CS2_T_FAZE = "KXCS2GAME-26SEP082300FAZEMGC-FAZE"
CS2_T_MGC = "KXCS2GAME-26SEP082300FAZEMGC-MGC"


def test_v2_cs2_match_winner():
    # independent: whale "magic" -> the -MGC ticker (code MGC is magic's side), YES leg
    markets = [{"ticker": CS2_T_FAZE, "yes_sub_title": "FaZe"}, {"ticker": CS2_T_MGC, "yes_sub_title": "magic"}]
    rp = _rp("cs2-mgc-faze-2026-09-08", "magic",
             "Counter-Strike: magic vs FaZe (BO3) - FISSURE PLAYGROUND Group B", 0)
    idx = CS2.build_kalshi_match_index(markets)
    parsed = CS2.parse_poly_cs2_bet(rp.slug, rp.outcome, rp.title)
    r = CS2.match_bet(parsed, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == CS2_T_MGC and r.leg == "yes", r
