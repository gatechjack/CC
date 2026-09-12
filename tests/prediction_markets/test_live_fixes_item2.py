"""Item 2 (2026-09-12) -- non-MLB game labels. The SAME label rule (matchup + signed shorthand, ONE shared
formatter `format_market_label`) applied to (a) the /live positions table, (b) the trade drawer's market column,
and (c) the Farm whale paper-trade list -- Kalshi tickers on the first two, a Polymarket slug on the third
(decoded by the engine's canonical `parse_poly_bet`). FAIL-CLOSED everywhere: a ticker/slug with no two-team
decode (tennis/ufc/fed, a prop, a non-sport slug, an ambiguous blob) yields matchup=None and the caller shows an
honest label, never an invented matchup or a raw ticker/slug."""
from trading_corp.prediction_markets.web import live_view as LV, marks as MK

NOW = 1789200000
G = "KXNCAAFGAME-26SEP11MIZZKU-MIZZ"
T = "KXNCAAFTOTAL-26SEP11MIZZKU-52"
S = "KXNCAAFSPREAD-26SEP11MIZZKU-MIZZ7"


# ── (1) Kalshi ticker decode: matchup + signed shorthand (positions table / drawer) ───────────────
def test_kalshi_matchup_and_gamekey():
    assert LV.market_matchup(G) == "MIZZ @ KU"
    assert LV.market_matchup(S) == "MIZZ @ KU"
    assert LV.structural_game_key(G) == ("MIZZ @ KU", "2026-09-11")
    assert LV.structural_game_key(T) == LV.structural_game_key(S)   # ML/TOT/SPR of one game share a key


def test_kalshi_shorthand_directional():
    assert LV._short_label(G, "moneyline", "yes") == "MIZZ"        # we hold Missouri
    assert LV._short_label(G, "moneyline", "no") == "KU"           # NO leg -> the other club
    assert LV._short_label(T, "total", "yes") == "+51.5"           # Over (strike N-0.5)
    assert LV._short_label(T, "total", "no") == "-51.5"            # Under
    assert LV._short_label(S, "spread", "yes") == "-6.5 MIZZ"      # anchor lays the spread
    assert LV._short_label(S, "spread", "no") == "+6.5 KU"         # the other team gets +line (4-char code decodes)


def test_kalshi_nfl_spread_shorthand():
    nfl = "KXNFLSPREAD-25SEP07BALIND-BAL4"
    assert LV.market_matchup(nfl) == "BAL @ IND"
    assert LV._short_label(nfl, "spread", "yes") == "-3.5 BAL"
    assert LV._short_label(nfl, "spread", "no") == "+3.5 IND"


def test_format_market_label_primary_secondary():
    p, s = LV.format_market_label("MIZZ @ KU", "spread", "-6.5 MIZZ", "Missouri -6.5")
    assert p == "SPR -6.5 MIZZ" and s == "Missouri -6.5"           # shorthand FIRST, title second
    p, s = LV.format_market_label("MIZZ @ KU", "total", "+51.5", None)
    assert p == "TOT +51.5" and s == "MIZZ @ KU"                   # no title -> matchup is the secondary


def test_kalshi_fail_closed_no_matchup():
    for tk in ("KXATPMATCH-25SEP07ALCSIN-ALC", "KXUFCFIGHT-25SEP07-JON", "KXFED-26MAR-CUT"):
        assert LV.market_matchup(tk) is None                       # tennis/ufc/fed -> no two-team decode
        assert LV.structural_game_key(tk) is None


# ── (2) Poly slug decode: the SAME rule on the Farm whale paper-trade list ─────────────────────────
def test_poly_moneyline_total_spread():
    assert LV.poly_market_label("cfb", "cfb-mizz-ku-2026-09-11", "Missouri", None) == ("MIZZ @ KU", "ML MIZZ", "MIZZ @ KU")
    assert LV.poly_market_label("cfb", "cfb-mizz-ku-2026-09-11", "Kansas", None)[1] == "ML KU"
    assert LV.poly_market_label("cfb", "cfb-mizz-ku-2026-09-11-total-52pt5", "Over", None)[1] == "TOT +52.5"
    assert LV.poly_market_label("cfb", "cfb-mizz-ku-2026-09-11-total-52pt5", "Under", None)[1] == "TOT -52.5"
    # spread anchored on the home team (KU): KU backed -> KU lays -6.5; MIZZ backed -> MIZZ gets +6.5
    assert LV.poly_market_label("cfb", "cfb-mizz-ku-2026-09-11-spread-home-6pt5", "Kansas", None)[1] == "SPR -6.5 KU"
    assert LV.poly_market_label("cfb", "cfb-mizz-ku-2026-09-11-spread-home-6pt5", "Missouri", None)[1] == "SPR +6.5 MIZZ"


def test_poly_uses_a_title_as_secondary():
    mu, primary, secondary = LV.poly_market_label("nfl", "nfl-bal-ind-2025-09-07", "Baltimore Ravens", "Ravens ML")
    assert mu == "BAL @ IND" and primary == "ML BAL" and secondary == "Ravens ML"


