"""Rung 4 (fed) matcher: event+bucket (FOMC rate decision). Centerpieces: the phrasing->bucket
transform (validated by the explicit bps+direction), the COARSE-hike GATE ("25+"/no-magnitude span
two buckets -> never placed), political/parlay exclusion, and the index self-validation (a Kalshi
bucket-code that disagrees with its own yes_sub_title is skipped -- the PSG independent-evidence lesson)."""
import pytest
from trading_corp.data import fed_poly_kalshi_match as FED

_MK = [{"ticker": "KXFEDDECISION-26SEP-H26", "yes_sub_title": "Hike >25bps"},
       {"ticker": "KXFEDDECISION-26SEP-H25", "yes_sub_title": "Hike 25bps"},
       {"ticker": "KXFEDDECISION-26SEP-H0",  "yes_sub_title": "Fed maintains rate"},
       {"ticker": "KXFEDDECISION-26SEP-C25", "yes_sub_title": "Cut 25bps"},
       {"ticker": "KXFEDDECISION-26SEP-C26", "yes_sub_title": "Cut >25bps"}]
_IDX = FED.build_bucket_index(_MK)


def _run(outcome, title, idx=_IDX):
    return FED.match_bet(FED.parse_poly_bet("fed-x", outcome, title), idx, set(idx))


@pytest.mark.parametrize("title,bucket", [
    ("Will there be no change in Fed interest rates after the September 2026 meeting?", "H0"),
    ("Will the Fed increase interest rates by 0 bps after the September 2026 meeting?", "H0"),
    ("Will the Fed increase interest rates by 25 bps after the September 2026 meeting?", "H25"),
    ("Will the Fed increase interest rates by 50 bps after the September 2026 meeting?", "H26"),
    ("Fed decreases interest rates by 25 bps after September 2026 meeting?", "C25"),
    ("Fed decreases interest rates by 50 bps after September 2026 meeting?", "C26"),
    ("Fed decreases interest rates by 50+ bps after September 2026 meeting?", "C26"),
    ("Fed decreases interest rates by 75+ bps after September 2026 meeting?", "C26"),
])
def test_bucket_mapping(title, bucket):
    r = _run("Yes", title)
    assert r.status == "matched" and r.kalshi_ticker == "KXFEDDECISION-26SEP-%s" % bucket and r.leg == "yes", r


def test_leg_yes_no():
    assert _run("Yes", "Fed decreases interest rates by 25 bps after September 2026 meeting?").leg == "yes"
    assert _run("No", "Fed decreases interest rates by 25 bps after September 2026 meeting?").leg == "no"


# ── the coarse gate: spans two buckets -> NEVER placed ───────────────────────────────────────
@pytest.mark.parametrize("title", [
    "Fed increases interest rates by 25+ bps after September 2026 meeting?",   # spans H25 + H26
    "Fed decreases interest rates by 25+ bps after September 2026 meeting?",   # spans C25 + C26
    "Fed Rate Hike by September 2026 Meeting?",                                # no magnitude
    "Will the Fed cut interest rates after the September 2026 meeting?",       # no magnitude
    "Will Fed cut interest rates 3 times in 2026?",                           # count market
])
def test_coarse_gated(title):
    p = FED.parse_poly_bet("fed-x", "Yes", title)
    assert p.market_type == "coarse", (title, p)
    r = _run("Yes", title)
    assert r.status == "skip_coarse" and r.kalshi_ticker is None, r


# ── political / personnel excluded ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("title", [
    "Will Trump nominate Kevin Warsh as the next Fed chair?",
    "Will Jerome Powell resign as Fed chair?",
    "Will Lisa Cook vote at the next FOMC meeting?",
    "Will one person dissent the January 2026 Fed decision?",
])
def test_political_excluded(title):
    p = FED.parse_poly_bet("fed-x", "Yes", title)
    assert p.market_type == "political", (title, p)
    assert _run("Yes", title).status == "skip_political"


# ── meeting parse ────────────────────────────────────────────────────────────────────────────
def test_meeting_parse_variants():
    assert FED.parse_poly_bet("fed-x", "Yes", "No change in Fed interest rates after 2024 November meeting?").meeting == "24NOV"
    # a bucket bet with NO year -> no meeting -> safe fail (never a wrong match)
    p = FED.parse_poly_bet("fed-x", "Yes", "Will the Fed increase interest rates by 25 bps after its March meeting?")
    assert p.market_type == "bucket" and p.meeting is None
    assert FED.match_bet(p, _IDX, set(_IDX)).status == "fail"


# ── index self-validation: a code that disagrees with its own yes_sub_title is SKIPPED ────────
def test_index_skips_code_label_mismatch():
    bad = [{"ticker": "KXFEDDECISION-26SEP-C25", "yes_sub_title": "Hike 25bps"}]   # code C25 but label says Hike
    idx = FED.build_bucket_index(bad)
    assert idx == {}, idx   # mismatch -> not trusted, not indexed
    # and a matching one IS indexed
    good = [{"ticker": "KXFEDDECISION-26SEP-C25", "yes_sub_title": "Cut 25bps"}]
    assert FED.build_bucket_index(good) == {"26SEP": {"C25": "KXFEDDECISION-26SEP-C25"}}


# ── non-fed + fail-safe + market-type gate ───────────────────────────────────────────────────
def test_non_fed_and_failsafe():
    p = FED.parse_poly_bet("nba-lal-bos-2026-09-05", "Yes", "Will the Lakers win?")
    assert p.market_type == "non_fed"
    assert FED.match_bet(p, _IDX, set(_IDX)).status == "skip_non_fed"
    # out-of-window meeting Kalshi doesn't list
    r = _run("Yes", "Fed decreases interest rates by 25 bps after January 2020 meeting?")
    assert r.status == "out_of_window" and r.kalshi_ticker is None, r


def test_market_type_excluded():
    p = FED.parse_poly_bet("fed-x", "Yes", "Fed decreases interest rates by 25 bps after September 2026 meeting?")
    assert FED.match_bet(p, _IDX, set(_IDX), allowed_market_types=()).status == "skip_market_type_excluded"
