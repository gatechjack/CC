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
    marks.MarksResult (or None before the first poll) whose `.marks` is the MERGED {ticker: Mark} (see update()).
    `refreshed_ts` is when this snapshot was assembled. `ready` is False until the first poll completes (render shows
    'marks loading', never fabricated values). `titles` (2026-09-12, Item 3.1) is the ticker->title map PERSISTED
    ACROSS POLLS and NEVER evicted -- a ticker that has EVER resolved a title keeps its human name through a failed or
    partial fetch; the render reads the title from HERE, not from a Mark a failed poll may lack.

    MILESTONE START-TIME INDEX (2026-09-12): `starts` maps a Kalshi EVENT ticker -> unix start ts, the cross-category
    LIVE/UPCOMING feed from milestones.fetch_starts, refreshed on a SLOW cadence and carried forward each poll.
    `starts_as_of` is the last successful sweep; `starts_attempt_ts` gates the retry cadence; `starts_ok`/
    `starts_error` are the last attempt's outcome. Absent/unknown -> the classifier stays honest (UPCOMING)."""
    slates: dict = field(default_factory=dict)
    marks: object = None
    refreshed_ts: int | None = None
    ready: bool = False
    last_error: str | None = None
    titles: dict = field(default_factory=dict)
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
        """Atomically swap in a new snapshot. ★ MARKS ARE MERGED, NOT REPLACED (2026-09-12, Item 3.2): a poll where a
        high-cardinality series HTTPErrors returns a PARTIAL map (that series' tickers absent). Replacing wholesale
        WIPED the failed series' prior marks -> the page flipped to 'no mark / 0 priced' (the observed alternation).
        Instead we overlay the new marks onto the prior ones: a ticker the new poll DID return gets the fresh Mark
        (fresh as_of); a ticker it did NOT return keeps its PRIOR Mark with its OLD as_of -> the render shows the last
        value banded by its real age, never blank, never a stale value presented as fresh. ★ TITLES accumulate
        separately and are NEVER evicted (Item 3.1). ★ The milestone `starts*` index is refreshed on its OWN slow
        cadence, so the poller reads the prior snapshot and passes the reused-or-refreshed `starts*` back in EVERY
        cycle -- they are carried onto the new snapshot here (omitting them would reset the index). (Merged-marks
        growth is bounded by the finite open-market set + pm_web's own restart; the render only reads HELD tickers.)"""
        from . import marks as _marks_mod   # lazy: avoid an import cycle; build the merged MarksResult with its class
        with self._lock:
            prior = self._snap
        prior_marks = dict(getattr(prior.marks, "marks", None) or {})
        new_marks = dict(getattr(marks, "marks", None) or {})
        merged = prior_marks
        merged.update(new_marks)                          # new wins; series absent from THIS poll keep their prior Mark
        titles = dict(prior.titles)
        for tk, m in merged.items():                      # accumulate every title we have ever seen (never evict)
            t = getattr(m, "title", None)
            if t:
                titles[tk] = t
        merged_result = _marks_mod.MarksResult(marks=merged, ok=getattr(marks, "ok", False),
                                               as_of=refreshed_ts, error=getattr(marks, "error", None))
        snap = CacheSnapshot(slates=dict(slates), marks=merged_result, refreshed_ts=refreshed_ts,
                             ready=True, last_error=last_error, titles=titles,
                             starts=dict(starts or {}), starts_as_of=starts_as_of,
                             starts_attempt_ts=starts_attempt_ts, starts_ok=starts_ok, starts_error=starts_error)
        with self._lock:
            self._snap = snap

    # convenience reads (each returns None/absent honestly -> the caller degrades)
    def slate(self, date_iso: str):
        return self.snapshot().slates.get(date_iso)

    def marks(self):
        return self.snapshot().marks

    def title(self, ticker: str):
        """The PERSISTED human title for a ticker (survives failed polls), or None if one has never resolved."""
        return self.snapshot().titles.get(ticker)


# process-wide singleton (single-worker uvicorn). The app wires the poller to write it; renders read it.
_CACHE = UICache()


def cache() -> UICache:
    return _CACHE
