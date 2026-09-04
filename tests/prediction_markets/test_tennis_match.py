"""Unit tests for tennis_poly_kalshi_match -- pair-keyed atp/wta match-winner matcher.

Real names/tickers (probed 2026-09-03, 2026 US Open). The design is driven by the postponement
finding (~10% of matches differ +/-1 day between venues), so the tests exercise the pair-keying,
the +/-1 window, the safe surname recovery, and the wrong-pick guards -- and the table-tennis
exclusion is a DEDICATED test, not an allowlist line.
"""
import pytest
from trading_corp.data import tennis_poly_kalshi_match as T

# Real KXATPMATCH markets (2026 US Open). Two YES markets share a (date, blob) = one match.
KHABON = [
    {"ticker": "KXATPMATCH-26SEP05KHABON-KHA", "title": "Karen Khachanov wins"},
    {"ticker": "KXATPMATCH-26SEP05KHABON-BON", "title": "Benjamin Bonzi wins"},
]
FRICER = [
    {"ticker": "KXATPMATCH-26SEP05FRICER-FRI", "title": "Taylor Fritz wins"},
    {"ticker": "KXATPMATCH-26SEP05FRICER-CER", "title": "Francisco Cerundolo wins"},
]
# a SECOND Cerundolo (brother) in a different match on the same date
CERGAL = [
    {"ticker": "KXATPMATCH-26SEP05CERGAL-CER", "title": "Juan Manuel Cerundolo wins"},
    {"ticker": "KXATPMATCH-26SEP05CERGAL-GAL", "title": "Daniel Galan wins"},
]
DATE = "2026-09-05"


def _idx(*groups):
    mk = [m for g in groups for m in g]
    return T.build_kalshi_match_index(mk)


def _match(slug, outcome, title=None, index=None, dates=None):
    index = index if index is not None else _idx(KHABON, FRICER)
    dates = dates if dates is not None else frozenset({DATE})
    return T.match_bet(T.parse_poly_tennis_bet(slug, outcome, title), index, dates)


class TestIndex:
    def test_two_sided_match_indexed(self):
        idx = _idx(KHABON)
        assert len(idx[DATE]) == 1

    def test_one_sided_blob_skipped(self):
        idx = T.build_kalshi_match_index([KHABON[0]])   # only one YES side
        assert idx == {}

    def test_title_without_wins_skipped(self):
        idx = T.build_kalshi_match_index([{"ticker": "KXATPMATCH-26SEP05KHABON-KHA", "title": "Karen Khachanov"},
                                          KHABON[1]])
        assert idx == {}


class TestTableTennisExclusion:
    """★ DEDICATED: no table-tennis / non-ATP-WTA-MATCH ticker can EVER enter the index (anchored regex)."""
    def test_table_tennis_series_unreachable(self):
        idx = T.build_kalshi_match_index([
            {"ticker": "KXTTMATCH-26SEP05ABCDEF-ABC", "title": "Some Player wins"},
            {"ticker": "KXTTMATCH-26SEP05ABCDEF-DEF", "title": "Other Player wins"},
            {"ticker": "KXITTFMENMATCH-26SEP05ABCDEF-ABC", "title": "Third Player wins"},
            {"ticker": "KXITTFWOMENMATCH-26SEP05ABCDEF-ABC", "title": "Fourth Player wins"},
            {"ticker": "KXWTTMATCH-26SEP05ABCDEF-ABC", "title": "Fifth Player wins"},
        ])
        assert idx == {}

    def test_itf_and_futures_not_match_series(self):
        # ITF (a deferred category, its own series) and futures must NOT enter the atp/wta match index
        idx = T.build_kalshi_match_index([
            {"ticker": "KXITFMATCH-26SEP04BERBAR-BER", "title": "Lorenzo Berto wins"},
            {"ticker": "KXATP-26USO-MIC", "title": "Will Alex Michelsen win the US Open Men's Singles?"},
        ])
        assert idx == {}


class TestPairKeyedMatch:
    def test_full_name_pair_matched(self):
        _, = [None]  # noqa
        r = _match("atp-kha-bon-2026-09-05", "Karen Khachanov", "US Open ATP: Karen Khachanov vs Benjamin Bonzi")
        assert r.status == "matched" and r.kalshi_ticker == "KXATPMATCH-26SEP05KHABON-KHA" and r.leg == "yes"

    def test_accent_and_folding_via_reused_norm(self):
        r = _match("atp-cer-fri-2026-09-05", "Francisco Cerundolo", "US Open ATP: Taylor Fritz vs Francisco Cerundolo")
        assert r.status == "matched" and r.kalshi_ticker == "KXATPMATCH-26SEP05FRICER-CER"


