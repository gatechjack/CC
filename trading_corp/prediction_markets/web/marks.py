"""Kalshi current-mark reader for pm_web (UI rewrite, Scope E). CURRENT MARK ONLY -- no price history.

STANDALONE + CREDENTIAL-FREE by construction. The market-data endpoint is PUBLIC: a probe from the box
(2026-09-02) returned HTTP 200 UNAUTHENTICATED with yes_bid/no_bid per market. So pm_web reads it directly
with the stdlib -- it does NOT import the engine broker, does NOT sign, and holds NO Kalshi credentials
(preserving the pm_web can-never-place-an-order guarantee). If Kalshi ever closed this endpoint behind auth,
this whole reader would fail -> ok=False -> every value degrades to the honest "no mark" state; pm_web would
still never gain credentials (that would be the engine's job to write a mark per cycle instead).

Card/drawer values are contracts x BID of the HELD leg (yes_bid for a YES position, no_bid for a NO position),
labelled as bid in the caveat -- the conservative, marketable side, never the ask or the mid.

Pure parse (`parse_markets`) is separated from the paginating fetch so it is unit-testable against a captured
markets page. One paginated call per series covers the whole slate; only OPEN markets are fetched (a settled
position needs no mark -- it is resolved).
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass

log = logging.getLogger(__name__)

# The public, key-less Kalshi market-data host (prod). Fractional MLB series expose authoritative *_dollars.
_MARKETS = "https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=%s&status=open&limit=200"
_UA = "curl/8.4.0"
_MAX_PAGES = 20   # runaway guard; 200/page * 20 = 4000 markets, far above a day's MLB slate

# The three MLB market-type series pm_web copies. Held tickers are one of these three prefixes.
MLB_SERIES = ("KXMLBGAME", "KXMLBTOTAL", "KXMLBSPREAD")


@dataclass(frozen=True)
class Mark:
    """One market's current top-of-book, in dollars (0..1). Any leg may be None (no resting bid on that side)."""
    ticker: str
    yes_bid: float | None
    no_bid: float | None
    yes_ask: float | None
    no_ask: float | None
    last: float | None
    status: str | None
    as_of: int          # unix seconds when fetched -- the mark's own age


@dataclass(frozen=True)
class MarksResult:
    marks: dict          # {ticker: Mark}
    ok: bool
    as_of: int | None
    error: str | None = None


def bid_for_leg(mark: Mark | None, leg: str | None) -> float | None:
    """The held leg's BID (the value side). 'yes' -> yes_bid, 'no' -> no_bid. None when we have no mark or no
    resting bid on that leg -> the caller renders the honest no-mark state (never $0, never the cost basis)."""
    if mark is None or not leg:
        return None
    return mark.yes_bid if str(leg).lower() == "yes" else mark.no_bid if str(leg).lower() == "no" else None


def _f(v):
    """Kalshi returns *_dollars as strings ('0.7100'); '' / None / unparseable -> None (never 0.0, which would
    read as a real zero bid)."""
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_markets(page_json: dict, *, now_ts: int) -> list[Mark]:
    """One /markets response page -> [Mark]. Pure; no network."""
    out = []
    for m in (page_json or {}).get("markets", []) or []:
        t = m.get("ticker")
        if not t:
            continue
        out.append(Mark(ticker=t, yes_bid=_f(m.get("yes_bid_dollars")), no_bid=_f(m.get("no_bid_dollars")),
                        yes_ask=_f(m.get("yes_ask_dollars")), no_ask=_f(m.get("no_ask_dollars")),
                        last=_f(m.get("last_price_dollars")), status=m.get("status"), as_of=now_ts))
    return out


def _http_get_json(url: str, *, timeout: float = 12.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 -- fixed https market-data host
        return json.loads(resp.read().decode("utf-8"))


def fetch_series_marks(series_ticker: str, *, now_ts: int, http_get=_http_get_json) -> dict:
    """All OPEN markets for one series -> {ticker: Mark}, following the cursor. Raises on transport/parse error
    (the caller aggregates and decides ok/degrade)."""
    marks: dict = {}
    cursor = ""
    for _ in range(_MAX_PAGES):
        url = _MARKETS % series_ticker
        if cursor:
            url += "&cursor=" + cursor
        page = http_get(url)
        for mk in parse_markets(page, now_ts=now_ts):
            marks[mk.ticker] = mk
        cursor = (page or {}).get("cursor") or ""
        if not cursor:
            break
    return marks


def fetch_marks(series=MLB_SERIES, *, now_ts: int, http_get=_http_get_json) -> MarksResult:
    """Fetch current marks for the given series and merge into one {ticker: Mark}. NEVER raises: if EVERY series
    fails, ok=False with an empty map (every current-value surface degrades to no-mark). A partial success (some
    series fetched, one failed) is still ok=True with whatever was collected -- a missing ticker simply has no
    mark, which the caller already renders honestly."""
    merged: dict = {}
    errors = []
    for s in series:
        try:
            merged.update(fetch_series_marks(s, now_ts=now_ts, http_get=http_get))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError, OSError) as exc:
            errors.append("%s:%s" % (s, type(exc).__name__))
            log.warning("pm marks: series %s fetch failed (%s)", s, type(exc).__name__)
    # ok unless EVERY series errored and nothing was collected (then every value degrades to no-mark).
    ok = bool(merged) or not errors
    return MarksResult(marks=merged, ok=ok, as_of=now_ts, error=";".join(errors) or None)
