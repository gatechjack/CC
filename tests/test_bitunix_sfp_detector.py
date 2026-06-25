"""Deploy-gate tests for the streaming SFP+BOS detector.

The load-bearing test is PARITY: feed a fixed bar fixture through the streaming
``SfpDetector`` one bar at a time and assert its fired events + BOS entries are
IDENTICAL to a faithful in-test transcription of the Exp-6 Phase-6 batch oracle
(``confluence_exp6_p6_sfp_bos_2026-06-24.py``, Mode-A long, md5
``6e411762ec5de2c04e5587934e788f67`` — the file that produced the +0.267R
result). The oracle functions below are transcribed line-for-line from p6
(``pivots`` 48-55, ``swings_arr`` 58-64, ``mr`` 67-69, ``sfp_events`` 72-109
long branch, ``watch_A`` 138-155). Only the event/entry-producing logic is
transcribed; p6's trade-outcome bar-walk is not part of signal parity.

``test_k1_prefix_stability`` proves no look-ahead on the streaming path: signals
emitted by bar ``b`` are identical whether or not any future bar exists.
"""
from __future__ import annotations

import random

from trading_corp.agents.strategies.bitunix_sfp import (
    MODE_CONSIDERABLE,
    MODE_REAL,
    SfpBar,
    SfpDetector,
    compute_geometry,
)

PIV = 50
BTB = 4
WATCH = 48


# --------------------------------------------------------------------------- #
# Fixture: deterministic random walk (no look-ahead concerns; both the oracle
# and the streaming detector see the identical bar list).
# --------------------------------------------------------------------------- #
def _make_bars(seed: int, n: int = 2000, vol: float = 0.012) -> list[SfpBar]:
    rng = random.Random(seed)
    bars: list[SfpBar] = []
    price = 100.0
    ts = 1_700_000_000_000
    for _ in range(n):
        o = price
        c = price * (1.0 + rng.gauss(0.0, vol))
        hi = max(o, c) * (1.0 + abs(rng.gauss(0.0, vol / 2)))
        lo = min(o, c) * (1.0 - abs(rng.gauss(0.0, vol / 2)))
        bars.append(SfpBar(ts_ms=ts, open=o, high=hi, low=lo, close=c))
        price = c
        ts += 900_000  # 15m
    return bars


# --------------------------------------------------------------------------- #
# Oracle (transcription of p6 — Mode A, long side).
# --------------------------------------------------------------------------- #
def _oracle_pivots_low(bars: list[SfpBar], L: int = PIV) -> list[bool]:
    n = len(bars)
    pl = [False] * n
    for p in range(L, n - L):
        lp = bars[p].low
        if all(lp < bars[j].low for j in range(p - L, p)) and all(
            lp < bars[j].low for j in range(p + 1, p + L + 1)
        ):
            pl[p] = True
    return pl


def _oracle_swing_highs(bars: list[SfpBar]) -> tuple[list[int], list[float]]:
    # swing high at bar j-2 when bars j and j-1 are both bearish; usable at
    # watch bar w iff j < w (p6 mr() on close-times).
    Hj: list[int] = []
    Hv: list[float] = []
    for j in range(2, len(bars)):
        if bars[j].close < bars[j].open and bars[j - 1].close < bars[j - 1].open:
            Hj.append(j)
            Hv.append(bars[j - 2].high)
    return Hj, Hv


def _oracle_mr(Hj: list[int], Hv: list[float], w: int) -> float | None:
    import bisect

    k = bisect.bisect_left(Hj, w) - 1  # last completed strictly before w
    return Hv[k] if k >= 0 else None


