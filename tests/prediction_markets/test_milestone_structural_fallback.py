"""Item A (2026-09-12): structural sports (cfb/nfl/wnba/nba/nhl) fall back to the Kalshi MILESTONE start
when their ticker carries no HHMM -- so cfb/nfl read LIVE while underway instead of UPCOMING.

Root cause (measured): cfb tickers carry 0/794 HHMM and cfb was LIVE_CAPABLE (HHMM path) but NOT in
MILESTONE_START_CATEGORIES -> served by NEITHER source. The fix makes the structural sports milestone-
eligible, HHMM-FIRST, while mlb (feed) + cs2 (HHMM) stay authoritative and never borrow a milestone.

Acceptance (the case that provoked it): OKLA@MICH, real milestone start 2026-09-12T16:00:00Z, reads LIVE
while underway; a live NFL game the same; served categories unchanged; a structural game with neither HHMM
nor a milestone reads time-unknown (honest) -- not midnight, not LIVE.

Offline; importing live_view runs on the box venv (like the other live_view tests).
"""
import datetime as _dt

from trading_corp.prediction_markets.web import live_view as LV


def _utc(y, mo, d, h, mi=0):
    return int(_dt.datetime(y, mo, d, h, mi, tzinfo=_dt.timezone.utc).timestamp())


OKLA_MKT = "KXNCAAFGAME-26SEP12OKLAMICH-OKLA"   # held cfb moneyline ticker (NO HHMM -- real format)
OKLA_EVT = "KXNCAAFGAME-26SEP12OKLAMICH"        # event ticker = market minus final '-OKLA'
OKLA_START = _utc(2026, 9, 12, 16)              # milestone start_date 2026-09-12T16:00:00Z (real, page 6)
NOW = _utc(2026, 9, 12, 21)                     # observation instant (game underway)
NFL_MKT = "KXNFLGAME-26SEP21NYGLAR-NYG"         # real NFL moneyline ticker (NO HHMM)
NFL_EVT = "KXNFLGAME-26SEP21NYGLAR"
NFL_START = _utc(2026, 9, 21, 17)


# ── the interdependent routing lists ──
def test_structural_sports_are_live_capable_AND_milestone_eligible():
    for c in ("cfb", "nfl", "wnba", "nba", "nhl"):
        assert c in LV.LIVE_CAPABLE, c                  # HHMM-first attempt
        assert c in LV.MILESTONE_START_CATEGORIES, c    # AND the milestone fallback (the fix)


def test_mlb_and_cs2_stay_hhmm_feed_authoritative_never_milestone():
    for c in ("mlb", "cs2"):
        assert c in LV.LIVE_CAPABLE, c
        assert c not in LV.MILESTONE_START_CATEGORIES, c


def test_date_only_sports_unchanged_milestone_only():
    for c in ("atp", "wta", "ufc", "epl", "ucl", "lal", "mex", "mls", "bra", "bun", "sea", "fl1"):
        assert c in LV.MILESTONE_START_CATEGORIES, c
        assert c not in LV.LIVE_CAPABLE, c


def test_fed_in_neither_list():
    assert "fed" not in LV.LIVE_CAPABLE
    assert "fed" not in LV.MILESTONE_START_CATEGORIES


# ── start_ts_for_ticker routing ──
def test_cfb_no_hhmm_falls_back_to_milestone():          # THE FIX
    assert LV.start_ts_for_ticker("cfb", OKLA_MKT, {OKLA_EVT: OKLA_START}) == OKLA_START


def test_nfl_no_hhmm_falls_back_to_milestone():          # the urgent one (in-season)
    assert LV.start_ts_for_ticker("nfl", NFL_MKT, {NFL_EVT: NFL_START}) == NFL_START


def test_structural_no_hhmm_and_no_milestone_is_time_unknown():   # honest, not midnight/LIVE
    assert LV.start_ts_for_ticker("cfb", OKLA_MKT, {}) is None
    assert LV.start_ts_for_ticker("cfb", OKLA_MKT, None) is None


def test_structural_with_hhmm_uses_hhmm_first_not_milestone():
    hhmm_mkt = "KXNCAAFGAME-26SEP121600OKLAMICH-OKLA"     # synthetic cfb ticker WITH a 1600 HHMM
    hhmm_evt = "KXNCAAFGAME-26SEP121600OKLAMICH"
    got = LV.start_ts_for_ticker("cfb", hhmm_mkt, {hhmm_evt: OKLA_START + 99999})
    assert got is not None and got != OKLA_START + 99999          # HHMM won, milestone ignored
    assert got == LV.parse_ticker_start("cfb", hhmm_mkt)          # exactly the ticker HHMM value


def test_mlb_never_borrows_a_milestone_even_without_hhmm():
    no_hhmm_mlb = "KXMLBGAME-26SEP12NYYBOS-NYY"           # (synthetic) mlb ticker w/o HHMM
    assert LV.start_ts_for_ticker("mlb", no_hhmm_mlb, {"KXMLBGAME-26SEP12NYYBOS": OKLA_START}) is None


def test_cs2_uses_hhmm_ignores_a_stray_milestone():
    cs2 = "KXCS2GAME-26SEP131130THETIT-TIT"               # real cs2 ticker WITH a 1130 HHMM
    got = LV.start_ts_for_ticker("cs2", cs2, {"KXCS2GAME-26SEP131130THETIT": OKLA_START})
    assert got is not None and got == LV.parse_ticker_start("cs2", cs2)


def test_tennis_ufc_soccer_still_milestone_routed():
    for cat, mkt, evt in (("atp", "KXATPMATCH-26SEP12ALCSIN-ALC", "KXATPMATCH-26SEP12ALCSIN"),
                          ("ufc", "KXUFCFIGHT-26SEP12ABCDEF-ABC", "KXUFCFIGHT-26SEP12ABCDEF"),
                          ("epl", "KXEPLGAME-26SEP12ARSCHE-ARS", "KXEPLGAME-26SEP12ARSCHE")):
        assert LV.start_ts_for_ticker(cat, mkt, {evt: OKLA_START}) == OKLA_START


# ── _event_underway acceptance (LIVE vs UPCOMING) ──
def test_okla_mich_reads_LIVE_while_underway():          # the case that provoked it
    assert LV._event_underway("cfb", [OKLA_MKT], None, {}, NOW, {OKLA_EVT: OKLA_START}) is True


def test_live_nfl_reads_LIVE_while_underway():
    assert LV._event_underway("nfl", [NFL_MKT], None, {}, NFL_START + 3600, {NFL_EVT: NFL_START}) is True


def test_cfb_with_no_milestone_stays_UPCOMING():         # never a fabricated LIVE
    assert LV._event_underway("cfb", [OKLA_MKT], None, {}, NOW, {}) is False


def test_cfb_before_kickoff_is_UPCOMING_not_LIVE():
    assert LV._event_underway("cfb", [OKLA_MKT], None, {}, OKLA_START - 3600, {OKLA_EVT: OKLA_START}) is False
