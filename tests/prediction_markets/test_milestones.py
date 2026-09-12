"""Kalshi milestone START-TIME feed (2026-09-12) -- the first cross-category LIVE/UPCOMING signal.

Offline, stdlib + fake http_get only (no network, no venue, no credentials). Proves, at three layers:
  * the reader (milestones.py): RFC3339 parse incl. the T00:00:00Z placeholder-to-unknown rule, the event-ticker
    join key, page parsing, the two-catalog cursor sweep with the HORIZON early-stop and the page-cap backstop, and
    the never-raise degrade (all-fail -> ok=False, partial -> ok=True);
  * the poller cadence (poller.py): a good index is swept once and REUSED across 60s cycles, re-swept only after the
    slow REFRESH, and NEVER blanked by a transient failure or an empty window (start times are immutable);
  * the classifier (live_view.py): tennis/ufc/soccer now read LIVE once a milestone start has passed, MLB stays
    FEED-authoritative (a milestone never overrides the feed), an unknown/placeholder/future start stays UPCOMING,
    a finalized market is not underway, and starts=None reproduces the pre-feed behaviour exactly.
"""
import pytest

from trading_corp.prediction_markets.web import milestones as M
from trading_corp.prediction_markets.web import live_view as LV
from trading_corp.prediction_markets.web import poller as P
from trading_corp.prediction_markets.web import ui_cache, feed_mlb, marks as marks_mod

NOW = 1789200000   # a fixed clock for every test


# ----------------------------------------------------------------------------------------------------------------
# reader: start_instant / event_ticker / start_for_event_ticker (pure)
# ----------------------------------------------------------------------------------------------------------------
def test_start_instant_real_utc():
    assert M.start_instant("2026-09-11T23:15:00Z") == 1789168500


def test_start_instant_placeholder_midnight_is_unknown():
    # a T00:00:00Z date-only placeholder MUST fall back to unknown, never read as a real midnight start
    assert M.start_instant("2026-09-12T00:00:00Z") is None


def test_start_instant_malformed_and_empty_are_none():
    for bad in ("", None, "garbage", "2026-13-40T99:99:99Z", 12345):
        assert M.start_instant(bad) is None


def test_start_instant_naive_treated_as_utc():
    assert M.start_instant("2026-09-11T23:15:00") == 1789168500


def test_start_instant_offset_form():
    # a real kickoff expressed with an explicit offset resolves to the same instant as its Z form
    assert M.start_instant("2026-09-11T19:15:00-04:00") == M.start_instant("2026-09-11T23:15:00Z")


def test_event_ticker_strips_final_suffix():
    assert M.event_ticker("KXNCAAFTOTAL-26SEP11MIZZKU-52") == "KXNCAAFTOTAL-26SEP11MIZZKU"
    assert M.event_ticker("KXATPMATCH-26SEP12ABCDEF-XYZ") == "KXATPMATCH-26SEP12ABCDEF"


def test_event_ticker_no_dash_is_none():
    assert M.event_ticker("NODASH") is None
    assert M.event_ticker("") is None
    assert M.event_ticker(None) is None


def test_start_for_event_ticker_joins_via_event_ticker():
    starts = {"KXATPMATCH-26SEP12ABCDEF": NOW - 600}
    assert M.start_for_event_ticker(starts, "KXATPMATCH-26SEP12ABCDEF-XYZ") == NOW - 600
    assert M.start_for_event_ticker(starts, "KXATPMATCH-99ZZZ-1") is None   # not in index
    assert M.start_for_event_ticker(None, "KXATPMATCH-26SEP12ABCDEF-XYZ") is None
    assert M.start_for_event_ticker({}, "KXATPMATCH-26SEP12ABCDEF-XYZ") is None


# ----------------------------------------------------------------------------------------------------------------
# reader: parse_milestones (pure page fold)
# ----------------------------------------------------------------------------------------------------------------
def test_parse_milestones_indexes_real_skips_placeholder():
    page = {"milestones": [
        {"start_date": "2026-09-11T23:15:00Z", "related_event_tickers": ["KXA-1", "KXB-1"], "details": {"status": "not_started"}},
        {"start_date": "2026-09-12T00:00:00Z", "related_event_tickers": ["KXC-1"]},   # placeholder -> skipped
        {"start_date": "garbage", "related_event_tickers": ["KXD-1"]},               # unparseable -> skipped
    ]}
    entries, mx = M.parse_milestones(page)
    got = dict(entries)
    assert got == {"KXA-1": 1789168500, "KXB-1": 1789168500}
    assert mx == 1789168500


