"""Pre-deploy additions (2026-09-02) -- game DATE/TIME on the card, honest PRE-GAME state, directional bet-slot
shorthand, and the separated toggle. Pure PM-package assembly tests (build_live_context / _feed_block /
_short_label / _fmt_et_datetime) plus one TestClient render for the template labels. No engine, no network.

  item 1 -- start line 'Wed Sep 2 . 6:40 PM ET' from the ticker (renders feed-down); feed<->ticker time
            mismatch is flagged, never silently.
  item 2 -- pre-game is 'not started' with NO score digits (not '0-0', not 'game over'); Postponed / Suspended /
            Delayed are their own labels.
  item 3 -- TOTAL shows over/under as a sign ('+8.5' / '-8.5'); SPREAD shows sign + team ('-1.5 ATL' / '+1.5 SD').
  item 4 -- the two Active/Complete toggles render as separate segmented anchors.
"""
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db
from trading_corp.prediction_markets.web import feed_mlb as F, live_view as LV, marks as MK

NOW = 1788366000
DATE = "2026-09-02"


# ── item 3: directional bet-slot shorthand ────────────────────────────────────────────────────────────────────
TOT = "KXMLBTOTAL-26SEP021840SDCIN-9"        # N=9 -> strike 8.5
SPR = "KXMLBSPREAD-26SEP021840SDCIN-SD2"     # SD anchor, N=2 -> strike 1.5 ; other = CIN


def test_short_label_total_over_and_under():
    assert LV._short_label(TOT, "total", "yes") == "+8.5"     # Over (YES)
    assert LV._short_label(TOT, "total", "no") == "-8.5"      # Under (NO)
    assert LV._short_label(TOT, "total", None) == "8.5"       # settled -> line only, no fabricated side


def test_short_label_spread_plus_and_minus():
    assert LV._short_label(SPR, "spread", "yes") == "-1.5 SD"     # anchor lays the spread (favourite)
    assert LV._short_label(SPR, "spread", "no") == "+1.5 CIN"     # the OTHER team gets +strike (underdog)
    assert LV._short_label(SPR, "spread", None) == "-1.5 SD"      # settled -> the market's anchor line


def test_short_label_moneyline_unchanged():
    assert LV._short_label("KXMLBGAME-26SEP021840SDCIN-SD", "moneyline", "yes") == "SD"


def test_settled_leg_derivation():
    def mk(leg, ex=0, st="filled"):
        return {"outcome_leg": leg, "is_exit": ex, "outcome_status": st}
    assert LV._settled_leg([mk("yes"), mk("yes", ex=1)]) == "yes"    # entry leg; the exit is ignored
    assert LV._settled_leg([mk("no")]) == "no"
    assert LV._settled_leg([mk("yes"), mk("no")]) is None            # entries on BOTH legs -> ambiguous, no sign
    assert LV._settled_leg([mk("yes", st="pending")]) is None        # no FILLED entry records a leg
    assert LV._settled_leg([]) is None


# ── item 1: scheduled date/time formatting ────────────────────────────────────────────────────────────────────
def test_fmt_et_datetime_shapes():
    assert LV._fmt_et_datetime("2026-09-02", "1840") == "Wed Sep 2 · 6:40 PM ET"
    assert LV._fmt_et_datetime("2026-09-02", "0905") == "Wed Sep 2 · 9:05 AM ET"
    assert LV._fmt_et_datetime("2026-09-02", "1200") == "Wed Sep 2 · 12:00 PM ET"   # noon
    assert LV._fmt_et_datetime("2026-09-02", "0000") == "Wed Sep 2 · 12:00 AM ET"   # midnight
    assert LV._fmt_et_datetime("2026-09-02", None) == "Wed Sep 2"                         # date only, feed-down safe
    assert LV._fmt_et_datetime(None, None) is None


# ── item 2: _feed_block status + started semantics ────────────────────────────────────────────────────────────
def _gs(status, away="SD", home="CIN", hhmm="1840", la=(), lh=(), ascore=None, hscore=None, inning=None, half=None):
    an, hn = F.canonical_team(away), F.canonical_team(home)
    key = F.feed_game_key(DATE, an, hn, hhmm, None)
    return F.GameState(key=key, date_iso=DATE, hhmm_et=hhmm, game_no=None, source="statsapi",
                       fetched_ts=NOW, game_pk=None, status=status,
                       away=F.TeamState(away, away, None, ascore), home=F.TeamState(home, home, None, hscore),
                       inning=inning, half=half, outs=None, balls=None, strikes=None, bases=(),
                       linescore_away=la, linescore_home=lh, last_play=None)


