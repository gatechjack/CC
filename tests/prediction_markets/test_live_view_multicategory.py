"""Multi-category live-view tests (2026-09-04). Proves the defect fix without regressing MLB:

  * item 6 -- the MLB summary strip values are LOCKED for a fixture journal (regression guard) and the MLB context
    is byte-identical whether or not `category` is passed (the new arg cannot change MLB rendering);
  * item 1 -- a NON-MLB sub-division's TOTALS (at-cost / count / value+coverage / realized-today / settled-today)
    come from the JOURNAL, never the sport parser, so the strip is no longer 0 while the drawer holds a trade;
  * item 2 -- a non-MLB category renders a positions VIEW (active/complete rows with desc/side/contracts/cost/
    value/status/whale), with the market title from the mark when present, and 'no mark' (never $0) when unpriced;
  * item 3 -- marks.series_from_tickers / subdivision.traded_series derive the poll series from held tickers, and
    the poller threads a series_provider into fetch_marks (fail-safe to MLB).
"""
import calendar
from datetime import datetime, timezone

import pytest

from trading_corp.prediction_markets.web import live_view as lv
from trading_corp.prediction_markets.web import feed_mlb, marks as marks_mod, poller, ui_cache


# ── shared MLB fixture (mirrors test_live_view, but pins a real ET 'today' so realized/settled-today are exercised)
def _et_now(y, m, d, hh, mm):
    return calendar.timegm(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).utctimetuple())


MLB_NOW = _et_now(2026, 9, 2, 16, 40)          # 12:40 EDT Sep 2 -> _et_date == 2026-09-02 (the card's game date)
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


def _mlb_orders():
    return [
        _order(id=1, ticker=ML, submitted_price=0.53, fill_price=0.52, wallet="0xa", user_name="Kingfish"),
        _order(id=2, ticker=TOT, submitted_price=0.48, fill_price=0.47, wallet="0xb"),
        _order(id=3, ticker=TOT, is_exit=1, close_source="settlement", realized_pnl=2.81, won=1,
               settled_ts=MLB_NOW, response_ts=MLB_NOW, wallet="0xb", fill_count=5),
        _order(id=4, ticker=SPR, submitted_price=0.55, fill_price=0.55, wallet="0xc", user_name="domer"),
        _order(id=5, ticker=SPR, is_exit=1, close_source="opposed", realized_pnl=None, won=None,
               response_ts=200, wallet="0xc"),
    ]


def _mlb_open():
    return [{"ticker": ML, "market_type": "moneyline", "held_leg": "yes", "contracts": 5,
             "cost_basis_usd": 2.60, "avg_price": 0.52, "fees_usd": 0.04}]


def _mlb_by_whale():
    return [{"ticker": ML, "wallet": "0xa", "user_name": "Kingfish"}]


def _mlb_slate(now_ts):
    gs = feed_mlb.GameState(key=GK, date_iso="2026-09-02", hhmm_et="1240", game_no=None, source="statsapi",
                            fetched_ts=now_ts, game_pk="1", status="in_progress",
                            away=feed_mlb.TeamState("SD", "Padres", "71-66", 0),
                            home=feed_mlb.TeamState("CIN", "Reds", "68-69", 1),
                            inning=3, half="TOP", outs=1, balls=1, strikes=2, bases=(False, False, False),
                            linescore_away=(0, 0), linescore_home=(1, 0), last_play="Flyout to center.")
    return feed_mlb.SlateResult("2026-09-02", {GK: gs}, True, "statsapi", now_ts)


def _mlb_marks(now_ts, yes_bid=0.58):
    return marks_mod.MarksResult(marks={ML: marks_mod.Mark(ML, yes_bid, 0.41, 0.60, 0.43, 0.59, "active", now_ts)},
                                 ok=True, as_of=now_ts)


# ── item 6: LOCK the MLB summary strip values (the numbers the operator reads) ────────────────────────────────
def test_mlb_summary_strip_values_locked():
    ctx = lv.build_live_context(orders=_mlb_orders(), open_positions=_mlb_open(),
                                open_positions_by_whale=_mlb_by_whale(), slate=_mlb_slate(MLB_NOW),
                                marks_result=_mlb_marks(MLB_NOW), now_ts=MLB_NOW, category="mlb")
    s = ctx["summary"]
    assert ctx["mode"] == "mlb_cards" and ctx["category"] == "mlb"
    assert s["n_active"] == 1 and s["n_complete"] == 0        # SD@CIN, one live slot -> active card
    assert s["unsettled_cost"] == 2.60                         # ML cost only (settled/opposed excluded)
    assert s["unsettled_value"] == 5 * 0.58 and s["unsettled_value_known"] is True
    assert s["unsettled_priced"] == 1 and s["unsettled_total"] == 1
    assert s["realized_today"] == 2.81 and s["settled_today"] == 1   # TOT settled today (game date == ET today)
    assert s["has_game_feed"] is True and s["n_open_positions"] == 1


