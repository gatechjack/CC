"""RUNG 1 -- INDEPENDENT leg/side verification for every Family-B matcher + structural total/spread.

The audit's worst finding: no matcher had ANY unit test, and the pre-arming dry-run "gate" was
TAUTOLOGICAL -- its expected side WAS the matcher's own output, so a side/leg transform error could
never be caught. These tests are the antidote: every expectation below is a HARDCODED (ticker, leg)
hand-derived from the ticker's OWN -CODE suffix and the Poly outcome semantics -- NOT computed by
calling the matcher. A fix verified by the same transform it fixes is not verified; these fixtures
share no transform with the code under test.

Coverage: cs2 / tennis / ufc (the title-anchored Family-B class that produced the magic->FaZe wrong
org), soccer (Yes/No leg faithfulness -- pins that the matcher is correct and the wrong fills are
UPSTREAM), and nfl total/spread (enabled 2026-09-10 on previously-untested leg logic).

The *_swap_* tests reproduce the REAL magic->FaZe class: a Kalshi market whose yes_sub_title/label is
bound to the WRONG ticker code. On the pre-fix matcher they go RED (a wrong-side match); the Rung-2
code-anchoring guard turns them GREEN (a safe miss). Red-first, then green.
"""
from __future__ import annotations

import pytest

from trading_corp.data import cs2_poly_kalshi_match as CS2
from trading_corp.data import tennis_poly_kalshi_match as TEN
from trading_corp.data import ufc_poly_kalshi_match as UFC
from trading_corp.data import soccer_poly_kalshi_match as SOC
from trading_corp.data import sports_structural_match as SSM


def _dates(idx):
    """kalshi_dates set from any {date_iso: [...]} index."""
    return frozenset(idx.keys())


# ══════════════════════════════════════════════════════════════════════════════════════════
# cs2 -- org must bind to the ticker's OWN -CODE, not a free-text yes_sub_title label
# ══════════════════════════════════════════════════════════════════════════════════════════
CS2_SLUG = "cs2-mgc-faze-2026-09-08"
CS2_TITLE = "Counter-Strike: magic vs FaZe (BO3) - FISSURE PLAYGROUND Group B"
CS2_T_FAZE = "KXCS2GAME-26SEP082300FAZEMGC-FAZE"
CS2_T_MGC = "KXCS2GAME-26SEP082300FAZEMGC-MGC"


def _cs2_match(markets, outcome):
    idx = CS2.build_kalshi_match_index(markets)
    parsed = CS2.parse_poly_cs2_bet(CS2_SLUG, outcome, CS2_TITLE)
    return CS2.match_bet(parsed, idx, _dates(idx))


def test_cs2_correct_binding_magic_buys_MGC():
    """Labels correct: whale 'magic' -> the -MGC ticker (hand-verified: code MGC is magic's side)."""
    markets = [{"ticker": CS2_T_FAZE, "yes_sub_title": "FaZe"},
               {"ticker": CS2_T_MGC, "yes_sub_title": "magic"}]
    r = _cs2_match(markets, "magic")
    assert r.status == "matched" and r.kalshi_ticker == CS2_T_MGC and r.leg == "yes", r


def test_cs2_correct_binding_faze_buys_FAZE():
    markets = [{"ticker": CS2_T_FAZE, "yes_sub_title": "FaZe"},
               {"ticker": CS2_T_MGC, "yes_sub_title": "magic"}]
    r = _cs2_match(markets, "FaZe")
    assert r.status == "matched" and r.kalshi_ticker == CS2_T_FAZE and r.leg == "yes", r


def test_cs2_swap_regression_magic_must_NOT_buy_FAZE():
    """THE magic->FaZe bug: the -FAZE ticker is MISLABELED 'magic' (and -MGC mislabeled 'FaZe').
    Pre-fix the matcher buys -FAZE for whale 'magic' (WRONG side). The code-anchoring guard must
    refuse: code FAZE is not a subsequence of 'magic' while the other code MGC is -> the org's code
    points to the OTHER ticker -> safe miss. RED before Rung 2, GREEN after."""
    markets = [{"ticker": CS2_T_FAZE, "yes_sub_title": "magic"},   # MISLABELED
               {"ticker": CS2_T_MGC, "yes_sub_title": "FaZe"}]     # MISLABELED
    r = _cs2_match(markets, "magic")
    assert not (r.status == "matched" and r.kalshi_ticker == CS2_T_FAZE), \
        "bought the opponent (FaZe) for a 'magic' bet -- the audit's confirmed wrong-org fill: %r" % (r,)


