"""Redesign assembler build_subdivisions_context (2026-09-10): the activity classifier per category (live-capable
vs date-only), the alarm-first + |today| sort (R5), R2 names that are NEVER a raw ticker, the orphan flag (R7),
and the honest event block. Pure/offline -- synthetic inputs, no DB, no server."""
import time
from types import SimpleNamespace

from trading_corp.prediction_markets.web import live_view
from trading_corp.prediction_markets.web import marks as marks_mod

NOW = int(time.time())
ACCTS = [{"account_id": "kalshi_jack", "account_label": "Jack (KALSHI)", "venue": "kalshi"}]


def _sub(cat, n_whales=1, n_live=0):
    return {"account_id": "kalshi_jack", "category": cat, "n_whales": n_whales, "n_live_trades": n_live,
            "account_label": "Jack (KALSHI)", "venue": "kalshi", "created_ts": 1, "sub_label": cat.upper()}


def _lv(state, age=5, sig=0, placed=0, err=0):
    return SimpleNamespace(state=state, age_sec=age, n_signals=sig, placed=placed, errors=err, ceiling_latched=0)


def _mark(ticker, bid=0.5, status="active", title=None):
    return marks_mod.Mark(ticker, yes_bid=bid, no_bid=0.4, yes_ask=0.6, no_ask=0.45, last=0.5, status=status,
                          as_of=NOW, title=title)


def _pos(ticker, leg="yes", contracts=2.0, cost=5.0):
    return {"ticker": ticker, "market_type": "moneyline", "held_leg": leg, "contracts": contracts,
            "cost_basis_usd": cost, "avg_price": cost / contracts, "fees_usd": 0.0}


def _build(subs, *, arm=None, liveness=None, pnl=None, windows=None, positions=None, marks=None,
           feed_games=None, viewer_role="admin", viewer_account=None, name_exceptions=None):
    arm_all = {"global": {"state": "armed", "ts": None},
               "subs": arm or {(s["account_id"], s["category"]): {"sub_state": "armed", "sub_ts": None,
                                                                   "effective_state": "armed"} for s in subs}}
    return live_view.build_subdivisions_context(
        subs=subs, accounts_meta=ACCTS, arm_all=arm_all, liveness_by_sub=liveness or {}, liveness_present=True,
        pnl_all=pnl or {}, realized_windows=windows or {}, positions_by_sub=positions or {},
        last_events={}, marks=marks or {}, feed_games=feed_games or {}, now_ts=NOW, thin_floor=50,
        mark_age_sec=11, active_account="kalshi_jack", viewer_role=viewer_role, viewer_account=viewer_account,
        logo_codes={"MLB", "CS2"}, poll_interval=60, global_arm={"state": "armed", "ts_age": 10},
        max_order_id=99, name_exceptions=name_exceptions)


def _tiles(ctx):
    out = {}
    for act, lst in ctx["sections"].items():
        for t in lst:
            out[t["code"]] = t
    for t in ctx["alarm"]:
        out[t["code"]] = t
    return out


def test_classifier_per_category():
    # cs2 (live-capable) with a PAST ticker start + non-finalized mark -> LIVE; lal (date-only) with open -> UPCOMING;
    # ucl history no-open -> SETTLED; bra attached no-history -> INACTIVE; fed unattached -> UNATTACHED
    cs2_tk = "KXCS2GAME-26SEP090000ACEBIG-ACE"   # 00:00 -> far in the past relative to NOW
    lal_tk = "KXLALIGAGAME-26SEP13GETDEP-GET"
    subs = [_sub("cs2"), _sub("lal"), _sub("ucl", n_live=4), _sub("bra"), _sub("fed", n_whales=0)]
    positions = {("kalshi_jack", "cs2"): [_pos(cs2_tk)], ("kalshi_jack", "lal"): [_pos(lal_tk)]}
    marks = {cs2_tk: _mark(cs2_tk, status="active", title="ACE wins"), lal_tk: _mark(lal_tk, title="Getafe wins")}
    pnl = {("kalshi_jack", "ucl"): {"realized": 7.9, "booked_closes": 4, "wins": 4, "losses": 0, "unbooked_closes": 0}}
    ctx = _build(subs, positions=positions, marks=marks, pnl=pnl,
                 liveness={(s["account_id"], s["category"]): _lv("RUNNING") for s in subs})
    t = _tiles(ctx)
    assert t["CS2"]["activity"] == "LIVE"
    assert t["LAL"]["activity"] == "UPCOMING"
    assert t["UCL"]["activity"] == "SETTLED"
    assert t["BRA"]["activity"] == "INACTIVE" and t["BRA"]["realized"] is None       # INACTIVE = no history block
    assert t["FED"]["activity"] == "UNATTACHED"
    # LIVE event block: positions valued at bid, cyan, never cost-as-value
    assert t["CS2"]["event"] is not None and t["CS2"]["event"]["positions"][0]["value_known"] is True