def test_feed_block_status_per_state():
    for st in ("preview", "in_progress", "final", "postponed", "suspended", "delayed"):
        fb = LV._feed_block(_gs(st), NOW)
        assert fb["available"] is True and fb["status"] == st
    fb = LV._feed_block(None, NOW)
    assert fb["available"] is False and fb["status"] == "unavailable" and fb["started"] is False


def test_pregame_not_started_even_if_feed_reports_zero_zero():
    # the exact screenshot bug: StatsAPI hands a pre-game 0-0 -> we must NOT treat it as started (no score digits).
    fb = LV._feed_block(_gs("preview", ascore=0, hscore=0), NOW)
    assert fb["status"] == "preview" and fb["started"] is False
    # postponed / delayed that never began are also not started
    assert LV._feed_block(_gs("postponed"), NOW)["started"] is False
    assert LV._feed_block(_gs("delayed"), NOW)["started"] is False


def test_started_when_play_has_happened():
    assert LV._feed_block(_gs("in_progress", inning=3, half="TOP"), NOW)["started"] is True
    assert LV._feed_block(_gs("final", la=(1, 0, 0), lh=(0, 0, 0)), NOW)["started"] is True
    # a SUSPENDED game carries a partial score -> it HAS started
    assert LV._feed_block(_gs("suspended", la=(2, 0), lh=(0, 1)), NOW)["started"] is True


# ── items 1/2/3 through build_live_context (pure assembly) ────────────────────────────────────────────────────
def _order(ticker, coid):
    return {"id": coid, "ticker": ticker, "wallet": "0xw", "user_name": "whale", "outcome_leg": "yes",
            "is_exit": 0, "outcome_status": "filled", "fill_count": 10.0, "fill_price": 0.52,
            "submitted_price": 0.55, "fee": 0.0, "response_ts": NOW, "submitted_ts": NOW}


def _open(ticker):
    return {"ticker": ticker, "wallet": "0xw", "held_leg": "yes", "contracts": 10.0,
            "cost_basis_usd": 5.2, "avg_price": 0.52, "fees_usd": 0.0}


def _ctx(specs):
    """specs: list of (ticker, GameState-or-None). Returns build_live_context over those games (one open TOT
    position each), with marks pricing every ticker."""
    orders, opens, by_whale, games, marks = [], [], [], {}, {}
    for i, (tk, gs) in enumerate(specs):
        orders.append(_order(tk, i + 1))
        opens.append(_open(tk))
        by_whale.append({"ticker": tk, "wallet": "0xw"})
        marks[tk] = MK.Mark(tk, yes_bid=0.60, no_bid=0.38, yes_ask=0.62, no_ask=0.40, last=0.59, status="a", as_of=NOW)
        if gs is not None:
            games[gs.key] = gs
    slate = F.SlateResult(DATE, games, True, "statsapi", NOW)
    return LV.build_live_context(orders=orders, open_positions=opens, open_positions_by_whale=by_whale,
                                 slate=slate, marks_result=MK.MarksResult(marks=marks, ok=True, as_of=NOW),
                                 now_ts=NOW)


def _card_for(ctx, away, home):
    an, hn = F.canonical_team(away), F.canonical_team(home)
    want = frozenset({an, hn})
    return next(c for c in ctx["cards"] if frozenset(c["key"][3]) == want)


def test_card_start_display_present_including_feed_down():
    # a game WITH a feed and one WITHOUT (feed-unavailable) both carry the ticker-sourced start line.
    gk_gs = _gs("in_progress", inning=2, half="TOP")
    ctx = _ctx([("KXMLBTOTAL-26SEP021840SDCIN-9", gk_gs),
                ("KXMLBTOTAL-26SEP022210NYYLAA-9", None)])            # no GameState -> feed unavailable
    live = _card_for(ctx, "SD", "CIN")
    dead = _card_for(ctx, "NYY", "LAA")
    assert live["start_display"] == "Wed Sep 2 · 6:40 PM ET"
    assert dead["feed"]["available"] is False
    assert dead["start_display"] == "Wed Sep 2 · 10:10 PM ET"    # item 1: renders even feed-down
    # item 3: the TOT slot on each card carries direction
    assert live["slots_by_kind"]["TOT"]["short"] == "+8.5"