def test_parse_milestones_ignores_status_field():
    # details.status LIES (finished matches read not_started); we index purely on start_date, never on status
    page = {"milestones": [{"start_date": "2026-09-11T23:15:00Z", "related_event_tickers": ["KXA-1"],
                            "details": {"status": "closed"}}]}
    entries, _ = M.parse_milestones(page)
    assert dict(entries) == {"KXA-1": 1789168500}    # 'closed' does not suppress indexing -- clock compare decides


def test_parse_milestones_empty_and_missing_key():
    assert M.parse_milestones({}) == ([], None)
    assert M.parse_milestones({"milestones": []}) == ([], None)
    assert M.parse_milestones(None) == ([], None)
    assert M.parse_milestones({"milestones": [{"start_date": "2026-09-11T23:15:00Z", "related_event_tickers": []}]}) == ([], 1789168500)


# ----------------------------------------------------------------------------------------------------------------
# reader: fetch_starts (two-catalog cursor sweep, with a fake http_get)
# ----------------------------------------------------------------------------------------------------------------
def _iso(ts):
    return M._rfc3339(ts)


class _FakeHttp:
    """A scripted paginator: maps a catalog category to a list of pages (each {'milestones':[...], 'cursor':...}).
    Records every URL so tests can assert the literal minimum_start_date / category / cursor encoding."""
    def __init__(self, pages_by_cat):
        self.pages_by_cat = pages_by_cat
        self.urls = []
        self._idx = {}

    def __call__(self, url, *, timeout=12.0):
        self.urls.append(url)
        cat = "Sports" if "category=Sports" in url else "Esports" if "category=Esports" in url else "?"
        i = self._idx.get(cat, 0)
        self._idx[cat] = i + 1
        pages = self.pages_by_cat.get(cat, [])
        return pages[i] if i < len(pages) else {"milestones": [], "cursor": ""}


def test_fetch_starts_merges_two_catalogs_and_paginates():
    sports = [
        {"milestones": [{"start_date": "2026-09-11T20:00:00Z", "related_event_tickers": ["KXMLB-1"]}], "cursor": "c1"},
        {"milestones": [{"start_date": "2026-09-11T21:00:00Z", "related_event_tickers": ["KXNFL-1"]}], "cursor": ""},
    ]
    esports = [
        {"milestones": [{"start_date": "2026-09-11T22:00:00Z", "related_event_tickers": ["KXCSGO-1"]}], "cursor": ""},
    ]
    http = _FakeHttp({"Sports": sports, "Esports": esports})
    res = M.fetch_starts(now_ts=NOW, http_get=http)
    assert res.ok is True
    assert set(res.starts) == {"KXMLB-1", "KXNFL-1", "KXCSGO-1"}
    assert res.n_indexed == 3
    assert res.pages == 3         # 2 Sports pages + 1 Esports page
    assert res.capped is False
    # the minimum_start_date is sent LITERALLY (colons intact), window = now - LOOKBACK
    assert ("minimum_start_date=" + _iso(NOW - M.LOOKBACK_SECONDS)) in http.urls[0]
    assert "category=Sports" in http.urls[0]


def test_fetch_starts_horizon_early_stop():
    # ascending sort: page 1 is within the horizon, page 2 is past it -> stop after page 2 (its data still indexed,
    # but no page 3 is fetched). The cap is far higher than 2, so hitting the horizon (not the cap) ends the sweep.
    far = NOW + M.HORIZON_SECONDS + 10_000
    sports = [
        {"milestones": [{"start_date": _iso(NOW - 100), "related_event_tickers": ["KXNEAR-1"]}], "cursor": "c1"},
        {"milestones": [{"start_date": _iso(far), "related_event_tickers": ["KXFAR-1"]}], "cursor": "c2"},
        {"milestones": [{"start_date": _iso(far + 100), "related_event_tickers": ["KXFARTHER-1"]}], "cursor": "c3"},
    ]
    http = _FakeHttp({"Sports": sports, "Esports": []})
    res = M.fetch_starts(now_ts=NOW, http_get=http)
    sports_pages = [u for u in http.urls if "category=Sports" in u]
    assert len(sports_pages) == 2                 # stopped after the first past-horizon page; page 3 never fetched
    assert "KXFARTHER-1" not in res.starts
    assert res.capped is False


