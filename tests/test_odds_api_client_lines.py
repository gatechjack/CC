"""Tests for the per-book Phase-0 extension to OddsAPIClient.

Focus: the `_parse_lines` bucketing logic — h2h, spreads (sign
normalization), totals (over/under), and multi-line handling. No
network calls; we construct synthetic the-odds-api response dicts.
"""
from __future__ import annotations

from trading_corp.data.odds_api_client import (
    BookPrice,
    GameLine,
    OddsAPIClient,
)


def _make_client() -> OddsAPIClient:
    # No API key needed for parser tests; we never reach the wire.
    return OddsAPIClient(api_key="test-key")


def _h2h_outcome(name: str, price: int) -> dict:
    return {"name": name, "price": price}


def _spread_outcome(name: str, price: int, point: float) -> dict:
    return {"name": name, "price": price, "point": point}


def _total_outcome(name: str, price: int, point: float) -> dict:
    return {"name": name, "price": price, "point": point}


def _game(home: str, away: str, bookmakers: list[dict]) -> dict:
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": "2026-05-24T00:00:00Z",
        "bookmakers": bookmakers,
    }


class TestH2HParsing:
    def test_two_books_two_sides(self):
        raw = _game(
            home="Oklahoma City Thunder",
            away="San Antonio Spurs",
            bookmakers=[
                {
                    "key": "draftkings",
                    "markets": [{
                        "key": "h2h",
                        "outcomes": [
                            _h2h_outcome("Oklahoma City Thunder", -200),
                            _h2h_outcome("San Antonio Spurs", +170),
                        ],
                    }],
                },
                {
                    "key": "fanduel",
                    "markets": [{
                        "key": "h2h",
                        "outcomes": [
                            _h2h_outcome("Oklahoma City Thunder", -210),
                            _h2h_outcome("San Antonio Spurs", +175),
                        ],
                    }],
                },
            ],
        )
        client = _make_client()
        lines = client._parse_lines("basketball_nba", raw)
        # One GameLine for h2h (line=None)
        assert len(lines) == 1
        gl = lines[0]
        assert gl.market == "h2h"
        assert gl.line is None
        assert len(gl.books) == 4   # 2 books × 2 sides
        # Side normalization
        home_prices = [b for b in gl.books if b.side == "home"]
        away_prices = [b for b in gl.books if b.side == "away"]
        assert len(home_prices) == 2
        assert len(away_prices) == 2
        assert {b.book_key for b in home_prices} == {"draftkings", "fanduel"}
        # Implied raw checks
        for b in home_prices:
            assert 0.0 < b.implied_raw < 1.0


class TestSpreadParsing:
    def test_signed_lines_same_bucket(self):
        # Home -5.5 / Away +5.5 → both should land in one GameLine bucket
        raw = _game(
            home="OKC",
            away="SAS",
            bookmakers=[{
                "key": "draftkings",
                "markets": [{
                    "key": "spreads",
                    "outcomes": [
                        _spread_outcome("OKC", -110, -5.5),
                        _spread_outcome("SAS", -110, +5.5),
                    ],
                }],
            }],
        )
        client = _make_client()
        lines = client._parse_lines("basketball_nba", raw)
        assert len(lines) == 1
        gl = lines[0]
        assert gl.market == "spreads"
        assert gl.line == 5.5    # bucketing uses abs()
        assert len(gl.books) == 2
        home_p = next(b for b in gl.books if b.side == "home")
        away_p = next(b for b in gl.books if b.side == "away")
        assert home_p.line == -5.5    # raw signed line preserved on BookPrice
        assert away_p.line == +5.5

    def test_different_lines_different_buckets(self):
        # DK offers -5.5, FD offers -6 → TWO GameLine entries
        raw = _game(
            home="OKC",
            away="SAS",
            bookmakers=[
                {
                    "key": "draftkings",
                    "markets": [{
                        "key": "spreads",
                        "outcomes": [
                            _spread_outcome("OKC", -110, -5.5),
                            _spread_outcome("SAS", -110, +5.5),
                        ],
                    }],
                },
                {
                    "key": "fanduel",
                    "markets": [{
                        "key": "spreads",
                        "outcomes": [
                            _spread_outcome("OKC", -115, -6.0),
                            _spread_outcome("SAS", -105, +6.0),
                        ],
                    }],
                },
            ],
        )
        client = _make_client()
        lines = client._parse_lines("basketball_nba", raw)
        spread_lines = [l for l in lines if l.market == "spreads"]
        assert len(spread_lines) == 2
        line_values = sorted([l.line for l in spread_lines])
        assert line_values == [5.5, 6.0]
        # Each bucket has 2 sides
        for gl in spread_lines:
            assert len(gl.books) == 2


class TestTotalParsing:
    def test_over_under_same_bucket(self):
        raw = _game(
            home="OKC",
            away="SAS",
            bookmakers=[{
                "key": "draftkings",
                "markets": [{
                    "key": "totals",
                    "outcomes": [
                        _total_outcome("Over", -110, 224.5),
                        _total_outcome("Under", -110, 224.5),
                    ],
                }],
            }],
        )
        client = _make_client()
        lines = client._parse_lines("basketball_nba", raw)
        totals = [l for l in lines if l.market == "totals"]
        assert len(totals) == 1
        gl = totals[0]
        assert gl.line == 224.5
        sides = {b.side for b in gl.books}
        assert sides == {"over", "under"}


class TestMixedMarkets:
    def test_one_game_three_market_types(self):
        raw = _game(
            home="OKC",
            away="SAS",
            bookmakers=[{
                "key": "draftkings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            _h2h_outcome("OKC", -200),
                            _h2h_outcome("SAS", +170),
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            _spread_outcome("OKC", -110, -5.5),
                            _spread_outcome("SAS", -110, +5.5),
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            _total_outcome("Over", -110, 224.5),
                            _total_outcome("Under", -110, 224.5),
                        ],
                    },
                ],
            }],
        )
        client = _make_client()
        lines = client._parse_lines("basketball_nba", raw)
        markets_returned = sorted({l.market for l in lines})
        assert markets_returned == ["h2h", "spreads", "totals"]


class TestBadDataResilience:
    def test_skips_missing_price(self):
        raw = _game(
            home="OKC", away="SAS",
            bookmakers=[{
                "key": "draftkings",
                "markets": [{
                    "key": "h2h",
                    "outcomes": [
                        {"name": "OKC", "price": None},
                        _h2h_outcome("SAS", +170),
                    ],
                }],
            }],
        )
        client = _make_client()
        lines = client._parse_lines("basketball_nba", raw)
        # One book on one side (the valid one) — still emits a GameLine
        assert len(lines) == 1
        assert len(lines[0].books) == 1
        assert lines[0].books[0].side == "away"

    def test_empty_bookmakers_returns_empty(self):
        raw = _game(home="OKC", away="SAS", bookmakers=[])
        client = _make_client()
        assert client._parse_lines("basketball_nba", raw) == []

    def test_missing_team_returns_empty(self):
        raw = {"home_team": "", "away_team": "SAS", "commence_time": "x", "bookmakers": []}
        client = _make_client()
        assert client._parse_lines("basketball_nba", raw) == []
