"""Tests for trading_corp.data.sports_structural_match -- the shared structural matcher for
nfl/nba/nhl/wnba/cfb (rung 1, moneyline-only). Pure, no network.

★ THE ACCEPTANCE TEST is test_mlb_equivalence: the generic matcher configured for mlb reproduces the
FROZEN mlb_poly_kalshi_match moneyline path BYTE-IDENTICALLY on a battery of real-shaped mlb data (the
B2 shape -- generalized path EQUALS the direct original call, asserted). The 8 armed subs trade on the
mlb matcher; this proves generalizing it changed nothing for them.
"""
from trading_corp.data import sports_structural_match as ssm
from trading_corp.data import mlb_poly_kalshi_match as M

MLB = ssm.LEAGUES["mlb"]

# real-shaped KXMLBGAME tickers: a plain game (with HHMM), a no-time game, and a DOUBLEHEADER (G1/G2)
_MLB_TICKERS = [
    "KXMLBGAME-26AUG161337NYYTOR-NYY", "KXMLBGAME-26AUG161337NYYTOR-TOR",
    "KXMLBGAME-26AUG16LADSF-LAD",      "KXMLBGAME-26AUG16LADSF-SF",
    "KXMLBGAME-26AUG151710MILSTLG1-MIL", "KXMLBGAME-26AUG151710MILSTLG1-STL",
    "KXMLBGAME-26AUG152010MILSTLG2-MIL", "KXMLBGAME-26AUG152010MILSTLG2-STL",
]

# (slug, outcome) battery: both sides, doubleheader, side-unresolved, out-of-window, no-contract,
# unrecognized code, and non-moneyline suffixes (total/spread/prop).
_MLB_BETS = [
    ("mlb-nyy-tor-2026-08-16", "Yankees"),
    ("mlb-nyy-tor-2026-08-16", "Blue Jays"),
    ("mlb-lad-sf-2026-08-16", "Dodgers"),
    ("mlb-lad-sf-2026-08-16", "Padres"),          # neither team -> side_unresolved (safe)
    ("mlb-mil-stl-2026-08-15", "Brewers"),        # DOUBLEHEADER -> ambiguous (never guessed)
    ("mlb-nyy-tor-2026-08-20", "Yankees"),        # date not in window -> out_of_window
    ("mlb-bos-hou-2026-08-16", "Red Sox"),        # date in window, no such game -> no_kalshi_contract
    ("mlb-xyz-tor-2026-08-16", "Yankees"),        # unrecognized code -> fail (safe)
    ("mlb-nyy-tor-2026-08-16-total-8pt5", "Over"),
    ("mlb-nyy-tor-2026-08-16-spread-home-1pt5", "Yankees"),
    ("mlb-nyy-tor-2026-08-16-nrfi", "Yes"),
    ("nba-lal-bos-2026-01-01", "Lakers"),         # not an mlb slug -> non_sport
]


def _dates(tickers):
    out = set()
    for t in tickers:
        # ticker date is the token after the series prefix: KXMLBGAME-{YYMMMDD}...
        core = t.split("-", 1)[1]
        d = ssm.kalshi_to_iso_date(core[:7])
        if d:
            out.add(d)
    return frozenset(out)


def test_mlb_equivalence():
    """generic(mlb-config) == frozen mlb moneyline matcher, field-for-field, on every bet in the battery.
    Non-moneyline bets: mlb matches the total/spread (out of rung-1 scope); the generic SKIPS them and
    NEVER emits a ticker -- asserted, so the moneyline-only generalization can't silently place a spread."""
    dates = _dates(_MLB_TICKERS)
    mlb_idx = M.build_kalshi_game_index(_MLB_TICKERS)
    g_idx = ssm.build_game_index(_MLB_TICKERS, MLB)
    for slug, outcome in _MLB_BETS:
        # generic
        gp = ssm.parse_poly_bet(slug, outcome, MLB)
        gr = ssm.match_bet(gp, g_idx, dates, MLB)
        # frozen mlb oracle (moneyline path), same inputs
        mp = M.parse_poly_mlb_bet(slug, outcome)
        mr = M.match_bet(mp, mlb_idx, {}, {}, dates, allowed_market_types=("moneyline",))
        if mp.market_type == "moneyline" and gp.market_type == "moneyline":
            assert gr.status == mr.status, (slug, outcome, "status", gr.status, mr.status)
            assert gr.kalshi_ticker == mr.kalshi_ticker, (slug, outcome, "ticker", gr.kalshi_ticker, mr.kalshi_ticker)
            assert gr.leg == mr.leg, (slug, outcome, "leg", gr.leg, mr.leg)
            assert tuple(gr.kalshi_candidates) == tuple(mr.kalshi_candidates), (slug, outcome, "cands")
        else:
            # non-moneyline (total/spread/prop) or non-sport: the generic must SKIP and never emit a ticker
            assert gr.status.startswith("skip"), (slug, outcome, "expected skip", gr.status)
            assert gr.kalshi_ticker is None and gr.leg is None, (slug, outcome, "no ticker on skip")


