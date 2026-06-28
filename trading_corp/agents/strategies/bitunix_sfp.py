"""Bitunix SFP (Swing Failure Pattern) + BOS detector — engine-side, streaming.

This is the LONG-only, 15m-only signal generator for the ``bitunix_sfp``
division. It is a faithful, INCREMENTAL (one-bar-at-a-time) port of the
validated Exp-6 Phase-6 batch oracle
(``confluence_exp6_p6_sfp_bos_2026-06-24.py``, Mode A = same-TF BOS), pinned at
md5 ``6e411762ec5de2c04e5587934e788f67``. The parity test feeds a fixed bar
fixture through this detector one bar at a time and asserts the fired events +
BOS entries are IDENTICAL to the batch oracle over the same bars.

THE SETUP (do not drift these without re-running the parity oracle):
  - Detector = TFlab SFP. Major swing low = ``pivotlow(50, 50)`` — a bar ``p``
    whose low is strictly below all 50 bars before AND all 50 bars after. It is
    therefore CONFIRMED only 50 bars forward: usable at bar ``b = p + 50``. This
    is the k=1 no-look-ahead crux — the streaming detector indexes only
    ``bars[0..b]`` and re-derives the pivot at ``b - 50``.
  - ``swing_low`` = most-recent CONFIRMED pivot low (re-armed on each new pivot).
  - REAL long SFP (one permit machine): ``low[b] < swing_low AND close[b] >
    swing_low`` fires; ``close[b] < swing_low`` disarms.
  - CONSIDERABLE long SFP (a SEPARATE permit machine): first ``close[b] <
    swing_low`` records the break; within ``back_to_break`` (=4) bars,
    ``swing_low < close[b] AND swing_low > close[b-1] AND close[b] > open[b-1]
    AND high[b] > high[b-1]`` fires; expiry past the window disarms.
  - Each permit fires its level ONCE.
  - BOS confirmation (Mode A, same 15m TF) is the TRIGGER, not the raw SFP:
    after an SFP fires, watch up to ``watch_bars`` (=48 = 12h) bars. The watch
    is INVALIDATED if a bar closes back below the swept swing level. It CONFIRMS
    when a bar closes ABOVE the most-recent two-candle swing high (a "lower
    high": the high of the bar before two consecutive bearish-body bars). On
    confirm, ENTER at the NEXT bar's open.
  - Stop = swept wick low − ``stop_buffer_pct`` (=0.001) · entry. R = entry −
    stop. TP = entry + ``tp_r`` (=2.0) · R. Long only.

The detector emits :class:`SfpEntrySignal` objects; geometry (stop/TP/qty) and
order placement are the observer's job (it anchors on the real fill). Run TWO
detectors per symbol — one ``REAL``, one ``CONSIDERABLE`` — and pool their
signals, exactly as the oracle runs the two modes as independent passes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Oracle constants (p6). Kept module-level so config drift is a one-line diff
# the parity test would catch.
PIVOT_LEN = 50          # p6 PIV — pivotlow(50, 50)
BACK_TO_BREAK = 4       # p6 BTB — CONSIDERABLE engulf-return window
WATCH_BARS = 48         # p6 int(WATCH_HOURS*60/15) = int(12*60/15)
STOP_BUFFER_PCT = 0.001  # p6 BUF
TP_R = 2.0              # p6 live-setup take-profit multiple

MODE_REAL = "REAL"
MODE_CONSIDERABLE = "CONSIDERABLE"


def compute_geometry(
    entry: float,
    swept_low: float,
    *,
    stop_buffer_pct: float = STOP_BUFFER_PCT,
    tp_r: float = TP_R,
) -> tuple[float, float, float] | None:
    """Long-only stop/TP/R from the entry and swept wick low (p6 ``trade_from``).

    ``stop = swept_low − stop_buffer_pct·entry``; ``R = entry − stop``;
    ``TP = entry + tp_r·R``. Returns ``(stop, tp, r)`` or ``None`` when ``R<=0``
    (invalid — caller must SKIP). The observer anchors ``entry`` on the real
    fill; the parity test anchors on the modeled next-bar open.
    """
    stop = swept_low - stop_buffer_pct * entry
    r = entry - stop
    if r <= 0:
        return None
    tp = entry + tp_r * r
    return stop, tp, r


@dataclass
class SfpBar:
    """One CLOSED 15m bar. ``ts_ms`` is the bar OPEN time in epoch ms."""
    ts_ms: int
    open: float
    high: float
    low: float
    close: float


@dataclass
class SfpEntrySignal:
    """A BOS-confirmed long entry. Enter at the open of bar ``entry_bar_index``.

    ``fire_bar_index`` / ``bos_bar_index`` / ``entry_bar_index`` are positions in
    the stream the detector has seen (0-based, in feed order). They make the
    detector's output directly comparable to the batch oracle and are echoed
    into the trade record for audit.
    """
    sfp_mode: str               # MODE_REAL | MODE_CONSIDERABLE
    swept_low: float            # the wick low that swept the level (p6 ev.swept)
    swept_swing_level: float    # the pivot-low level that was swept (p6 ev.lvl)
    bos_ref_high: float         # the two-candle swing high broken by the BOS
    fire_bar_index: int         # bar index where the SFP fired
    bos_bar_index: int          # bar index where the BOS close confirmed
    entry_bar_index: int        # = bos_bar_index + 1 (enter at this bar's open)
    bos_bar_ts_ms: int          # ts_ms of the BOS-confirming bar
    bos_tf: str = "15m"         # "15m" = Mode A (same-TF BOS); "3m" = Mode B (LTF BOS)


@dataclass
class SfpWatchTransition:
    """OBSERVE-ONLY lifecycle event for one watch (dashboard Tier-B source).

    The detector WRITES these into a write-only buffer (:attr:`SfpDetector._transitions`)
    at each transition and the observer drains them via :meth:`SfpDetector.drain_transitions`.
    The detector NEVER reads this buffer back — it has ZERO effect on signal
    generation. ``symbol`` is intentionally absent (the detector is symbol-agnostic);
    the observer composes ``watch_id = f"{symbol}:{mode}:{fired_bar_ts_ms}"``.
    """
    status: str                 # ARMED | CONFIRMED | INVALIDATED | TIMED_OUT
    mode: str                   # MODE_REAL | MODE_CONSIDERABLE
    fired_bar_ts_ms: int        # ts_ms of the arming bar (stable watch identity)
    swept_level: float          # swept swing-low (invalidation line)
    swept_wick: float           # wick low that swept it
    bos_watch_level: float | None   # arm-time BOS target; bos_ref_high on CONFIRMED
    status_bar_ts_ms: int       # ts_ms of the bar that caused THIS transition
    bos_ref_high: float | None = None      # set on CONFIRMED
    entry_bar_index: int | None = None     # set on CONFIRMED


@dataclass
class _Watch:
    fire_index: int
    level: float                # swept swing-low level (invalidation line)
    swept_low: float
    fired_ts_ms: int = 0        # ts_ms of the arming bar (for the watch_id)
    bos_watch_level: float | None = None   # BOS target captured at ARM (v1, no per-bar update)


@dataclass
class SfpDetector:
    """Streaming SFP+BOS detector for ONE symbol and ONE mode.

    Feed CLOSED bars in chronological order via :meth:`on_closed_bar`. It never
    reads beyond the current bar. State is fully rebuildable by replaying bars
    (see :meth:`warm_start`), so a restart needs no DB latch — the bars are the
    state.
    """
    mode: str
    pivot_len: int = PIVOT_LEN
    back_to_break: int = BACK_TO_BREAK
    watch_bars: int = WATCH_BARS

    bars: list[SfpBar] = field(default_factory=list)
    # SFP permit state (mirrors p6 sfp_events locals sl/slp/slbrk/slpi).
    _swing_low: float | None = None
    _permit: bool = False
    _break_index: int | None = None
    _swing_low_pivot_index: int | None = None
    # Two-candle swing-high references: (available_from_index, high_value).
    # A swing high completed at bar j is usable at watch bars w > j (p6 mr()
    # semantics: swing time = close(j) <= open(w) iff j < w). We store
    # ``j`` as available_from so a check at bar w uses entries with j < w.
    _swing_highs: list[tuple[int, float]] = field(default_factory=list)
    _watches: list[_Watch] = field(default_factory=list)
    # OBSERVE-ONLY transition buffer (dashboard Tier-B). WRITE-ONLY from the
    # detector — no method ever reads it, so it cannot influence signal
    # generation. Drained by the observer via drain_transitions().
    _transitions: list[SfpWatchTransition] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.mode not in (MODE_REAL, MODE_CONSIDERABLE):
            raise ValueError(f"SfpDetector mode must be REAL|CONSIDERABLE, got {self.mode!r}")

    # ------------------------------------------------------------------ #
    def warm_start(self, bars: list[SfpBar]) -> list[SfpEntrySignal]:
        """Replay a history of closed bars to rebuild state after a restart.

        Returns any signals the replay produces (normally ignored at warm-start;
        the caller only wants the rebuilt watch/permit state). Deterministic:
        warm_start(H) then on_closed_bar(x) == feeding H+x from cold.
        """
        out: list[SfpEntrySignal] = []
        for b in bars:
            out.extend(self.on_closed_bar(b))
        return out

    # ------------------------------------------------------------------ #
    def on_closed_bar(self, bar: SfpBar) -> list[SfpEntrySignal]:
        """Process one CLOSED bar. Returns 0+ BOS-confirmed entry signals.

        Ordering reproduces the oracle exactly:
          1) append bar; re-arm the swing low if bar ``b-pivot_len`` is a pivot;
          2) evaluate the SFP fire (may arm a new watch at this bar);
          3) advance pre-existing watches with THIS bar (invalid / BOS / timeout);
          4) record THIS bar's two-candle swing high for FUTURE watch bars.
        Step 4 runs last so a watch check at bar w only sees swing highs that
        completed strictly before w (p6 ``j < w``).
        """
        self.bars.append(bar)
        b = len(self.bars) - 1

        # (1) pivot-low confirmation 50 bars forward → re-arm swing low.
        p = b - self.pivot_len
        if p >= 0 and self._is_pivot_low(p):
            self._swing_low = self.bars[p].low
            self._permit = True
            self._break_index = None
            self._swing_low_pivot_index = p

        if b < 1:
            return []

        signals: list[SfpEntrySignal] = []

        # (2) SFP fire for this mode (permit-once).
        fired_swept = self._maybe_fire(b)
        if fired_swept is not None:
            self._permit = False
            # arm-time BOS target (v1: captured once at ARM, finalized at CONFIRM;
            # _most_recent_swing_high is a pure read — no state mutation).
            bos_target = self._most_recent_swing_high(before_index=b)
            w = _Watch(fire_index=b, level=float(self._swing_low), swept_low=fired_swept,
                       fired_ts_ms=bar.ts_ms, bos_watch_level=bos_target)
            self._watches.append(w)
            # OBSERVE-ONLY: record ARMED (write-only buffer; the permit/watch
            # decision above is unchanged — this only logs what was decided).
            self._transitions.append(SfpWatchTransition(
                status="ARMED", mode=self.mode, fired_bar_ts_ms=bar.ts_ms,
                swept_level=w.level, swept_wick=w.swept_low, bos_watch_level=bos_target,
                status_bar_ts_ms=bar.ts_ms))

        # (3) advance watches armed on an EARLIER bar.
        still_active: list[_Watch] = []
        for w in self._watches:
            if w.fire_index >= b:
                still_active.append(w)          # armed this bar; first checked next bar
                continue
            outcome = self._advance_watch(w, b)
            if outcome is None:
                still_active.append(w)          # no resolution yet
            elif isinstance(outcome, SfpEntrySignal):
                signals.append(outcome)         # BOS → entry; watch resolved
                # OBSERVE-ONLY: record CONFIRMED (signals list above is unchanged).
                self._transitions.append(SfpWatchTransition(
                    status="CONFIRMED", mode=self.mode, fired_bar_ts_ms=w.fired_ts_ms,
                    swept_level=w.level, swept_wick=w.swept_low,
                    bos_watch_level=outcome.bos_ref_high, status_bar_ts_ms=self.bars[b].ts_ms,
                    bos_ref_high=outcome.bos_ref_high, entry_bar_index=outcome.entry_bar_index))
            elif outcome == "invalid":           # watch dropped (not re-added) — unchanged
                # OBSERVE-ONLY: record INVALIDATED.
                self._transitions.append(SfpWatchTransition(
                    status="INVALIDATED", mode=self.mode, fired_bar_ts_ms=w.fired_ts_ms,
                    swept_level=w.level, swept_wick=w.swept_low,
                    bos_watch_level=w.bos_watch_level, status_bar_ts_ms=self.bars[b].ts_ms))
            elif outcome == "timeout":           # watch dropped (not re-added) — unchanged
                # OBSERVE-ONLY: record TIMED_OUT.
                self._transitions.append(SfpWatchTransition(
                    status="TIMED_OUT", mode=self.mode, fired_bar_ts_ms=w.fired_ts_ms,
                    swept_level=w.level, swept_wick=w.swept_low,
                    bos_watch_level=w.bos_watch_level, status_bar_ts_ms=self.bars[b].ts_ms))
        self._watches = still_active

        # (4) record this bar's two-candle swing high for future watch bars.
        if b >= 2 and self._is_bearish(b) and self._is_bearish(b - 1):
            self._swing_highs.append((b, self.bars[b - 2].high))

        return signals

    # ------------------------------------------------------------------ #
    def drain_transitions(self) -> list[SfpWatchTransition]:
        """OBSERVE-ONLY: return + clear buffered lifecycle transitions.

        Called by the observer after :meth:`on_closed_bar`. The detector never
        reads ``_transitions`` itself, so draining (or not draining) has ZERO
        effect on signal generation — it is purely an output channel for the
        dashboard's Tier-B panels.
        """
        out = self._transitions
        self._transitions = []
        return out

    # ------------------------------------------------------------------ #
    def _is_pivot_low(self, p: int) -> bool:
        """p6 pivotlow(50,50): low[p] strictly below all 50 bars each side."""
        lp = self.bars[p].low
        L = self.pivot_len
        for j in range(p - L, p):
            if not (lp < self.bars[j].low):
                return False
        for j in range(p + 1, p + L + 1):
            if not (lp < self.bars[j].low):
                return False
        return True

    def _is_bearish(self, i: int) -> bool:
        return self.bars[i].close < self.bars[i].open

    def _maybe_fire(self, b: int) -> float | None:
        """Return the swept wick low if the SFP fires at bar ``b``, else None.

        Mutates permit/break state exactly as the oracle (disarm on the
        non-firing branches). Only evaluated while a swing low is armed.
        """
        if self._swing_low is None or not self._permit:
            return None
        sl = self._swing_low
        cur = self.bars[b]
        if self.mode == MODE_REAL:
            if cur.low < sl and cur.close > sl:
                return cur.low
            if cur.close < sl:
                self._permit = False
            return None
        # CONSIDERABLE
        if cur.close < sl and self._break_index is None:
            self._break_index = b
        if self._break_index is not None:
            prev = self.bars[b - 1]
            if (b - self._break_index) <= self.back_to_break:
                if sl < cur.close and sl > prev.close and cur.close > prev.open and cur.high > prev.high:
                    return cur.low
            elif (b - self._break_index) > self.back_to_break:
                self._permit = False
        return None

    def _advance_watch(self, w: _Watch, b: int):
        """Advance one watch with bar ``b``. Returns SfpEntrySignal | "invalid"
        | "timeout" | None (no resolution yet)."""
        if (b - w.fire_index) > self.watch_bars:
            return "timeout"
        cur = self.bars[b]
        if cur.close < w.level:
            return "invalid"
        ref = self._most_recent_swing_high(before_index=b)
        if ref is not None and cur.close > ref:
            return SfpEntrySignal(
                sfp_mode=self.mode,
                swept_low=w.swept_low,
                swept_swing_level=w.level,
                bos_ref_high=ref,
                fire_bar_index=w.fire_index,
                bos_bar_index=b,
                entry_bar_index=b + 1,
                bos_bar_ts_ms=cur.ts_ms,
            )
        return None

    def _most_recent_swing_high(self, *, before_index: int) -> float | None:
        """Most-recent two-candle swing high completed strictly before
        ``before_index`` (p6 mr() with j < w)."""
        for completed_at, value in reversed(self._swing_highs):
            if completed_at < before_index:
                return value
        return None


# ════════════════════════════════════════════════════════════════════════════
# Mode B — 15m SFP arms the watch, BOS confirmation advances on 3m closes.
# ════════════════════════════════════════════════════════════════════════════
# ADDITIVE: this path does NOT modify the validated Mode-A ``SfpDetector`` above.
# It EMBEDS one as a 15m "fire engine" (its 15m BOS advancement still runs but its
# returned signals are IGNORED — Mode B consumes only the ARMED transition, i.e.
# the SFP fire) and re-implements BOS confirmation on the 3m stream as a faithful
# port of the p6 oracle ``watch_B`` (confluence_exp6_p6_sfp_bos_percoin.py:161-179)
# PLUS the 2026-06-26 contiguity guard: the 3m bar opening EXACTLY at the 15m
# fire-close (t0) must exist, else the watch is dropped as OUT-OF-RANGE. On the
# steady-state live feed the guard is a no-op (the t0 3m bar always exists); on a
# shallow/gappy 3m warm-start it prevents binding a 15m fire to a non-contiguous
# (months-later) 3m bar — the exact alignment bug the 4-coin report caught.

WATCH_BARS_3M = 240          # int(WATCH_HOURS*60/3) = int(12*60/3) — watch_B ``wb``
BOS_TF_SECONDS_3M = 180      # 3m bar duration in seconds
_15M_MS = 900_000


@dataclass
class _WatchB:
    """One armed Mode-B watch. Identity + invalidation come from the 15m SFP fire;
    resolution advances on the 3m stream."""
    lvl: float                   # 15m swept swing-low (invalidation line) = ev["lvl"]
    swept: float                 # 15m swept wick low (stop reference)     = ev["swept"]
    fired_15m_ts_ms: int         # arming 15m bar ts_ms (stable watch identity)
    t0_ms: int                   # 15m close = fired_15m_ts_ms + 900_000 (3m bind anchor)
    arm_index3: int | None = None    # 3m index where the watch bound (== watch_B w0)
    bound: bool = False              # contiguity-resolved (bound XOR dropped)


@dataclass
class SfpModeBDetector:
    """Streaming 15m-SFP → 3m-BOS detector for ONE symbol and ONE mode.

    Feed CLOSED 15m bars via :meth:`on_closed_15m_bar` (arms watches; reuses the
    validated :class:`SfpDetector` as the fire engine) and CLOSED 3m bars via
    :meth:`on_closed_3m_bar` (advances / confirms / invalidates / times out each
    watch per the oracle ``watch_B`` + the contiguity guard). Emits the same
    :class:`SfpEntrySignal` (with ``bos_tf="3m"``) and the same write-only
    :class:`SfpWatchTransition` buffer as Mode A. State is fully rebuildable by
    replaying bars (see :meth:`warm_start`) — the bars ARE the state.
    """
    mode: str
    pivot_len: int = PIVOT_LEN
    back_to_break: int = BACK_TO_BREAK
    watch_bars_3m: int = WATCH_BARS_3M

    _fire: "SfpDetector" = field(init=False)
    _bars3: list[SfpBar] = field(default_factory=list)
    _swing_highs3: list[tuple[int, float]] = field(default_factory=list)
    _watches3: list[_WatchB] = field(default_factory=list)
    _transitions: list[SfpWatchTransition] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.mode not in (MODE_REAL, MODE_CONSIDERABLE):
            raise ValueError(
                f"SfpModeBDetector mode must be REAL|CONSIDERABLE, got {self.mode!r}")
        # The fire engine's own 15m watch_bars is irrelevant — Mode B ignores its
        # 15m watch outcomes and consumes only its ARMED transitions.
        self._fire = SfpDetector(mode=self.mode, pivot_len=self.pivot_len,
                                 back_to_break=self.back_to_break)

    # ------------------------------------------------------------------ #
    def warm_start(self, bars15: list[SfpBar], bars3: list[SfpBar]) -> list[SfpEntrySignal]:
        """Replay history: ALL 15m bars (arm every watch), THEN ALL 3m bars in
        order (bind + advance). Equivalent to the interleaved live feed because a
        15m fire depends only on the 15m stream and a watch records only
        (lvl, swept, t0) — it consumes no 3m bar until bound. Mirrors the oracle's
        own structure (sfp_events over all 15m, then watch_B over all 3m). Replay
        signals are normally discarded by the caller."""
        for b in bars15:
            self.on_closed_15m_bar(b)
        out: list[SfpEntrySignal] = []
        for b in bars3:
            out.extend(self.on_closed_3m_bar(b))
        return out

    # ------------------------------------------------------------------ #
    def on_closed_15m_bar(self, bar15: SfpBar) -> list[SfpEntrySignal]:
        """Feed one CLOSED 15m bar to the fire engine and arm a Mode-B watch for
        each SFP fire (the ARMED transition). Returns [] — Mode B never confirms on
        15m. The fire engine's own 15m BOS / invalid / timeout transitions are
        intentionally discarded here."""
        self._fire.on_closed_bar(bar15)
        for t in self._fire.drain_transitions():
            if t.status != "ARMED":
                continue
            t0 = int(t.fired_bar_ts_ms) + _15M_MS
            self._watches3.append(_WatchB(
                lvl=float(t.swept_level), swept=float(t.swept_wick),
                fired_15m_ts_ms=int(t.fired_bar_ts_ms), t0_ms=t0))
            # OBSERVE-ONLY ARMED (bos_watch_level=None: the 3m BOS target is not
            # known until a 3m swing high is broken at CONFIRM).
            self._transitions.append(SfpWatchTransition(
                status="ARMED", mode=self.mode, fired_bar_ts_ms=int(t.fired_bar_ts_ms),
                swept_level=float(t.swept_level), swept_wick=float(t.swept_wick),
                bos_watch_level=None, status_bar_ts_ms=int(t.fired_bar_ts_ms)))
        return []

    # ------------------------------------------------------------------ #
    def on_closed_3m_bar(self, bar3: SfpBar) -> list[SfpEntrySignal]:
        """Advance every active watch with one CLOSED 3m bar (port of ``watch_B``
        + the contiguity guard). Per-bar order: bind+contiguity → timeout →
        invalidate → BOS; record THIS bar's 3m two-candle swing high LAST so a
        check at bar ``w`` only sees swings completed strictly before ``w`` (the
        oracle ``mr`` j<w semantics)."""
        self._bars3.append(bar3)
        w = len(self._bars3) - 1
        ts = int(bar3.ts_ms)
        signals: list[SfpEntrySignal] = []
        still: list[_WatchB] = []
        for wt in self._watches3:
            # (a) bind + contiguity — once, at the first 3m bar with open >= t0.
            if not wt.bound:
                if ts < wt.t0_ms:
                    still.append(wt)               # t0 not reached yet — keep waiting
                    continue
                if ts != wt.t0_ms:
                    # the exact-t0 3m bar is MISSING (gap) → OUT-OF-RANGE; drop.
                    self._transitions.append(self._terminal(wt, "TIMED_OUT", ts))
                    continue
                wt.bound = True
                wt.arm_index3 = w                  # == watch_B w0
            # (b) timeout — window is [w0, w0+wb-1]; expire at w0+wb.
            if (w - wt.arm_index3) >= self.watch_bars_3m:
                self._transitions.append(self._terminal(wt, "TIMED_OUT", ts))
                continue
            # (c) invalidate — a 3m close back below the swept 15m level.
            if bar3.close < wt.lvl:
                self._transitions.append(self._terminal(wt, "INVALIDATED", ts))
                continue
            # (d) BOS confirm — 3m close above the most-recent 3m two-candle swing
            #     high. Enter at the NEXT 3m open (entry_bar_index = w + 1).
            ref = self._most_recent_swing_high3(before_index=w)
            if ref is not None and bar3.close > ref:
                signals.append(SfpEntrySignal(
                    sfp_mode=self.mode, swept_low=wt.swept, swept_swing_level=wt.lvl,
                    bos_ref_high=ref, fire_bar_index=int(wt.arm_index3),
                    bos_bar_index=w, entry_bar_index=w + 1,
                    bos_bar_ts_ms=ts, bos_tf="3m"))
                self._transitions.append(SfpWatchTransition(
                    status="CONFIRMED", mode=self.mode,
                    fired_bar_ts_ms=wt.fired_15m_ts_ms, swept_level=wt.lvl,
                    swept_wick=wt.swept, bos_watch_level=ref, status_bar_ts_ms=ts,
                    bos_ref_high=ref, entry_bar_index=w + 1))
                continue                            # resolved → drop
            still.append(wt)
        # record THIS 3m bar's two-candle swing high for FUTURE watch bars.
        if w >= 2 and self._is_bearish3(w) and self._is_bearish3(w - 1):
            self._swing_highs3.append((w, self._bars3[w - 2].high))
        self._watches3 = still
        return signals

    # ------------------------------------------------------------------ #
    def drain_transitions(self) -> list[SfpWatchTransition]:
        """OBSERVE-ONLY: return + clear buffered lifecycle transitions (ARMED on
        15m fire; CONFIRMED / INVALIDATED / TIMED_OUT on 3m). Never read back."""
        out = self._transitions
        self._transitions = []
        return out

    # ------------------------------------------------------------------ #
    def _terminal(self, wt: _WatchB, status: str, status_ts_ms: int) -> SfpWatchTransition:
        return SfpWatchTransition(
            status=status, mode=self.mode, fired_bar_ts_ms=wt.fired_15m_ts_ms,
            swept_level=wt.lvl, swept_wick=wt.swept, bos_watch_level=None,
            status_bar_ts_ms=int(status_ts_ms))

    def _is_bearish3(self, i: int) -> bool:
        return self._bars3[i].close < self._bars3[i].open

    def _most_recent_swing_high3(self, *, before_index: int) -> float | None:
        """Most-recent 3m two-candle swing high completed strictly before
        ``before_index`` (oracle ``mr`` on SWING3 with j < w)."""
        for completed_at, value in reversed(self._swing_highs3):
            if completed_at < before_index:
                return value
        return None
