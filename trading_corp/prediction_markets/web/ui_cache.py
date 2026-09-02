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
    never fabricated values)."""
    slates: dict = field(default_factory=dict)
    marks: object = None
    refreshed_ts: int | None = None
    ready: bool = False
    last_error: str | None = None


class UICache:
    def __init__(self) -> None:
        self._snap = CacheSnapshot()
        self._lock = threading.Lock()

    def snapshot(self) -> CacheSnapshot:
        with self._lock:
            return self._snap

    def update(self, *, slates: dict, marks, refreshed_ts: int, last_error: str | None = None) -> None:
        """Atomically swap in a new snapshot (whole-object replace under the lock -- a reader either sees the old
        snapshot or the new one, never a torn mix)."""
        snap = CacheSnapshot(slates=dict(slates), marks=marks, refreshed_ts=refreshed_ts,
                             ready=True, last_error=last_error)
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
