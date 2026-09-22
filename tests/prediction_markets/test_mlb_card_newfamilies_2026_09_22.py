"""MLB game-card + new-family labels (2026-09-22).

The card classified market kind by SUBSTRING, so KXMLBTEAMTOTAL / KXMLBF5TOTAL / KXMLBTOTAL all collapsed into the
one TOT slot (alpha-first won -> an F5/team total SHADOWED the game total), KXMLBF5SPREAD collided into SPR, and the
F5 winner fell through to a no-slot kind and vanished. Worse: the losers dropped out of the card money AND the
detail-page P&L (both were card/slot-derived).

This pins: (1) DISTINCT kinds per family (winner|total|spread, matched before the generic TOTAL/SPREAD substrings);
(2) EVERY held family gets a card line (fixed ML/TOT/SPR/RFI slots + a conditional extra line per new-family
position); (3) ★ card + page money is POSITION-based, independent of slot assignment -- a rendering gap can cost a
LINE, never a money figure; (4) the game total/spread are NEVER shadowed; (5) an original-only card is unchanged;
(6) the NFL positions-table labels read clearly through the same functions.
"""
from trading_corp.prediction_markets.web import live_view as LV, marks as MK

NOW = 1789200000
MSTEM = "26SEP222210SDLAD"          # SD @ LAD (away=SD, home=LAD)
NSTEM = "26SEP27LARDEN"             # LAR @ DEN


# ── (1) _kind: DISTINCT kinds, matched before the generic substrings; originals unchanged ─────────────────────────
def test_kind_distinct_new_families():
    assert LV._kind("KXMLBTEAMTOTAL-%s-SD8" % MSTEM) == "team_total"
    assert LV._kind("KXNFLTEAMTOTAL-%s-LAR8" % NSTEM) == "team_total"
    assert LV._kind("KXMLBF5-%s-SD" % MSTEM) == "f5_winner"
    assert LV._kind("KXMLBF5TOTAL-%s-8" % MSTEM) == "f5_total"
    assert LV._kind("KXMLBF5SPREAD-%s-SD2" % MSTEM) == "f5_spread"
    assert LV._kind("KXNFL1Q-%s-LAR" % NSTEM) == "q1_winner"
    assert LV._kind("KXNFL1QTOTAL-%s-11" % NSTEM) == "q1_total"
    assert LV._kind("KXNFL1QSPREAD-%s-LAR3" % NSTEM) == "q1_spread"
    assert LV._kind("KXNFL2H-%s-LAR" % NSTEM) == "h2_winner"
    assert LV._kind("KXNFL2HTOTAL-%s-24" % NSTEM) == "h2_total"
    assert LV._kind("KXNFL1H-%s-LAR" % NSTEM) == "first_half_winner"
    assert LV._kind("KXNFL1HSPREAD-%s-LAR8" % NSTEM) == "first_half_spread"


def test_kind_originals_unchanged():
    assert LV._kind("KXMLBGAME-%s-SD" % MSTEM) == "moneyline"
    assert LV._kind("KXMLBTOTAL-%s-8" % MSTEM) == "total"          # game total is NOT team_total / f5_total
    assert LV._kind("KXMLBSPREAD-%s-SD2" % MSTEM) == "spread"      # game spread is NOT f5_spread
    assert LV._kind("KXMLBRFI-%s" % MSTEM) == "first_inning_run"
    assert LV._kind("KXNFLGAME-%s-LAR" % NSTEM) == "moneyline"
    assert LV._kind("KXNFLTOTAL-%s-44" % NSTEM) == "total"
    assert LV._kind("KXNFLSPREAD-%s-LAR3" % NSTEM) == "spread"


# ── (2) KIND_LABEL + _short_label read as a person would say it ───────────────────────────────────────────────────
def test_kind_labels_registered():
    for k, lab in (("team_total", "TEAM"), ("f5_winner", "F5"), ("f5_total", "F5 TOT"), ("f5_spread", "F5 SPR"),
                   ("first_half_total", "1H TOT"), ("q1_winner", "1Q"), ("q1_spread", "1Q SPR"), ("h2_total", "2H TOT")):
        assert LV.KIND_LABEL[k] == lab