def test_mlb_context_byte_identical_with_and_without_category():
    """The `category` arg must not change MLB rendering: the whole context is identical apart from the echoed
    `category` field (None vs 'mlb'). Proves item 6 at the whole-context level, not just the strip."""
    common = dict(orders=_mlb_orders(), open_positions=_mlb_open(), open_positions_by_whale=_mlb_by_whale(),
                  slate=_mlb_slate(MLB_NOW), marks_result=_mlb_marks(MLB_NOW), now_ts=MLB_NOW)
    a = lv.build_live_context(**common)                 # legacy caller: no category -> MLB detected from tickers
    b = lv.build_live_context(**common, category="mlb")
    assert a["mode"] == b["mode"] == "mlb_cards"
    a2 = dict(a); b2 = dict(b)
    a2.pop("category"); b2.pop("category")
    assert a2 == b2                                      # everything but the echoed category is identical


# ── item 1/2: a non-MLB sub-division -- totals from the journal, a positions view, no games ───────────────────
ATP_NOW = _et_now(2026, 9, 3, 18, 0)
HAL = "KXATPMATCH-26SEP03ZVEHAL-HAL"          # open
DJO = "KXATPMATCH-26SEP03ALCDJO-DJO"          # settled won today


def _atp_orders():
    return [
        _order(id=10, ticker=HAL, fill_price=0.10, fee=0.01, wallet="0x64", user_name="STC14",
               submitted_ts=ATP_NOW - 3600, response_ts=ATP_NOW - 3600),
        _order(id=11, ticker=DJO, fill_price=0.40, fee=0.02, wallet="0x77", user_name="ClayKing",
               submitted_ts=ATP_NOW - 7200, response_ts=ATP_NOW - 7200),
        _order(id=12, ticker=DJO, is_exit=1, close_source="settlement", realized_pnl=3.0, won=1,
               fill_count=5, settled_ts=ATP_NOW, response_ts=ATP_NOW, wallet="0x77", user_name="ClayKing"),
    ]


def _atp_open():
    return [{"ticker": HAL, "market_type": "moneyline", "held_leg": "yes", "contracts": 5,
             "cost_basis_usd": 0.50, "avg_price": 0.10, "fees_usd": 0.01}]


def _atp_by_whale():
    return [{"ticker": HAL, "wallet": "0x64", "user_name": "STC14"}]


def _atp_marks(now_ts, *, title="Zverev vs Halys", yes_bid=0.12):
    return marks_mod.MarksResult(
        marks={HAL: marks_mod.Mark(HAL, yes_bid, 0.87, 0.13, 0.88, 0.12, "active", now_ts, title=title)},
        ok=True, as_of=now_ts)


def test_non_mlb_totals_from_journal_not_parser():
    ctx = lv.build_live_context(orders=_atp_orders(), open_positions=_atp_open(),
                                open_positions_by_whale=_atp_by_whale(), slate=_mlb_slate(ATP_NOW),
                                marks_result=_atp_marks(ATP_NOW), now_ts=ATP_NOW, category="atp")
    assert ctx["mode"] == "positions" and ctx["category"] == "atp"
    assert ctx["cards"] == []                                   # no game cards for a non-MLB category
    s = ctx["summary"]
    assert s["has_game_feed"] is False and s["n_active"] == 0 and s["n_complete"] == 0
    assert s["n_open_positions"] == 1                           # <-- the defect: this was 0 while the drawer held HAL
    assert s["unsettled_cost"] == 0.50                          # from live_positions, not the (empty) card sum
    assert s["unsettled_value"] == 5 * 0.12 and s["unsettled_priced"] == 1 and s["unsettled_total"] == 1
    assert s["realized_today"] == 3.0 and s["settled_today"] == 1   # DJO settled today, journal-derived


def test_non_mlb_positions_view_rows():
    ctx = lv.build_live_context(orders=_atp_orders(), open_positions=_atp_open(),
                                open_positions_by_whale=_atp_by_whale(), slate=_mlb_slate(ATP_NOW),
                                marks_result=_atp_marks(ATP_NOW), now_ts=ATP_NOW, category="atp")
    pv = ctx["positions_view"]
    assert pv["n_active"] == 1 and pv["n_complete"] == 1
    a = pv["active"][0]
    assert a["ticker"] == HAL and a["desc"] == "Zverev vs Halys"      # market TITLE from the mark, not type:ticker
    assert a["held_leg"] == "yes" and a["contracts"] == 5 and a["cost"] == 0.50
    assert a["current_value"] == 5 * 0.12 and a["value_known"] is True and a["status"] == "open"
    assert a["whales"] == ["STC14"]
    c = pv["complete"][0]
    assert c["ticker"] == DJO and c["status"] == "settled" and c["won"] is True
    assert c["current_value"] == 5 and c["realized"] == 3.0           # payout = contracts x $1 (won)