def test_time_mismatch_flagged_when_feed_time_differs():
    # feed says 20:10, ticker says 20:07 -> card shows the feed's time, mismatch recorded for the drawer.
    an, hn = F.canonical_team("HOU"), F.canonical_team("SEA")
    key = F.feed_game_key(DATE, an, hn, "2010", None)
    gs = F.GameState(key=key, date_iso=DATE, hhmm_et="2010", game_no=None, source="statsapi", fetched_ts=NOW,
                     game_pk=None, status="in_progress", away=F.TeamState("HOU", "HOU", None, 1),
                     home=F.TeamState("SEA", "SEA", None, 0), inning=1, half="TOP", outs=None, balls=None,
                     strikes=None, bases=(), linescore_away=(1,), linescore_home=(0,), last_play=None)
    ctx = _ctx([("KXMLBTOTAL-26SEP022007HOUSEA-9", gs)])
    card = ctx["cards"][0]
    assert card["start_display"] == "Wed Sep 2 · 8:10 PM ET"     # the FEED's time
    assert card["time_mismatch"] is not None
    assert card["time_mismatch"]["ticker"] == "Wed Sep 2 · 8:07 PM ET"
    assert card["time_mismatch"]["feed"] == "Wed Sep 2 · 8:10 PM ET"
    # and the drawer trade row carries the same mismatch (so it is flagged, never silent)
    assert ctx["trades"] and ctx["trades"][0]["time_mismatch"] is not None


def test_no_mismatch_when_feed_matches_ticker():
    gs = _gs("in_progress", inning=1, half="TOP")
    ctx = _ctx([("KXMLBTOTAL-26SEP021840SDCIN-9", gs)])
    assert ctx["cards"][0]["time_mismatch"] is None


# ── step 0: a SETTLED slot carries the same directional shorthand as a live slot ──────────────────────────────
def _settled_orders(ticker, leg, won=1, realized=4.8):
    entry = {"id": 1, "ticker": ticker, "wallet": "0xw", "user_name": "w", "outcome_leg": leg, "is_exit": 0,
             "outcome_status": "filled", "fill_count": 10.0, "fill_price": 0.52, "submitted_price": 0.55,
             "fee": 0.0, "response_ts": NOW, "submitted_ts": NOW}
    settle = {"id": 2, "ticker": ticker, "wallet": "0xw", "user_name": "w", "outcome_leg": leg, "is_exit": 1,
              "close_source": "settlement", "won": won, "realized_pnl": realized, "outcome_status": "filled",
              "fill_count": 10.0, "fill_price": 1.0 if won else 0.0, "settled_ts": NOW, "response_ts": NOW}
    return [entry, settle]


def _settled_ctx(orders):
    return LV.build_live_context(orders=orders, open_positions=[], open_positions_by_whale=[],
                                 slate=F.SlateResult(DATE, {}, True, "statsapi", NOW),
                                 marks_result=MK.MarksResult(marks={}, ok=True, as_of=NOW), now_ts=NOW)


def test_settled_total_slot_carries_over_under_sign():
    ctx = _settled_ctx(_settled_orders("KXMLBTOTAL-26SEP021840SDCIN-9", "yes"))     # held Over
    slot = ctx["cards"][0]["slots_by_kind"]["TOT"]
    assert slot["settled"] is True and slot["short"] == "+8.5"      # SAME directional label as a live Over slot
    ctx2 = _settled_ctx(_settled_orders("KXMLBTOTAL-26SEP021840SDCIN-9", "no", won=0))
    assert ctx2["cards"][0]["slots_by_kind"]["TOT"]["short"] == "-8.5"   # held Under, lost -> still labelled Under


def test_settled_spread_slot_carries_sign_and_team():
    ctx = _settled_ctx(_settled_orders("KXMLBSPREAD-26SEP021840SDCIN-SD2", "yes"))
    assert ctx["cards"][0]["slots_by_kind"]["SPR"]["short"] == "-1.5 SD"      # anchor lays the spread
    ctx2 = _settled_ctx(_settled_orders("KXMLBSPREAD-26SEP021840SDCIN-SD2", "no"))
    assert ctx2["cards"][0]["slots_by_kind"]["SPR"]["short"] == "+1.5 CIN"    # the other team gets +strike


def test_settled_slot_without_recorded_leg_shows_no_sign():
    # entries on BOTH legs -> the held side is genuinely ambiguous -> the settled line shows WITHOUT a sign.
    orders = _settled_orders("KXMLBTOTAL-26SEP021840SDCIN-9", "yes")
    orders.append({"id": 3, "ticker": "KXMLBTOTAL-26SEP021840SDCIN-9", "wallet": "0xw", "outcome_leg": "no",
                   "is_exit": 0, "outcome_status": "filled", "fill_count": 1.0, "fill_price": 0.4,
                   "submitted_price": 0.4, "fee": 0.0, "response_ts": NOW, "submitted_ts": NOW})
    slot = _settled_ctx(orders)["cards"][0]["slots_by_kind"]["TOT"]
    assert slot["short"] == "8.5"      # line only, no fabricated direction


