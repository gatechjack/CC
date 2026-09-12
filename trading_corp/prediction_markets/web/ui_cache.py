"""pm_web-owned live cache for the UI rewrite: the sports-feed slates + Kalshi marks, each stamped with its own
as_of, refreshed by the background poller and read by the render.

WHY A CACHE, NOT A TABLE (the brief's "your call, but not an engine table"): pm_web runs as a SINGLE uvicorn
process (loopback, one worker -- see scripts/pm_web.py), so an in-process cache is coherent for every request and
touches NO database schema -- it cannot alter, or even reach for, an engine-owned table. It is volatile by design
(current-mark-only, no history): on a restart it simply repopulates within one 60s poll. Access is guarded by a
lock and served as an immutable snapshot so a render never sees a half-written refresh.

Holds NOTHING credential-bearing. Renders read `snapshot()` and band each value by its own as_of.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CacheSnapshot:
    """An immutable view of the last completed poll. `slates` maps date_iso -> feed_mlb.SlateResult; `marks` is a
    marks.MarksResult (or None before the first poll). `refreshed_ts` is when this snapshot was assembled -- the
    'generated' age the header shows. `ready` is False until the first poll completes (render shows 'warming up',
    never fabricated values).

    MILESTONE START-TIME INDEX (2026-09-12): `starts` maps a Kalshi EVENT ticker -> unix start ts, the cross-category
    LIVE/UPCOMING feed from milestones.fetch_starts. It is refreshed on a SLOW cadence (start times don't change), so
    it PERSISTS across the 60s marks/feed polls -- each poll carries it forward. `starts_as_of` is the last successful
    sweep; `starts_attempt_ts` gates the retry cadence; `starts_ok`/`starts_error` are the last attempt's outcome.
    Absent/unknown -> the classifier stays honest (UPCOMING)."""
    slates: dict = field(default_factory=dict)
    marks: object = None
    refreshed_ts: int | None = None
    ready: bool = False
    last_error: str | None = None
    starts: dict = field(default_factory=dict)
    starts_as_of: int | None = None
    starts_attempt_ts: int | None = None
    starts_ok: bool = False
    starts_error: str | None = None


class UICache:
    def __init__(self) -> None:
        self._snap = CacheSnapshot()
        self._lock = threading.Lock()

    def snapshot(self) -> CacheSnapshot:
        with self._lock:
            return self._snap

    def update(self, *, slates: dict, marks, refreshed_ts: int, last_error: str | None = None,
               starts: dict | None = None, starts_as_of: int | None = None,
               starts_attempt_ts: int | None = None, starts_ok: bool = False,
               starts_error: str | None = None) -> None:
        """Atomically swap in a new snapshot (whole-object replace under the lock -- a reader either sees the old
        snapshot or the new one, never a torn mix). The milestone start-time index is refreshed on its OWN slow
        cadence, so the poller reads the prior snapshot and passes the reused-or-refreshed `starts*` fields back in
        on EVERY cycle -- omitting them (a caller that only writes slates/marks) resets the index, which is why the
        poller always forwards them."""
        snap = CacheSnapshot(slates=dict(slates), marks=marks, refreshed_ts=refreshed_ts,
                             ready=True, last_error=last_error, starts=dict(starts or {}),
                             starts_as_of=starts_as_of, starts_attempt_ts=starts_attempt_ts,
                             starts_ok=starts_ok, starts_error=starts_error)
        with self._lock:
            self._snap = snap

    # convenience reads (each returns None/absent honestly -> the caller degrades)
    def slate(self, date_iso: str):
        return self.snapshot().slates.get(date_iso)

    def marks(self):
        return self.snapshot().marks


# process-wide singleton (single-worker uvicorn). The app wires the poller to write it; renders read it.
_CACHE = UICache()


def cache() -> UICache:
    return _CACHE