def test_fetch_starts_page_cap_backstop_is_loud(caplog):
    # a catalog whose cursor never ends and whose starts never pass the horizon -> the page cap must stop it, mark
    # capped=True, and LOG it (no silent truncation)
    endless = [{"milestones": [{"start_date": _iso(NOW - 100), "related_event_tickers": ["KX-%d" % i]}],
                "cursor": "c%d" % i} for i in range(200)]
    http = _FakeHttp({"Sports": endless, "Esports": []})
    import logging
    with caplog.at_level(logging.WARNING):
        res = M.fetch_starts(now_ts=NOW, http_get=http, max_pages=5)
    assert res.capped is True
    assert res.pages == 6                          # 5 Sports pages at the cap (not 200) + 1 empty Esports page
    assert len([u for u in http.urls if "category=Sports" in u]) == 5
    assert any("cap" in r.message.lower() for r in caplog.records)


def test_fetch_starts_all_fail_returns_ok_false_empty():
    def boom(url, *, timeout=12.0):
        raise OSError("network down")
    res = M.fetch_starts(now_ts=NOW, http_get=boom)
    assert res.ok is False
    assert res.starts == {}
    assert res.error and "Sports" in res.error and "Esports" in res.error


def test_fetch_starts_never_raises_on_non_dict_body():
    # a top-level JSON LIST (or None) instead of the expected object must NOT raise -- it degrades to that catalog's
    # miss (the "NEVER raises" contract). A bare AttributeError here would escape the per-catalog except.
    def weird(url, *, timeout=12.0):
        return [] if "category=Sports" in url else None
    res = M.fetch_starts(now_ts=NOW, http_get=weird)
    assert res.ok is True           # no exception; both catalogs returned a (degenerate) body, just no milestones
    assert res.starts == {}


def test_fetch_starts_unexpected_exception_is_caught_per_catalog():
    # an exception type NOT in the old narrow tuple (e.g. KeyError) must still be caught per catalog
    def kaboom(url, *, timeout=12.0):
        raise KeyError("surprise")
    res = M.fetch_starts(now_ts=NOW, http_get=kaboom)
    assert res.ok is False and res.starts == {}
    assert res.error and "KeyError" in res.error


def test_fetch_starts_partial_fail_is_ok_with_what_it_got():
    sports = [{"milestones": [{"start_date": "2026-09-11T20:00:00Z", "related_event_tickers": ["KXMLB-1"]}], "cursor": ""}]
    def http(url, *, timeout=12.0):
        if "category=Esports" in url:
            raise OSError("esports down")
        return sports[0]
    res = M.fetch_starts(now_ts=NOW, http_get=http)
    assert res.ok is True                           # one catalog succeeded
    assert res.starts == {"KXMLB-1": M.start_instant("2026-09-11T20:00:00Z")}
    assert res.error and "Esports" in res.error


# ----------------------------------------------------------------------------------------------------------------
# poller cadence: slow refresh, reuse between cycles, never blank a good index
# ----------------------------------------------------------------------------------------------------------------
def _slate_ok(d, *, now_ts):
    return feed_mlb.SlateResult(date_iso=d, games={}, ok=True, source="test", as_of=now_ts, error=None)


def _marks_ok(*args, now_ts, **kw):
    return marks_mod.MarksResult(marks={}, ok=True, as_of=now_ts, error=None)


def _refresh(cache, now_ts, fetch_starts):
    P.refresh_once(cache, now_ts=now_ts, fetch_slate=_slate_ok, fetch_marks=_marks_ok, enrich=False,
                   fetch_starts=fetch_starts)


def test_poller_sweeps_once_then_reuses_then_resweeps():
    cache = ui_cache.UICache()
    calls = {"n": 0}

    def fs(*, now_ts):
        calls["n"] += 1
        return M.StartsResult(starts={"KXA-1": now_ts - 100}, ok=True, as_of=now_ts, n_indexed=1, pages=2)

    _refresh(cache, NOW, fs)                                   # cycle 1: due (never swept) -> sweep
    assert calls["n"] == 1 and cache.snapshot().starts == {"KXA-1": NOW - 100}
    _refresh(cache, NOW + 60, fs)                              # cycle 2: 60s later -> NOT due -> reuse
    assert calls["n"] == 1 and cache.snapshot().starts == {"KXA-1": NOW - 100}
    _refresh(cache, NOW + P.MILESTONE_REFRESH_SECONDS + 61, fs)  # cycle 3: past REFRESH -> re-sweep
    assert calls["n"] == 2