def _oracle_events(bars: list[SfpBar], mode: str, pl: list[bool]) -> list[dict]:
    n = len(bars)
    sl: float | None = None
    slp = False
    slbrk: int | None = None
    ev: list[dict] = []
    for b in range(n):
        p = b - PIV
        if p >= 0 and pl[p]:
            sl = bars[p].low
            slp = True
            slbrk = None
        if b < 1:
            continue
        fired = False
        swept = None
        lvl = None
        if sl is not None and slp:
            lvl = sl
            if mode == MODE_REAL:
                if bars[b].low < sl and bars[b].close > sl:
                    fired = True
                    swept = bars[b].low
                elif bars[b].close < sl:
                    slp = False
            else:
                if bars[b].close < sl and slbrk is None:
                    slbrk = b
                if slbrk is not None:
                    if (b - slbrk) <= BTB and sl < bars[b].close and sl > bars[b - 1].close and bars[b].close > bars[b - 1].open and bars[b].high > bars[b - 1].high:
                        fired = True
                        swept = bars[b].low
                    elif (b - slbrk) > BTB:
                        slp = False
        if not fired:
            continue
        slp = False
        ev.append({"b": b, "swept": swept, "lvl": lvl})
    return ev


def _oracle_confirms(bars: list[SfpBar], mode: str) -> set[tuple]:
    """Set of BOS-confirmed tuples (mode, fire_b, bos_w, swept, lvl)."""
    n = len(bars)
    pl = _oracle_pivots_low(bars)
    Hj, Hv = _oracle_swing_highs(bars)
    out: set[tuple] = set()
    for e in _oracle_events(bars, mode, pl):
        b = e["b"]
        lvl = e["lvl"]
        for w in range(b + 1, min(n, b + 1 + WATCH)):
            if bars[w].close < lvl:
                break  # invalid
            ref = _oracle_mr(Hj, Hv, w)
            if ref is not None and bars[w].close > ref:
                out.add((mode, b, w, e["swept"], lvl))
                break  # bos
    return out


def _streaming_confirms(bars: list[SfpBar]) -> set[tuple]:
    out: set[tuple] = set()
    for mode in (MODE_REAL, MODE_CONSIDERABLE):
        det = SfpDetector(mode=mode)
        for bar in bars:
            for sig in det.on_closed_bar(bar):
                out.add(
                    (sig.sfp_mode, sig.fire_bar_index, sig.bos_bar_index, sig.swept_low, sig.swept_swing_level)
                )
    return out


# --------------------------------------------------------------------------- #
# THE GATE
# --------------------------------------------------------------------------- #
def test_parity_streaming_matches_oracle():
    total = 0
    for seed in (1, 7, 42, 2024):
        bars = _make_bars(seed)
        oracle = _oracle_confirms(bars, MODE_REAL) | _oracle_confirms(bars, MODE_CONSIDERABLE)
        streaming = _streaming_confirms(bars)
        assert streaming == oracle, (
            f"seed={seed}: streaming != oracle; "
            f"only_streaming={sorted(streaming - oracle)[:5]} "
            f"only_oracle={sorted(oracle - streaming)[:5]}"
        )
        total += len(oracle)
    # Guard against a vacuous pass: the fixtures must actually produce BOS
    # confirmations across the seeds.
    assert total > 0, "no BOS-confirmed events generated — parity is vacuous"


def test_parity_includes_both_modes_across_seeds():
    reals = cons = 0
    for seed in (1, 7, 42, 2024, 99, 123):
        bars = _make_bars(seed)
        reals += len(_oracle_confirms(bars, MODE_REAL) & _streaming_confirms(bars))
        cons += len([t for t in _streaming_confirms(bars) if t[0] == MODE_CONSIDERABLE])
    assert reals > 0, "no REAL BOS confirms across seeds"
    assert cons > 0, "no CONSIDERABLE BOS confirms across seeds"


def test_k1_prefix_stability():
    """No look-ahead: signals emitted at bar b do not depend on future bars.

    For several cut points, run the detector over the prefix bars[:cut] and
    assert the signals emitted on bars < cut are identical to those emitted
    when the full series is fed. Prefix-stability == causality.
    """
    bars = _make_bars(7)
    for mode in (MODE_REAL, MODE_CONSIDERABLE):
        full: list[tuple] = []
        det = SfpDetector(mode=mode)
        for i, bar in enumerate(bars):
            for sig in det.on_closed_bar(bar):
                full.append((i, sig.fire_bar_index, sig.bos_bar_index, sig.swept_low))
        for cut in (300, 800, 1500):
            prefix: list[tuple] = []
            det2 = SfpDetector(mode=mode)
            for i, bar in enumerate(bars[:cut]):
                for sig in det2.on_closed_bar(bar):
                    prefix.append((i, sig.fire_bar_index, sig.bos_bar_index, sig.swept_low))
            expected = [t for t in full if t[0] < cut]
            assert prefix == expected, f"mode={mode} cut={cut}: look-ahead detected"


