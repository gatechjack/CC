"""Item 1 (2026-09-12): the LIVE tile's event block is bounded to ONE FEATURED game (closest to settling) + <=3
one-line chips (other underway games) + '+N more live', so the 2x2 tile keeps a FIXED height regardless of game
count. These prove the DETERMINISTIC featured pick (baseball: latest inning, then most outs, then most held, then
away code A->Z) and the bounded structure -- the CSS then caps + clips the block."""
import types
import pytest

from trading_corp.prediction_markets.web import live_view as LV

NOW = 1789200000


def _gs(away, ascore, home, hscore, inning, outs):
    A = types.SimpleNamespace(abbr=away, name=away, record=None, score=ascore)
    H = types.SimpleNamespace(abbr=home, name=home, record=None, score=hscore)
    return types.SimpleNamespace(is_live=True, is_final=False, inning=inning, half="Bot", outs=outs,
                                 balls=1, strikes=2, bases=[], last_play=None, linescore_away=[], linescore_home=[],
                                 away=A, home=H, age_sec=10, status="in_progress", source="test", fetched_ts=NOW - 10)


def _ml(stem, leg="yes"):
    return {"ticker": "KXMLBGAME-%s-X" % stem, "held_leg": leg, "contracts": 1.0, "cost_basis_usd": 0.6,
            "market_type": "moneyline"}


def _wire(monkeypatch, gsmap):
    """map ticker-stem -> gs, wired through a fake game_key_from_ticker (returns a tuple keyed by stem) + a fake
    match_in_slate (returns gsmap[key]). Keeps the featured pick under test without the real team-map decode."""
    def gk(tk):
        stem = str(tk).split("-")[1] if "-" in str(tk) else None
        return (stem, None, None, None) if stem in gsmap else None
    monkeypatch.setattr(LV, "game_key_from_ticker", gk)
    monkeypatch.setattr(LV.feed_mlb, "match_in_slate", lambda games, date, teams, hhmm, dh: gsmap.get(date))
    # the away/home tiebreak + marker use _ordered_teams; split our 6-char test stems at the midpoint
    monkeypatch.setattr(LV, "_ordered_teams",
                        lambda tk: (str(tk).split("-")[1][:3], str(tk).split("-")[1][3:]) if "-" in str(tk) else (None, None))


def test_featured_is_closest_to_settling(monkeypatch):
    gsmap = {"PHIATL": _gs("PHI", 2, "ATL", 4, inning=8, outs=1),
             "NYYBOS": _gs("NYY", 1, "BOS", 0, inning=9, outs=1),
             "SDNCIN": _gs("SDN", 3, "CIN", 3, inning=9, outs=2)}      # closest: 9th, 2 outs
    _wire(monkeypatch, gsmap)
    ev = LV._live_event("mlb", [_ml("PHIATL"), _ml("NYYBOS"), _ml("SDNCIN")], {"x": 1}, {}, NOW)
    assert ev["featured"]["label"] == "SDN @ CIN"        # the game closest to settling (deterministic)
    assert ev["n_live"] == 3
    assert [c["label"] for c in ev["others"]] == ["NYY@BOS", "PHI@ATL"]   # then most-settling first among the chips
    assert ev["more"] == 0


def test_tie_breaks_most_held_then_away_code(monkeypatch):
    # two games both 9th/2-out: the one with MORE held positions is featured; equal held -> away code A->Z
    gsmap = {"ZZZAAA": _gs("ZZZ", 0, "AAA", 0, inning=9, outs=2),
             "BBBCCC": _gs("BBB", 0, "CCC", 0, inning=9, outs=2)}
    _wire(monkeypatch, gsmap)
    # ZZZAAA has TWO held positions, BBBCCC one -> ZZZAAA featured (most held)
    ev = LV._live_event("mlb", [_ml("ZZZAAA"), _ml("ZZZAAA", leg="no"), _ml("BBBCCC")], {"x": 1}, {}, NOW)
    assert ev["featured"]["label"] == "ZZZ @ AAA"
    # equal held (one each) -> away code A->Z: BBB(away) < ZZZ(away) -> BBBCCC featured
    ev2 = LV._live_event("mlb", [_ml("ZZZAAA"), _ml("BBBCCC")], {"x": 1}, {}, NOW)
    assert ev2["featured"]["label"] == "BBB @ CCC"


def test_others_capped_at_three_with_more(monkeypatch):
    gsmap = {s: _gs(s[:3], 0, s[3:], 0, inning=i, outs=0) for i, s in
             enumerate(["AAABBB", "CCCDDD", "EEEFFF", "GGGHHH", "IIIJJJ", "KKKLLL"], start=1)}
    _wire(monkeypatch, gsmap)
    ev = LV._live_event("mlb", [_ml(s) for s in gsmap], {"x": 1}, {}, NOW)
    assert ev["n_live"] == 6
    assert len(ev["others"]) == 3 and ev["more"] == 2    # 1 featured + 3 chips + "+2 more live" -> BOUNDED HEIGHT
    # highest inning is featured; the block never grows past 1 featured + 3 chips regardless of the 6 games
    assert ev["featured"]["label"] == "KKK @ LLL"        # inning 6 = closest to settling


def test_featured_positions_capped_at_three(monkeypatch):
    gsmap = {"AAABBB": _gs("AAA", 0, "BBB", 0, inning=5, outs=0)}
    _wire(monkeypatch, gsmap)
    # 5 held positions on the featured game -> only 3 rows shown + "more_positions" = 2
    pos = [_ml("AAABBB"), _ml("AAABBB", "no"), _ml("AAABBB"), _ml("AAABBB", "no"), _ml("AAABBB")]
    ev = LV._live_event("mlb", pos, {"x": 1}, {}, NOW)
    assert len(ev["featured"]["positions"]) == 3 and ev["featured"]["more_positions"] == 2


def test_none_when_no_underway_game(monkeypatch):
    _wire(monkeypatch, {})                                # no game is live
    assert LV._live_event("mlb", [_ml("PHIATL")], {"x": 1}, {}, NOW) is None
