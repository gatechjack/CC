"""Phase-2 Live-Sub-divisions TILE assembly (live_view.build_tiles_context) + template render. Pure/offline.
Proves: R2 segmentation + armed->attached-disarmed->unattached sort + all 43 shown; R1 alarm strip = armed AND
(STALE|NEVER) only (a DISARMED stale sub is NOT an alarm); R3 realized split (booked/unbooked, W-L from
settlements, thin flag); R4 open = three distinct figures + honest coverage; and the template renders each state."""
from types import SimpleNamespace as NS

from trading_corp.prediction_markets.web import live_view
from trading_corp.prediction_markets.heartbeat import SubLiveness

NOW = 1_788_500_000
JACK, KAREN = "kalshi_jack", "kalshi_karen"


def _sub(aid, cat, n_whales, label):
    return {"account_id": aid, "category": cat, "account_label": label, "sub_label": None,
            "created_ts": 1, "n_whales": n_whales, "n_live_trades": 0, "venue": "kalshi"}


def _arm(state):
    eff = "armed" if state == "armed" else ("unavailable" if state == "unavailable" else "disarmed")
    return {"sub_state": state, "sub_ts": None if state == "absent" else "2026-09-04T04:31:42+00:00",
            "effective_state": eff}


def _lv(aid, cat, state, age=5):
    return SubLiveness(account_id=aid, category=cat, state=state, band="fresh", age_sec=age,
                       n_signals=0, placed=0, ceiling_latched=False, detail=state)


def _ctx(subs, arm_subs, liveness=None, whales=None, pnl=None, pnl24=None, positions=None, marks=None,
         present=True, gstate="armed"):
    return live_view.build_tiles_context(
        subs=subs, arm_all={"global": {"state": gstate, "ts": "2026-08-31T02:35:38+00:00"}, "subs": arm_subs},
        liveness_by_sub=liveness or {}, liveness_present=present, whales_by_sub=whales or {},
        pnl_all=pnl or {}, pnl24_all=pnl24 or {}, positions_by_sub=positions or {}, marks=marks or {},
        now_ts=NOW, thin_floor=50)


def test_segments_sort_and_counts():
    subs = [_sub(JACK, "mlb", 1, "Jack"), _sub(JACK, "nfl", 1, "Jack"), _sub(JACK, "cs2", 0, "Jack"),
            _sub(KAREN, "mlb", 1, "Karen")]
    arms = {(JACK, "mlb"): _arm("armed"), (JACK, "nfl"): _arm("absent"), (JACK, "cs2"): _arm("absent"),
            (KAREN, "mlb"): _arm("armed")}
    ctx = _ctx(subs, arms, whales={(JACK, "mlb"): [{"user_name": "w"}], (JACK, "nfl"): [{"user_name": "x"}],
                                   (KAREN, "mlb"): [{"user_name": "y"}]})
    assert ctx["counts"] == {"total": 4, "attached": 3, "unattached": 1, "armed": 2}
    labels = [a["account_label"] for a in ctx["accounts"]]
    assert labels == ["Jack", "Karen"]                                    # segmented by account
    jack = ctx["accounts"][0]["tiles"]
    assert [t["category"] for t in jack] == ["mlb", "nfl", "cs2"]         # armed -> attached-disarmed -> unattached


def test_alarm_strip_is_armed_and_stale_only():
    subs = [_sub(JACK, "mlb", 1, "Jack"), _sub(JACK, "atp", 1, "Jack"), _sub(JACK, "ufc", 1, "Jack"),
            _sub(JACK, "wta", 1, "Jack")]
    arms = {(JACK, "mlb"): _arm("armed"), (JACK, "atp"): _arm("armed"),
            (JACK, "ufc"): _arm("disarmed"), (JACK, "wta"): _arm("armed")}
    liveness = {(JACK, "mlb"): _lv(JACK, "mlb", "STALE", 2000),      # armed + STALE -> ALARM
                (JACK, "atp"): _lv(JACK, "atp", "RUNNING"),          # armed + RUNNING -> not
                (JACK, "ufc"): _lv(JACK, "ufc", "STALE", 3000),      # DISARMED + STALE -> NOT an alarm (R1: armed only)
                (JACK, "wta"): _lv(JACK, "wta", "NEVER", None)}      # armed + NEVER -> ALARM
    whales = {k: [{"user_name": "w"}] for (k) in arms}
    ctx = _ctx(subs, arms, liveness=liveness, whales=whales)
    alarmed = {(a["category"]) for a in ctx["alarm_subs"]}
    assert alarmed == {"mlb", "wta"}                                  # exactly the armed+STALE and armed+NEVER
    tiles = {t["category"]: t for t in ctx["accounts"][0]["tiles"]}
    assert tiles["mlb"]["is_alarm"] and tiles["wta"]["is_alarm"]
    assert not tiles["ufc"]["is_alarm"] and not tiles["atp"]["is_alarm"]


def test_unattached_is_compact_no_rich_fields():
    subs = [_sub(JACK, "cs2", 0, "Jack")]
    ctx = _ctx(subs, {(JACK, "cs2"): _arm("absent")})
    t = ctx["accounts"][0]["tiles"][0]
    assert t["attached"] is False and t["whale_tag"] is None and t["liveness"] is None
    assert t["realized"] is None and t["open_count"] == 0            # compact: no rich metrics computed