# --------------------------------------------------------------------------- #
# Targeted unit tests (small pivot_len for compact fixtures).
# --------------------------------------------------------------------------- #
def _b(ts, o, h, l, c):
    return SfpBar(ts_ms=ts, open=o, high=h, low=l, close=c)


def _armed_real(**kw):
    """Detector with swing_low=90 armed (pivot_len=2): bars 0-1 high, bar2 the
    pivot low, bars 3-4 confirm. Returns (detector, next_index)."""
    det = SfpDetector(mode=MODE_REAL, pivot_len=2, **kw)
    seq = [
        _b(0, 100, 101, 100, 100),
        _b(1, 100, 101, 100, 100),
        _b(2, 95, 96, 90, 95),     # pivot low = 90
        _b(3, 100, 101, 100, 100),
        _b(4, 100, 101, 100, 100),  # b=4, p=2 confirms → swing_low=90 armed
    ]
    for bar in seq:
        det.on_closed_bar(bar)
    assert det._swing_low == 90 and det._permit is True
    return det, 5


def test_real_fire_and_disarm():
    det, i = _armed_real()
    # pierces 90 and closes above → REAL fire
    sigs = det.on_closed_bar(_b(i, 95, 96, 85, 95))
    assert det._permit is False  # permit consumed
    assert len(det._watches) == 1 and det._watches[0].swept_low == 85

    det2, j = _armed_real()
    # pierces 90 but closes BELOW → disarm, no fire, no watch
    sigs2 = det2.on_closed_bar(_b(j, 88, 89, 85, 87))
    assert sigs2 == [] and det2._permit is False and det2._watches == []
    # a subsequent perfect-fire bar must NOT fire (permit gone, no new pivot)
    det2.on_closed_bar(_b(j + 1, 95, 96, 85, 95))
    assert det2._watches == []


def test_considerable_fire_and_expire():
    det = SfpDetector(mode=MODE_CONSIDERABLE, pivot_len=2)
    for bar in [
        _b(0, 100, 101, 100, 100),
        _b(1, 100, 101, 100, 100),
        _b(2, 95, 96, 90, 95),
        _b(3, 100, 101, 100, 100),
        _b(4, 100, 101, 100, 100),
    ]:
        det.on_closed_bar(bar)
    assert det._swing_low == 90 and det._permit
    det.on_closed_bar(_b(5, 92, 93, 85, 88))  # close 88 < 90 → break recorded
    assert det._break_index == 5
    # engulf-return within BTB: sl<c AND sl>c[-1] AND c>o[-1] AND h>h[-1]
    sigs = det.on_closed_bar(_b(6, 89, 95, 88, 96))
    assert len(sigs) == 0  # fire arms a watch, no entry yet
    assert det._permit is False and len(det._watches) == 1
    assert det._watches[0].swept_low == 88

    # expire variant: break, then no engulf for >BTB bars → disarm
    det2 = SfpDetector(mode=MODE_CONSIDERABLE, pivot_len=2)
    for bar in [
        _b(0, 100, 101, 100, 100),
        _b(1, 100, 101, 100, 100),
        _b(2, 95, 96, 90, 95),
        _b(3, 100, 101, 100, 100),
        _b(4, 100, 101, 100, 100),
    ]:
        det2.on_closed_bar(bar)
    det2.on_closed_bar(_b(5, 92, 93, 85, 88))  # break at 5
    # waiting bars use strictly-descending lows so NO new pivot low re-arms the
    # permit; closes stay below the swing level so CONS never engulf-fires.
    for offset, low in enumerate([84, 83, 82, 81, 80]):  # bars 6..10
        det2.on_closed_bar(_b(6 + offset, 88, 89, low, 88))
    # at b=10, (10 - break_index 5) = 5 > BTB(4) → permit disarmed
    assert det2._permit is False and det2._watches == []


