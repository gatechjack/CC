"""Unit tests for kalshi_sports_arb_observer helpers.

Focus: the deterministic helpers (ticker classification, vig-removal,
sharp-book selection, per-book A-arb evaluation). The async cycle
itself is integration territory and not tested here.
"""
from __future__ import annotations

import pytest

from trading_corp.data.odds_api_client import BookPrice
from trading_corp.agents.strategies.kalshi_sports_arb_observer import (
    _ArbCandidate,
    _PHASE0_LEAGUE_CLASSIFIERS,
    _PHASE0_LEAGUE_SERIES_FILTER,
    _evaluate_a_arb_for_ml,
    _pick_pinnacle_or_proxy,
    _vig_remove_two_sides,
    classify_mlb_ticker,
    classify_nba_ticker,
)


class TestClassifyTicker:
    def test_in_scope_game_ml(self):
        assert classify_nba_ticker("KXNBAGAME-26MAY22OKCSAS-OKC") == ("in_scope", "game_ml")

    def test_out_of_scope_known_prefixes(self):
        for ticker in (
            "KXNBA1HSPREAD-26MAY22LALDEN-OVER",
            "KXNBASERIESROADWIN-26CLENYKECF-4",
            "KXNBADRAFTPICK-26-15-NAME",
            "KXNBAMVP-2026-LAJ",
            "KXNBAOVERTIME-26MAY22LALDEN",
        ):
            status, mt = classify_nba_ticker(ticker)
            assert status == "out_of_scope", f"expected out_of_scope for {ticker}"
            assert mt is None

    def test_unknown_prefix(self):
        # A made-up future Kalshi prefix we haven't catalogued
        status, mt = classify_nba_ticker("KXNBAQUARTERSPREAD-foo")
        assert status == "unknown"
        assert mt is None

    def test_empty_ticker(self):
        assert classify_nba_ticker("") == ("unknown", None)


class TestVigRemoveTwoSides:
    def test_standard_juice(self):
        # -110/-110 → raw 0.5238 each, total 1.0476, vig-removed both 0.5
        h, a = _vig_remove_two_sides(0.5238, 0.5238)
        assert h == pytest.approx(0.5, abs=1e-3)
        assert a == pytest.approx(0.5, abs=1e-3)

    def test_asymmetric(self):
        h, a = _vig_remove_two_sides(0.7, 0.35)
        # total 1.05 → vh 0.667, va 0.333
        assert h == pytest.approx(0.7 / 1.05)
        assert a == pytest.approx(0.35 / 1.05)

    def test_zero_total_returns_zeros(self):
        assert _vig_remove_two_sides(0.0, 0.0) == (0.0, 0.0)


class TestPickPinnacleOrProxy:
    def test_pinnacle_present_preferred(self):
        books = (
            BookPrice("pinnacle", "home", None, -110, 110 / 210),
            BookPrice("pinnacle", "away", None, +100, 100 / 200),
            BookPrice("draftkings", "home", None, -120, 120 / 220),
            BookPrice("draftkings", "away", None, +105, 100 / 205),
        )
        result = _pick_pinnacle_or_proxy(books)
        assert result is not None
        book_used, vh, va, is_pin = result
        assert book_used == "pinnacle"
        assert is_pin is True
        assert vh + va == pytest.approx(1.0, abs=1e-9)

    def test_proxy_fallback_when_no_pinnacle(self):
        books = (
            BookPrice("draftkings", "home", None, -110, 110 / 210),
            BookPrice("draftkings", "away", None, -110, 110 / 210),
            BookPrice("fanduel", "home", None, -115, 115 / 215),
            BookPrice("fanduel", "away", None, -105, 105 / 205),
        )
        result = _pick_pinnacle_or_proxy(books)
        assert result is not None
        book_used, vh, va, is_pin = result
        assert is_pin is False
        assert book_used.startswith("median:")
        assert "draftkings" in book_used and "fanduel" in book_used

    def test_pinnacle_partial_falls_through_to_proxy(self):
        # Pinnacle has only home side → can't vig-remove; fall back to proxy
        books = (
            BookPrice("pinnacle", "home", None, -110, 110 / 210),
            BookPrice("draftkings", "home", None, -110, 110 / 210),
            BookPrice("draftkings", "away", None, -110, 110 / 210),
        )
        result = _pick_pinnacle_or_proxy(books)
        assert result is not None
        _, _, _, is_pin = result
        assert is_pin is False

    def test_no_proxy_books_returns_none(self):
        # Some random book outside the proxy preference, and no Pinnacle
        books = (
            BookPrice("circa", "home", None, -110, 110 / 210),
            BookPrice("circa", "away", None, -110, 110 / 210),
        )
        result = _pick_pinnacle_or_proxy(books)
        assert result is None


