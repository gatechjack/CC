"""CP5 tests for the Kalshi-matchable gate applied by the reseed job so
refresh_polymarket_whales can never drag esports/mixed whales back."""
from __future__ import annotations

from types import SimpleNamespace

from trading_corp.data.kalshi_matchable import (
    MATCHABLE_CATEGORIES, classify, classify_dominant, is_kalshi_matchable,
)


def _row(title, event_slug=""):
    return SimpleNamespace(title=title, event_slug=event_slug)


def test_launch_matchable_set_is_mlb_only():
    assert MATCHABLE_CATEGORIES == frozenset({"mlb"})


def test_classify_buckets():
    assert classify("New York Yankees vs. Toronto Blue Jays", "mlb-nyy-tor-2026-08-16") == "mlb"
    assert classify("LOL: LCK Sentinels vs Gen.G", "lol-ly-sen-2026-08-16") == "esports_series"
    assert classify("Will the Democrats win the Senate?", "us-senate-2026") == "politics"
    assert classify("Boston Celtics vs. Miami Heat", "nba-bos-mia-2026-08-16") == "nba"


def test_dominant_and_matchable():
    mlb_whale = [_row("Rays vs Sox", "mlb-tb-bos-2026-08-16")] * 8 + [_row("LOL worlds", "lol-x")]
    esports_whale = [_row("LOL: LCK", "lol-a-b")] * 6 + [_row("Rays vs Sox", "mlb-tb-bos-2026-08-16")]
    assert classify_dominant(mlb_whale) == "mlb"
    assert is_kalshi_matchable(mlb_whale) is True
    assert classify_dominant(esports_whale).startswith("esports")
    assert is_kalshi_matchable(esports_whale) is False
    assert is_kalshi_matchable([]) is False           # unclassifiable -> not matchable (safe)


def test_reseed_gate_keeps_only_matchable():
    # simulate the reseed finalists as (wallet, activity_rows); apply the gate.
    candidates = {
        "0xMLB1": [_row("Yankees vs Jays", "mlb-nyy-tor-2026-08-16")] * 10,
        "0xMLB2": [_row("Rays vs Sox", "mlb-tb-bos-2026-08-16")] * 10,
        "0xESPORTS": [_row("LOL: LCK finals", "lol-a-b-2026-08-16")] * 10,
        "0xPOLITICS": [_row("Senate control 2026", "us-senate-2026")] * 10,
    }
    kept = [w for w, rows in candidates.items() if is_kalshi_matchable(rows)]
    assert kept == ["0xMLB1", "0xMLB2"]               # esports + politics dropped