# ── items 1/2/4 through the rendered template ─────────────────────────────────────────────────────────────────
def _render_client(monkeypatch, tmp_path, specs):
    from trading_corp.prediction_markets.web import ui_cache
    p = str(tmp_path / "pm.db"); monkeypatch.setenv("PM_DB_PATH", p); monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    db.init_db(p)
    games, marks = {}, {}
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_account (account_id,venue,secret_ref,label,active,created_ts) "
                     "VALUES ('kalshi_jack','kalshi','KALSHI','Jack',1,1787000000)")
        conn.execute("INSERT INTO pm_subdivision (account_id,category,label,market_types,sizing_mode,fixed_stake_usd,"
                     "active,created_ts) VALUES ('kalshi_jack','mlb','Jack MLB','moneyline,total,spread','fixed',5.0,1,1787000000)")
        conn.execute("INSERT INTO pm_subdivision_attachment (account_id,category,wallet,active,source,added_ts) "
                     "VALUES ('kalshi_jack','mlb','0xw',1,'seed',1787000000)")
        base = dict(account_id="kalshi_jack", category="mlb", wallet="0xw", condition_id="0xc", outcome_index=1,
                    signal_id="s", order_side="bid", outcome_leg="yes", is_exit=0, submitted_count=10,
                    submitted_price=0.55, time_in_force="ioc", outcome_status="filled", fill_count=10.0,
                    fill_price=0.52, fee=0.0, dry_run=0, submitted_ts=NOW, response_ts=NOW)
        for i, (tk, gs) in enumerate(specs):
            row = dict(base); row["client_order_id"] = "c%d" % i; row["ticker"] = tk
            cols = ",".join(row)
            conn.execute("INSERT INTO pm_subdivision_order (%s) VALUES (%s)" % (cols, ",".join(["?"] * len(row))),
                         tuple(row.values()))
            marks[tk] = MK.Mark(tk, yes_bid=0.60, no_bid=0.38, yes_ask=0.62, no_ask=0.40, last=0.59, status="a", as_of=NOW)
            if gs is not None:
                games[gs.key] = gs
    slate = F.SlateResult(DATE, games, True, "statsapi", NOW)
    primed = ui_cache.UICache()
    primed.update(slates={DATE: slate}, marks=MK.MarksResult(marks=marks, ok=True, as_of=NOW), refreshed_ts=NOW)
    monkeypatch.setattr(ui_cache, "cache", lambda: primed)
    from trading_corp.prediction_markets.web.app import app
    cl = TestClient(app); cl.headers.update({"Remote-User": "jack"})
    return cl


def test_template_renders_pregame_and_final_states(monkeypatch, tmp_path):
    cl = _render_client(monkeypatch, tmp_path, [
        ("KXMLBTOTAL-26SEP021840SDCIN-9", _gs("preview", ascore=0, hscore=0)),          # pre-game, feed says 0-0
        ("KXMLBTOTAL-26SEP021610ATHTEX-9",
         _gs("final", away="ATH", home="TEX", hhmm="1610", la=(1, 0, 0), lh=(0, 1, 0), ascore=1, hscore=1)),
        ("KXMLBTOTAL-26SEP022210NYYLAA-9", None),                                        # feed unavailable
    ])
    html = cl.get("/live/kalshi_jack/mlb").text
    assert cl.get("/live/kalshi_jack/mlb").status_code == 200
    # item 1: a date/time line on EVERY card (3), incl. the feed-unavailable one
    assert html.count('class="gdt"') == 3
    assert "Wed Sep 2 · 10:10 PM ET" in html          # feed-down card still shows the ticker time
    # item 2: pre-game is 'not started', NOT 'game over'; final IS 'game over'
    assert "not started" in html
    assert "game over" in html                             # the final card
    # a pre-game 0-0 renders NO score digit in the linescore total column (the score span is empty)
    assert '<span class="r">0</span>' not in html
    # item 4: two separate toggle anchors
    assert html.count('class="tgl"') == 2


def test_template_flags_time_mismatch_in_drawer(monkeypatch, tmp_path):
    an, hn = F.canonical_team("HOU"), F.canonical_team("SEA")
    key = F.feed_game_key(DATE, an, hn, "2010", None)
    gs = F.GameState(key=key, date_iso=DATE, hhmm_et="2010", game_no=None, source="statsapi", fetched_ts=NOW,
                     game_pk=None, status="in_progress", away=F.TeamState("HOU", "HOU", None, 1),
                     home=F.TeamState("SEA", "SEA", None, 0), inning=1, half="TOP", outs=None, balls=None,
                     strikes=None, bases=(), linescore_away=(1,), linescore_home=(0,), last_play=None)
    cl = _render_client(monkeypatch, tmp_path, [("KXMLBTOTAL-26SEP022007HOUSEA-9", gs)])
    html = cl.get("/live/kalshi_jack/mlb").text
    assert "Wed Sep 2 · 8:10 PM ET" in html           # the feed time on the card
    assert "&dagger;" in html                              # main-row mismatch marker (never silent)
    assert "Scheduled start" in html and "8:07 PM ET" in html   # drawer detail records the ticker time too
