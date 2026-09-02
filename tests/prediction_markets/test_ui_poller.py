"""Unit tests for the pm_web live cache + background poller (UI rewrite). No network -- fetchers injected."""
from trading_corp.prediction_markets.web import ui_cache, poller, feed_mlb, marks as marks_mod


def test_eastern_date_window_is_today_and_yesterday():
    import calendar
    from datetime import datetime, timezone
    # 2026-09-02 12:00Z -> ET 08:00 Sep2; window = [Sep2, Sep1]
    ts = calendar.timegm(datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc).utctimetuple())
    assert poller.eastern_date_window(ts) == ["2026-09-02", "2026-09-01"]


def _slate(date_iso, ok=True, games=None):
    return feed_mlb.SlateResult(date_iso, games or {}, ok, "statsapi" if ok else None, 100,
                                None if ok else "down")


def test_refresh_once_populates_cache():
    c = ui_cache.UICache()
    def fs(d, now_ts):
        return _slate(d, ok=True, games={("k",): "gs"})
    def fm(now_ts):
        return marks_mod.MarksResult(marks={"T": "m"}, ok=True, as_of=now_ts)
    poller.refresh_once(c, now_ts=1000, fetch_slate=fs, fetch_marks=fm, enrich=False)
    snap = c.snapshot()
    assert snap.ready and snap.refreshed_ts == 1000
    assert set(snap.slates) == set(poller.eastern_date_window(1000))
    assert snap.marks.ok and snap.last_error is None


def test_refresh_once_degrades_but_still_writes_snapshot():
    c = ui_cache.UICache()
    def fs(d, now_ts):
        raise TimeoutError("feed down")
    def fm(now_ts):
        raise TimeoutError("marks down")
    poller.refresh_once(c, now_ts=2000, fetch_slate=fs, fetch_marks=fm, enrich=False)
    snap = c.snapshot()
    assert snap.ready is True                     # a snapshot IS written (honest degrade, not a hang)
    assert snap.marks is None and snap.last_error
    assert "feed:" in snap.last_error and "marks:" in snap.last_error


def test_marks_failure_alone_still_keeps_slates():
    c = ui_cache.UICache()
    def fs(d, now_ts):
        return _slate(d, ok=True, games={("k",): "gs"})
    def fm(now_ts):
        return marks_mod.MarksResult(marks={}, ok=False, as_of=now_ts, error="all down")
    poller.refresh_once(c, now_ts=3000, fetch_slate=fs, fetch_marks=fm, enrich=False)
    snap = c.snapshot()
    assert all(s.ok for s in snap.slates.values())
    assert snap.marks.ok is False and "marks:" in snap.last_error


def test_cache_update_is_atomic_snapshot():
    c = ui_cache.UICache()
    before = c.snapshot()
    assert before.ready is False and before.slates == {}
    c.update(slates={"2026-09-02": _slate("2026-09-02")}, marks=None, refreshed_ts=5)
    after = c.snapshot()
    assert before.ready is False and after.ready is True   # old snapshot object unchanged (immutable swap)


def test_enrich_last_play_fills_live_game():
    live = feed_mlb.GameState(key=("2026-09-02", "1240", None, frozenset({"A", "B"})), date_iso="2026-09-02",
                              hhmm_et="1240", game_no=None, source="statsapi", fetched_ts=1, game_pk="999",
                              status="in_progress",
                              away=feed_mlb.TeamState("A", "A", None, 1), home=feed_mlb.TeamState("B", "B", None, 2),
                              inning=3, half="TOP", outs=1, balls=0, strikes=0, bases=(False, False, False),
                              linescore_away=(1,), linescore_home=(2,), last_play=None)
    slate = feed_mlb.SlateResult("2026-09-02", {live.key: live}, True, "statsapi", 1)
    out = poller._enrich_last_play(slate, now_ts=1, http_get=lambda url, timeout=8.0: {
        "liveData": {"plays": {"currentPlay": {"result": {"description": "Home run to left."}}}}})
    assert list(out.games.values())[0].last_play == "Home run to left."
