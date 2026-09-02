"""pm_web background poller (UI rewrite): refreshes the feed slates + Kalshi marks into ui_cache every ~60s.

ONE task, ONE writer (single-worker uvicorn). It does NO DB access: it fetches the ET date window (today +
yesterday, covering the 24h card-retention) of sports slates and the current Kalshi marks for the MLB series,
enriches last-play for LIVE games, and swaps the result into the cache. All the DB reads + the journal<->game
join happen at RENDER time in live_view -- the poller is purely the network-into-cache loop, so it can never
touch an order path and never stalls the event loop (blocking urllib fetches run via asyncio.to_thread).

Started/stopped from the app lifespan. It RUNS only while pm_web is up; building it is in scope, running it in
prod is a (Board) restart. Absent the task, renders read an empty cache and degrade to warming-up/unavailable.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from . import feed_mlb, marks as marks_mod, ui_cache

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
_LASTPLAY_MAX = 16   # only live games get a last-play fetch; a full slate is ~15 games -> bounded per cycle


def eastern_date_window(now_ts: int) -> list[str]:
    """The ET calendar dates whose games can still be on a card: today and yesterday (Eastern), covering the
    24h retention from a night game's end. Deterministic from the clock; no DB."""
    now_utc = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    today_et = feed_mlb.utc_to_eastern(now_utc).date()
    return [today_et.isoformat(), (today_et - timedelta(days=1)).isoformat()]


def _enrich_last_play(slate, now_ts: int, http_get=feed_mlb._http_get_json):
    """Return a SlateResult with last_play filled for LIVE games (bounded). Best-effort: a failed fetch leaves
    last_play None. StatsAPI-sourced games only (ESPN already carries last_play inline)."""
    if not slate.ok or not slate.games:
        return slate
    games = dict(slate.games)
    n = 0
    for key, gs in list(games.items()):
        if n >= _LASTPLAY_MAX:
            break
        if gs.is_live and gs.game_pk and gs.last_play is None:
            n += 1
            lp = feed_mlb.fetch_last_play(gs.game_pk, http_get=http_get)
            if lp:
                games[key] = replace(gs, last_play=lp)
    return replace(slate, games=games)


def refresh_once(cache: ui_cache.UICache, *, now_ts: int,
                 fetch_slate=feed_mlb.fetch_slate, fetch_marks=marks_mod.fetch_marks,
                 enrich=True) -> None:
    """One synchronous refresh pass (runs off the loop via asyncio.to_thread from poll_loop). Fetches slates for
    the ET date window + current marks and swaps them into the cache. NEVER raises -- a failure still writes a
    snapshot (empty/degraded) so the render shows honest unavailable, not a stale value."""
    errors = []
    slates = {}
    for d in eastern_date_window(now_ts):
        try:
            slate = fetch_slate(d, now_ts=now_ts)
            if enrich:
                slate = _enrich_last_play(slate, now_ts)
            slates[d] = slate
            if not slate.ok:
                errors.append("feed:%s:%s" % (d, slate.error or "empty"))
        except Exception as exc:   # noqa: BLE001 -- a bad slate must not sink the whole refresh
            errors.append("feed:%s:%s" % (d, type(exc).__name__))
            log.warning("pm poller: slate %s failed (%s)", d, type(exc).__name__)
    try:
        mk = fetch_marks(now_ts=now_ts)
        if not mk.ok:
            errors.append("marks:%s" % (mk.error or "empty"))
    except Exception as exc:   # noqa: BLE001
        mk = None
        errors.append("marks:%s" % type(exc).__name__)
        log.warning("pm poller: marks failed (%s)", type(exc).__name__)
    cache.update(slates=slates, marks=mk, refreshed_ts=now_ts, last_error=";".join(errors) or None)


async def poll_loop(cache: ui_cache.UICache, *, interval: int = POLL_INTERVAL_SECONDS) -> None:
    """The forever loop: refresh immediately, then every `interval`s. Resilient -- a raised cycle is logged and
    the loop continues (a transient feed/network blip must not kill the poller). Cancels cleanly on shutdown."""
    log.info("pm poller: starting (interval=%ss)", interval)
    while True:
        try:
            await asyncio.to_thread(refresh_once, cache, now_ts=int(time.time()))
        except asyncio.CancelledError:
            log.info("pm poller: cancelled -- stopping")
            raise
        except Exception as exc:   # noqa: BLE001 -- never let the loop die on a transient error
            log.warning("pm poller: refresh cycle raised (%s) -- continuing", type(exc).__name__)
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