def test_cs2_team_liquid_kept_code_not_a_subsequence():
    """Guard must NOT over-skip: Kalshi codes TL (Team Liquid) / NAVI etc. are legit non-subsequences.
    The opponent's code does not claim 'Liquid', so the pick stands (verified 0 legit live skips)."""
    t_tl = "KXCS2GAME-26SEP101300TLNAVI-TL"
    t_navi = "KXCS2GAME-26SEP101300TLNAVI-NAVI"
    markets = [{"ticker": t_tl, "yes_sub_title": "Liquid"},
               {"ticker": t_navi, "yes_sub_title": "Natus Vincere"}]
    idx = CS2.build_kalshi_match_index(markets)
    parsed = CS2.parse_poly_cs2_bet("cs2-liq-navi-2026-09-10", "Liquid",
                                    "Counter-Strike: Liquid vs Natus Vincere (BO3) - X")
    r = CS2.match_bet(parsed, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == t_tl and r.leg == "yes", r


# -- adversarial-review regressions (short-code hole + accent false-positive) --
CS2_VIT = "KXCS2GAME-26SEP101300VITTL-VIT"
CS2_TL2 = "KXCS2GAME-26SEP101300VITTL-TL"


def test_cs2_short_code_swap_vitality_must_NOT_buy_TL():
    """Adversarial review's BLOCKER: -VIT mislabeled 'Team Liquid', -TL mislabeled 'Vitality'; whale bets
    'Vitality'. The earlier ASYMMETRIC subseq guard MISSED this (TL is a subseq of 'vitality', so it never
    fired) -> bought Team Liquid for a Vitality bet. The bipartite assignment check catches it (the cross
    assignment scores strictly higher). RED on the box (no guard) + on the asymmetric guard; GREEN now."""
    markets = [{"ticker": CS2_VIT, "yes_sub_title": "Team Liquid"},   # MISLABELED
               {"ticker": CS2_TL2, "yes_sub_title": "Vitality"}]      # MISLABELED
    idx = CS2.build_kalshi_match_index(markets)
    parsed = CS2.parse_poly_cs2_bet("cs2-vit-tl-2026-09-10", "Vitality",
                                    "Counter-Strike: Vitality vs Team Liquid (BO3) - X")
    r = CS2.match_bet(parsed, idx, _dates(idx))
    assert not (r.status == "matched" and r.kalshi_ticker == CS2_TL2), \
        "bought Team Liquid's ticker (-TL) for a Vitality bet -- the short-code hole: %r" % (r,)


def test_cs2_vitality_correct_kept():
    markets = [{"ticker": CS2_VIT, "yes_sub_title": "Vitality"},
               {"ticker": CS2_TL2, "yes_sub_title": "Team Liquid"}]
    idx = CS2.build_kalshi_match_index(markets)
    parsed = CS2.parse_poly_cs2_bet("cs2-vit-tl-2026-09-10", "Vitality",
                                    "Counter-Strike: Vitality vs Team Liquid (BO3) - X")
    r = CS2.match_bet(parsed, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == CS2_VIT, r


def test_cs2_team_liquid_correct_kept_short_code():
    markets = [{"ticker": CS2_VIT, "yes_sub_title": "Vitality"},
               {"ticker": CS2_TL2, "yes_sub_title": "Team Liquid"}]
    idx = CS2.build_kalshi_match_index(markets)
    parsed = CS2.parse_poly_cs2_bet("cs2-vit-tl-2026-09-10", "Team Liquid",
                                    "Counter-Strike: Vitality vs Team Liquid (BO3) - X")
    r = CS2.match_bet(parsed, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == CS2_TL2, r


def test_tennis_accented_name_correct_kept():
    """Adversarial review's HIGH false-positive: Borna Coric (code COR) with an accent must still bind --
    the guard accent-folds, so 'Ćorić' -> 'coric' and COR is a word-prefix (kept, not dropped)."""
    t_cor = "KXATPMATCH-26SEP09CORZVE-COR"
    t_zve = "KXATPMATCH-26SEP09CORZVE-ZVE"
    markets = [{"ticker": t_cor, "title": "Borna Ćorić wins"},
               {"ticker": t_zve, "title": "Alexander Zverev wins"}]
    idx = TEN.build_kalshi_match_index(markets)
    parsed = TEN.parse_poly_tennis_bet("atp-cor-zve-2026-09-09", "Borna Ćorić",
                                       "ATP: Borna Ćorić vs Alexander Zverev")
    r = TEN.match_bet(parsed, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == t_cor, r


# ══════════════════════════════════════════════════════════════════════════════════════════
# tennis -- same title-anchored class (cs2 was cloned from tennis)
# ══════════════════════════════════════════════════════════════════════════════════════════
TEN_SLUG = "atp-med-rin-2026-09-04"
TEN_TITLE = "ATP: Daniil Medvedev vs Arthur Rinderknech"
TEN_T_MED = "KXATPMATCH-26SEP04MEDRIN-MED"
TEN_T_RIN = "KXATPMATCH-26SEP04MEDRIN-RIN"


def _ten_match(markets, outcome):
    idx = TEN.build_kalshi_match_index(markets)
    parsed = TEN.parse_poly_tennis_bet(TEN_SLUG, outcome, TEN_TITLE)
    return TEN.match_bet(parsed, idx, _dates(idx))


def test_tennis_correct_binding():
    markets = [{"ticker": TEN_T_MED, "title": "Daniil Medvedev wins"},
               {"ticker": TEN_T_RIN, "title": "Arthur Rinderknech wins"}]
    r = _ten_match(markets, "Arthur Rinderknech")
    assert r.status == "matched" and r.kalshi_ticker == TEN_T_RIN and r.leg == "yes", r


def test_tennis_swap_regression_rinderknech_must_NOT_buy_MED():
    """-MED mislabeled 'Arthur Rinderknech', -RIN mislabeled 'Daniil Medvedev'. Whale bets
    'Arthur Rinderknech'. Guard: code MED not a subseq of the org, code RIN is -> refuse -MED."""
    markets = [{"ticker": TEN_T_MED, "title": "Arthur Rinderknech wins"},   # MISLABELED
               {"ticker": TEN_T_RIN, "title": "Daniil Medvedev wins"}]      # MISLABELED
    r = _ten_match(markets, "Arthur Rinderknech")
    assert not (r.status == "matched" and r.kalshi_ticker == TEN_T_MED), \
        "bought Medvedev's ticker for a Rinderknech bet: %r" % (r,)


def test_tennis_van_de_zandschulp_kept():
    """Non-first-3 code VAN (Botic Van de Zandschulp) must still bind -- subsequence handles it,
    opponent code does not claim it."""
    t_van = "KXATPMATCH-26SEP09ZVEVAN-VAN"
    t_zve = "KXATPMATCH-26SEP09ZVEVAN-ZVE"
    markets = [{"ticker": t_van, "title": "Botic Van de Zandschulp wins"},
               {"ticker": t_zve, "title": "Alexander Zverev wins"}]
    idx = TEN.build_kalshi_match_index(markets)
    parsed = TEN.parse_poly_tennis_bet("atp-van-zve-2026-09-09", "Botic Van de Zandschulp",
                                       "ATP: Botic Van de Zandschulp vs Alexander Zverev")
    r = TEN.match_bet(parsed, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == t_van, r


# ══════════════════════════════════════════════════════════════════════════════════════════
# ufc -- title-anchored; fighter_kcode exists but was never used to cross-check
# ══════════════════════════════════════════════════════════════════════════════════════════
UFC_T_HOO = "KXUFCFIGHT-26SEP05HOOPAR-HOO"
UFC_T_PAR = "KXUFCFIGHT-26SEP05HOOPAR-PAR"


def _ufc_match(markets, outcome):
    idx = UFC.build_kalshi_fight_index(markets)
    parsed = UFC.parse_poly_ufc_bet("ufc-dan6-salpar-2026-09-05", outcome)
    return UFC.match_bet(parsed, idx, _dates_ufc(idx))


def _dates_ufc(idx):
    return frozenset(k[0] for k in idx.keys())


def test_ufc_correct_binding():
    markets = [{"ticker": UFC_T_HOO, "title": "Daniel Hooker wins"},
               {"ticker": UFC_T_PAR, "title": "Salahdine Parnasse wins"}]
    r = _ufc_match(markets, "Salahdine Parnasse")
    assert r.status == "matched" and r.kalshi_ticker == UFC_T_PAR and r.leg == "yes", r


def test_ufc_swap_regression_parnasse_must_NOT_buy_HOO():
    """-HOO mislabeled 'Salahdine Parnasse', -PAR mislabeled 'Daniel Hooker'. Whale bets Parnasse.
    Guard via fighter_kcode/subsequence: PAR is Parnasse's code, not HOO."""
    markets = [{"ticker": UFC_T_HOO, "title": "Salahdine Parnasse wins"},   # MISLABELED
               {"ticker": UFC_T_PAR, "title": "Daniel Hooker wins"}]        # MISLABELED
    r = _ufc_match(markets, "Salahdine Parnasse")
    assert not (r.status == "matched" and r.kalshi_ticker == UFC_T_HOO), \
        "bought Hooker's ticker for a Parnasse bet: %r" % (r,)


# ══════════════════════════════════════════════════════════════════════════════════════════
# soccer -- PIN that Yes->yes / No->no leg is faithful (matcher is CORRECT; wrong fills are upstream)
# ══════════════════════════════════════════════════════════════════════════════════════════
SOC_CFG = SOC.LEAGUES["mls"]
SOC_T_LAFC = "KXMLSGAME-26SEP09LAFCNYRB-LAFC"
SOC_T_NYRB = "KXMLSGAME-26SEP09LAFCNYRB-NYRB"
SOC_T_TIE = "KXMLSGAME-26SEP09LAFCNYRB-TIE"


def _soc_markets():
    return [{"ticker": SOC_T_LAFC, "yes_sub_title": "Los Angeles F"},
            {"ticker": SOC_T_NYRB, "yes_sub_title": "New York RB"},
            {"ticker": SOC_T_TIE, "yes_sub_title": "Tie"}]


def _soc_match(outcome):
    idx = SOC.build_game_index(_soc_markets(), SOC_CFG)
    parsed = SOC.parse_poly_bet("mls-laf-nyr-2026-09-09-laf", outcome, SOC_CFG,
                                title="Will Los Angeles F win on 2026-09-09?")
    return SOC.match_bet(parsed, idx, _dates(idx), SOC_CFG)


def test_soccer_win_yes_is_yes_leg_on_team_market():
    r = _soc_match("Yes")
    assert r.status == "matched" and r.kalshi_ticker == SOC_T_LAFC and r.leg == "yes", r


def test_soccer_win_no_is_NO_leg_on_same_team_market():
    """Whale 'No' -> buy NO on 'LAFC wins' (= LAFC does not win). The matcher is faithful; the audit's
    wrong-side mls fills recorded leg=yes, proving the divergence is UPSTREAM (signal-time outcome),
    NOT here. Rung 3 persists the signal-time side to make that provable."""
    r = _soc_match("No")
    assert r.status == "matched" and r.kalshi_ticker == SOC_T_LAFC and r.leg == "no", r


# ══════════════════════════════════════════════════════════════════════════════════════════
# nfl total/spread -- enabled 2026-09-10 on previously-untested leg logic
# ══════════════════════════════════════════════════════════════════════════════════════════
NFL = SSM.LEAGUES["nfl"]


def _nfl_ctx(game_tickers, total_tickers=(), spread_tickers=()):
    gi = SSM.build_game_index(list(game_tickers), NFL)
    ti = SSM.build_total_index(list(total_tickers), NFL)
    si = SSM.build_spread_index(list(spread_tickers), NFL)
    return gi, ti, si, frozenset(k[0] for k in gi.keys())


def test_nfl_total_over_is_yes_at_exact_strike():
    gi, ti, si, dates = _nfl_ctx(["KXNFLGAME-26SEP13NODET-NO", "KXNFLGAME-26SEP13NODET-DET"],
                                 total_tickers=["KXNFLTOTAL-26SEP13NODET-50"])
    parsed = SSM.parse_poly_bet("nfl-no-det-2026-09-13-total-49pt5", "Over", NFL)
    r = SSM.match_bet(parsed, gi, dates, NFL, allowed_market_types=("moneyline", "total", "spread"),
                      total_index=ti, spread_index=si)
    assert r.status == "matched" and r.kalshi_ticker == "KXNFLTOTAL-26SEP13NODET-50" \
        and r.leg == "yes" and r.strike == 49.5, r


def test_nfl_total_under_is_NO_leg():
    gi, ti, si, dates = _nfl_ctx(["KXNFLGAME-26SEP13NODET-NO", "KXNFLGAME-26SEP13NODET-DET"],
                                 total_tickers=["KXNFLTOTAL-26SEP13NODET-50"])
    parsed = SSM.parse_poly_bet("nfl-no-det-2026-09-13-total-49pt5", "Under", NFL)
    r = SSM.match_bet(parsed, gi, dates, NFL, allowed_market_types=("moneyline", "total", "spread"),
                      total_index=ti, spread_index=si)
    assert r.status == "matched" and r.kalshi_ticker == "KXNFLTOTAL-26SEP13NODET-50" and r.leg == "no", r


def test_nfl_total_wrong_strike_is_safe_miss_not_rounded():
    """A -49.5 whale must NEVER land on a -56.5 ticker. Exact-strike-only."""
    gi, ti, si, dates = _nfl_ctx(["KXNFLGAME-26SEP13NODET-NO", "KXNFLGAME-26SEP13NODET-DET"],
                                 total_tickers=["KXNFLTOTAL-26SEP13NODET-57"])  # strike 56.5 only
    parsed = SSM.parse_poly_bet("nfl-no-det-2026-09-13-total-49pt5", "Over", NFL)
    r = SSM.match_bet(parsed, gi, dates, NFL, allowed_market_types=("moneyline", "total", "spread"),
                      total_index=ti, spread_index=si)
    assert r.status != "matched", r


def test_nfl_spread_anchor_home_yes_recovers_via_prevday():
    """Whale spread on the home anchor (SEA -6.5). Poly dates NE@SEA 2026-09-10, Kalshi 26SEP09 (night
    game) -> the shared resolver's -1 fallback must recover it. Expected: SEA7 ticker, leg yes, 6.5."""
    gi, ti, si, dates = _nfl_ctx(["KXNFLGAME-26SEP09NESEA-NE", "KXNFLGAME-26SEP09NESEA-SEA"],
                                 spread_tickers=["KXNFLSPREAD-26SEP09NESEA-SEA7"])
    parsed = SSM.parse_poly_bet("nfl-ne-sea-2026-09-10-spread-home-6pt5", "Seahawks", NFL)
    r = SSM.match_bet(parsed, gi, dates, NFL, allowed_market_types=("moneyline", "total", "spread"),
                      total_index=ti, spread_index=si)
    assert r.status == "matched" and r.kalshi_ticker == "KXNFLSPREAD-26SEP09NESEA-SEA7" \
        and r.leg == "yes" and r.strike == 6.5, r


def test_nfl_spread_wrong_strike_safe_miss():
    gi, ti, si, dates = _nfl_ctx(["KXNFLGAME-26SEP09NESEA-NE", "KXNFLGAME-26SEP09NESEA-SEA"],
                                 spread_tickers=["KXNFLSPREAD-26SEP09NESEA-SEA8"])  # 7.5, not 6.5
    parsed = SSM.parse_poly_bet("nfl-ne-sea-2026-09-10-spread-home-6pt5", "Seahawks", NFL)
    r = SSM.match_bet(parsed, gi, dates, NFL, allowed_market_types=("moneyline", "total", "spread"),
                      total_index=ti, spread_index=si)
    assert r.status != "matched", r


def test_nfl_total_inert_when_moneyline_only():
    """Sanity: a moneyline-only sub still refuses total (the market_types gate), independent of leg."""
    gi, ti, si, dates = _nfl_ctx(["KXNFLGAME-26SEP13NODET-NO", "KXNFLGAME-26SEP13NODET-DET"],
                                 total_tickers=["KXNFLTOTAL-26SEP13NODET-50"])
    parsed = SSM.parse_poly_bet("nfl-no-det-2026-09-13-total-49pt5", "Over", NFL)
    r = SSM.match_bet(parsed, gi, dates, NFL, allowed_market_types=("moneyline",),
                      total_index=ti, spread_index=si)
    assert r.status == "skip_market_type_excluded", r
