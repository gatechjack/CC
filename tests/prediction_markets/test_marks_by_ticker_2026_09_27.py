"""Mark-poller scoping fix (2026-09-27): price BY HELD TICKER via GET /markets/{ticker} instead of paginating a
whole series. Offline, stdlib + fake http_get only (no network, no venue, no credentials).

Proves: (1) fetch_ticker_mark parses the single-market {"market": {...}} shape; (2) fetch_marks_by_ticker
aggregates + de-dupes + NEVER raises on a per-ticker failure; (3) empty tickers -> ok, empty map;
(4) the poller prefers ticker_provider and falls back to the by-series default on empty/failure.
"""
from trading_corp.prediction_markets.web import poller as P
from trading_corp.prediction_markets.web import ui_cache, feed_mlb, marks as marks_mod

NOW = 1789200000


def _mkt(ticker, yb="0.07", nb="0.20", status="active", title="Team wins"):
    return {"market": {"ticker": ticker, "yes_bid_dollars": yb, "no_bid_dollars": nb,
                       "yes_ask_dollars": "0.80", "no_ask_dollars": "0.93",
                       "last_price_dollars": "0.00", "status": status, "title": title}}


def test_fetch_ticker_mark_parses_single_market_shape():
    got = marks_mod.fetch_ticker_mark("KXNCAAFGAME-A-FRES", now_ts=NOW,
                                      http_get=lambda url: _mkt("KXNCAAFGAME-A-FRES"))
    assert got is not None
    assert got.ticker == "KXNCAAFGAME-A-FRES"
    assert got.yes_bid == 0.07 and got.no_bid == 0.20
    assert got.title == "Team wins" and got.as_of == NOW


def test_fetch_ticker_mark_none_when_no_market_object():
    assert marks_mod.fetch_ticker_mark("KX-NOPE", now_ts=NOW, http_get=lambda url: {}) is None
    assert marks_mod.fetch_ticker_mark("KX-NOPE", now_ts=NOW, http_get=lambda url: {"market": None}) is None


def test_fetch_marks_by_ticker_aggregates_and_dedupes():
    seen = []

    def http(url):
        seen.append(url)
        tk = url.rsplit("/", 1)[-1]
        return _mkt(tk)

    res = marks_mod.fetch_marks_by_ticker(["KXA-1", "KXB-2", "KXA-1"], now_ts=NOW, http_get=http)
    assert res.ok is True
    assert set(res.marks.keys()) == {"KXA-1", "KXB-2"}          # deduped: KXA-1 fetched once
    assert len(seen) == 2
    assert res.error is None


def test_fetch_marks_by_ticker_never_raises_on_per_ticker_failure():
    import urllib.error

    def http(url):
        if url.endswith("KXBAD-9"):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return _mkt(url.rsplit("/", 1)[-1])

    res = marks_mod.fetch_marks_by_ticker(["KXOK-1", "KXBAD-9"], now_ts=NOW, http_get=http)
    assert res.ok is True                                       # one priced -> ok
    assert set(res.marks.keys()) == {"KXOK-1"}
    assert res.error and "KXBAD-9" in res.error


def test_fetch_marks_by_ticker_all_fail_is_not_ok():
    import urllib.error

    def boom(url):
        raise urllib.error.URLError("down")

    res = marks_mod.fetch_marks_by_ticker(["KXA-1", "KXB-2"], now_ts=NOW, http_get=boom)
    assert res.ok is False and not res.marks and res.error


def test_fetch_marks_by_ticker_empty_is_ok_empty():
    res = marks_mod.fetch_marks_by_ticker([], now_ts=NOW, http_get=lambda url: _mkt("x"))
    assert res.ok is True and res.marks == {}


def test_zero_of_n_logs_warning(caplog):
    # requested 2, priced 0 (both return no market object) -> the 0-of-N total-outage WARNING must fire
    import logging
    with caplog.at_level(logging.WARNING, logger="trading_corp.prediction_markets.web.marks"):
        res = marks_mod.fetch_marks_by_ticker(["KXA-1", "KXB-2"], now_ts=NOW, http_get=lambda url: {"market": None})
    assert res.marks == {}
    assert any("0 of 2 requested tickers returned a mark" in r.getMessage() for r in caplog.records)


def test_partial_success_does_not_log_zero_of_n(caplog):
    import logging

    def http(url):
        return _mkt(url.rsplit("/", 1)[-1]) if url.endswith("KXA-1") else {"market": None}

    with caplog.at_level(logging.WARNING, logger="trading_corp.prediction_markets.web.marks"):
        res = marks_mod.fetch_marks_by_ticker(["KXA-1", "KXB-2"], now_ts=NOW, http_get=http)
    assert set(res.marks.keys()) == {"KXA-1"}
    assert not any("0 of" in r.getMessage() for r in caplog.records)


def _slate_ok(d, *, now_ts):
    return feed_mlb.SlateResult(date_iso=d, games={}, ok=True, source="test", as_of=now_ts, error=None)


def test_poller_prefers_ticker_provider():
    cache = ui_cache.UICache()
    got = {"by_ticker": None, "by_series": 0}

    def fbt(tickers, *, now_ts):
        got["by_ticker"] = tuple(tickers)
        return marks_mod.MarksResult(marks={"KXA-1": object()}, ok=True, as_of=now_ts, error=None)

    def fm(*a, now_ts, **k):
        got["by_series"] += 1
        return marks_mod.MarksResult(marks={}, ok=True, as_of=now_ts, error=None)

    P.refresh_once(cache, now_ts=NOW, fetch_slate=_slate_ok, fetch_marks=fm, fetch_marks_by_ticker=fbt,
                   enrich=False, ticker_provider=lambda: ["KXA-1", "KXB-2"], fetch_starts=None)
    assert got["by_ticker"] == ("KXA-1", "KXB-2")              # priced by held ticker
    assert got["by_series"] == 0                               # series path NOT used


def test_poller_falls_back_to_series_default_when_no_held_tickers():
    cache = ui_cache.UICache()
    got = {"by_ticker": 0, "by_series": 0}

    def fbt(tickers, *, now_ts):
        got["by_ticker"] += 1
        return marks_mod.MarksResult(marks={}, ok=True, as_of=now_ts, error=None)

    def fm(*a, now_ts, **k):
        got["by_series"] += 1
        return marks_mod.MarksResult(marks={}, ok=True, as_of=now_ts, error=None)

    P.refresh_once(cache, now_ts=NOW, fetch_slate=_slate_ok, fetch_marks=fm, fetch_marks_by_ticker=fbt,
                   enrich=False, ticker_provider=lambda: [], fetch_starts=None)
    assert got["by_ticker"] == 0                               # empty held -> not priced by ticker
    assert got["by_series"] == 1                               # fell back to the by-series MLB default