def test_short_label_team_total():
    tk = "KXMLBTEAMTOTAL-%s-SD8" % MSTEM
    assert LV._short_label(tk, "team_total", "yes") == "SD O 7.5"
    assert LV._short_label(tk, "team_total", "no") == "SD U 7.5"
    assert LV._short_label(tk, "team_total", None) == "SD O/U 7.5"


def test_short_label_f5_and_period_reuse_base_logic():
    assert LV._short_label("KXMLBF5-%s-SD" % MSTEM, "f5_winner", "yes") == "SD"
    assert LV._short_label("KXMLBF5-%s-SD" % MSTEM, "f5_winner", "no") == "LAD"        # NO -> the other club
    assert LV._short_label("KXMLBF5TOTAL-%s-8" % MSTEM, "f5_total", "yes") == "+7.5"
    assert LV._short_label("KXMLBF5TOTAL-%s-8" % MSTEM, "f5_total", "no") == "-7.5"
    assert LV._short_label("KXMLBF5SPREAD-%s-SD2" % MSTEM, "f5_spread", "yes") == "-1.5 SD"
    assert LV._short_label("KXMLBF5SPREAD-%s-SD2" % MSTEM, "f5_spread", "no") == "+1.5 LAD"
    assert LV._short_label("KXNFL1QTOTAL-%s-11" % NSTEM, "q1_total", "yes") == "+10.5"
    assert LV._short_label("KXNFL1QSPREAD-%s-LAR3" % NSTEM, "q1_spread", "yes") == "-2.5 LAR"
    assert LV._short_label("KXNFL1Q-%s-LAR" % NSTEM, "q1_winner", "yes") == "LAR"


# ── (3)-(5) the MLB card: every family a line, money position-based, no shadow, byte-identical original ────────────
def _o(tk):
    return {"account_id": "kalshi_jack", "category": "mlb", "wallet": "0xa", "ticker": tk, "outcome_leg": "yes",
            "is_exit": 0, "dry_run": 0, "outcome_status": "filled", "fill_count": 10.0, "fill_price": 0.50,
            "fee": 0.0, "id": tk}


def _p(tk):
    return {"ticker": tk, "held_leg": "yes", "contracts": 10.0, "cost_basis_usd": 5.0, "avg_price": 0.50,
            "fees_usd": 0.0, "market_type": LV._kind(tk)}


def _pw(tk):
    return dict(_p(tk), wallet="0xa", user_name=None)


def _mlb_ctx(tickers):
    return LV.build_live_context(orders=[_o(t) for t in tickers], open_positions=[_p(t) for t in tickers],
                                 open_positions_by_whale=[_pw(t) for t in tickers], slate=None,
                                 marks_result=MK.MarksResult(marks={}, ok=True, as_of=NOW, error=None),
                                 now_ts=NOW, category="mlb")


ALL8 = ["KXMLBGAME-%s-SD" % MSTEM, "KXMLBTOTAL-%s-8" % MSTEM, "KXMLBSPREAD-%s-SD2" % MSTEM, "KXMLBRFI-%s" % MSTEM,
        "KXMLBF5-%s-SD" % MSTEM, "KXMLBF5TOTAL-%s-8" % MSTEM, "KXMLBF5SPREAD-%s-SD2" % MSTEM,
        "KXMLBTEAMTOTAL-%s-SD8" % MSTEM]


def test_card_every_family_has_a_line_and_money_equals_held():
    ctx = _mlb_ctx(ALL8)
    assert len(ctx["cards"]) == 1
    c = ctx["cards"][0]
    fixed = [c["slots_by_kind"][k] for k in ("ML", "TOT", "SPR", "RFI")]
    assert all(s is not None for s in fixed)                     # the 4 fixed slots all held
    assert len(c["extra_lines"]) == 4                            # F5 winner + F5 total + F5 spread + team total
    lines = fixed + c["extra_lines"]
    assert len({s["ticker"] for s in lines}) == 8                # EVERY position (8) has exactly one line
    assert c["open_cost"] == 40.0                                # 8 x $5 -- money counts all 8, not just the 4 slots
    assert ctx["summary"]["unsettled_cost"] == 40.0             # the page P&L is position-based too


