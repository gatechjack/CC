"""B1 (2026-11-18): the F1 race index must key on the RACE date, not close_time-first.

THE BUG (found read-only 2026-11-18, pre-Las-Vegas): KXF1RACE-LASV26 lists close_time 2026-11-29T10:00Z --
a +7d SETTLEMENT BUFFER -- while the race itself is 2026-11-22 (expected_expiration_time ==
occurrence_datetime == 2026-11-22T10:00Z, probed live from the public /markets endpoint). The old rule
(`close_time or expected_expiration_time`) keyed the race index on 2026-11-29, so a Poly Vegas winner slug
(dated 11-21/11-22, +/-1-day window -> 11-20..11-23) could NEVER join -- out_of_window on every cycle.

THE FIX: live_driver._f1_race_date_iso = the EARLIEST of expected_expiration_time / occurrence_datetime /
close_time (ISO date part). On the settled shape (close == race end, the shape the join was originally
verified on) min() reduces to the old behaviour exactly -- the regression test below pins that.
"""
from types import SimpleNamespace as NS

from trading_corp.data import f1_poly_kalshi_match as F1X
from trading_corp.prediction_markets import live_driver as LD

RACE = "2026-11-22"
CLOSE = "2026-11-29T10:00:00Z"            # the LIVE LASV26 close_time (race + 7d), probed 2026-11-18
EXP = "2026-11-22T10:00:00Z"              # == occurrence_datetime == the race date


def _lasv_market(code, name):
    return NS(ticker="KXF1RACE-LASV26-%s" % code, yes_sub_title=name,
              close_time=CLOSE, expected_expiration_time=EXP, occurrence_datetime=EXP)


def test_race_date_lasv26_shape_close_is_race_plus_7d():
    m = _lasv_market("VER", "Max Verstappen")
    assert LD._f1_race_date_iso(m) == RACE
    # the old close_time-first rule would have said 2026-11-29 -- the exact regression this fix removes
    assert str(m.close_time)[:10] == "2026-11-29"


def test_race_date_settled_shape_regression():
    # settled shape (ITAGP26 style): close_time == the race end -> byte-identical to the old behaviour
    m = NS(ticker="KXF1RACE-ITAGP26-GAS", yes_sub_title="Pierre Gasly",
           close_time="2026-09-06T18:00:00Z", expected_expiration_time="2026-09-06T18:00:00Z",
           occurrence_datetime=None)
    assert LD._f1_race_date_iso(m) == "2026-09-06"
    # SDK-sparse object: ONLY close_time present (occurrence_datetime is SDK-dropped on some models)
    m2 = NS(ticker="KXF1RACE-ITAGP26-VER", yes_sub_title="Max Verstappen",
            close_time="2026-09-06T18:00:00Z", expected_expiration_time=None, occurrence_datetime=None)
    assert LD._f1_race_date_iso(m2) == "2026-09-06"
    # no date fields at all -> None (build_kalshi_f1_index then skips the market, its documented contract)
    assert LD._f1_race_date_iso(NS(close_time=None, expected_expiration_time=None,
                                   occurrence_datetime=None)) is None


def test_lasv26_index_keys_on_race_date_and_vegas_slug_joins():
    # a LASV26-shaped field (close = race + 7d on EVERY driver) must index on the RACE date...
    field = [_lasv_market("VER", "Max Verstappen"), _lasv_market("NOR", "Lando Norris"),
             _lasv_market("PIA", "Oscar Piastri")]
    mkts = [{"ticker": m.ticker, "yes_sub_title": m.yes_sub_title,
             "close_date_iso": LD._f1_race_date_iso(m)} for m in field]
    idx = F1X.build_kalshi_f1_index(mkts)
    assert set(idx.keys()) == {RACE}
    assert idx[RACE].event_ticker == "KXF1RACE-LASV26" and len(idx[RACE].drivers) == 3
    # ...and a Vegas winner slug dated 11-21 AND 11-22 now joins it (+/-1-day window, UTC boundary)
    for slug_date in ("2026-11-21", "2026-11-22"):
        parsed = F1X.parse_poly_f1_bet(
            "f1-las-vegas-grand-prix-winner-verstappen-%s" % slug_date, "Yes",
            "Will Max Verstappen win the 2026 F1 Las Vegas Grand Prix?")
        res = F1X.match_bet(parsed, idx, frozenset(idx.keys()), allowed_market_types=("race_winner",))
        assert res.status == "matched", (slug_date, res.status, res.reason)
        assert res.kalshi_ticker == "KXF1RACE-LASV26-VER" and res.leg == "yes"


def test_close_time_first_would_have_missed_counterfactual():
    # COUNTERFACTUAL pin: index the same field by the close_time date (the OLD rule) -> the Vegas slug
    # misses out_of_window. This is the live failure the fix removes; if it ever passes, the window or
    # the index grew lenient enough that the fix is moot -- re-examine either way.
    mkts = [{"ticker": "KXF1RACE-LASV26-VER", "yes_sub_title": "Max Verstappen",
             "close_date_iso": "2026-11-29"}]
    idx = F1X.build_kalshi_f1_index(mkts)
    parsed = F1X.parse_poly_f1_bet("f1-las-vegas-grand-prix-winner-verstappen-2026-11-22", "Yes",
                                   "Will Max Verstappen win the 2026 F1 Las Vegas Grand Prix?")
    res = F1X.match_bet(parsed, idx, frozenset(idx.keys()), allowed_market_types=("race_winner",))
    assert res.status == "out_of_window", (res.status, res.reason)
