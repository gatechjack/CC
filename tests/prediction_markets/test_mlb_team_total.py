"""PHASE 1b (2026-09-22): MLB TEAM TOTALS (KXMLBTEAMTOTAL). Matcher-level proofs (offline; data-side import only).
Built against REAL Polymarket slugs (mlb-tor-bal-2026-09-21-team-total-<team>-Npt5, all half-point) + REAL Kalshi
shape (KXMLBTEAMTOTAL-{stem}-{TEAM}{N}, N-0.5). Proves: parse; ROUTE-ONLY (team total never binds a full-game total);
EXACT-STRIKE; leg from OUTCOME (Over->yes/Under->no); team AND strike each an independent safe miss; whole-number
line -> SAFE MISS (Poly 'equal to or exceeds' push hazard); enable-token gate (blank never auto-enables)."""
from trading_corp.data import mlb_poly_kalshi_match as M

STEM = "26SEP222210SDLAD"   # SD @ LAD (away=SD, home=LAD)
DATE = "2026-09-22"


def _ctx():
    gi = M.build_kalshi_game_index(["KXMLBGAME-%s-SD" % STEM, "KXMLBGAME-%s-LAD" % STEM])
    dates = frozenset([DATE, "KXMLBGAME-%s-SD" % STEM, "KXMLBGAME-%s-LAD" % STEM])
    tt = M.build_kalshi_team_total_index(["KXMLBTEAMTOTAL-%s-SD8" % STEM, "KXMLBTEAMTOTAL-%s-LAD5" % STEM])
    full_tot = M.build_kalshi_total_index(["KXMLBTOTAL-%s-8" % STEM])   # full-game 7.5 present -> must stay unreachable
    return gi, dates, tt, full_tot


def _run(slug, outcome, allow=("moneyline", "total", "spread", "team_total")):
    gi, dates, tt, full_tot = _ctx()
    pb = M.parse_poly_mlb_bet(slug, outcome)
    r = M.match_bet(pb, gi, full_tot, {}, dates, allowed_market_types=allow, team_total_index=tt)
    return pb, r


def test_parse_teamtotal():
    pb = M.parse_poly_mlb_bet("mlb-sd-lad-%s-team-total-sd-7pt5" % DATE, "Over")
    assert pb.market_type == "team_total" and pb.line == 7.5 and pb.leg == "yes" and pb.anchor_side == "away"


def test_match_leg_from_outcome():
    _, over = _run("mlb-sd-lad-%s-team-total-sd-7pt5" % DATE, "Over")
    _, under = _run("mlb-sd-lad-%s-team-total-sd-7pt5" % DATE, "Under")
    assert over.status == "matched" and over.leg == "yes" and over.kalshi_ticker == "KXMLBTEAMTOTAL-%s-SD8" % STEM
    assert under.status == "matched" and under.leg == "no" and under.kalshi_ticker == "KXMLBTEAMTOTAL-%s-SD8" % STEM


def test_route_only_never_fullgame_total():
    _, r = _run("mlb-sd-lad-%s-team-total-sd-7pt5" % DATE, "Over")
    assert r.kalshi_ticker == "KXMLBTEAMTOTAL-%s-SD8" % STEM and not r.kalshi_ticker.startswith("KXMLBTOTAL-")


def test_exact_strike_only():
    _, r = _run("mlb-sd-lad-%s-team-total-sd-8pt5" % DATE, "Over")   # need SD9; only SD8 exists
    assert r.status == "no_kalshi_strike"


def test_wrong_team_safe_miss():
    _, r = _run("mlb-sd-lad-%s-team-total-nyy-7pt5" % DATE, "Over")   # NYY not in this game
    assert r.status == "fail" and "team_total_team_not_in_game" in (r.reason or "")


def test_whole_number_line_safe_miss():
    pb = M.parse_poly_mlb_bet("mlb-sd-lad-%s-team-total-sd-7pt0" % DATE, "Over")
    assert pb.market_type == "team_total" and "whole_number" in (pb.fail_reason or "")
    _, r = _run("mlb-sd-lad-%s-team-total-sd-7pt0" % DATE, "Over")
    assert r.status == "fail"


def test_blank_default_never_autoenables():
    _, r = _run("mlb-sd-lad-%s-team-total-sd-7pt5" % DATE, "Over", allow=("moneyline", "total", "spread"))
    assert r.status == "skip_market_type_excluded"


def test_teamtotal_registered_and_moneyline_unchanged():
    assert "team_total" in M.COPYABLE_MARKET_TYPES
    # moneyline path byte-unchanged: a bare game slug still matches moneyline
    pb, r = _run("mlb-sd-lad-%s" % DATE, "San Diego")
    assert pb.market_type == "moneyline" and r.status == "matched"
