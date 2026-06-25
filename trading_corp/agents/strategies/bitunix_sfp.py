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


@dataclass
class _Watch:
    fire_index: int
    level: float                # swept swing-low level (invalidation line)
    swept_low: float


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
            self._watches.append(
                _Watch(fire_index=b, level=float(self._swing_low), swept_low=fired_swept)
            )

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
            # "invalid" / "timeout" → watch dropped (not re-added)
        self._watches = still_active

        # (4) record this bar's two-candle swing high for future watch bars.
        if b >= 2 and self._is_bearish(b) and self._is_bearish(b - 1):
            self._swing_highs.append((b, self.bars[b - 2].high))

        return signals

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
