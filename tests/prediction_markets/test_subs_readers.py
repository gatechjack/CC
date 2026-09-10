"""Redesign readers (2026-09-10): ET-calendar realized windows, last trade/close, the events pulse, the ticker
start parse, and the ET window boundaries incl. DST. Pure/offline -- a temp PM DB + direct inserts, no server."""
import sqlite3
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_corp.prediction_markets import db, subdivision
from trading_corp.prediction_markets.web import live_view

JACK = "kalshi_jack"
_ET = ZoneInfo("America/New_York")


def _pm(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_account(account_id,venue,label,active,created_ts) VALUES(?,?,?,1,1)",
                     (JACK, "kalshi", "Jack (KALSHI)"))
        conn.execute("INSERT INTO pm_subdivision(account_id,category,label,market_types,sizing_mode,fixed_stake_usd,active,created_ts)"
                     " VALUES(?,?,?,?, 'fixed',5.0,1,1)", (JACK, "mlb", "Jack MLB", "moneyline"))
    return p


def _order(conn, *, cat="mlb", ticker="KXMLBGAME-26SEP091835CLEBAL-CLE", is_exit=0, realized=None, won=None,
           close_source=None, settled_ts=None, response_ts=None, submitted_ts=None, dry_run=0, leg="yes"):
    conn.execute(
        "INSERT INTO pm_subdivision_order(account_id,category,ticker,outcome_leg,is_exit,outcome_status,"
        "fill_count,fill_price,dry_run,submitted_ts,response_ts,close_source,realized_pnl,won,settled_ts)"
        " VALUES(?,?,?,?,?, 'filled', 1,0.5,?,?,?,?,?,?,?)",
        (JACK, cat, ticker, leg, is_exit, dry_run, submitted_ts, response_ts, close_source, realized, won, settled_ts))


def test_realized_windows_all_calendar_and_ties_out(monkeypatch, tmp_path):
    p = _pm(monkeypatch, tmp_path)
    now = int(time.time())
    cutoffs = {"today": now - 3600, "week": now - 5 * 86400, "month": now - 20 * 86400}
    with db.connect(p) as conn:
        _order(conn, is_exit=1, realized=3.0, won=1, close_source="settlement", settled_ts=now - 600)          # today
        _order(conn, is_exit=1, realized=-1.0, won=0, close_source="settlement", settled_ts=now - 2 * 86400)   # this week
        _order(conn, is_exit=1, realized=2.5, won=1, close_source="settlement", settled_ts=now - 10 * 86400)   # this month
        _order(conn, is_exit=1, realized=4.0, close_source="whale_exit", response_ts=now - 600)                # booked exit, today, won=NULL
        _order(conn, is_exit=1, realized=None, close_source="opposed", response_ts=now - 300)                  # UNBOOKED (excluded)
        conn.commit()
        w = subdivision.realized_windows_all(conn, cutoffs)[(JACK, "mlb")]
        pnl = subdivision.subdivision_pnl_all(conn)[(JACK, "mlb")]
    assert round(w["today"], 2) == 7.0        # 3.0 settlement + 4.0 booked exit (both anchored today); opposed excluded
    assert round(w["week"], 2) == 6.0         # + (-1.0)
    assert round(w["month"], 2) == 8.5        # + 2.5
    assert round(w["all_time"], 2) == 8.5     # no lower bound == booked total
    assert round(pnl["realized"], 2) == 8.5   # TIES OUT to the account-page reader (same booked definition)
    assert pnl["unbooked_closes"] == 1        # the opposed close counted separately, never in realized


def test_et_window_cutoffs_week_is_monday_and_dst_correct(monkeypatch, tmp_path):
    # a summer (EDT) instant and a winter (EST) instant both resolve to local ET midnight / Monday
    summer = int(datetime(2026, 7, 15, 18, 0, tzinfo=_ET).timestamp())   # Wed
    c = live_view.et_window_cutoffs(summer)
    today_et = datetime.fromtimestamp(c["today"], tz=timezone.utc).astimezone(_ET)
    week_et = datetime.fromtimestamp(c["week"], tz=timezone.utc).astimezone(_ET)
    assert (today_et.hour, today_et.minute) == (0, 0)
    assert week_et.weekday() == 0 and (week_et.hour, week_et.minute) == (0, 0)   # Monday 00:00 ET
    winter = int(datetime(2026, 1, 14, 18, 0, tzinfo=_ET).timestamp())
    cw = live_view.et_window_cutoffs(winter)
    tew = datetime.fromtimestamp(cw["today"], tz=timezone.utc).astimezone(_ET)
    assert (tew.hour, tew.minute) == (0, 0)   # still ET midnight despite EST/EDT offset difference


def test_parse_ticker_start_by_category():
    # live-capable + HHMM present -> a ts; date-only or no-HHMM -> None (item 10)
    assert live_view.parse_ticker_start("mlb", "KXMLBGAME-26SEP091835CLEBAL-CLE") is not None
    assert live_view.parse_ticker_start("cs2", "KXCS2GAME-26SEP090700ACE3DMAX-3DMAX") is not None
    assert live_view.parse_ticker_start("lal", "KXLALIGAGAME-26SEP13GETDEP-GET") is None      # soccer: date only
    assert live_view.parse_ticker_start("atp", "KXATPMATCH-26SEP09ZVEVAN-VAN") is None         # tennis: date only
    assert live_view.parse_ticker_start("nba", "KXNBAGAME-26SEP09LALBOS-LAL") is None           # structural, HHMM omitted
    # the parsed MLB start is BEFORE the ET calendar 'today' of the next day (26SEP09 18:35 ET)
    st = live_view.parse_ticker_start("mlb", "KXMLBGAME-26SEP091835CLEBAL-CLE")
    et = datetime.fromtimestamp(st, tz=timezone.utc).astimezone(_ET)
    assert (et.year, et.month, et.day, et.hour, et.minute) == (2026, 9, 9, 18, 35)


def test_last_events_all(monkeypatch, tmp_path):
    p = _pm(monkeypatch, tmp_path)
    now = int(time.time())
    with db.connect(p) as conn:
        _order(conn, is_exit=0, submitted_ts=now - 1000)                                                # an entry
        _order(conn, is_exit=1, realized=-1.0, won=0, close_source="settlement", settled_ts=now - 5000)  # older close
        _order(conn, is_exit=1, realized=2.26, won=1, close_source="settlement", settled_ts=now - 500)   # newest close
        conn.commit()
        le = subdivision.last_events_all(conn)[(JACK, "mlb")]
    assert le["last_close"]["won"] == 1 and round(le["last_close"]["realized"], 2) == 2.26     # newest by anchor
    assert le["last_trade"] is not None


def test_events_since_splits_and_advances(monkeypatch, tmp_path):
    p = _pm(monkeypatch, tmp_path)
    now = int(time.time())
    with db.connect(p) as conn:
        _order(conn, is_exit=0, submitted_ts=now)                                        # placed
        _order(conn, is_exit=1, realized=2.26, won=1, close_source="settlement", settled_ts=now)  # closed won
        _order(conn, is_exit=1, realized=None, close_source="opposed", response_ts=now)  # unbooked close -> NOT an event
        conn.commit()
        ev = subdivision.events_since(conn, 0)
    assert len(ev["placed"]) == 1 and len(ev["closed"]) == 1
    assert ev["closed"][0]["won"] == 1
    assert ev["max_id"] >= 3
    with db.connect(p) as conn:
        assert subdivision.events_since(conn, ev["max_id"]) == {"max_id": ev["max_id"], "placed": [], "closed": []}
