"""Unit tests for the pm_web Kalshi mark reader (Scope E). Pure parse + aggregation; fetch is injected."""
from trading_corp.prediction_markets.web import marks as m


def _page(markets, cursor=""):
    return {"markets": markets, "cursor": cursor}


def _mkt(ticker, yb="0.58", nb="0.41", ya="0.60", na="0.43", last="0.59", status="active"):
    return {"ticker": ticker, "yes_bid_dollars": yb, "no_bid_dollars": nb, "yes_ask_dollars": ya,
            "no_ask_dollars": na, "last_price_dollars": last, "status": status}


def test_parse_markets_reads_dollars():
    (mk,) = m.parse_markets(_page([_mkt("KXMLBGAME-26SEP021240SDCIN-SD")]), now_ts=7)
    assert mk.ticker.endswith("SDCIN-SD")
    assert mk.yes_bid == 0.58 and mk.no_bid == 0.41 and mk.last == 0.59 and mk.status == "active"
    assert mk.as_of == 7


def test_missing_bid_is_none_not_zero():
    # an empty / absent bid must be None (no resting bid), never 0.0 -- a 0.0 would read as a real zero bid.
    (mk,) = m.parse_markets(_page([_mkt("T", yb="", nb=None)]), now_ts=1)
    assert mk.yes_bid is None and mk.no_bid is None


def test_bid_for_leg_picks_held_side():
    (mk,) = m.parse_markets(_page([_mkt("T", yb="0.58", nb="0.41")]), now_ts=1)
    assert m.bid_for_leg(mk, "yes") == 0.58
    assert m.bid_for_leg(mk, "no") == 0.41
    assert m.bid_for_leg(mk, None) is None
    assert m.bid_for_leg(None, "yes") is None


def test_fetch_series_follows_cursor():
    pages = {"": _page([_mkt("A")], cursor="c1"), "c1": _page([_mkt("B")], cursor="")}
    def get(url, timeout=12.0):
        cur = url.split("cursor=")[1] if "cursor=" in url else ""
        return pages[cur]
    res = m.fetch_series_marks("KXMLBGAME", now_ts=1, http_get=get)
    assert set(res) == {"A", "B"}


def test_fetch_marks_partial_failure_is_ok():
    def get(url, timeout=12.0):
        if "KXMLBTOTAL" in url:
            raise TimeoutError("totals down")
        return _page([_mkt("KXMLBGAME-X")])
    res = m.fetch_marks(now_ts=1, http_get=get)
    assert res.ok and "KXMLBGAME-X" in res.marks and res.error and "KXMLBTOTAL" in res.error


def test_fetch_marks_total_failure_is_not_ok():
    def get(url, timeout=12.0):
        raise TimeoutError("all down")
    res = m.fetch_marks(now_ts=1, http_get=get)
    assert res.ok is False and res.marks == {}
