"""Phase D: MLB per-inning winner (KXMLBINNINGWIN-{stem}-{N}-{SIDE}). Proves parse->inning_winner, inert-without-token,
matched (exact inning+side), TIE binding, ROUTE-ONLY-to-its-own-inning (an inning-3 bet NEVER binds inning-9 or a
full-game ticker), and fail-closed bad inning. Ships INERT behind the 'inning_winner' token (0 subs carry it)."""
from trading_corp.data import mlb_poly_kalshi_match as M

GIDX = M.build_kalshi_game_index(["KXMLBGAME-26SEP061840ATLPHI-PHI"])
IWIDX = M.build_kalshi_inningwin_index([
    "KXMLBINNINGWIN-26SEP061840ATLPHI-9-ATL",
    "KXMLBINNINGWIN-26SEP061840ATLPHI-9-PHI",
    "KXMLBINNINGWIN-26SEP061840ATLPHI-9-TIE",
    "KXMLBINNINGWIN-26SEP061840ATLPHI-3-PHI",   # inning 3 lists ONLY PHI (no ATL) -> route-only proof
])
DATES = frozenset({"2026-09-06"})
ALL = ("moneyline", "inning_winner")


def _p(slug, outcome):
    return M.parse_poly_mlb_bet(slug, outcome, "")


def test_parse_inning_winner():
    pb = _p("mlb-atl-phi-2026-09-06-inning-9-winner-away", "Yes")
    assert pb.market_type == "inning_winner" and pb.raw["inning"] == 9 and pb.side == "away" and pb.leg == "yes"


def test_parse_draw():
    pb = _p("mlb-atl-phi-2026-09-06-inning-9-winner-draw", "Yes")
    assert pb.market_type == "inning_winner" and pb.side == "draw" and pb.raw["inning"] == 9


def test_index_shape():
    assert IWIDX["26SEP061840ATLPHI"][(9, "ATL")] == "KXMLBINNINGWIN-26SEP061840ATLPHI-9-ATL"
    assert IWIDX["26SEP061840ATLPHI"][(9, "TIE")] == "KXMLBINNINGWIN-26SEP061840ATLPHI-9-TIE"


def test_inert_without_token():
    pb = _p("mlb-atl-phi-2026-09-06-inning-9-winner-away", "Yes")
    r = M.match_bet(pb, GIDX, {}, {}, DATES, allowed_market_types=("moneyline",), inningwin_index=IWIDX)
    assert r.status == "skip_market_type_excluded"


def test_matched_away():
    pb = _p("mlb-atl-phi-2026-09-06-inning-9-winner-away", "Yes")   # away = ATL
    r = M.match_bet(pb, GIDX, {}, {}, DATES, allowed_market_types=ALL, inningwin_index=IWIDX)
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBINNINGWIN-26SEP061840ATLPHI-9-ATL" and r.leg == "yes"


def test_matched_tie():
    pb = _p("mlb-atl-phi-2026-09-06-inning-9-winner-draw", "Yes")
    r = M.match_bet(pb, GIDX, {}, {}, DATES, allowed_market_types=ALL, inningwin_index=IWIDX)
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBINNINGWIN-26SEP061840ATLPHI-9-TIE" and r.leg == "yes"


def test_leg_no():
    pb = _p("mlb-atl-phi-2026-09-06-inning-9-winner-home", "No")   # home = PHI
    r = M.match_bet(pb, GIDX, {}, {}, DATES, allowed_market_types=ALL, inningwin_index=IWIDX)
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBINNINGWIN-26SEP061840ATLPHI-9-PHI" and r.leg == "no"


def test_route_only_own_inning():
    # inning-3 lists ONLY PHI; an inning-3-away(ATL) bet must MISS -- never bind inning-9-ATL or inning-3-PHI.
    pb = _p("mlb-atl-phi-2026-09-06-inning-3-winner-away", "Yes")
    r = M.match_bet(pb, GIDX, {}, {}, DATES, allowed_market_types=ALL, inningwin_index=IWIDX)
    assert r.status == "no_kalshi_contract" and r.kalshi_ticker is None


def test_route_only_never_fullgame():
    # empty inning index but a real game -> must NOT fall back to a full-game / moneyline ticker.
    pb = _p("mlb-atl-phi-2026-09-06-inning-9-winner-away", "Yes")
    r = M.match_bet(pb, GIDX, {}, {}, DATES, allowed_market_types=ALL, inningwin_index={})
    assert r.status == "no_kalshi_contract" and r.kalshi_ticker is None


def test_bad_inning_not_inning_winner():
    pb = _p("mlb-atl-phi-2026-09-06-inning-0-winner-away", "Yes")   # [1-9] only -> not recognised
    assert pb.market_type != "inning_winner"


def test_bad_outcome_fails_closed():
    pb = _p("mlb-atl-phi-2026-09-06-inning-9-winner-away", "Maybe")   # not Yes/No -> leg None
    r = M.match_bet(pb, GIDX, {}, {}, DATES, allowed_market_types=ALL, inningwin_index=IWIDX)
    assert r.status == "fail" and r.kalshi_ticker is None
