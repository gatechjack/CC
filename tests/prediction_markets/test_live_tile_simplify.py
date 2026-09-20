"""LIVE tile simplify (2026-09-20) -- the template / CSS / JS invariants that go with live_view._live_event's new
structure (the per-function line-count tests live in test_live_fixes_item1.py). Proves R1/R5/R8 at the asset level:
no 2x2 span or fixed 480px height, the 2x2 event-block partial + CSS deleted, the JUST-CLOSED banner removed, the
flash kept, the compact liveline wired -- plus the tile SORT order is unchanged (R6)."""
import os
import types

from trading_corp.prediction_markets.web import live_view as LV

WEB = os.path.dirname(LV.__file__)


def _read(rel):
    with open(os.path.join(WEB, rel), encoding="utf-8") as f:
        return f.read()


# ── CSS: R1/R8 -- no 2x2 span, no fixed 480px, event-block + banner CSS gone; liveline + flash kept ──────────────
def test_css_no_2x2_span_or_fixed_height():
    css = _read("static/pm_desk.css")
    assert "grid-column:span 2" not in css and "grid-row:span 2" not in css     # R1: no 2x2 span
    assert "height:480px" not in css                                            # R1: no fixed LIVE height
    assert ".subs .evt" not in css and ".subs .evchip" not in css              # R8: event-block CSS deleted
    assert "pulse-banner" not in css                                            # R5: banner CSS gone


def test_css_keeps_live_border_flash_and_adds_liveline():
    css = _read("static/pm_desk.css")
    assert ".subs .t.live{border-color:#2a5f78" in css                          # R4: LIVE border stays
    assert ".subs .t.fx-closed-won" in css and ".subs .t.fx-placed" in css      # R5: flash kept
    assert ".subs .liveln" in css                                               # R3: the compact line style


# ── JS: R5 -- banner removed, flash kept ────────────────────────────────────────────────────────────────────────
def test_js_banner_removed_flash_kept():
    js = _read("static/pm_live_subs.js")
    assert "pulse-banner" not in js and "JUST CLOSED" not in js and "JUST PLACED" not in js   # R5: no banner
    assert 'pulse(tileFor(p), "fx-placed")' in js                               # R5: the flash still fires
    assert "fx-closed-won" in js and "fx-closed-lost" in js


# ── TEMPLATE: the 2x2 partial is deleted, the compact liveline is wired ─────────────────────────────────────────
def test_template_wires_liveline_not_event_block():
    tpl = _read("templates/pm_live_list.html")
    assert "pm_subs_event.html" not in tpl                                      # R8: 2x2 event-block include removed
    assert "pm_subs_liveline.html" in tpl                                       # R3: compact line included
    assert not os.path.exists(os.path.join(WEB, "templates/partials/pm_subs_event.html"))   # R8: partial deleted


def test_liveline_partial_has_no_scoreboard_markup():
    p = _read("templates/partials/pm_subs_liveline.html")
    assert "class=\"liveln" in p and "summary_only" in p                        # summary + per-game lines
    for scoreboard in ("class=\"scr\"", "class=\"evt\"", "has_scoreboard", "class=\"plist\""):
        assert scoreboard not in p                                             # R3/R7: no score/inning markup


# ── SORT unchanged (R6): within a section, |today| desc; the section order is the fixed template list ────────────
def _sub(cat, today, n_whales=1, n_live=1):
    return {"account_id": "kalshi_jack", "category": cat, "n_whales": n_whales, "n_live_trades": n_live}


def test_tile_sort_within_section_is_abs_today_desc():
    subs = [_sub("mlb", 1.0), _sub("nfl", 9.0), _sub("nba", -5.0)]     # all LIVE (positions + underway below)
    arm = {"global": {"state": "armed", "ts": 1}, "subs": {}}
    pos = {("kalshi_jack", c): [{"ticker": "KX%s-26SEP20AAABBB-X" % c.upper(), "held_leg": "yes",
                                 "contracts": 1.0, "cost_basis_usd": 0.5, "market_type": "moneyline"}]
           for c in ("mlb", "nfl", "nba")}
    rw = {("kalshi_jack", c): {"today": t} for c, t in (("mlb", 1.0), ("nfl", 9.0), ("nba", -5.0))}
    # force every sub LIVE: underway=True regardless of feed
    import trading_corp.prediction_markets.web.live_view as _lv
    orig = _lv._event_underway
    _lv._event_underway = lambda *a, **k: True
    try:
        ctx = LV.build_subdivisions_context(
            subs=subs, accounts_meta=[{"account_id": "kalshi_jack"}], arm_all=arm, liveness_by_sub={},
            liveness_present=False, pnl_all={}, realized_windows=rw, positions_by_sub=pos, last_events={},
            marks={}, feed_games={}, now_ts=1789200000, thin_floor=50, mark_age_sec=None,
            active_account="kalshi_jack", viewer_role="admin", viewer_account=None, logo_codes=set(),
            poll_interval=60, global_arm={}, max_order_id=0)
    finally:
        _lv._event_underway = orig
    live = [t["code"] for t in ctx["sections"]["LIVE"]]
    assert live == ["NFL", "NBA", "MLB"]     # |today|: 9 > 5 > 1  (R6 sort unchanged)
    assert list(ctx["sections"].keys()) == ["LIVE", "UPCOMING", "SETTLED", "INACTIVE", "UNATTACHED"]