def test_mlb_equivalence_named_cases():
    """spot-assert the specific outcomes so a silent drift in BOTH modules can't pass test_mlb_equivalence."""
    dates = _dates(_MLB_TICKERS)
    g_idx = ssm.build_game_index(_MLB_TICKERS, MLB)
    def m(slug, oc): return ssm.match_bet(ssm.parse_poly_bet(slug, oc, MLB), g_idx, dates, MLB)
    assert m("mlb-nyy-tor-2026-08-16", "Yankees").kalshi_ticker == "KXMLBGAME-26AUG161337NYYTOR-NYY"
    assert m("mlb-nyy-tor-2026-08-16", "Blue Jays").kalshi_ticker == "KXMLBGAME-26AUG161337NYYTOR-TOR"
    assert m("mlb-mil-stl-2026-08-15", "Brewers").status == "doubleheader_ambiguous"
    assert m("mlb-lad-sf-2026-08-16", "Padres").status == "matched" and m("mlb-lad-sf-2026-08-16", "Padres").kalshi_ticker is None
    assert m("mlb-nyy-tor-2026-08-20", "Yankees").status == "out_of_window"
    assert m("mlb-bos-hou-2026-08-16", "Red Sox").status == "no_kalshi_contract"
    assert m("mlb-xyz-tor-2026-08-16", "Yankees").status == "fail"


def _match(cat, tickers, slug, outcome):
    cfg = ssm.LEAGUES[cat]
    idx = ssm.build_game_index(tickers, cfg)
    dates = _dates(tickers)
    return ssm.match_bet(ssm.parse_poly_bet(slug, outcome, cfg), idx, dates, cfg)


def test_nfl_moneyline():
    tk = ["KXNFLGAME-26SEP21NYGLAR-NYG", "KXNFLGAME-26SEP21NYGLAR-LAR"]
    assert _match("nfl", tk, "nfl-nyg-lar-2026-09-21", "Giants").kalshi_ticker == "KXNFLGAME-26SEP21NYGLAR-NYG"
    assert _match("nfl", tk, "nfl-nyg-lar-2026-09-21", "Rams").kalshi_ticker == "KXNFLGAME-26SEP21NYGLAR-LAR"


def test_nba_moneyline():
    tk = ["KXNBAGAME-26OCT20OKCSAS-SAS", "KXNBAGAME-26OCT20OKCSAS-OKC"]
    assert _match("nba", tk, "nba-okc-sas-2026-10-20", "Spurs").kalshi_ticker == "KXNBAGAME-26OCT20OKCSAS-SAS"
    assert _match("nba", tk, "nba-okc-sas-2026-10-20", "Thunder").kalshi_ticker == "KXNBAGAME-26OCT20OKCSAS-OKC"


def test_nhl_moneyline():
    tk = ["KXNHLGAME-26OCT10BOSTOR-BOS", "KXNHLGAME-26OCT10BOSTOR-TOR"]
    assert _match("nhl", tk, "nhl-bos-tor-2026-10-10", "Bruins").kalshi_ticker == "KXNHLGAME-26OCT10BOSTOR-BOS"
    assert _match("nhl", tk, "nhl-bos-tor-2026-10-10", "Maple Leafs").kalshi_ticker == "KXNHLGAME-26OCT10BOSTOR-TOR"


def test_wnba_cross_venue_code_alias():
    # Poly slug uses `gsv`; Kalshi ticker uses `GS` -- both must resolve to the SAME game (the ARI/AZ case).
    tk = ["KXWNBAGAME-26AUG30GSPDX-GS", "KXWNBAGAME-26AUG30GSPDX-PDX"]
    r = _match("wnba", tk, "wnba-gsv-por-2026-08-30", "Golden State Valkyries")
    assert r.kalshi_ticker == "KXWNBAGAME-26AUG30GSPDX-GS", r
    r2 = _match("wnba", tk, "wnba-gsv-por-2026-08-30", "Portland Fire")
    assert r2.kalshi_ticker == "KXWNBAGAME-26AUG30GSPDX-PDX", r2


def test_collision_stays_a_safe_miss():
    # An outcome naming NEITHER team (a code/name collision the map can't disambiguate) is a MISS,
    # never a wrong pick -- the cs2/Cerundolo guard, structural version.
    tk = ["KXNBAGAME-26OCT20OKCSAS-SAS", "KXNBAGAME-26OCT20OKCSAS-OKC"]
    r = _match("nba", tk, "nba-okc-sas-2026-10-20", "Lakers")   # Lakers are in neither
    assert r.status == "matched" and r.kalshi_ticker is None    # side_unresolved -> no ticker (safe)
    # an unmapped Poly code -> fail (never guesses a side)
    assert _match("nba", tk, "nba-zzz-sas-2026-10-20", "Spurs").status == "fail"
    # a TIE ticker is never indexed as a two-team game (soccer-shaped ticker can't leak in)
    tie = ["KXWNBAGAME-26AUG30GSPDX-GS", "KXWNBAGAME-26AUG30GSPDX-PDX", "KXWNBAGAME-26AUG30GSPDX-TIE"]
    idx = ssm.build_game_index(tie, ssm.LEAGUES["wnba"])
    games = idx[ssm._game_key("2026-08-30", "Golden State Valkyries", "Portland Fire")]
    assert len(games) == 1 and set(games[0].ticker_by_side_code) == {"GS", "PDX"}   # TIE dropped


def test_registry_shape():
    for cat in ("mlb", "nfl", "nba", "nhl", "wnba"):
        cfg = ssm.LEAGUES[cat]
        assert cfg.category == cat and cfg.game_series.startswith("KX") and cfg.team_map
    assert ssm.LEAGUES["mlb"].has_doubleheader is True
    assert ssm.LEAGUES["nfl"].has_doubleheader is False
