"""Macro calendar — looks up scheduled events that should halt trading.

Phase 1 implementation: read events from `config/macro_calendar.yaml`
(hand-maintained). Phase 1.5 will swap in a fetcher that pulls FOMC
dates from FRED + CPI/NFP from BLS into the same YAML shape, so this
module's interface stays stable across the upgrade.

The contract is simple:

    cal = MacroCalendar.load()
    if cal.is_within_halt_window(now_utc(), window_minutes=30, impact_levels=["high"]):
        # halt — high-impact event ±30 min from now
        ...

Stale events (past ones) are filtered automatically, so the YAML can
accumulate history without bloating the lookup. mtime-based reload
means edits to the YAML take effect within a few seconds without
restart.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import yaml

log = logging.getLogger(__name__)

# Reload window — re-stat the YAML at most this often. Avoids a stat()
# on every signal evaluation while still catching edits within seconds.
_RELOAD_SEC = 5.0


@dataclass(frozen=True)
class MacroEvent:
    ts: datetime           # UTC
    impact: str            # "high" | "medium" | "low"
    name: str
    source: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "MacroEvent":
        raw_ts = d["ts"]
        if isinstance(raw_ts, datetime):
            ts = raw_ts if raw_ts.tzinfo else raw_ts.replace(tzinfo=timezone.utc)
        else:
            # Accept "2026-05-07T18:00:00Z" or with offset
            ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        return cls(
            ts=ts.astimezone(timezone.utc),
            impact=str(d.get("impact", "low")).lower(),
            name=str(d.get("name", "")),
            source=str(d.get("source", "")),
        )


class MacroCalendar:
    """Hot-reloading view over `config/macro_calendar.yaml`.

    Pass `path` to point at a different file (used in tests). Use
    `MacroCalendar.load()` for the production default path.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._mtime: float = 0.0
        self._last_check: float = 0.0
        self._events: list[MacroEvent] = []

    @classmethod
    def load(cls, path: str | Path = "config/macro_calendar.yaml") -> "MacroCalendar":
        cal = cls(Path(path))
        cal._reload()
        return cal

    # --------------------------------------------------------------
    # Reload
    # --------------------------------------------------------------

    def _reload_if_stale(self) -> None:
        # Avoid stat()ing on every call.
        import time
        now = time.monotonic()
        if now - self._last_check < _RELOAD_SEC:
            return
        self._last_check = now
        self._reload()

    def _reload(self) -> None:
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            log.debug("MacroCalendar: %s does not exist (no events loaded)", self._path)
            self._events = []
            return
        if mtime == self._mtime:
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            log.warning("MacroCalendar: failed to load %s: %s", self._path, e)
            return
        raw = data.get("events", []) or []
        events: list[MacroEvent] = []
        for d in raw:
            try:
                events.append(MacroEvent.from_dict(d))
            except Exception as e:
                log.warning("MacroCalendar: skipping bad event %r: %s", d, e)
        # Sort ascending by ts so window checks short-circuit fast.
        events.sort(key=lambda e: e.ts)
        self._events = events
        self._mtime = mtime
        log.info("MacroCalendar reloaded %d events from %s", len(events), self._path)

    # --------------------------------------------------------------
    # Public API
    # --------------------------------------------------------------

    def upcoming(
        self,
        now: datetime,
        within_minutes: int,
        impact_levels: Iterable[str] = ("high",),
    ) -> list[MacroEvent]:
        """Events whose ts is within ±within_minutes of `now`."""
        self._reload_if_stale()
        levels = {lv.lower() for lv in impact_levels}
        delta = timedelta(minutes=within_minutes)
        lo, hi = now - delta, now + delta
        return [
            e for e in self._events
            if lo <= e.ts <= hi and e.impact in levels
        ]

    def is_within_halt_window(
        self,
        now: datetime,
        window_minutes: int = 30,
        impact_levels: Iterable[str] = ("high",),
    ) -> tuple[bool, MacroEvent | None]:
        """True iff a qualifying event is within ±window_minutes of `now`.

        Returns (True, event) on hit so callers can log/explain the halt.
        Returns (False, None) on miss.
        """
        evs = self.upcoming(now, window_minutes, impact_levels)
        if evs:
            # Closest in time — most likely the one the user cares about.
            closest = min(evs, key=lambda e: abs((e.ts - now).total_seconds()))
            return True, closest
        return False, None