def test_game_total_and_spread_never_shadowed():
    ctx = _mlb_ctx(ALL8)
    c = ctx["cards"][0]
    assert c["slots_by_kind"]["TOT"]["ticker"] == "KXMLBTOTAL-%s-8" % MSTEM     # game total keeps its slot...
    assert c["slots_by_kind"]["SPR"]["ticker"] == "KXMLBSPREAD-%s-SD2" % MSTEM  # ...and the game spread its slot
    extra_tks = {s["ticker"] for s in c["extra_lines"]}
    assert "KXMLBF5TOTAL-%s-8" % MSTEM in extra_tks             # F5 total is its OWN line, not the game TOT
    assert "KXMLBF5SPREAD-%s-SD2" % MSTEM in extra_tks          # F5 spread its OWN line, not the game SPR


def test_original_only_card_is_unchanged():
    tks = ["KXMLBGAME-%s-SD" % MSTEM, "KXMLBTOTAL-%s-8" % MSTEM, "KXMLBSPREAD-%s-SD2" % MSTEM]
    c = _mlb_ctx(tks)["cards"][0]
    assert c["extra_lines"] == []                               # nothing extra -> template renders byte-identical
    assert [k for k in ("ML", "TOT", "SPR") if c["slots_by_kind"][k]] == ["ML", "TOT", "SPR"]
    assert c["slots_by_kind"]["RFI"] is None
    assert c["open_cost"] == 15.0 and c["n_live"] == 3


def test_two_team_totals_both_get_a_line_and_both_count():
    # a game can carry BOTH teams' totals; both must render AND both must count in the money (invariant beyond the gate)
    tks = ["KXMLBGAME-%s-SD" % MSTEM, "KXMLBTEAMTOTAL-%s-SD8" % MSTEM, "KXMLBTEAMTOTAL-%s-LAD5" % MSTEM]
    c = _mlb_ctx(tks)["cards"][0]
    tt = [s for s in c["extra_lines"] if s["kind"] == "team_total"]
    assert len(tt) == 2                                         # both team totals get their own line (no same-kind drop)
    assert c["open_cost"] == 15.0                               # all three positions counted


# ── (6) NFL positions table: labels read clearly through the shared formatter ─────────────────────────────────────
def test_nfl_table_labels_read_clearly():
    nfl = ["KXNFLGAME-%s-LAR" % NSTEM, "KXNFL1Q-%s-LAR" % NSTEM, "KXNFL1QTOTAL-%s-11" % NSTEM,
           "KXNFL1QSPREAD-%s-LAR3" % NSTEM, "KXNFL2H-%s-LAR" % NSTEM, "KXNFLTEAMTOTAL-%s-LAR8" % NSTEM]
    ctx = LV.build_live_context(orders=[dict(_o(t), category="nfl") for t in nfl],
                                open_positions=[_p(t) for t in nfl], open_positions_by_whale=[_pw(t) for t in nfl],
                                slate=None, marks_result=MK.MarksResult(marks={}, ok=True, as_of=NOW, error=None),
                                now_ts=NOW, category="nfl", titles={})
    descs = {r["ticker"].split("-")[0]: r["desc"] for r in ctx["positions_view"]["active"]}
    assert descs["KXNFL1Q"] == "1Q LAR"
    assert descs["KXNFL1QTOTAL"] == "1Q TOT +10.5"
    assert descs["KXNFL1QSPREAD"] == "1Q SPR -2.5 LAR"
    assert descs["KXNFL2H"] == "2H LAR"
    assert descs["KXNFLTEAMTOTAL"] == "TEAM LAR O 7.5"
    assert all("KX" not in d for d in descs.values())           # never a raw ticker


# ── (7) skeptic-driven pins (2026-09-22): period-variant kinds, WNBA/unlisted-sport classification, multi-digit
#        strikes, and the two invariant holes the money rewrite must close (dup-strike coverage; unrecognized family) ──
def test_kind_period_variants_totals_spreads_and_nba():
    assert LV._kind("KXNFL1HTOTAL-%s-44" % NSTEM) == "first_half_total"
    assert LV._kind("KXNFL2HSPREAD-%s-LAR3" % NSTEM) == "h2_spread"
    assert LV._kind("KXNFL3Q-%s-LAR" % NSTEM) == "q3_winner"
    assert LV._kind("KXNFL3QTOTAL-%s-14" % NSTEM) == "q3_total"
    assert LV._kind("KXNFL4QSPREAD-%s-LAR3" % NSTEM) == "q4_spread"
    assert LV._kind("KXNBA1Q-%s-LAR" % NSTEM) == "q1_winner"        # period grammar is not NFL-only