def test_poller_keeps_prior_index_on_failure_and_retries_sooner():
    cache = ui_cache.UICache()

    def good(*, now_ts):
        return M.StartsResult(starts={"KXA-1": now_ts - 100}, ok=True, as_of=now_ts, n_indexed=1, pages=1)

    _refresh(cache, NOW, good)
    assert cache.snapshot().starts == {"KXA-1": NOW - 100}
    # a failed sweep after REFRESH keeps the prior index (immutable start times -> safe) and reports the error
    def fail(*, now_ts):
        return M.StartsResult(starts={}, ok=False, as_of=now_ts, error="Sports:OSError;Esports:OSError")
    _refresh(cache, NOW + P.MILESTONE_REFRESH_SECONDS + 61, fail)
    snap = cache.snapshot()
    assert snap.starts == {"KXA-1": NOW - 100}                 # NOT blanked
    assert snap.starts_ok is False and "milestones:" in (snap.last_error or "")
    # after a failure the retry gate is the SHORT RETRY, not the long REFRESH
    t_fail = NOW + P.MILESTONE_REFRESH_SECONDS + 61
    calls = {"n": 0}
    def good2(*, now_ts):
        calls["n"] += 1
        return M.StartsResult(starts={"KXB-1": now_ts}, ok=True, as_of=now_ts, n_indexed=1, pages=1)
    _refresh(cache, t_fail + P.MILESTONE_RETRY_SECONDS + 1, good2)   # RETRY elapsed -> re-sweep
    assert calls["n"] == 1 and cache.snapshot().starts == {"KXB-1": t_fail + P.MILESTONE_RETRY_SECONDS + 1}


def test_poller_empty_window_keeps_prior_index():
    cache = ui_cache.UICache()

    def good(*, now_ts):
        return M.StartsResult(starts={"KXA-1": now_ts - 100}, ok=True, as_of=now_ts, n_indexed=1, pages=1)

    _refresh(cache, NOW, good)
    def empty(*, now_ts):
        return M.StartsResult(starts={}, ok=True, as_of=now_ts, n_indexed=0, pages=1)
    _refresh(cache, NOW + P.MILESTONE_REFRESH_SECONDS + 61, empty)
    # an OK-but-empty sweep does NOT wipe a good index (immutable start times); as_of is stamped as refreshed
    snap = cache.snapshot()
    assert snap.starts == {"KXA-1": NOW - 100}
    assert snap.starts_ok is True


def test_poller_empty_first_boot_retries_soon_not_in_six_hours():
    # an ok-but-EMPTY first sweep (no prior data) must keep the SHORT retry cadence, not blackout for 6h
    cache = ui_cache.UICache()
    calls = {"n": 0}

    def empty_then_full(*, now_ts):
        calls["n"] += 1
        if calls["n"] == 1:
            return M.StartsResult(starts={}, ok=True, as_of=now_ts, n_indexed=0, pages=1)   # empty first boot
        return M.StartsResult(starts={"KXA-1": now_ts}, ok=True, as_of=now_ts, n_indexed=1, pages=1)

    _refresh(cache, NOW, empty_then_full)                       # cycle 1: empty
    assert calls["n"] == 1 and cache.snapshot().starts == {}
    # 60s later: NOT due yet (RETRY is 20m), but well before REFRESH (6h)
    _refresh(cache, NOW + 60, empty_then_full)
    assert calls["n"] == 1
    # after RETRY (not REFRESH): re-sweep and pick up the data
    _refresh(cache, NOW + P.MILESTONE_RETRY_SECONDS + 1, empty_then_full)
    assert calls["n"] == 2 and cache.snapshot().starts == {"KXA-1": NOW + P.MILESTONE_RETRY_SECONDS + 1}


def test_poller_disabled_when_fetch_starts_none():
    cache = ui_cache.UICache()
    _refresh(cache, NOW, None)                                 # milestone sweep disabled
    snap = cache.snapshot()
    assert snap.starts == {} and snap.starts_as_of is None
    assert snap.ready is True                                  # marks/feed refresh still ran


# ----------------------------------------------------------------------------------------------------------------
# classifier: _event_underway / _live_event / _next_event with the milestone index
# ----------------------------------------------------------------------------------------------------------------
def _pos(ticker, leg="yes", contracts=2, cost=1.0):
    return {"ticker": ticker, "held_leg": leg, "contracts": contracts, "cost_basis_usd": cost}


