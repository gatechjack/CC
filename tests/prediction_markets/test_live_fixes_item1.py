"""LIVE tile event block -- 2026-09-20 REPLACEMENT of the Deploy-10 Item 1 featured-scoreboard tests.

The Deploy-10 2x2 tile (one FEATURED game with a scoreboard + <=3 chips + '+N more live', fixed 480px) did not
survive a football weekend, so it is REPLACED by a money-first STANDARD-size tile with at most ONE compact game line
per underway game (<=2 games) or a single summary line (>2 games). These tests replace the old ones:

  REPLACED ASSERTIONS (old -> new), and why:
   - test_featured_is_closest_to_settling / test_tie_breaks_most_held_then_away_code -> DELETED: there is no
     "featured" game any more (no scoreboard, no deterministic settling pick) -- every underway game is just a line.
   - test_others_capped_at_three_with_more -> test_summary_line_when_more_than_two: >2 underway games now collapse to
     ONE summary line (game_count / position_count), not 1 featured + 3 chips + '+N more live'.
   - test_featured_positions_capped_at_three -> DELETED: no per-game position rows on the tile any more (scores +
     positions live on the detail page, R7); the tile shows the signed shorthand joined on one line, truncated by CSS.
   - test_none_when_no_underway_game -> kept (test_none_when_no_underway_game): unchanged behaviour.
  NEW: line count at 0/1/2/3/6 games; the summary line; an unjoinable game counted-not-labelled (R2/R3).
"""
import types

from trading_corp.prediction_markets.web import live_view as LV

NOW = 1789200000


def _gs(live=True):
    return types.SimpleNamespace(is_live=live, is_final=not live)


def _ml(stem, leg="yes"):
    return {"ticker": "KXMLBGAME-%s-X" % stem, "held_leg": leg, "contracts": 1.0, "cost_basis_usd": 0.6,
            "market_type": "moneyline"}


def _wire(monkeypatch, live_stems, ordered=True):
    """map a ticker-stem -> an underway gs; a fake game_key_from_ticker/match_in_slate keyed on the stem, and
    _ordered_teams that splits a 6-char stem at the midpoint (ordered=False -> (None,None): an UNJOINABLE game)."""
    def gk(tk):
        stem = str(tk).split("-")[1] if "-" in str(tk) else None
        return (stem, None, None, None) if stem in live_stems else None
    monkeypatch.setattr(LV, "game_key_from_ticker", gk)
    monkeypatch.setattr(LV.feed_mlb, "match_in_slate", lambda games, date, teams, hhmm, dh: _gs(date in live_stems))
    if ordered:
        monkeypatch.setattr(LV, "_ordered_teams",
                            lambda tk: (str(tk).split("-")[1][:3], str(tk).split("-")[1][3:]) if "-" in str(tk) else (None, None))
    else:
        monkeypatch.setattr(LV, "_ordered_teams", lambda tk: (None, None))


def test_none_when_no_underway_game(monkeypatch):
    _wire(monkeypatch, set())                                  # nothing live
    assert LV._live_event("mlb", [_ml("PHIATL")], {"x": 1}, {}, NOW) is None


def test_one_live_game_one_line(monkeypatch):
    _wire(monkeypatch, {"WSHSTL"})
    ev = LV._live_event("mlb", [_ml("WSHSTL"), _ml("WSHSTL", "no")], {"x": 1}, {}, NOW)
    assert ev["game_count"] == 1 and ev["position_count"] == 2 and ev["summary_only"] is False
    assert len(ev["games"]) == 1
    assert ev["games"][0]["matchup"] == "WSH @ STL"           # matchup from the ticker team map (no score)
    assert isinstance(ev["games"][0]["positions_shorthand"], str) and ev["games"][0]["positions_shorthand"]


def test_two_live_games_two_lines(monkeypatch):
    _wire(monkeypatch, {"AAABBB", "CCCDDD"})
    ev = LV._live_event("mlb", [_ml("AAABBB"), _ml("CCCDDD")], {"x": 1}, {}, NOW)
    assert ev["game_count"] == 2 and ev["summary_only"] is False and len(ev["games"]) == 2
    assert {g["matchup"] for g in ev["games"]} == {"AAA @ BBB", "CCC @ DDD"}


def test_three_live_games_summary_line(monkeypatch):
    _wire(monkeypatch, {"AAABBB", "CCCDDD", "EEEFFF"})
    ev = LV._live_event("mlb", [_ml("AAABBB"), _ml("CCCDDD"), _ml("EEEFFF")], {"x": 1}, {}, NOW)
    assert ev["game_count"] == 3 and ev["position_count"] == 3
    assert ev["summary_only"] is True and ev["games"] == []   # >2 -> summary only, NO per-game lines


def test_six_live_games_summary_line(monkeypatch):
    stems = ["AAABBB", "CCCDDD", "EEEFFF", "GGGHHH", "IIIJJJ", "KKKLLL"]
    _wire(monkeypatch, set(stems))
    pos = [_ml(s) for s in stems] + [_ml("AAABBB", "no"), _ml("CCCDDD", "no"), _ml("EEEFFF", "no")]  # 9 positions on 6 games
    ev = LV._live_event("mlb", pos, {"x": 1}, {}, NOW)
    assert ev["game_count"] == 6 and ev["position_count"] == 9
    assert ev["summary_only"] is True and ev["games"] == []   # the tile renders "6 games live . 9 positions >"


def test_unjoinable_game_counted_not_labelled(monkeypatch):
    # two underway games, both with NO ticker matchup (tennis/ufc/unmapped) -> COUNTED, but zero labelled lines (R2/R3)
    _wire(monkeypatch, {"AAABBB", "CCCDDD"}, ordered=False)
    ev = LV._live_event("mlb", [_ml("AAABBB"), _ml("CCCDDD")], {"x": 1}, {}, NOW)
    assert ev["game_count"] == 2 and ev["position_count"] == 2
    assert ev["summary_only"] is False and ev["games"] == []  # counted (2) but never labelled with a ticker


def test_mixed_joinable_and_unjoinable_only_labels_the_joinable(monkeypatch):
    # one joinable + one unjoinable underway game -> game_count 2, ONE labelled line (the joinable), never a ticker
    def _ot(tk):
        stem = str(tk).split("-")[1]
        return (stem[:3], stem[3:]) if stem == "AAABBB" else (None, None)
    _wire(monkeypatch, {"AAABBB", "ZZZTEN"})
    monkeypatch.setattr(LV, "_ordered_teams", _ot)
    ev = LV._live_event("mlb", [_ml("AAABBB"), _ml("ZZZTEN")], {"x": 1}, {}, NOW)
    assert ev["game_count"] == 2 and len(ev["games"]) == 1 and ev["games"][0]["matchup"] == "AAA @ BBB"
    joined = " ".join(g["matchup"] for g in ev["games"])
    assert "KX" not in joined and "ZZZTEN" not in joined       # R2: the unjoinable game is never shown as a ticker