def test_permit_fires_once():
    det, i = _armed_real()
    det.on_closed_bar(_b(i, 95, 96, 85, 95))      # fire #1
    det.on_closed_bar(_b(i + 1, 95, 96, 84, 95))  # would fire again but permit gone
    assert len(det._watches) == 1  # only the first fire armed a watch


def test_bos_invalidation():
    det, i = _armed_real()
    det.on_closed_bar(_b(i, 95, 96, 85, 95))  # fire (level=90)
    assert len(det._watches) == 1
    # next bar closes back below the swept level → watch invalidated
    sigs = det.on_closed_bar(_b(i + 1, 92, 93, 88, 89))
    assert sigs == [] and det._watches == []


def test_bos_timeout():
    det, i = _armed_real(watch_bars=3)
    det.on_closed_bar(_b(i, 95, 96, 85, 95))  # fire at index 5 (level=90)
    # stay above level, never close above a swing high → timeout after 3 bars
    for k in range(i + 1, i + 4):  # 6,7,8 — within window, no resolution
        out = det.on_closed_bar(_b(k, 95, 96, 94, 95))
        assert out == []
    assert len(det._watches) == 1
    out = det.on_closed_bar(_b(i + 4, 95, 96, 94, 95))  # b-fire = 4 > 3 → timeout
    assert out == [] and det._watches == []


def test_bos_confirm_emits_entry():
    det, i = _armed_real(watch_bars=10)
    det.on_closed_bar(_b(i, 95, 96, 85, 95))  # fire at 5, level=90, swept=85
    # two consecutive bearish bars (6,7) → swing high = high of bar 5 (=96),
    # usable at watch bars w > 7.
    det.on_closed_bar(_b(i + 1, 95, 95, 92, 93))  # bearish
    det.on_closed_bar(_b(i + 2, 93, 94, 91, 92))  # bearish → swing high = bars[5].high = 96
    # bar 8 closes above the swing high (96) and above the level → BOS confirm
    sigs = det.on_closed_bar(_b(i + 3, 95, 98, 94, 97))
    assert len(sigs) == 1
    s = sigs[0]
    assert s.sfp_mode == MODE_REAL
    assert s.swept_low == 85 and s.swept_swing_level == 90
    assert s.bos_ref_high == 96
    assert s.bos_bar_index == i + 3 and s.entry_bar_index == i + 4
    assert det._watches == []  # resolved


def test_geometry():
    geo = compute_geometry(entry=100.0, swept_low=99.0)
    assert geo is not None
    stop, tp, r = geo
    assert abs(stop - 98.9) < 1e-9      # 99 - 0.001*100
    assert abs(r - 1.1) < 1e-9          # 100 - 98.9
    assert abs(tp - 102.2) < 1e-9       # 100 + 2*1.1
    # R<=0 (swept above entry) → SKIP
    assert compute_geometry(entry=100.0, swept_low=100.5) is None


def test_warm_start_equals_cold():
    bars = _make_bars(42, n=600)
    cold = SfpDetector(mode=MODE_REAL)
    cold_sigs = []
    for bar in bars:
        cold_sigs.extend(cold.on_closed_bar(bar))
    warm = SfpDetector(mode=MODE_REAL)
    warm.warm_start(bars[:400])
    warm_tail = []
    for bar in bars[400:]:
        warm_tail.extend(warm.on_closed_bar(bar))
    cold_tail = [s for s in cold_sigs if s.bos_bar_index >= 400]
    assert [(s.fire_bar_index, s.bos_bar_index, s.swept_low) for s in warm_tail] == [
        (s.fire_bar_index, s.bos_bar_index, s.swept_low) for s in cold_tail
    ]