def test_upcoming_not_live_for_date_only_even_if_recent():
    # a soccer ticker has NO HHMM -> parse_ticker_start None -> never underway -> stays UPCOMING (honest, item 12)
    tk = "KXEPLGAME-26SEP09ARSCHE-ARS"
    ctx = _build([_sub("epl")], positions={("kalshi_jack", "epl"): [_pos(tk)]},
                 marks={tk: _mark(tk, title="Arsenal wins")}, liveness={("kalshi_jack", "epl"): _lv("RUNNING")})
    assert _tiles(ctx)["EPL"]["activity"] == "UPCOMING"


def test_alarm_first_and_today_sort():
    # armed + STALE -> alarm bucket (ahead of everything); within SETTLED, |today| desc then code
    subs = [_sub("mlb", n_live=4), _sub("atp", n_live=4), _sub("wta")]
    liveness = {("kalshi_jack", "mlb"): _lv("STALE", age=4000), ("kalshi_jack", "atp"): _lv("RUNNING"),
                ("kalshi_jack", "wta"): _lv("RUNNING")}
    pnl = {("kalshi_jack", "mlb"): {"realized": 1, "booked_closes": 5, "wins": 3, "losses": 2, "unbooked_closes": 0},
           ("kalshi_jack", "atp"): {"realized": 1, "booked_closes": 5, "wins": 2, "losses": 3, "unbooked_closes": 0},
           ("kalshi_jack", "wta"): {"realized": 1, "booked_closes": 5, "wins": 5, "losses": 0, "unbooked_closes": 0}}
    windows = {("kalshi_jack", "atp"): {"today": -8.0, "week": 0, "month": 0, "all_time": 1},
               ("kalshi_jack", "wta"): {"today": 2.0, "week": 0, "month": 0, "all_time": 1},
               ("kalshi_jack", "mlb"): {"today": 50.0, "week": 0, "month": 0, "all_time": 1}}
    ctx = _build(subs, liveness=liveness, pnl=pnl, windows=windows)
    assert [t["code"] for t in ctx["alarm"]] == ["MLB"]                 # the STALE+armed sub, pulled out first
    assert ctx["alarm_strip"][0]["code"] == "MLB"
    assert [t["code"] for t in ctx["sections"]["SETTLED"]] == ["ATP", "WTA"]   # |−8.0| > |2.0|, then code


def test_name_market_is_never_a_ticker():
    # a non-MLB position with NO mark title + no feed -> falls back to "<CAT> market", NOT the ticker; logged
    tk = "KXUFCFIGHT-26SEP05HOOPAR-HOO"
    exc = []
    ctx = _build([_sub("ufc")], positions={("kalshi_jack", "ufc"): [_pos(tk)]},
                 marks={tk: _mark(tk, title=None)}, liveness={("kalshi_jack", "ufc"): _lv("RUNNING")},
                 name_exceptions=exc)
    ne, ok = live_view.name_market(tk, "yes", None, None, "ufc")
    assert tk not in ne and ne == "UFC market" and ok is False
    assert exc and exc[0]["ticker"] == tk           # the R2 exception is collected for the report/log
    # with a Mark.title present, the name is the title (meaningful, not a ticker)
    n2, ok2 = live_view.name_market(tk, "yes", _mark(tk, title="Hooker wins"), None, "ufc")
    assert n2 == "Hooker wins" and ok2 is True


def test_orphan_flag_on_retired_category():
    ctx = _build([_sub("soccer", n_whales=0)])
    assert _tiles(ctx)["SOCCER"]["orphan"] is True


def test_viewer_role_and_tabs_passthrough():
    ctx = _build([_sub("mlb")], viewer_role="account", viewer_account="kalshi_jack")
    assert ctx["meta"]["viewer_role"] == "account" and ctx["meta"]["viewer_account"] == "kalshi_jack"
    assert [tab["id"] for tab in ctx["tabs"]] == ["kalshi_jack"]