def test_realized_split_and_thin_flag():
    subs = [_sub(JACK, "mlb", 1, "Jack"), _sub(JACK, "atp", 1, "Jack")]
    arms = {(JACK, "mlb"): _arm("armed"), (JACK, "atp"): _arm("armed")}
    whales = {(JACK, "mlb"): [{"user_name": "w"}], (JACK, "atp"): [{"user_name": "w"}]}
    pnl = {(JACK, "mlb"): {"realized": 10.34, "booked_closes": 76, "wins": 38, "losses": 32, "unbooked_closes": 6},
           (JACK, "atp"): {"realized": -5.32, "booked_closes": 14, "wins": 3, "losses": 9, "unbooked_closes": 2}}
    ctx = _ctx(subs, arms, whales=whales, pnl=pnl, liveness={(JACK, "mlb"): _lv(JACK, "mlb", "RUNNING"),
                                                             (JACK, "atp"): _lv(JACK, "atp", "RUNNING")})
    tiles = {t["category"]: t for t in ctx["accounts"][0]["tiles"]}
    assert tiles["mlb"]["realized_thin"] is False                    # 76 >= 50 booked -> not thin
    assert tiles["atp"]["realized_thin"] is True                     # 14 < 50 -> thin
    assert tiles["mlb"]["unbooked_closes"] == 6 and tiles["mlb"]["wins"] == 38


def test_open_three_figures_and_coverage():
    subs = [_sub(JACK, "mlb", 1, "Jack")]
    arms = {(JACK, "mlb"): _arm("armed")}
    positions = {(JACK, "mlb"): [{"ticker": "A", "held_leg": "yes", "contracts": 5, "cost_basis_usd": 2.90},
                                 {"ticker": "B", "held_leg": "no", "contracts": 3, "cost_basis_usd": 2.15}]}
    marks = {"A": NS(yes_bid=0.60, no_bid=None)}                     # only A priced -> 1 of 2, partial
    ctx = _ctx(subs, arms, whales={(JACK, "mlb"): [{"user_name": "w"}]}, positions=positions, marks=marks,
               liveness={(JACK, "mlb"): _lv(JACK, "mlb", "RUNNING")})
    t = ctx["accounts"][0]["tiles"][0]
    assert t["open_count"] == 2
    assert round(t["open_at_cost"], 2) == 5.05                       # cost sum, DISTINCT from value
    assert round(t["open_value"], 2) == 3.00                        # 5 x 0.60 (only A priced)
    assert t["open_priced"] == 1 and t["open_total"] == 2 and t["open_complete"] is False


def test_liveness_absent_reads_na_not_alarm():
    subs = [_sub(JACK, "mlb", 1, "Jack")]
    ctx = _ctx(subs, {(JACK, "mlb"): _arm("armed")}, whales={(JACK, "mlb"): [{"user_name": "w"}]},
               liveness={(JACK, "mlb"): _lv(JACK, "mlb", "STALE", 9999)}, present=False)  # monitor not deployed
    assert ctx["alarm_subs"] == []                                   # table absent -> no alarm (don't cry wolf)
    assert ctx["accounts"][0]["tiles"][0]["liveness"] is None


def test_template_renders_all_states():
    from trading_corp.prediction_markets.web import app as webapp
    subs = [_sub(JACK, "mlb", 1, "Jack"), _sub(JACK, "wta", 1, "Jack"), _sub(JACK, "cs2", 0, "Jack")]
    arms = {(JACK, "mlb"): _arm("armed"), (JACK, "wta"): _arm("armed"), (JACK, "cs2"): _arm("absent")}
    liveness = {(JACK, "mlb"): _lv(JACK, "mlb", "RUNNING"), (JACK, "wta"): _lv(JACK, "wta", "STALE", 1930)}
    whales = {(JACK, "mlb"): [{"user_name": "domer"}, {"user_name": "b"}], (JACK, "wta"): [{"user_name": "w"}]}
    pnl = {(JACK, "mlb"): {"realized": 10.34, "booked_closes": 76, "wins": 38, "losses": 32, "unbooked_closes": 6},
           (JACK, "wta"): {"realized": 4.66, "booked_closes": 2, "wins": 2, "losses": 0, "unbooked_closes": 0}}
    positions = {(JACK, "mlb"): [{"ticker": "A", "held_leg": "yes", "contracts": 5, "cost_basis_usd": 2.90}]}
    ctx = _ctx(subs, arms, liveness=liveness, whales=whales, pnl=pnl, positions=positions,
               marks={"A": NS(yes_bid=0.60, no_bid=None)})
    req = NS(url=NS(path="/live"))
    html = webapp.templates.env.get_template("pm_live_list.html").render(request=req, **ctx)
    assert "DRIVER NOT RUNNING" in html and "wta" in html.lower()    # R1 alarm strip lists the armed+STALE sub
    assert "no whales attached" in html                              # R2 compact tile
    assert "1 of 1 priced" in html                                   # R4 coverage label
    assert "booked close" in html and "38&ndash;32 settled" in html or "38–32 settled" in html  # R3
    assert "NEVER ARMED" in html                                     # cs2 arm state
    assert "THIN" in html                                            # wta 2<50 booked -> thin marker
    assert 'class="tl-tile-alarm"' in html or "tl-tile-alarm" in html  # the armed+STALE tile flagged