class TestWindow:
    def test_plus_minus_one_day(self):
        # Poly says 09-04, Kalshi lists 09-05 -> matched via the +/-1 window
        r = _match("atp-kha-bon-2026-09-04", "Karen Khachanov", "US Open ATP: Karen Khachanov vs Benjamin Bonzi",
                   dates=frozenset({"2026-09-04"}))
        assert r.status == "matched" and r.kalshi_ticker == "KXATPMATCH-26SEP05KHABON-KHA"

    def test_beyond_window_is_miss(self):
        # 3 days off -> out of window (safe miss, never a wrong pick)
        r = _match("atp-kha-bon-2026-09-08", "Karen Khachanov", "US Open ATP: Karen Khachanov vs Benjamin Bonzi",
                   dates=frozenset({"2026-09-08"}))
        assert r.status != "matched"


class TestSurnameRecoveryAndBrothers:
    def test_surname_only_recovered_via_title_opponent(self):
        # bare "Cerundolo" + title (opponent Fritz) -> the PAIR identifies the match -> Francisco's ticker
        r = _match("atp-cer-fri-2026-09-05", "Cerundolo", "US Open ATP: Taylor Fritz vs Francisco Cerundolo")
        assert r.status == "matched" and r.kalshi_ticker == "KXATPMATCH-26SEP05FRICER-CER"

    def test_surname_only_without_title_is_safe_miss(self):
        # both Cerundolo brothers in the draw; bare surname + NO title -> must NOT match (safe miss)
        r = _match("atp-cer-x-2026-09-05", "Cerundolo", None, index=_idx(FRICER, CERGAL))
        assert r.status != "matched"

    def test_same_surname_both_sides_would_be_ambiguous(self):
        # contrived: a match of two same-surname players; bare surname cannot pick a side -> not matched
        both = [{"ticker": "KXATPMATCH-26SEP05CERCER-AAA", "title": "Francisco Cerundolo wins"},
                {"ticker": "KXATPMATCH-26SEP05CERCER-BBB", "title": "Juan Manuel Cerundolo wins"}]
        r = _match("atp-cer-cer-2026-09-05", "Cerundolo", "US Open ATP: Francisco Cerundolo vs Juan Manuel Cerundolo",
                   index=T.build_kalshi_match_index(both))
        assert r.status != "matched"


class TestWrongPickGuards:
    def test_different_surname_never_matches(self):
        r = _match("atp-x-y-2026-09-05", "Andrey Rublev", "US Open ATP: Andrey Rublev vs Someone Else")
        assert r.status != "matched"

    def test_pair_matches_two_windowed_matches_is_ambiguous(self):
        # same player-pair listed on two adjacent days (rematch/relist) -> ambiguous, not a guess
        d1 = [{"ticker": "KXATPMATCH-26SEP04KHABON-KHA", "title": "Karen Khachanov wins"},
              {"ticker": "KXATPMATCH-26SEP04KHABON-BON", "title": "Benjamin Bonzi wins"}]
        idx = _idx(KHABON, d1)   # KHABON on 09-05 AND 09-04
        r = _match("atp-kha-bon-2026-09-05", "Karen Khachanov", "US Open ATP: Karen Khachanov vs Benjamin Bonzi",
                   index=idx, dates=frozenset({"2026-09-05"}))
        assert r.status == "abbrev_collision_ambiguous"


class TestScope:
    def test_non_tennis_slug_skipped(self):
        r = _match("mlb-nyy-bos-2026-09-05", "New York Yankees", "MLB: NYY vs BOS")
        assert r.status == "skip_non_tennis"

    def test_prop_suffix_skipped(self):
        r = _match("atp-kha-bon-2026-09-05-total-games", "Over", "US Open ATP: total games")
        assert r.status == "skip_prop"


class TestDispatchAdapter:
    def test_registered_in_matcher_adapters(self):
        from trading_corp.prediction_markets import execution
        assert "atp" in execution.MATCHER_ADAPTERS and "wta" in execution.MATCHER_ADAPTERS
        parse, match = execution.MATCHER_ADAPTERS["atp"]
        ctx = execution.MarketContext({}, {}, {}, frozenset({DATE}), {}, match_index=_idx(KHABON))
        parsed = parse("atp-kha-bon-2026-09-05", "Karen Khachanov", "US Open ATP: Karen Khachanov vs Benjamin Bonzi")
        r = match(parsed, ctx, ("moneyline",))
        assert r.status == "matched" and r.kalshi_ticker == "KXATPMATCH-26SEP05KHABON-KHA"
