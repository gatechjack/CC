"""Unit tests for the live sub-division assembly (Scope A/F). Pure -- journal rows + slate + marks injected."""
from trading_corp.prediction_markets.web import live_view as lv
from trading_corp.prediction_markets.web import feed_mlb, marks as marks_mod

STEM = "26SEP021240SDCIN"
ML = "KXMLBGAME-%s-SD" % STEM
TOT = "KXMLBTOTAL-%s-O8.5" % STEM
SPR = "KXMLBSPREAD-%s-SD1.5" % STEM
GK = ("2026-09-02", "1240", None, frozenset({"San Diego Padres", "Cincinnati Reds"}))


def _order(**kw):
    base = {"id": None, "ticker": None, "order_side": "bid", "outcome_leg": "yes", "is_exit": 0,
            "submitted_count": 5, "submitted_price": None, "outcome_status": "filled", "fill_count": 5,
            "fill_price": None, "fee": 0.04, "submitted_ts": 100, "response_ts": 100, "close_source": None,
            "realized_pnl": None, "won": None, "settled_ts": None, "wallet": "0xw", "user_name": None}
    base.update(kw)
    return base


def _orders():
    return [
        _order(id=1, ticker=ML, submitted_price=0.53, fill_price=0.52, wallet="0xa", user_name="Kingfish"),
        _order(id=2, ticker=TOT, submitted_price=0.48, fill_price=0.47, wallet="0xb", user_name=None),
        _order(id=3, ticker=TOT, is_exit=1, close_source="settlement", realized_pnl=2.81, won=1,
               settled_ts=5000, response_ts=5000, wallet="0xb"),
        _order(id=4, ticker=SPR, submitted_price=0.55, fill_price=0.55, wallet="0xc", user_name="domer"),
        _order(id=5, ticker=SPR, is_exit=1, close_source="opposed", realized_pnl=None, won=None,
               response_ts=200, wallet="0xc"),
    ]


def _open_positions():   # live_positions: only the ML ticker is still net-held
    return [{"ticker": ML, "market_type": "moneyline", "held_leg": "yes", "contracts": 5,
             "cost_basis_usd": 2.60, "avg_price": 0.52, "fees_usd": 0.04}]


def _by_whale():
    return [{"ticker": ML, "wallet": "0xa"}]


def _live_slate(now_ts=6000):
    gs = feed_mlb.GameState(key=GK, date_iso="2026-09-02", hhmm_et="1240", game_no=None, source="statsapi",
                            fetched_ts=now_ts, game_pk="1", status="in_progress",
                            away=feed_mlb.TeamState("SD", "Padres", "71-66", 0),
                            home=feed_mlb.TeamState("CIN", "Reds", "68-69", 1),
                            inning=3, half="TOP", outs=1, balls=1, strikes=2, bases=(False, False, False),
                            linescore_away=(0, 0), linescore_home=(1, 0), last_play="Flyout to center.")
    return feed_mlb.SlateResult("2026-09-02", {GK: gs}, True, "statsapi", now_ts)


def _marks(now_ts=6000, yes_bid=0.58):
    return marks_mod.MarksResult(marks={ML: marks_mod.Mark(ML, yes_bid, 0.41, 0.60, 0.43, 0.59, "active", now_ts)},
                                 ok=True, as_of=now_ts)


# ── the ticker->game join for all three market types ─────────────────────────────────────────────────────────
def test_game_key_from_ticker_matches_feed_key_all_market_types():
    fk = feed_mlb.feed_game_key("2026-09-02", "San Diego Padres", "Cincinnati Reds", "1240", None)
    assert lv.game_key_from_ticker(ML) == fk
    assert lv.game_key_from_ticker(TOT) == fk
    assert lv.game_key_from_ticker(SPR) == fk


def test_split_blob_hard_cases():
    assert lv._split_team_blob("SEABOS") == ("SEA", "BOS")
    assert lv._split_team_blob("NYYLAA") == ("NYY", "LAA")
    assert lv._split_team_blob("CWSHOU") == ("CWS", "HOU")