def test_poly_fail_closed():
    assert LV.poly_market_label("atp", "atp-alcaraz-sinner-2025-09-07", "Carlos Alcaraz", None) == (None, None, None)   # no LEAGUES entry
    assert LV.poly_market_label("cfb", "cfb-mizz-ku-2026-09-11-nrfi", "Yes", None) == (None, None, None)               # a prop suffix
    assert LV.poly_market_label("cfb", "not-a-sports-slug", "Yes", None) == (None, None, None)                        # not a game slug
    assert LV.poly_market_label(None, None, None, None) == (None, None, None)                                          # None-safe


# ── (3) grouping: a game's rows sit under one header; a no-matchup row is header-less ──────────────
def test_group_by_game():
    rows = [{"ticker": G, "game_key": LV.structural_game_key(G)},
            {"ticker": T, "game_key": LV.structural_game_key(T)},
            {"ticker": S, "game_key": LV.structural_game_key(S)},
            {"ticker": "KXATPMATCH-25SEP07ALCSIN-ALC", "game_key": None}]
    groups = LV._group_by_game(rows)
    assert len(groups) == 2                                         # one CFB game group + one header-less tennis row
    cfb = groups[0]
    assert cfb["matchup"] == "MIZZ @ KU" and cfb["date"] == "2026-09-11" and len(cfb["rows"]) == 3
    assert groups[1]["matchup"] is None and len(groups[1]["rows"]) == 1


# ── (4) integration: build_live_context groups a CFB sub by game, shorthand-first, no raw ticker ───
def _orders(*tks):
    return [{"account_id": "kalshi_jack", "category": "cfb", "wallet": "0xa", "ticker": tk, "outcome_leg": "yes",
             "is_exit": 0, "dry_run": 0, "outcome_status": "filled", "fill_count": 2.0, "fill_price": 0.4,
             "fee": 0.01, "id": i} for i, tk in enumerate(tks, 1)]


def _pos(*tks):
    return [{"ticker": tk, "held_leg": "yes", "contracts": 2.0, "cost_basis_usd": 0.8, "avg_price": 0.4,
             "fees_usd": 0.01, "market_type": LV._kind(tk)} for tk in tks]


def _posw(*tks):
    return [dict(p, wallet="0xa", user_name=None) for p in _pos(*tks)]


def test_build_live_context_cfb_grouped_shorthand():
    ctx = LV.build_live_context(orders=_orders(G, T, S), open_positions=_pos(G, T, S),
                                open_positions_by_whale=_posw(G, T, S), slate=None,
                                marks_result=MK.MarksResult(marks={}, ok=True, as_of=NOW, error=None),
                                now_ts=NOW, category="cfb", titles={})
    pv = ctx["positions_view"]
    assert pv["n_active"] == 3
    assert len(pv["active_groups"]) == 1                            # all three rows are ONE game
    g = pv["active_groups"][0]
    assert g["matchup"] == "MIZZ @ KU" and len(g["rows"]) == 3
    descs = {r["desc"] for r in pv["active"]}
    assert descs == {"ML MIZZ", "TOT +51.5", "SPR -6.5 MIZZ"}      # shorthand-first, all directional
    assert all("KX" not in r["desc"] for r in pv["active"])         # never a raw ticker
    assert all(r["matchup"] == "MIZZ @ KU" for r in pv["active"])


def test_build_live_context_tennis_fail_closed_base_floor():
    atp = "KXATPMATCH-25SEP07ALCSIN-ALC"
    ctx = LV.build_live_context(orders=_orders(atp) and [dict(o, category="atp") for o in _orders(atp)],
                                open_positions=_pos(atp), open_positions_by_whale=_posw(atp), slate=None,
                                marks_result=MK.MarksResult(marks={}, ok=True, as_of=NOW, error=None),
                                now_ts=NOW, category="atp", titles={})
    pv = ctx["positions_view"]
    row = pv["active"][0]
    assert row["matchup"] is None                                   # fail-closed: no two-team decode
    assert row["desc"] == "ATP" and "KX" not in row["desc"]         # clean category floor, never a series token
    assert len(pv["active_groups"]) == 1 and pv["active_groups"][0]["matchup"] is None   # header-less single row


# ── (5) drawer: the market column names the game via the ticker when there is no MLB feed ──────────
def test_trade_rows_matchup_falls_back_to_ticker_decode():
    order = {"id": 7, "ticker": S, "wallet": "0xa", "user_name": None, "outcome_leg": "no", "is_exit": 0,
             "outcome_status": "filled", "fill_count": 3.0, "fill_price": 0.45, "submitted_price": 0.44,
             "response_ts": NOW}
    rows = LV._trade_rows([order], {}, {}, {}, {}, NOW)             # no slate -> no feed match -> ticker fallback
    assert len(rows) == 1
    r = rows[0]
    assert r["matchup"] == "MIZZ @ KU"                              # named from the ticker, not '-'
    assert r["short"] == "+6.5 KU"                                  # the held (NO) leg's signed shorthand
    assert r["label"] == "SPR +6.5 KU"                             # the TAGGED shorthand (shared formatter), shown in the Type column
    assert "KX" not in r["matchup"] and "KX" not in r["label"]
