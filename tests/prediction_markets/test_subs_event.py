"""LIVE event-block grouping + labels (fix 2026-09-11). The block must list, per underway game, ONLY that game's
held positions (was attaching every open position to one game); ML/TOT/SPR labels carry the held side; an
unjoinable ticker is omitted (never guessed onto a game); multiple live games stack + cap at 3. Pure/offline."""
import time
from types import SimpleNamespace

from trading_corp.prediction_markets.web import live_view as lv
from trading_corp.prediction_markets.web import marks as marks_mod

NOW = int(time.time())

# two real MLB games; blob is away+home. Game A = TB@ATL, Game B = PHI@NYM.
A_ML = "KXMLBGAME-26SEP111905TBATL-ATL"      # yes = ATL (home)
A_TOT = "KXMLBTOTAL-26SEP111905TBATL-9"
A_SPR = "KXMLBSPREAD-26SEP111905TBATL-ATL2"
B_ML = "KXMLBGAME-26SEP111940PHINYM-PHI"      # yes = PHI (away)


def _gs(away, ascore, home, hscore, live=True):
    return SimpleNamespace(is_live=live, is_final=not live, inning=6, half="TOP", outs=1, balls=2, strikes=2,
                           age_sec=12, away=SimpleNamespace(abbr=away, score=ascore),
                           home=SimpleNamespace(abbr=home, score=hscore))


def _mark(t, bid=0.5, status="active", title=None):
    return marks_mod.Mark(t, yes_bid=bid, no_bid=0.4, yes_ask=0.6, no_ask=0.45, last=0.5, status=status, as_of=NOW, title=title)


def _pos(t, leg="yes", n=2.0, cost=5.0):
    return {"ticker": t, "market_type": "moneyline", "held_leg": leg, "contracts": n, "cost_basis_usd": cost,
            "avg_price": cost / n, "fees_usd": 0.0}


def _patch_feed(monkeypatch, live_teamsets):
    """match_in_slate returns a live GameState for a team-set in `live_teamsets`, else None. Keyed by frozenset."""
    def fake(games, date_iso, team_set, hhmm, game_no):
        return games.get(team_set)
    monkeypatch.setattr(lv.feed_mlb, "match_in_slate", fake)
    fg = {}
    for ts, gs in live_teamsets.items():
        fg[ts] = gs
    return fg


def _mkts(*ts):
    return {t: _mark(t) for t in ts}


def test_event_groups_by_game_A_never_lists_Bs_position(monkeypatch):
    # the core defect: game A's block must NOT contain game B's position
    from trading_corp.data.sports_team_mapping import MLB_TEAMS
    setA = frozenset({MLB_TEAMS["TB"], MLB_TEAMS["ATL"]})
    setB = frozenset({MLB_TEAMS["PHI"], MLB_TEAMS["NYM"]})
    fg = _patch_feed(monkeypatch, {setA: _gs("TB", 0, "ATL", 0), setB: _gs("PHI", 1, "NYM", 2)})
    ev = lv._live_event("mlb", [_pos(A_ML, "yes"), _pos(B_ML, "yes")], fg, _mkts(A_ML, B_ML), NOW)
    assert ev is not None and len(ev["rows"]) == 2 and ev["more"] == 0
    by_label = {r["label"]: r for r in ev["rows"]}
    a, b = by_label["TB @ ATL"], by_label["PHI @ NYM"]
    a_mkts = [p["market"] for p in a["positions"]]
    b_mkts = [p["market"] for p in b["positions"]]
    assert a_mkts == ["ATL"] and "PHI" not in a_mkts           # A lists only ATL, never PHI (the defect)
    assert b_mkts == ["PHI"] and "ATL" not in b_mkts


