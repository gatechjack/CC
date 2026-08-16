"""Unit tests for the deterministic Poly-MLB -> Kalshi-KXMLBGAME matcher.

Pure (no network). Cases are built from REAL formats observed 2026-08-15:
  Poly slug   mlb-col-sf-2026-08-16 (+ -total-/-spread- suffixes)
  Kalshi tkr  KXMLBGAME-26AUG161337NYYTOR-NYY
"""
from __future__ import annotations

from trading_corp.data.sports_team_mapping import MLB_TEAMS
from trading_corp.data.mlb_poly_kalshi_match import (
    ParsedPolyBet, build_kalshi_game_index, iso_to_kalshi_date, kalshi_to_iso_date,
    match_poly_to_kalshi, parse_poly_mlb_bet, resolve_side,
)


# ── codebook completeness (Q1) ─────────────────────────────────────────────
def test_codebook_has_all_30_clubs():
    assert len(set(MLB_TEAMS.values())) == 30


def test_codebook_variant_codes_resolve():
    # Poly/Kalshi abbreviation variants must all resolve to a club.
    for code in ("AZ", "ARI", "ATH", "OAK", "CWS", "CHW", "SD", "SDP",
                 "TB", "TBR", "WSH", "WAS", "WSN", "KC", "KCR", "SF", "SFG"):
        assert MLB_TEAMS.get(code) is not None, code


# ── date conversion ────────────────────────────────────────────────────────
def test_date_roundtrip():
    assert iso_to_kalshi_date("2026-08-16") == "26AUG16"
    assert kalshi_to_iso_date("26AUG16") == "2026-08-16"
    assert iso_to_kalshi_date("2026-13-01") is None
    assert kalshi_to_iso_date("26XYZ16") is None


# ── Poly parsing + market-type gate ────────────────────────────────────────
def test_moneyline_parse():
    b = parse_poly_mlb_bet("mlb-col-sf-2026-08-16", "San Francisco Giants")
    assert b.market_type == "moneyline"
    assert b.date_iso == "2026-08-16"
    assert b.away_name == "Colorado Rockies" and b.home_name == "San Francisco Giants"
    assert b.side == "home" and b.side_name == "San Francisco Giants"


def test_total_is_skip_not_ml():
    b = parse_poly_mlb_bet("mlb-col-sf-2026-08-16-total-7pt5", "Over")
    assert b.market_type == "total"


def test_spread_outcome_is_team_but_still_skip():
    # The spread outcome is a TEAM NAME — must NOT be mistaken for moneyline.
    b = parse_poly_mlb_bet("mlb-ari-atl-2026-08-16-spread-home-1pt5", "Arizona Diamondbacks")
    assert b.market_type == "spread"


def test_prop_and_non_mlb():
    assert parse_poly_mlb_bet("mlb-sd-cle-2026-08-14-nrfi", "Yes").market_type == "prop"
    assert parse_poly_mlb_bet("nba-bos-nyk-2026-08-16", "Boston Celtics").market_type == "non_mlb"


def test_unrecognized_team_code_fails_loudly():
    b = parse_poly_mlb_bet("mlb-zzz-sf-2026-08-16", "San Francisco Giants")
    assert b.market_type == "moneyline" and b.away_name is None
    assert b.fail_reason and "unrecognized_team_code" in b.fail_reason


def test_side_resolution_nickname():
    # "Athletics" must resolve to "Oakland Athletics" against the two candidates.
    assert resolve_side("Athletics", "Texas Rangers", "Oakland Athletics") == "home"
    assert resolve_side("Rangers", "Texas Rangers", "Oakland Athletics") == "away"


# ── Kalshi index + matching ────────────────────────────────────────────────
NYYTOR = ["KXMLBGAME-26AUG161337NYYTOR-NYY", "KXMLBGAME-26AUG161337NYYTOR-TOR"]
AZATL = ["KXMLBGAME-26AUG161335AZATL-AZ", "KXMLBGAME-26AUG161335AZATL-ATL"]
# synthetic doubleheader: same BAL/TB teams+date, two different HHMM
BALTB_DH = ["KXMLBGAME-26AUG161215BALTB-BAL", "KXMLBGAME-26AUG161215BALTB-TB",
            "KXMLBGAME-26AUG161915BALTB-BAL", "KXMLBGAME-26AUG161915BALTB-TB"]


def _index(tickers):
    idx = build_kalshi_game_index(tickers)
    dates = frozenset(k[0] for k in idx)
    return idx, dates


def test_unique_match_resolves_side_ticker():
    idx, dates = _index(NYYTOR)
    b = parse_poly_mlb_bet("mlb-nyy-tor-2026-08-16", "New York Yankees")
    r = match_poly_to_kalshi(b, idx, dates)
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBGAME-26AUG161337NYYTOR-NYY"
    assert r.confidence == 1.0


def test_abbreviation_divergence_poly_ari_vs_kalshi_az():
    # Poly slug 'ari' vs Kalshi ticker 'AZ' — both are Arizona; must match.
    idx, dates = _index(AZATL)
    b = parse_poly_mlb_bet("mlb-ari-atl-2026-08-16", "Arizona Diamondbacks")
    r = match_poly_to_kalshi(b, idx, dates)
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBGAME-26AUG161335AZATL-AZ"


def test_doubleheader_is_ambiguous_not_guessed():
    idx, dates = _index(BALTB_DH)
    b = parse_poly_mlb_bet("mlb-bal-tb-2026-08-16", "Baltimore Orioles")
    r = match_poly_to_kalshi(b, idx, dates)
    assert r.status == "doubleheader_ambiguous"
    assert len(r.kalshi_candidates) == 4 and r.kalshi_ticker is None


def test_no_contract_vs_out_of_window():
    idx, dates = _index(NYYTOR)  # only 2026-08-16 present
    # same-window date, teams not offered -> no_kalshi_contract
    b_in = parse_poly_mlb_bet("mlb-bos-pit-2026-08-16", "Boston Red Sox")
    assert match_poly_to_kalshi(b_in, idx, dates).status == "no_kalshi_contract"
    # date outside the fetched window -> out_of_window
    b_out = parse_poly_mlb_bet("mlb-nyy-tor-2026-05-01", "New York Yankees")
    assert match_poly_to_kalshi(b_out, idx, dates).status == "out_of_window"


def test_skip_buckets_route_correctly():
    idx, dates = _index(NYYTOR)
    total = parse_poly_mlb_bet("mlb-nyy-tor-2026-08-16-total-7pt5", "Over")
    assert match_poly_to_kalshi(total, idx, dates).status == "skip_non_ml"
    nonmlb = parse_poly_mlb_bet("nba-bos-nyk-2026-08-16", "Boston Celtics")
    assert match_poly_to_kalshi(nonmlb, idx, dates).status == "skip_non_game"