class TestEvaluateAArbForML:
    def test_per_book_candidates_returned_sorted(self):
        # Kalshi YES = home (yes_is_home=True), so opposing book side = away.
        # Three books with various away prices.
        books = (
            BookPrice("draftkings", "home", None, -200, 200 / 300),
            BookPrice("draftkings", "away", None, +170, 100 / 270),
            BookPrice("fanduel", "home", None, -210, 210 / 310),
            BookPrice("fanduel", "away", None, +180, 100 / 280),
            BookPrice("betmgm", "home", None, -205, 205 / 305),
            BookPrice("betmgm", "away", None, +175, 100 / 275),
        )
        candidates = _evaluate_a_arb_for_ml(
            kalshi_yes_ask=0.50,        # Kalshi prices YES at 50% (heavy mismatch vs books)
            kalshi_no_ask=0.55,
            books_h2h=books,
            yes_is_home=True,
            qty=10,
        )
        assert len(candidates) == 3
        # Sorted descending by EV
        for i in range(len(candidates) - 1):
            assert candidates[i].ev_dollars >= candidates[i + 1].ev_dollars
        # All have opposing book side (away)
        for c in candidates:
            assert c.book_side == "away"

    def test_arb_positive_when_kalshi_underpriced(self):
        # Kalshi YES at $0.30; book away +120 (implied 100/220 = 45.5%)
        # Cost = 10*0.30 + fee + 10*0.4545
        #      = 3.00 + 0.15 + 4.545 = 7.695; payoff 10; EV +2.30 → arb
        books = (
            BookPrice("draftkings", "home", None, -150, 150 / 250),
            BookPrice("draftkings", "away", None, +120, 100 / 220),
        )
        candidates = _evaluate_a_arb_for_ml(
            kalshi_yes_ask=0.30,
            kalshi_no_ask=0.72,
            books_h2h=books,
            yes_is_home=True,
            qty=10,
        )
        assert len(candidates) == 1
        c = candidates[0]
        assert c.is_arb is True
        assert c.ev_dollars > 2.0

    def test_no_opposing_side_returns_empty(self):
        # Books only quote the home side → no opposing leg available
        books = (
            BookPrice("draftkings", "home", None, -150, 150 / 250),
        )
        candidates = _evaluate_a_arb_for_ml(
            kalshi_yes_ask=0.50,
            kalshi_no_ask=0.55,
            books_h2h=books,
            yes_is_home=True,
            qty=10,
        )
        assert candidates == []

    def test_yes_is_away_picks_home_as_opposing(self):
        # When Kalshi YES = away team, opposing book side is home.
        books = (
            BookPrice("draftkings", "home", None, -200, 200 / 300),
            BookPrice("draftkings", "away", None, +170, 100 / 270),
        )
        candidates = _evaluate_a_arb_for_ml(
            kalshi_yes_ask=0.45,
            kalshi_no_ask=0.60,
            books_h2h=books,
            yes_is_home=False,    # YES is away side
            qty=10,
        )
        assert len(candidates) == 1
        assert candidates[0].book_side == "home"


# ── MLB sibling tests — added 2026-05-23 alongside NBA tests ─────────────
# Constraint: must not modify the NBA test classes above. MLB classifier
# is its own function; the dispatch table is the only shared structure
# and gets its own test class below.

class TestClassifyMLBTicker:
    def test_in_scope_game_ml(self):
        # MLB game tickers include HHMM time (NBA does not)
        assert classify_mlb_ticker("KXMLBGAME-26MAY241610ATHSD-SD") == ("in_scope", "game_ml")
        assert classify_mlb_ticker("KXMLBGAME-26MAY241420HOUCHC-HOU") == ("in_scope", "game_ml")

    def test_out_of_scope_season_props(self):
        # Catalogued from 9-day scout corpus prefix audit (2026-05-23)
        for ticker in (
            "KXMLBWINS-26-NYY",            # season-win totals
            "KXMLBSTATCOUNT-26-OPS",        # generic stat counter
            "KXMLBRFI-26MAY24-OAKBOS",     # Run First Inning
            "KXMLBKS-26MAY24-COLE",        # pitcher strikeouts
            "KXMLBPLAYOFFS-26-NYY",
            "KXMLBPITCHEROTM-26MAY-DEG",
            "KXMLBLSTREAK-26-LAD",
            "KXMLBEOTY-26-MHAZ",
            "KXMLBNL-26-LAD", "KXMLBNLWEST-26-LAD", "KXMLBNLEAST-26-ATL",
            "KXMLBNLCENT-26-MIL", "KXMLBNLROTY-26-PSKE",
            "KXMLBNLMVP-26-AAC", "KXMLBNLHAARON-26-AAC",
            "KXMLBAL-26-NYY", "KXMLBALMVP-26-AAJ", "KXMLBALHAARON-26-AAJ",
            "KXMLBALRELOTY-26-RDUR", "KXMLBALCPOTY-26-RMAR",
        ):
            status, mt = classify_mlb_ticker(ticker)
            assert status == "out_of_scope", f"expected out_of_scope for {ticker}, got {status}"
            assert mt is None

    def test_unknown_prefix(self):
        # Future Kalshi MLB market type we haven't catalogued
        status, mt = classify_mlb_ticker("KXMLBNEWPROP-foo")
        assert status == "unknown"
        assert mt is None

    def test_empty_ticker(self):
        assert classify_mlb_ticker("") == ("unknown", None)

    def test_nba_ticker_NOT_classified_as_mlb_in_scope(self):
        # Cross-league safety: NBA tickers should NOT be in-scope under the MLB classifier.
        assert classify_mlb_ticker("KXNBAGAME-26MAY22OKCSAS-OKC") == ("unknown", None)