def test_atp_underway_via_milestone():
    starts = {"KXATPMATCH-26SEP12ABCDEF": NOW - 600}
    assert LV._event_underway("atp", ["KXATPMATCH-26SEP12ABCDEF-XYZ"], {}, {}, NOW, starts) is True


def test_atp_future_start_is_upcoming():
    starts = {"KXATPMATCH-26SEP12ABCDEF": NOW + 3600}
    assert LV._event_underway("atp", ["KXATPMATCH-26SEP12ABCDEF-XYZ"], {}, {}, NOW, starts) is False


def test_unknown_start_stays_upcoming():
    # ufc held ticker with no milestone in the index -> honest UPCOMING (never a fabricated LIVE)
    assert LV._event_underway("ufc", ["KXUFCFIGHT-26SEP12-AB"], {}, {}, NOW, {}) is False


def test_fed_never_uses_milestone_even_with_matching_key():
    # fed is NOT in the milestone allowlist -> even a start index whose key EXACTLY matches fed's event ticker
    # (a hypothetical mis-swept / future-calendar entry) can never make fed read LIVE
    fed_evt = M.event_ticker("KXFED-26SEP-T3")            # 'KXFED-26SEP'
    assert LV._event_underway("fed", ["KXFED-26SEP-T3"], {}, {}, NOW, {fed_evt: NOW - 1}) is False
    assert LV.start_ts_for_ticker("fed", "KXFED-26SEP-T3", {fed_evt: NOW - 1}) is None


def test_live_capable_category_never_uses_milestone():
    # a LIVE_CAPABLE category keeps its ticker-HHMM/feed source: a HHMM-less LIVE_CAPABLE ticker stays UNKNOWN even
    # when a milestone exists for it (the milestone must not bypass the feed/HHMM authority). cs2 ticker here has no
    # HHMM group, so parse_ticker_start -> None; the milestone is present but must be IGNORED.
    evt = "KXCSGOGAME-26SEP12TEAMAB"
    starts = {evt: NOW - 600}
    assert LV.start_ts_for_ticker("cs2", evt + "-YES", starts) is None
    assert LV._event_underway("cs2", [evt + "-YES"], {}, {}, NOW, starts) is False


def test_mlb_no_hhmm_ticker_does_not_borrow_milestone_when_feed_down():
    # MLB with the feed DOWN (feed_games empty) and a (contrived) HHMM-less MLB ticker: the milestone must NOT make
    # it LIVE -- MLB is feed/HHMM authoritative, so no HHMM + no feed = honest UPCOMING (feed-authority preserved).
    tk = "KXMLBGAME-BADFORMAT-CHC"                        # no YYMONDD+HHMM -> parse_ticker_start returns None
    assert LV.parse_ticker_start("mlb", tk) is None
    starts = {M.event_ticker(tk): NOW - 600}
    assert LV._event_underway("mlb", [tk], {}, {}, NOW, starts) is False   # feed_games={} -> generic loop, milestone ignored


def test_milestone_start_categories_membership():
    # the allowlist covers the date-only sports the feed serves, and EXCLUDES every ticker-HHMM/feed cat + fed
    for c in ("atp", "wta", "ufc", "epl", "ucl", "uel", "lal", "fl1", "sea", "bun", "mls", "bra", "mex"):
        assert c in LV.MILESTONE_START_CATEGORIES, c
    for c in ("mlb", "cs2", "nfl", "cfb", "nba", "nhl", "wnba", "fed", "soccer"):
        assert c not in LV.MILESTONE_START_CATEGORIES, c


def test_none_index_reproduces_pre_feed_behaviour():
    # starts=None: date-only categories behave EXACTLY as before the milestone feed (UPCOMING), the backward-compat gate
    assert LV._event_underway("atp", ["KXATPMATCH-26SEP12ABCDEF-XYZ"], {}, {}, NOW, None) is False
    assert LV._event_underway("wta", ["KXWTAMATCH-26SEP12GHIJKL-XYZ"], {}, {}, NOW, None) is False


def test_finalized_market_not_underway_even_if_started():
    starts = {"KXATPMATCH-26SEP12ABCDEF": NOW - 600}
    marks = {"KXATPMATCH-26SEP12ABCDEF-XYZ": marks_mod.Mark(
        ticker="KXATPMATCH-26SEP12ABCDEF-XYZ", yes_bid=None, no_bid=None, yes_ask=None, no_ask=None,
        last=None, status="finalized", as_of=NOW)}
    assert LV._event_underway("atp", ["KXATPMATCH-26SEP12ABCDEF-XYZ"], {}, marks, NOW, starts) is False