def test_non_mlb_unpriced_is_no_mark_never_zero():
    ctx = lv.build_live_context(orders=_atp_orders(), open_positions=_atp_open(),
                                open_positions_by_whale=_atp_by_whale(), slate=_mlb_slate(ATP_NOW),
                                marks_result=marks_mod.MarksResult({}, True, ATP_NOW), now_ts=ATP_NOW,
                                category="atp")
    a = ctx["positions_view"]["active"][0]
    assert a["current_value"] is None and a["value_known"] is False   # never a $0 that means 'unpriced'
    s = ctx["summary"]
    assert s["unsettled_value"] is None and s["unsettled_priced"] == 0 and s["unsettled_total"] == 1


def test_non_mlb_desc_falls_back_to_market_describe_without_title():
    ctx = lv.build_live_context(orders=_atp_orders(), open_positions=_atp_open(),
                                open_positions_by_whale=_atp_by_whale(), slate=_mlb_slate(ATP_NOW),
                                marks_result=_atp_marks(ATP_NOW, title=None), now_ts=ATP_NOW, category="atp")
    a = ctx["positions_view"]["active"][0]
    assert a["market_title"] is None and HAL in a["desc"]             # honest type:ticker fallback, never blank


# ── item 3: series derivation + poller wiring ────────────────────────────────────────────────────────────────
def test_series_from_tickers_distinct_prefixes():
    got = marks_mod.series_from_tickers([HAL, DJO, ML, "KXUFCFIGHT-26SEP06X-Y", None, ""])
    assert got == ("KXATPMATCH", "KXMLBGAME", "KXUFCFIGHT")           # distinct, sorted, junk dropped
    assert marks_mod.series_from_tickers([]) == tuple(marks_mod.MLB_SERIES)   # empty -> MLB default (cold start)


def test_parse_markets_carries_title():
    page = {"markets": [{"ticker": HAL, "yes_bid_dollars": "0.12", "no_bid_dollars": "0.87",
                         "status": "active", "title": "Zverev vs Halys"}]}
    m = marks_mod.parse_markets(page, now_ts=1)[0]
    assert m.title == "Zverev vs Halys" and m.yes_bid == 0.12


def _capturing_fetch_marks(captured):
    def fm(series=marks_mod.MLB_SERIES, *, now_ts):
        captured["series"] = tuple(series)
        return marks_mod.MarksResult(marks={}, ok=True, as_of=now_ts)
    return fm


def test_poller_threads_series_provider_to_fetch_marks():
    c = ui_cache.UICache(); captured = {}
    poller.refresh_once(c, now_ts=1000, fetch_slate=lambda d, now_ts: feed_mlb.SlateResult(d, {}, True, "x", now_ts),
                        fetch_marks=_capturing_fetch_marks(captured), enrich=False,
                        series_provider=lambda: ("KXATPMATCH", "KXUFCFIGHT"))
    assert captured["series"] == ("KXATPMATCH", "KXUFCFIGHT")         # the held series, not hardcoded MLB


def test_poller_series_provider_failure_falls_back_to_mlb():
    c = ui_cache.UICache(); captured = {}
    def boom():
        raise RuntimeError("db locked")
    poller.refresh_once(c, now_ts=1000, fetch_slate=lambda d, now_ts: feed_mlb.SlateResult(d, {}, True, "x", now_ts),
                        fetch_marks=_capturing_fetch_marks(captured), enrich=False, series_provider=boom)
    assert captured["series"] == tuple(marks_mod.MLB_SERIES)          # fail-safe: MLB default
    assert "series:" in (c.snapshot().last_error or "")


def test_poller_empty_series_falls_back_to_mlb():
    c = ui_cache.UICache(); captured = {}
    poller.refresh_once(c, now_ts=1000, fetch_slate=lambda d, now_ts: feed_mlb.SlateResult(d, {}, True, "x", now_ts),
                        fetch_marks=_capturing_fetch_marks(captured), enrich=False, series_provider=lambda: ())
    assert captured["series"] == tuple(marks_mod.MLB_SERIES)          # nothing held -> MLB default primes the slate
