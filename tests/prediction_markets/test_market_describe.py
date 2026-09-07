"""Plain-language market descriptions (interim item a, 2026-09-01). Pure -- no DB, no fixtures. Asserts the
formatter turns REAL live tickers (from tonight's journal) into human sentences, keys the side off the held leg,
and falls back HONESTLY (market type + raw ticker) for anything it cannot confidently parse -- never a fabricated
matchup. Runnable standalone (`python test_market_describe.py`) as well as under pytest."""
from trading_corp.prediction_markets.market_describe import describe_market, _split_two_team_codes


def test_moneyline_names_teams_and_side():
    # KXMLBGAME-26AUG301920CINCHC-CHC : YES = Cubs, other = Reds
    d = describe_market("KXMLBGAME-26AUG301920CINCHC-CHC")
    assert "Chicago Cubs" in d and "Cincinnati Reds" in d
    assert "Chicago Cubs to win" in d          # YES side named by the suffix
    assert "Aug 30" in d and "7:20pm ET" in d
    # holding the NO leg backs the OTHER team
    dno = describe_market("KXMLBGAME-26AUG301920CINCHC-CHC", leg="no")
    assert "Cincinnati Reds to win" in dno


def test_moneyline_doubleheader_tag():
    d = describe_market("KXMLBGAME-26AUG311840STLCING2-STL", leg="yes")
    assert "G2" in d


def test_total_over_under_and_strike():
    # KXMLBTOTAL-...-9 -> strike 8.5, YES=Over
    over = describe_market("KXMLBTOTAL-26AUG311940DETMIN-9", leg="yes")
    assert "Over 8.5 runs" in over
    assert "Detroit Tigers" in over and "Minnesota Twins" in over
    under = describe_market("KXMLBTOTAL-26AUG311940DETMIN-9", leg="no")
    assert "Under 8.5 runs" in under
    none = describe_market("KXMLBTOTAL-26AUG311940DETMIN-9")
    assert "8.5" in none and "Over" in none


def test_spread_anchor_and_line():
    # KXMLBSPREAD-...SDCIN-SD2 -> SD anchor, strike 1.5
    yes = describe_market("KXMLBSPREAD-26AUG311840SDCIN-SD2", leg="yes")
    assert "San Diego Padres -1.5" in yes
    assert "Cincinnati Reds" in yes
    no = describe_market("KXMLBSPREAD-26AUG311840SDCIN-SD2", leg="no")
    assert "Cincinnati Reds +1.5" in no


def test_honest_fallback_non_mlb_and_unparseable():
    # a non-MLB / unknown series -> market_type + raw ticker, never a fabricated matchup
    d = describe_market("KXNFLGAME-26SEP01FOO-BAR")
    assert "KXNFLGAME-26SEP01FOO-BAR" in d
    # a GAME ticker whose 'teams' aren't clubs -> parser returns None -> fallback contains the raw ticker
    d2 = describe_market("KXMLBGAME-26AUG31ALNL-AL")
    assert "KXMLBGAME-26AUG31ALNL-AL" in d2
    # empty
    assert describe_market("") == "-"


def test_ambiguous_blob_falls_back_not_guesses():
    # a blob that does not split uniquely into two known codes -> None (honest), so a TOTAL with such a stem
    # still renders the strike/side but WITHOUT a fabricated matchup.
    assert _split_two_team_codes("ZZZZZZ") is None
    d = describe_market("KXMLBTOTAL-26AUG311940ZZZZZZ-9", leg="yes")
    assert "Over 8.5 runs" in d and "vs" not in d


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print("ALL %d PASS" % len(fns))