def test_mlb_feed_is_authoritative_over_milestone():
    tk = "KXMLBGAME-26AUG301920CINCHC-CHC"
    # milestone says started long ago, but the feed knows the game is NOT live -> NOT underway (feed wins)
    starts = {M.event_ticker(tk): NOW - 99999}

    class _G:
        is_live = False
    orig = feed_mlb.match_in_slate
    try:
        feed_mlb.match_in_slate = lambda *a, **k: _G()
        assert LV._event_underway("mlb", [tk], {"x": 1}, {}, NOW, starts) is False
        feed_mlb.match_in_slate = lambda *a, **k: type("G2", (), {"is_live": True})()
        assert LV._event_underway("mlb", [tk], {"x": 1}, {}, NOW, starts) is True
    finally:
        feed_mlb.match_in_slate = orig


def test_live_event_block_non_mlb_via_milestone():
    starts = {"KXATPMATCH-26SEP12ABCDEF": NOW - 600}
    ev = LV._live_event("atp", [_pos("KXATPMATCH-26SEP12ABCDEF-XYZ")], {}, {}, NOW, starts)
    assert ev is not None
    assert len(ev["rows"]) == 1
    row = ev["rows"][0]
    assert row["has_scoreboard"] is False               # no scores in scope for non-MLB
    assert row["label"] and "KXATPMATCH" not in row["label"]   # R2: never a raw ticker
    assert len(row["positions"]) == 1


def test_live_event_none_when_no_start():
    assert LV._live_event("atp", [_pos("KXATPMATCH-26SEP12ABCDEF-XYZ")], {}, {}, NOW, {}) is None


def test_next_event_countdown_via_milestone():
    starts = {"KXATPMATCH-26SEP12ABCDEF": NOW + 3600}
    ne = LV._next_event("atp", [_pos("KXATPMATCH-26SEP12ABCDEF-XYZ")], {}, NOW, starts)
    assert ne["starts_in_seconds"] == 3600
    # unknown start -> no countdown (template shows 'start time unavailable'), never a guessed time
    ne2 = LV._next_event("atp", [_pos("KXATPMATCH-26SEP12ABCDEF-XYZ")], {}, NOW, {})
    assert ne2["starts_in_seconds"] is None


def _atp_ctx(starts):
    """Build the /live context for one armed+attached atp sub holding one started match, at the given start index."""
    subs = [{"account_id": "kalshi_jack", "category": "atp", "n_whales": 1, "n_live_trades": 1}]
    arm_all = {"global": {"state": "armed", "ts": NOW}, "subs": {("kalshi_jack", "atp"): {
        "sub_state": "armed", "sub_ts": NOW, "effective_state": "armed"}}}
    positions_by_sub = {("kalshi_jack", "atp"): [_pos("KXATPMATCH-26SEP12ABCDEF-XYZ")]}
    return LV.build_subdivisions_context(
        subs=subs, accounts_meta=[{"account_id": "kalshi_jack"}], arm_all=arm_all, liveness_by_sub={},
        liveness_present=False, pnl_all={}, realized_windows={}, positions_by_sub=positions_by_sub,
        last_events={}, marks={}, feed_games={}, now_ts=NOW, thin_floor=50, mark_age_sec=None,
        active_account="kalshi_jack", viewer_role="admin", viewer_account=None, logo_codes={"ATP"},
        poll_interval=60, global_arm={}, max_order_id=0, starts=starts)


def test_build_subdivisions_context_classifies_atp_live_via_milestone():
    # end-to-end through the pure assembler: an atp sub holding a started match -> LIVE tile with an event block
    ctx = _atp_ctx({"KXATPMATCH-26SEP12ABCDEF": NOW - 600})
    live = [t for t in ctx["sections"]["LIVE"] if t["category"] == "atp"]
    assert len(live) == 1 and live[0]["activity"] == "LIVE"
    assert live[0]["event"] is not None and len(live[0]["event"]["rows"]) == 1


def test_build_subdivisions_context_atp_upcoming_without_milestone():
    # the SAME sub with NO milestone index reads UPCOMING (the pre-feed behaviour) -- the feed is what flips it
    ctx = _atp_ctx(None)
    assert not [t for t in ctx["sections"]["LIVE"] if t["category"] == "atp"]
    up = [t for t in ctx["sections"]["UPCOMING"] if t["category"] == "atp"]
    assert len(up) == 1 and up[0]["activity"] == "UPCOMING"