def test_kind_wnba_strip_and_unlisted_sports_unchanged():
    assert LV._kind("KXWNBATEAMTOTAL-%s-LV8" % NSTEM) == "team_total"   # WNBA stripped before NBA (no NBA misparse)
    assert LV._kind("KXNBATEAMTOTAL-%s-LAL8" % NSTEM) == "team_total"
    assert LV._kind("KXEPLGAME-%s-ARS" % NSTEM) == "moneyline"          # unlisted sport -> generic GAME (as before)
    assert LV._kind("KXUCLTOTAL-%s-3" % NSTEM) == "total"               # unlisted sport -> generic TOTAL (as before)
    assert LV._kind("KXATPMATCH-26SEP22DJONAD").startswith("kx")        # no GAME/MONEY -> series.lower() fallback


def test_short_label_multidigit_strikes():
    assert LV._short_label("KXNFL1QSPREAD-%s-LAR12" % NSTEM, "q1_spread", "yes") == "-11.5 LAR"
    assert LV._short_label("KXNFL1QTOTAL-%s-105" % NSTEM, "q1_total", "yes") == "+104.5"
    assert LV._short_label("KXMLBTEAMTOTAL-%s-SD12" % MSTEM, "team_total", "yes") == "SD O 11.5"


def _mk(tk, yb=0.60):
    return MK.Mark(ticker=tk, yes_bid=yb, no_bid=None, yes_ask=None, no_ask=None, last=None,
                   status="active", as_of=NOW, title=None)


def _mlb_ctx_marks(tickers, marks):
    return LV.build_live_context(orders=[_o(t) for t in tickers], open_positions=[_p(t) for t in tickers],
                                 open_positions_by_whale=[_pw(t) for t in tickers], slate=None,
                                 marks_result=MK.MarksResult(marks=marks, ok=True, as_of=NOW, error=None),
                                 now_ts=NOW, category="mlb")


def test_two_same_kind_strikes_money_counts_both_and_coverage_is_position_based():
    # two DIFFERENT total strikes on one game (Over 7.5 + Over 8.5): they share the ONE 'total' fixed slot, so the
    # 2nd strike is LINELESS -- allowed. But BOTH must count in the money AND in the mark-coverage denominator, else
    # "N of M priced" would claim full coverage while an unpriced open position is silently absent from the value.
    t8 = "KXMLBTOTAL-%s-8" % MSTEM
    t9 = "KXMLBTOTAL-%s-9" % MSTEM
    ctx = _mlb_ctx_marks([t8, t9], {t8: _mk(t8, 0.60)})            # t8 priced, t9 unpriced
    c = ctx["cards"][0]
    assert c["slots_by_kind"]["TOT"] is not None and c["extra_lines"] == []   # one line; the 2nd strike is lineless
    assert c["open_cost"] == 10.0 and c["n_live"] == 2                         # BOTH positions counted in the money
    s = ctx["summary"]
    assert s["unsettled_cost"] == 10.0                                         # cost never drops the lineless leg
    assert s["unsettled_total"] == 2 and s["unsettled_priced"] == 1           # position-based -> honest "1 of 2"


def test_unrecognized_future_family_keeps_money_and_gets_a_line():
    # a family with no keyword (kind falls to series.lower()) still joins its game (the stem parses), COUNTS in the
    # money, and renders as an extra line -- a rendering gap can cost a LINE, never a MONEY figure.
    fut = "KXMLBWEATHER-%s-8" % MSTEM
    assert LV._kind(fut) == "kxmlbweather"                         # unrecognized -> series fallback, not a fixed kind
    c = _mlb_ctx(["KXMLBGAME-%s-SD" % MSTEM, fut])["cards"][0]
    assert any(s["ticker"] == fut for s in c["extra_lines"])       # it DID get a line (extra), not dropped
    assert c["open_cost"] == 10.0 and c["n_live"] == 2            # and it counts in the money
