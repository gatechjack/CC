"""Unit tests for the pm_web MLB sports-feed adapter (Scope D). Pure parse/join -- no network (fetch is
injected). Covers the named failure modes: team-code mismatch, DST/date rollover, doubleheaders,
postponed/suspended, feed-down, and the exact Kalshi-ticker <-> feed join.
"""
from trading_corp.prediction_markets.web import feed_mlb as f
from trading_corp.data.mlb_poly_kalshi_match import game_key_and_side


# ── time / canonicalization ──────────────────────────────────────────────────────────────────────────────────
def test_eastern_edt_and_est():
    assert f.eastern_key_parts("2026-09-02T16:40:00Z") == ("2026-09-02", "1240")   # EDT (UTC-4)
    assert f.eastern_key_parts("2026-01-15T00:10:00Z") == ("2026-01-14", "1910")   # EST (UTC-5)


def test_eastern_night_game_date_rollover():
    # a 10:10pm ET game on Sep 2 is 02:10Z Sep 3 in the feed -- must key back to Sep 2, HHMM 2210.
    assert f.eastern_key_parts("2026-09-03T02:10:00Z") == ("2026-09-02", "2210")


def test_canonical_team_reconciles_source_variants():
    assert f.canonical_team("AZ") == f.canonical_team("ARI") == "Arizona Diamondbacks"
    assert f.canonical_team("CWS") == f.canonical_team("CHW") == "Chicago White Sox"
    assert f.canonical_team("ATH") == f.canonical_team("OAK") == "Oakland Athletics"
    assert f.canonical_team("ZZZ") is None       # unknown -> None (degrade, never wrong club)


# ── THE JOIN: a Kalshi ticker's game_key must equal the feed key for the same game ───────────────────────────
def test_kalshi_ticker_joins_feed_key():
    gk, side, _ = game_key_and_side("KXMLBGAME-26SEP021240SDCIN-SD")
    fk = f.feed_game_key("2026-09-02", f.canonical_team("SD"), f.canonical_team("CIN"), "1240", None)
    assert gk == fk and side == "SD"


# ── StatsAPI parse ───────────────────────────────────────────────────────────────────────────────────────────
def _statsapi_game(state="In Progress", abstract="Live", dh="N", gn=1, gamedate="2026-09-02T16:40:00Z",
                   away="SD", home="CIN"):
    return {"gamePk": 824470, "gameDate": gamedate, "doubleHeader": dh, "gameNumber": gn,
            "status": {"detailedState": state, "abstractGameState": abstract},
            "teams": {"away": {"score": 3, "leagueRecord": {"wins": 71, "losses": 66},
                               "team": {"abbreviation": away, "teamName": "Padres", "name": "San Diego Padres"}},
                      "home": {"score": 4, "leagueRecord": {"wins": 68, "losses": 69},
                               "team": {"abbreviation": home, "teamName": "Reds", "name": "Cincinnati Reds"}}},
            "linescore": {"currentInning": 6, "inningState": "Top", "balls": 2, "strikes": 1, "outs": 1,
                          "offense": {"first": {"id": 1}, "third": {"id": 2}},
                          "innings": [{"num": 1, "away": {"runs": 1}, "home": {"runs": 1}},
                                      {"num": 2, "away": {"runs": 0}, "home": {"runs": 2}}]}}


def _sched(games):
    return {"dates": [{"games": games}]}


def test_parse_statsapi_live_game():
    games = f.parse_statsapi_schedule(_sched([_statsapi_game()]), now_ts=1000)
    (gs,) = list(games.values())
    assert gs.status == "in_progress" and gs.is_live
    assert gs.inning == 6 and gs.half == "TOP" and (gs.balls, gs.strikes, gs.outs) == (2, 1, 1)
    assert gs.bases == (True, False, True)               # first + third occupied, second empty
    assert gs.away.score == 3 and gs.home.score == 4
    assert gs.away.record == "71-66" and gs.home.name == "Reds"
    assert gs.linescore_away == (1, 0) and gs.linescore_home == (1, 2)
    assert gs.game_pk == "824470"


def test_parse_statsapi_final_has_no_live_fields():
    gs = list(f.parse_statsapi_schedule(_sched([_statsapi_game("Final", "Final")]), now_ts=1).values())[0]
    assert gs.status == "final" and gs.is_final
    assert gs.inning is None and gs.balls is None and gs.bases == ()


def test_parse_statsapi_postponed_and_suspended():
    p = list(f.parse_statsapi_schedule(_sched([_statsapi_game("Postponed", "Preview")]), now_ts=1).values())[0]
    s = list(f.parse_statsapi_schedule(_sched([_statsapi_game("Suspended: Rain", "Live")]), now_ts=1).values())[0]
    assert p.status == "postponed" and p.inning is None
    assert s.status == "suspended" and s.bases == ()     # suspended is not live -> no fabricated count/bases