# ── full assembly: the four terminal states on one card ─────────────────────────────────────────────────────
def test_card_open_settled_opposed_states():
    ctx = lv.build_live_context(orders=_orders(), open_positions=_open_positions(),
                                open_positions_by_whale=_by_whale(), slate=_live_slate(),
                                marks_result=_marks(), now_ts=6000)
    assert len(ctx["cards"]) == 1
    card = ctx["cards"][0]
    ml = card["slots_by_kind"]["ML"]
    tot = card["slots_by_kind"]["TOT"]
    spr = card["slots_by_kind"]["SPR"]
    assert ml and not ml["settled"] and ml["current_value"] == 5 * 0.58 and ml["value_known"]
    assert ml["cost"] == 2.60 and ml["bid"] == 0.58
    assert tot and tot["settled"] and tot["won"] is True and tot["realized"] == 2.81
    assert tot["current_value"] == 5 and tot["value_known"]        # settled payout = contracts x $1 (won)
    assert spr is None                       # opposed position comes off the CARD (it's in the drawer)
    assert card["mixed"] and card["n_settled"] == 1 and card["n_live"] == 1
    assert card["open_cost"] == 2.60 and card["open_value"] == 5 * 0.58 and not card["complete"]
    assert card["feed"]["available"] and card["feed"]["status"] == "in_progress"


def test_trades_cover_all_states_with_honest_realized():
    ctx = lv.build_live_context(orders=_orders(), open_positions=_open_positions(),
                                open_positions_by_whale=_by_whale(), slate=_live_slate(),
                                marks_result=_marks(), now_ts=6000)
    trades = {t["order_id"]: t for t in ctx["trades"]}
    assert set(trades) == {1, 2, 4}          # only entry fills are drawer rows
    assert trades[1]["status"] == "open" and trades[1]["value_now"] == 5 * 0.58
    assert trades[2]["status"] == "settled" and trades[2]["realized"] == 2.81 and trades[2]["realized_booked"]
    assert trades[4]["status"] == "opposed" and trades[4]["realized"] is None
    assert trades[4]["realized_booked"] is False   # opposed -> "not booked", never a guessed number
    assert trades[1]["whale_label"] == "Kingfish" and trades[2]["whale_label"] == "0xb"   # unknown -> wallet


def test_no_mark_degrades_value_never_zero():
    ctx = lv.build_live_context(orders=_orders(), open_positions=_open_positions(),
                                open_positions_by_whale=_by_whale(), slate=_live_slate(),
                                marks_result=marks_mod.MarksResult({}, True, 6000), now_ts=6000)
    ml = ctx["cards"][0]["slots_by_kind"]["ML"]
    assert ml["current_value"] is None and ml["value_known"] is False and ml["bid"] is None
    assert ctx["cards"][0]["value_known"] is False and ctx["cards"][0]["open_value"] is None


def test_feed_unavailable_renders_nothing_from_feed():
    empty = feed_mlb.SlateResult("2026-09-02", {}, False, None, 6000, error="down")
    ctx = lv.build_live_context(orders=_orders(), open_positions=_open_positions(),
                                open_positions_by_whale=_by_whale(), slate=empty,
                                marks_result=_marks(), now_ts=6000)
    feed = ctx["cards"][0]["feed"]
    assert feed["available"] is False and feed["status"] == "unavailable" and "unavailable" in feed["note"]


def test_completed_game_drops_after_retention():
    # every slot settled, last settlement 25h ago -> card dropped past the 24h window
    orders = [_order(id=2, ticker=TOT, submitted_price=0.48, fill_price=0.47, wallet="0xb"),
              _order(id=3, ticker=TOT, is_exit=1, close_source="settlement", realized_pnl=2.81, won=1,
                     settled_ts=1000, response_ts=1000, wallet="0xb")]
    now = 1000 + 25 * 3600
    ctx = lv.build_live_context(orders=orders, open_positions=[], open_positions_by_whale=[],
                                slate=feed_mlb.SlateResult("2026-09-02", {}, True, "statsapi", now),
                                marks_result=_marks(now), now_ts=now)
    assert ctx["cards"] == []                # dropped 24h after last settlement

    now2 = 1000 + 3 * 3600                    # 3h after settlement -> still shown, marked complete
    ctx2 = lv.build_live_context(orders=orders, open_positions=[], open_positions_by_whale=[],
                                 slate=feed_mlb.SlateResult("2026-09-02", {}, True, "statsapi", now2),
                                 marks_result=_marks(now2), now_ts=now2)
    assert len(ctx2["cards"]) == 1 and ctx2["cards"][0]["complete"] and ctx2["cards"][0]["drops_in_h"] == 21