class TestLeagueDispatchTable:
    """The dispatch table is the only structure shared across NBA and
    MLB code paths. Verify both entries are present and well-formed."""

    def test_nba_and_mlb_registered(self):
        assert "NBA" in _PHASE0_LEAGUE_CLASSIFIERS
        assert "MLB" in _PHASE0_LEAGUE_CLASSIFIERS

    def test_nba_dispatch_points_to_nba_classifier_and_prefix(self):
        classifier, kx_prefix = _PHASE0_LEAGUE_CLASSIFIERS["NBA"]
        assert classifier is classify_nba_ticker
        assert kx_prefix == "KXNBA"

    def test_mlb_dispatch_points_to_mlb_classifier_and_prefix(self):
        classifier, kx_prefix = _PHASE0_LEAGUE_CLASSIFIERS["MLB"]
        assert classifier is classify_mlb_ticker
        assert kx_prefix == "KXMLB"

    def test_nba_and_mlb_dispatch_are_distinct(self):
        # The two classifiers MUST be different functions — otherwise
        # 'generalize by addition, not mutation' was violated.
        nba_classifier, _ = _PHASE0_LEAGUE_CLASSIFIERS["NBA"]
        mlb_classifier, _ = _PHASE0_LEAGUE_CLASSIFIERS["MLB"]
        assert nba_classifier is not mlb_classifier


class TestSeriesFilterDispatch:
    """Verifies the series_filter mechanism that prevents the
    rotating-slice discovery bug (Kalshi Sports has ~2000 series; the
    discovery cap returns a 50-series rotating slice without a filter).
    Sibling of dispatch table; existing dispatch tests untouched.
    """

    def test_nba_filter_includes_KXNBAGAME(self):
        assert "NBA" in _PHASE0_LEAGUE_SERIES_FILTER
        assert "KXNBAGAME" in _PHASE0_LEAGUE_SERIES_FILTER["NBA"]

    def test_mlb_filter_includes_KXMLBGAME(self):
        assert "MLB" in _PHASE0_LEAGUE_SERIES_FILTER
        assert "KXMLBGAME" in _PHASE0_LEAGUE_SERIES_FILTER["MLB"]

    def test_nba_and_mlb_filters_are_disjoint(self):
        # Sanity: NBA filter should not pick up MLB series and vice versa.
        nba_set = set(_PHASE0_LEAGUE_SERIES_FILTER["NBA"])
        mlb_set = set(_PHASE0_LEAGUE_SERIES_FILTER["MLB"])
        assert nba_set.isdisjoint(mlb_set), (
            f"NBA and MLB filters overlap: {nba_set & mlb_set}"
        )

    def test_filter_entries_match_in_scope_ticker_prefixes(self):
        # The filter values MUST match the classifier's in-scope prefix
        # set exactly. If they diverge, discovery would either pre-filter
        # to nothing (no in-scope tickers ever found) or include series
        # the classifier rejects (silent waste).
        from trading_corp.agents.strategies.kalshi_sports_arb_observer import (
            _PHASE0_NBA_TICKER_PREFIXES,
            _PHASE0_MLB_TICKER_PREFIXES,
        )
        assert set(_PHASE0_LEAGUE_SERIES_FILTER["NBA"]) == set(_PHASE0_NBA_TICKER_PREFIXES.keys())
        assert set(_PHASE0_LEAGUE_SERIES_FILTER["MLB"]) == set(_PHASE0_MLB_TICKER_PREFIXES.keys())
