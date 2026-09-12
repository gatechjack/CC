"""Kalshi milestone START-TIME reader for pm_web -- the first cross-category LIVE/UPCOMING feed (2026-09-12).

WHAT THIS IS. The /live tile classifier can only mark a sub-division LIVE where it knows an event has STARTED.
Today that is MLB (a game feed) plus the sports whose Kalshi ticker carries an HHMM (cs2/nfl/nba/nhl/wnba/cfb).
Tennis, UFC and the soccer leagues have date-only tickers, so they stayed UPCOMING even once underway. This
reader lands a real cross-category START TIME from Kalshi's public milestones catalog; the classifier then decides
LIVE vs UPCOMING by a pure clock compare (start <= now, our position still open) -- no live scores, no per-event
call. START TIMES ONLY: live scores / play-by-play are deliberately OUT OF SCOPE (a separate, per-event cost).

STANDALONE + CREDENTIAL-FREE, exactly like marks.py. The milestone catalog is a FIRST-PARTY, PUBLIC, UNAUTHENTICATED
endpoint (probed 2026-09-12), so pm_web reads it with the stdlib -- it does NOT import the engine broker, does NOT
sign, and holds NO Kalshi credentials (preserving the pm_web can-never-place-an-order guarantee). If Kalshi ever
closed the endpoint behind auth, every sweep would fail -> ok=False -> the classifier keeps its previous index (or,
first-boot, has none) and every affected tile degrades HONESTLY to UPCOMING; pm_web would never gain credentials.

THE JOIN IS A CATALOG SWEEP, NOT A PER-TICKER LOOKUP. The documented ?related_event_ticker= filter returns HTTP 400
unauthenticated, so we cannot ask per ticker. We sweep the catalog once and index `related_event_tickers -> start`
LOCALLY. The key is EXACT and NON-FUZZY: a held market ticker minus its final '-<suffix>' == the event ticker
(probe-verified 14/14 vs /markets/{t}.event_ticker), and related_event_tickers is a SUPERSET carrying every
market-type event ticker for one game (moneyline / spread / total / 1H), so whichever series we hold resolves.

start_date IS A REAL UTC KICKOFF INSTANT (Polymarket convention), NOT the ticker's US-local date -- so the underway
test is a pure clock compare with no date reassembly. A minority of milestones carry a T00:00:00Z date-only
PLACEHOLDER instead of a real time; those MUST fall back to time-unknown (never read midnight as a start).

details.status arrives free in the catalog but LIES (the probe found finished matches still read not_started); it is
DELIBERATELY ignored. "Finished" is something we already know from our own settlement, so underway needs no status.

COST (pm_web runs ON THE BOX and shares the engine's Kalshi source IP -- a 429 storm there costs real copies, so the
sweep is BOUNDED). Start times DO NOT CHANGE, so the caller sweeps on a SLOW cadence (hours) and reuses the cached
index between sweeps -- NOT one call per event, and NOT every 60s. One sweep is a handful of paginated GETs:
  - minimum_start_date starts the window RECENTLY (now - LOOKBACK) so we never page from epoch through months of
    finished games (the probe's pagination-window near-miss). LOOKBACK covers the 24h card retention with margin.
  - the catalog is sorted ascending by start_date, so once a page's latest real start is past now + HORIZON we stop.
  - _MAX_PAGES is the hard backstop if that sort assumption ever breaks; hitting it is logged LOUD, never silent.
This mirrors the working sweep already proven in cc/pm_milestone_heldtickers_ro.ps1.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Public, key-less Kalshi milestone catalog (prod) -- same host + stance as marks.py. minimum_start_date and cursor
# are appended by the fetcher (minimum_start_date is sent LITERALLY, exactly as the proven probe runner does).
_MILESTONES = "https://api.elections.kalshi.com/trade-api/v2/milestones?category=%s&minimum_start_date=%s&limit=200"
_UA = "curl/8.4.0"

# The two catalogs that cover every category we run except fed: Sports (mlb/cfb/nfl/ufc/atp/wta/soccer leagues) and
# Esports (cs2, which lives under Esports, NOT Sports). fed is absent by construction -- the milestone category enum
# is {Sports, Elections, Esports, Crypto} with no Economics, and a rate decision has no start state.
CATALOG_CATEGORIES = ("Sports", "Esports")

# Cost bounds (see the module docstring).
LOOKBACK_SECONDS = 36 * 3600      # window START = now - 36h: covers the 24h card retention (game end ~= start+4h) + margin
HORIZON_SECONDS = 3 * 86400       # ascending-sort early-stop: we need starts only up to a few days out for LIVE/UPCOMING
_MAX_PAGES = 30                   # hard backstop per catalog: 200/page * 30 = 6000 milestones, far above a few days' slate


@dataclass(frozen=True)
class StartsResult:
    """The milestone start-time index for one sweep. `starts` maps EVENT ticker (a held market ticker minus its
    final '-<suffix>') -> unix start ts. `ok` is False ONLY if EVERY catalog failed (the caller then keeps its
    previous index rather than blanking it -- start times are immutable, so a stale index is safe). Diagnostics
    (`n_indexed`/`pages`/`capped`/`error`) travel so the poller can log what a sweep actually cost."""
    starts: dict
    ok: bool
    as_of: int | None
    error: str | None = None
    n_indexed: int = 0
    pages: int = 0
    capped: bool = False


def _parse_rfc3339(s):
    """RFC3339 UTC string ('2026-09-11T23:15:00Z') -> aware datetime, or None. Accepts a trailing 'Z' or an explicit
    offset; a naive value is treated as UTC. stdlib-only and version-safe (fromisoformat rejects 'Z' before 3.11, so
    we normalise it first)."""
    if not s or not isinstance(s, str):
        return None
    txt = s.strip()
    if txt.endswith("Z") or txt.endswith("z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def start_instant(start_date) -> int | None:
    """A milestone's start_date -> unix seconds, or None. None when unparseable OR a date-only PLACEHOLDER
    (T00:00:00Z): the probe found a minority of milestones carry a midnight placeholder rather than a real kickoff,
    which MUST fall back to time-unknown so the tile never reads midnight as a start. A real kickoff at exactly
    00:00:00Z is astronomically rare and indistinguishable from the placeholder -> treated as unknown, the honest
    and safe choice (an unknown start reads UPCOMING, never a fabricated LIVE)."""
    dt = _parse_rfc3339(start_date)
    if dt is None:
        return None
    u = dt.astimezone(timezone.utc)
    if u.hour == 0 and u.minute == 0 and u.second == 0 and u.microsecond == 0:
        return None
    return int(u.timestamp())


def event_ticker(market_ticker) -> str | None:
    """The Kalshi EVENT ticker for a held MARKET ticker = the market ticker minus its final '-<suffix>'
    (probe-verified 14/14 vs /markets/{t}.event_ticker), e.g. 'KXNCAAFTOTAL-26SEP11MIZZKU-52' ->
    'KXNCAAFTOTAL-26SEP11MIZZKU'. This is exactly how related_event_tickers keys the index, so the two sides join.
    None when the ticker has no '-' (nothing to strip -> no join)."""
    t = str(market_ticker or "").strip()
    if "-" not in t:
        return None
    return t.rsplit("-", 1)[0]


def start_for_event_ticker(starts, market_ticker) -> int | None:
    """The cached milestone start ts for a HELD market ticker, via its event ticker, or None. `starts` is the index
    from a sweep (event ticker -> start ts). None => start unknown -> the caller stays honest (UPCOMING)."""
    if not starts:
        return None
    et = event_ticker(market_ticker)
    return starts.get(et) if et else None


def parse_milestones(page_json) -> tuple:
    """One /milestones response page -> ([(event_ticker, start_ts)], max_real_start_ts_on_page). Pure; no network.
    Placeholders (T00:00:00Z) and unparseable starts are skipped (not emitted). details.status is IGNORED (it lies).
    max_real_start_ts is the latest real start on the page -- the caller uses it for the ascending-sort early-stop
    (None when a page carried no real start, which simply doesn't advance the early-stop)."""
    entries = []
    mx = None
    page = page_json if isinstance(page_json, dict) else {}   # a non-dict body (list/None) -> no milestones, no raise
    for m in page.get("milestones", []) or []:
        st = start_instant((m or {}).get("start_date"))
        if st is None:
            continue
        if mx is None or st > mx:
            mx = st
        for et in ((m or {}).get("related_event_tickers") or []):
            ets = str(et or "").strip()
            if ets:
                entries.append((ets, st))
    return entries, mx


def _rfc3339(ts) -> str:
    """Unix seconds -> the RFC3339 UTC string the catalog's minimum_start_date expects (literal, colons and all)."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _http_get_json(url: str, *, timeout: float = 12.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 -- fixed https catalog host, no auth
        return json.loads(resp.read().decode("utf-8"))


def _fetch_catalog(category, index, *, now_ts, http_get, max_pages, horizon_ts, min_start_iso) -> tuple:
    """Sweep ONE catalog into `index` (event ticker -> start ts), cursor-paginated, stopping at the HORIZON or the
    page cap. Returns (pages_fetched, capped). Raises on transport/parse error -- the caller aggregates per catalog
    so one failed catalog never sinks the other."""
    pages = 0
    capped = True                                    # assume capped until we break out on cursor-end or horizon
    cursor = ""
    for _ in range(max_pages):
        url = _MILESTONES % (category, min_start_iso)
        if cursor:
            url += "&cursor=" + urllib.parse.quote(cursor, safe="")   # cursor is opaque -> encode once (ctx-fix pattern)
        page = http_get(url)
        if not isinstance(page, dict):
            page = {}                                    # a non-dict body degrades to an empty page (no raise, no cursor)
        pages += 1
        entries, mx = parse_milestones(page)
        for et, st in entries:
            index[et] = st
        cursor = page.get("cursor") or ""
        if not cursor:
            capped = False
            break
        if mx is not None and mx > horizon_ts:       # ascending sort -> everything after is further out than we need
            capped = False
            break
    return pages, capped


def fetch_starts(*, now_ts: int, http_get=_http_get_json, categories=CATALOG_CATEGORIES,
                 max_pages: int = _MAX_PAGES, lookback_seconds: int = LOOKBACK_SECONDS,
                 horizon_seconds: int = HORIZON_SECONDS) -> StartsResult:
    """Sweep the milestone catalogs and return the start-time index. NEVER raises: if EVERY catalog fails, ok=False
    with an empty index (the caller keeps its previous index -- immutable start times make a stale index safe). A
    partial success (one catalog fetched, one failed) is ok=True with what was collected; a missing category simply
    yields no starts for its tickers, which the classifier already renders honestly as UPCOMING."""
    index: dict = {}
    errors = []
    total_pages = 0
    any_capped = False
    ok_any = False
    min_start_iso = _rfc3339(int(now_ts) - int(lookback_seconds))
    horizon_ts = int(now_ts) + int(horizon_seconds)
    for cat in categories:
        try:
            pages, capped = _fetch_catalog(cat, index, now_ts=now_ts, http_get=http_get, max_pages=max_pages,
                                           horizon_ts=horizon_ts, min_start_iso=min_start_iso)
            total_pages += pages
            any_capped = any_capped or capped
            ok_any = True
            if capped:
                log.warning("pm milestones: catalog %s hit the %d-page cap -- coverage may be partial", cat, max_pages)
        except Exception as exc:   # noqa: BLE001 -- fetch_starts MUST never raise: ANY catalog failure (transport,
            # JSON parse, or an unexpected body shape) degrades to that catalog's miss; the classifier then reads
            # honest UPCOMING for its tickers. One catalog failing must never sink the other or the whole refresh.
            errors.append("%s:%s" % (cat, type(exc).__name__))
            log.warning("pm milestones: catalog %s sweep failed (%s)", cat, type(exc).__name__)
    return StartsResult(starts=index, ok=ok_any, as_of=int(now_ts), error=";".join(errors) or None,
                        n_indexed=len(index), pages=total_pages, capped=any_capped)