def test_parse_statsapi_drops_unknown_club():
    # a non-club 'team' (e.g. all-star) canonicalizes to None -> the game is dropped, never mis-joined.
    assert f.parse_statsapi_schedule(_sched([_statsapi_game(away="ZZZ")]), now_ts=1) == {}


# ── doubleheaders + match tolerance ─────────────────────────────────────────────────────────────────────────
def test_doubleheader_distinct_keys_and_match():
    g1 = _statsapi_game(dh="Y", gn=1, gamedate="2026-09-02T17:05:00Z")
    g2 = _statsapi_game(dh="Y", gn=2, gamedate="2026-09-02T21:05:00Z")
    games = f.parse_statsapi_schedule(_sched([g1, g2]), now_ts=1)
    assert len(games) == 2                                # two distinct keys (game_no differs)
    team_set = frozenset({"San Diego Padres", "Cincinnati Reds"})
    # ticket for game 2 (its own HHMM + game_no) resolves to exactly game 2
    m = f.match_in_slate(games, "2026-09-02", team_set, "1705", 2)
    assert m is not None and m.game_no == 2


def test_match_in_slate_tolerates_minute_skew_when_unambiguous():
    games = f.parse_statsapi_schedule(_sched([_statsapi_game()]), now_ts=1)   # single SD@CIN 1240
    team_set = frozenset({"San Diego Padres", "Cincinnati Reds"})
    # ticker minute off by one (1241) but only one game that day for these clubs -> still matches
    assert f.match_in_slate(games, "2026-09-02", team_set, "1241", None) is not None
    # wrong date -> no match (never cross-day)
    assert f.match_in_slate(games, "2026-09-03", team_set, "1240", None) is None


# ── ESPN fallback parse (ARI code) ──────────────────────────────────────────────────────────────────────────
def _espn(state="in", name="STATUS_IN_PROGRESS", short="Top 7th"):
    return {"events": [{"date": "2026-09-02T20:10:00Z", "competitions": [{
        "date": "2026-09-02T20:10:00Z", "doubleheader": None,
        "status": {"type": {"state": state, "name": name, "shortDetail": short}, "period": 7},
        "situation": {"balls": 1, "strikes": 2, "outs": 2, "onFirst": True, "onSecond": False,
                      "onThird": False, "lastPlay": {"text": "Single to left field."}},
        "competitors": [
            {"homeAway": "away", "score": "2", "team": {"abbreviation": "ARI", "shortDisplayName": "D-backs"},
             "records": [{"type": "total", "summary": "70-67"}], "linescores": [{"value": 1}, {"value": 1}]},
            {"homeAway": "home", "score": "3", "team": {"abbreviation": "LAD", "shortDisplayName": "Dodgers"},
             "records": [{"type": "total", "summary": "85-52"}], "linescores": [{"value": 0}, {"value": 3}]}]}]}]}


def test_parse_espn_live_joins_by_canonical_name():
    games = f.parse_espn_scoreboard(_espn(), now_ts=5)
    (gs,) = list(games.values())
    assert gs.source == "espn" and gs.status == "in_progress"
    assert gs.key[3] == frozenset({"Arizona Diamondbacks", "Los Angeles Dodgers"})
    assert gs.key[:2] == ("2026-09-02", "1610")          # 20:10Z -> 16:10 ET
    assert gs.bases == (True, False, False) and (gs.balls, gs.strikes, gs.outs) == (1, 2, 2)
    assert gs.last_play == "Single to left field." and gs.inning == 7 and gs.half == "TOP"


# ── orchestration: primary -> fallback -> both-down ─────────────────────────────────────────────────────────
def test_fetch_slate_falls_back_to_espn_when_statsapi_raises():
    def fake_get(url, timeout=12.0):
        if "statsapi" in url:
            raise TimeoutError("statsapi down")
        return _espn()
    res = f.fetch_slate("2026-09-02", now_ts=9, http_get=fake_get)
    assert res.ok and res.source == "espn" and len(res.games) == 1


def test_fetch_slate_both_down_is_ok_false_empty():
    def fake_get(url, timeout=12.0):
        raise TimeoutError("both down")
    res = f.fetch_slate("2026-09-02", now_ts=9, http_get=fake_get)
    assert res.ok is False and res.games == {} and res.source is None and res.error == "TimeoutError"


def test_fetch_slate_prefers_statsapi_when_it_has_games():
    def fake_get(url, timeout=12.0):
        return _sched([_statsapi_game()]) if "statsapi" in url else _espn()
    res = f.fetch_slate("2026-09-02", now_ts=9, http_get=fake_get)
    assert res.ok and res.source == "statsapi"