def test_event_ml_tot_spr_labels_and_held_team_marked(monkeypatch):
    from trading_corp.data.sports_team_mapping import MLB_TEAMS
    setA = frozenset({MLB_TEAMS["TB"], MLB_TEAMS["ATL"]})
    fg = _patch_feed(monkeypatch, {setA: _gs("TB", 0, "ATL", 0)})
    # ML held NO (yes=ATL -> we hold TB), TOT held NO (Under), SPR held NO (other team +)
    pos = [_pos(A_ML, "no"), _pos(A_TOT, "no"), _pos(A_SPR, "no")]
    ev = lv._live_event("mlb", pos, fg, _mkts(A_ML, A_TOT, A_SPR), NOW)
    row = ev["rows"][0]
    labels = {p["kind"]: p["market"] for p in row["positions"]}
    assert labels["ML"] == "TB"           # held the NO leg of yes=ATL -> our team is TB (leg-aware)
    assert labels["TOT"] == "-8.5"        # Under, signed
    assert labels["SPR"] == "+1.5 TB"     # other team gets +strike
    # score line marks OUR ML team (TB = away) -> away_ours, not home
    assert row["away_ours"] is True and row["home_ours"] is False


def test_event_our_team_home_marker(monkeypatch):
    from trading_corp.data.sports_team_mapping import MLB_TEAMS
    setA = frozenset({MLB_TEAMS["TB"], MLB_TEAMS["ATL"]})
    fg = _patch_feed(monkeypatch, {setA: _gs("TB", 0, "ATL", 0)})
    ev = lv._live_event("mlb", [_pos(A_ML, "yes")], fg, _mkts(A_ML), NOW)   # yes=ATL (home)
    row = ev["rows"][0]
    assert row["home_ours"] is True and row["away_ours"] is False and row["positions"][0]["market"] == "ATL"


def test_event_unjoinable_ticker_omitted(monkeypatch):
    from trading_corp.data.sports_team_mapping import MLB_TEAMS
    setA = frozenset({MLB_TEAMS["TB"], MLB_TEAMS["ATL"]})
    fg = _patch_feed(monkeypatch, {setA: _gs("TB", 0, "ATL", 0)})
    junk = "KXMLBGAME-GARBAGE-ZZ"           # game_key_from_ticker -> None
    ev = lv._live_event("mlb", [_pos(A_ML, "yes"), _pos(junk, "yes")], fg, _mkts(A_ML, junk), NOW)
    assert len(ev["rows"]) == 1                                   # only the joinable game
    allmk = [p["market"] for r in ev["rows"] for p in r["positions"]]
    assert allmk == ["ATL"]                                       # the junk position is NOT attached to any game (FIX 3)


def test_event_multi_live_cap_at_3_plus_more(monkeypatch):
    from trading_corp.data.sports_team_mapping import MLB_TEAMS
    games = {"TB": "ATL", "PHI": "NYM", "SD": "CIN", "LAA": "BOS"}   # 4 live games, distinct start times
    tsets, tickers = {}, []
    hh = 1900
    for i, (aw, hm) in enumerate(games.items()):
        ts = frozenset({MLB_TEAMS[aw], MLB_TEAMS[hm]})
        tsets[ts] = _gs(aw, 0, hm, 0)
        tickers.append("KXMLBGAME-26SEP11%04d%s%s-%s" % (hh + i * 5, aw, hm, aw))
    fg = _patch_feed(monkeypatch, tsets)
    ev = lv._live_event("mlb", [_pos(t, "yes") for t in tickers], fg, _mkts(*tickers), NOW)
    assert len(ev["rows"]) == 3 and ev["more"] == 1               # capped at 3, +1 more live


def test_short_label_ml_leg_aware():
    T = "KXMLBGAME-26SEP021840SDCIN-SD"
    assert lv._short_label(T, "moneyline", "yes") == "SD"        # yes leg -> the yes club (unchanged)
    assert lv._short_label(T, "moneyline", "no") == "CIN"        # NO leg -> the OTHER club (the fix)


def test_event_cs2_non_mlb_no_scoreboard(monkeypatch):
    # a live-capable non-MLB match: HHMM start in the past + non-finalized mark -> a labelled row, no scoreboard
    tk = "KXCS2GAME-26AUG010000ACEBIG-ACE"    # Aug 1 00:00 -> unambiguously in the past vs NOW
    ev = lv._live_event("cs2", [_pos(tk, "yes")], {}, {tk: _mark(tk, status="active", title="ACE wins")}, NOW)
    assert ev is not None and len(ev["rows"]) == 1
    r = ev["rows"][0]
    assert r["has_scoreboard"] is False and r["label"] == "ACE wins" and len(r["positions"]) == 1
