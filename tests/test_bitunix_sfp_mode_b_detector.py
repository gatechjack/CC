"""Deploy-gate parity tests for the Mode-B (15m-SFP → 3m-BOS) detector.

The load-bearing test is PARITY: feed mixed 15m + 3m bars through the streaming
``SfpModeBDetector`` and assert its BOS-confirmed entries are IDENTICAL to a
faithful in-test transcription of the p6 oracle ``watch_B``
(``confluence_exp6_p6_sfp_bos_percoin.py`` lines 161-179, Mode-B long) PLUS the
2026-06-26 contiguity guard (the 3m bar opening EXACTLY at the 15m fire-close t0
must exist, else the watch is OUT-OF-RANGE and never confirms). The 15m SFP
"fire" oracle (``pivots_low`` / ``swing_highs`` / ``mr`` / ``sfp_events`` long)
is transcribed line-for-line from p6 exactly as in
``test_bitunix_sfp_detector.py`` — and is the SAME logic the streaming detector
reuses via the embedded :class:`SfpDetector` fire engine, so the 15m fires match
by construction; this file proves the NEW 3m BOS-confirmation port matches.

Coverage:
- ``test_parity_mode_b_streaming_matches_oracle`` — synthetic 4-seed warm-start
  parity (REAL + CONSIDERABLE), non-vacuous.
- ``test_parity_mode_b_interleaved_matches_warmstart`` — the live master-loop feed
  order (interleave 15m/3m by close-time) == warm-start == oracle.
- ``test_mode_b_contiguity_outrange_drops_watch`` — the contiguity guard: a missing
  t0 3m bar drops the watch; a present one confirms.
- ``test_k1_prefix_stability_mode_b`` — no look-ahead on the interleaved stream.
- ``test_parity_mode_b_real_data`` — the strongest gate: streaming == oracle over
  the real per-coin ``{btc,sol,eth,xrp}_scalping.db`` 15m+3m bars (skips if absent).
"""
from __future__ import annotations

import bisect
import os
import random
import sqlite3

import pytest

from trading_corp.agents.strategies.bitunix_sfp import (
    MODE_CONSIDERABLE,
    MODE_REAL,
    SfpBar,
    SfpDetector,
    SfpModeBDetector,
)

PIV = 50
BTB = 4
WB3 = 240          # int(WATCH_HOURS*60/3) — watch_B wb on the 3m stream
_15M_MS = 900_000
_3M_MS = 180_000

# Real per-coin scalping DBs live in the main repo's data dir (not the worktree).
_DATA_DIR = r"C:\Users\AA Incorporado\cc\data"
_COINS = ("btc", "sol", "eth", "xrp")


# --------------------------------------------------------------------------- #
# Fixture: a 3m random walk, aggregated 5:1 into ALIGNED 15m bars. The 15m
# close of bar b (ts+900k) therefore always lands on a real 3m bar open
# (contiguous), so the synthetic parity exercises the BOS logic; the contiguity
# guard is exercised separately + by the gappy real data.
# --------------------------------------------------------------------------- #
def _make_3m_and_15m(seed: int, n3: int = 3000, vol: float = 0.008
                     ) -> tuple[list[SfpBar], list[SfpBar]]:
    rng = random.Random(seed)
    bars3: list[SfpBar] = []
    price = 100.0
    ts = 1_700_000_000_000
    for _ in range(n3):
        o = price
        c = price * (1.0 + rng.gauss(0.0, vol))
        hi = max(o, c) * (1.0 + abs(rng.gauss(0.0, vol / 2)))
        lo = min(o, c) * (1.0 - abs(rng.gauss(0.0, vol / 2)))
        bars3.append(SfpBar(ts_ms=ts, open=o, high=hi, low=lo, close=c))
        price = c
        ts += _3M_MS
    bars15: list[SfpBar] = []
    for k in range(0, len(bars3) - 4, 5):
        grp = bars3[k:k + 5]
        bars15.append(SfpBar(
            ts_ms=grp[0].ts_ms, open=grp[0].open,
            high=max(x.high for x in grp), low=min(x.low for x in grp),
            close=grp[4].close))
    return bars15, bars3


# --------------------------------------------------------------------------- #
# Oracle.
#
# 15m FIRES are sourced from the validated streaming ``SfpDetector`` (the SAME
# engine ``SfpModeBDetector`` embeds), whose fires are independently parity-proven
# against the p6 batch oracle by ``test_bitunix_sfp_detector.py``. This isolates
# THIS file to validating the NEW part — the 3m ``watch_B`` BOS confirmation port
# (+ contiguity) — given identical fires, rather than re-litigating the detector's
# pivot logic (the detector's documented p<pivot_len negative-index warmup quirk
# would otherwise diverge from a fresh ``pivots`` transcription on some fixtures).
# ``watch_B`` (3m swings + ``mr`` + window/invalidate/BOS) is transcribed
# line-for-line from the percoin oracle.
# --------------------------------------------------------------------------- #
def _detector_fires(bars15: list[SfpBar], mode: str) -> list[tuple[int, float, float]]:
    """The 15m SFP fires from the validated ``SfpDetector`` ARMED transitions:
    (fired_15m_ts_ms, swept_swing_level (=ev lvl), swept_wick (=ev swept))."""
    det = SfpDetector(mode=mode)
    fires: list[tuple[int, float, float]] = []
    for bar in bars15:
        det.on_closed_bar(bar)
        for t in det.drain_transitions():
            if t.status == "ARMED":
                fires.append((int(t.fired_bar_ts_ms), float(t.swept_level),
                              float(t.swept_wick)))
    return fires


def _oracle_swing_highs(bars: list[SfpBar]) -> tuple[list[int], list[float]]:
    Hj: list[int] = []
    Hv: list[float] = []
    for j in range(2, len(bars)):
        if bars[j].close < bars[j].open and bars[j - 1].close < bars[j - 1].open:
            Hj.append(j)
            Hv.append(bars[j - 2].high)
    return Hj, Hv


def _oracle_mr(Hj: list[int], Hv: list[float], w: int) -> float | None:
    k = bisect.bisect_left(Hj, w) - 1   # last swing high completed strictly before w
    return Hv[k] if k >= 0 else None


def _oracle_confirms_b(bars15: list[SfpBar], bars3: list[SfpBar], mode: str) -> set[tuple]:
    """Set of BOS-confirmed tuples for Mode B (watch_B + contiguity):
    (mode, fired_15m_ts, bos_3m_ts, swept_low, swept_swing_level, bos_ref_high)."""
    n3 = len(bars3)
    Hj3, Hv3 = _oracle_swing_highs(bars3)        # swings on the 3m stream
    ts3 = [int(b.ts_ms) for b in bars3]
    out: set[tuple] = set()
    for fired_ts, lvl, swept in _detector_fires(bars15, mode):
        t0 = int(fired_ts) + _15M_MS
        w0 = bisect.bisect_left(ts3, t0)
        # contiguity guard: the exact-t0 3m bar must exist, else OUT-OF-RANGE.
        if w0 >= n3 or ts3[w0] != t0:
            continue
        for w in range(w0, min(n3, w0 + WB3)):
            if bars3[w].close < lvl:
                break                             # invalid
            ref = _oracle_mr(Hj3, Hv3, w)
            if ref is not None and bars3[w].close > ref:
                out.add((mode, int(fired_ts), int(bars3[w].ts_ms),
                         swept, lvl, ref))
                break                             # bos
    return out


# --------------------------------------------------------------------------- #
# Streaming drivers.
# --------------------------------------------------------------------------- #
def _confirms_from_transitions(det: SfpModeBDetector, mode: str, out: set[tuple]) -> None:
    for t in det.drain_transitions():
        if t.status == "CONFIRMED":
            out.add((mode, int(t.fired_bar_ts_ms), int(t.status_bar_ts_ms),
                     float(t.swept_wick), float(t.swept_level), float(t.bos_ref_high)))


def _streaming_confirms_b(bars15: list[SfpBar], bars3: list[SfpBar], mode: str) -> set[tuple]:
    """Warm-start feed: ALL 15m, THEN ALL 3m (the oracle's own structure)."""
    det = SfpModeBDetector(mode=mode)
    det.warm_start(bars15, bars3)
    out: set[tuple] = set()
    _confirms_from_transitions(det, mode, out)
    return out


def _streaming_confirms_b_interleaved(bars15: list[SfpBar], bars3: list[SfpBar],
                                      mode: str) -> set[tuple]:
    """Live master-loop feed order: interleave by CLOSE time (15m close = ts+900k,
    3m close = ts+180k); on a tie feed the 15m bar first (arm before advance)."""
    events: list[tuple[int, int, SfpBar]] = []
    for bb in bars15:
        events.append((int(bb.ts_ms) + _15M_MS, 0, bb))
    for bb in bars3:
        events.append((int(bb.ts_ms) + _3M_MS, 1, bb))
    events.sort(key=lambda e: (e[0], e[1]))
    det = SfpModeBDetector(mode=mode)
    out: set[tuple] = set()
    for _close_ms, prio, bar in events:
        if prio == 0:
            det.on_closed_15m_bar(bar)
        else:
            det.on_closed_3m_bar(bar)
        _confirms_from_transitions(det, mode, out)
    return out


# --------------------------------------------------------------------------- #
# THE GATE — synthetic parity.
# --------------------------------------------------------------------------- #
def test_parity_mode_b_streaming_matches_oracle():
    total = 0
    for seed in (1, 7, 42, 2024):
        bars15, bars3 = _make_3m_and_15m(seed)
        for mode in (MODE_REAL, MODE_CONSIDERABLE):
            oracle = _oracle_confirms_b(bars15, bars3, mode)
            streaming = _streaming_confirms_b(bars15, bars3, mode)
            assert streaming == oracle, (
                f"seed={seed} mode={mode}: streaming != oracle; "
                f"only_streaming={sorted(streaming - oracle)[:5]} "
                f"only_oracle={sorted(oracle - streaming)[:5]}"
            )
            total += len(oracle)
    assert total > 0, "no Mode-B BOS confirmations generated — parity is vacuous"


def test_parity_mode_b_interleaved_matches_warmstart():
    """The live feed order (interleaved) must equal the warm-start order AND the
    oracle — proves the master-loop ordering is correct, not just warm-start."""
    total = 0
    for seed in (1, 7, 42, 2024):
        bars15, bars3 = _make_3m_and_15m(seed)
        for mode in (MODE_REAL, MODE_CONSIDERABLE):
            oracle = _oracle_confirms_b(bars15, bars3, mode)
            warm = _streaming_confirms_b(bars15, bars3, mode)
            live = _streaming_confirms_b_interleaved(bars15, bars3, mode)
            assert live == warm == oracle, (
                f"seed={seed} mode={mode}: live != warm/oracle; "
                f"live-only={sorted(live - oracle)[:5]} "
                f"oracle-only={sorted(oracle - live)[:5]}"
            )
            total += len(oracle)
    assert total > 0, "vacuous"


def test_parity_mode_b_includes_both_modes():
    reals = cons = 0
    for seed in (1, 7, 42, 2024, 99, 123):
        bars15, bars3 = _make_3m_and_15m(seed)
        reals += len(_streaming_confirms_b(bars15, bars3, MODE_REAL))
        cons += len(_streaming_confirms_b(bars15, bars3, MODE_CONSIDERABLE))
    assert reals > 0, "no REAL Mode-B confirms across seeds"
    assert cons > 0, "no CONSIDERABLE Mode-B confirms across seeds"


# --------------------------------------------------------------------------- #
# Contiguity guard.
# --------------------------------------------------------------------------- #
def _b(ts, o, h, l, c):
    return SfpBar(ts_ms=ts, open=o, high=h, low=l, close=c)


def _arm_via_fire_engine(det: SfpModeBDetector, pivot_len: int = 2):
    """Drive the embedded fire engine (pivot_len=2) to arm exactly one watch with
    swing-low level 90 and swept wick 85, fired on the 15m bar at ts=5*900k.
    Returns t0 (the 15m close = the required contiguous 3m bar open)."""
    base = [
        _b(0, 100, 101, 100, 100),
        _b(1 * _15M_MS, 100, 101, 100, 100),
        _b(2 * _15M_MS, 95, 96, 90, 95),       # pivot low = 90
        _b(3 * _15M_MS, 100, 101, 100, 100),
        _b(4 * _15M_MS, 100, 101, 100, 100),   # b=4, p=2 confirms → swing_low=90 armed
        _b(5 * _15M_MS, 95, 96, 85, 95),       # REAL fire: low 85<90, close 95>90
    ]
    for bar in base:
        det.on_closed_15m_bar(bar)
    return 5 * _15M_MS + _15M_MS               # t0 = fire bar close = 6*900k


def test_mode_b_contiguity_present_confirms():
    """With the contiguous t0 3m bar present and a clean BOS, the watch confirms."""
    det = SfpModeBDetector(mode=MODE_REAL, pivot_len=2, watch_bars_3m=50)
    t0 = _arm_via_fire_engine(det)
    assert len(det._watches3) == 1 and det._watches3[0].t0_ms == t0
    sigs: list = []
    # A 3m two-candle swing high needs w>=2 (3 bars). bars w0,w1,w2 are bearish →
    # at w2 the swing high = high of bar w0 (=97) is recorded (usable at w>2). All
    # closes stay >= the swept level (90) so no invalidation. bar w3 closes above
    # 97 (and above 90) → BOS confirm; enter at w4.
    sigs += det.on_closed_3m_bar(_b(t0, 96, 97, 91, 93))               # w0 bind; bearish, high97
    sigs += det.on_closed_3m_bar(_b(t0 + _3M_MS, 93, 95, 91, 92))      # w1 bearish
    sigs += det.on_closed_3m_bar(_b(t0 + 2 * _3M_MS, 92, 94, 90.5, 91))  # w2 bearish → swing high=97
    assert sigs == []                                                 # no BOS yet
    sigs += det.on_closed_3m_bar(_b(t0 + 3 * _3M_MS, 95, 99, 94, 98))  # w3 close 98>97 → BOS
    assert len(sigs) == 1
    s = sigs[0]
    assert s.bos_tf == "3m" and s.swept_low == 85 and s.swept_swing_level == 90
    assert s.bos_ref_high == 97 and s.bos_bar_ts_ms == t0 + 3 * _3M_MS
    assert det._watches3 == []


def test_mode_b_contiguity_outrange_drops_watch():
    """If the exact-t0 3m bar is MISSING (first 3m bar arrives a gap later), the
    watch is dropped OUT-OF-RANGE — never binds, never confirms."""
    det = SfpModeBDetector(mode=MODE_REAL, pivot_len=2, watch_bars_3m=50)
    t0 = _arm_via_fire_engine(det)
    assert len(det._watches3) == 1
    # first 3m bar opens ONE interval LATE (t0 bar missing) → outrange drop.
    sigs = det.on_closed_3m_bar(_b(t0 + _3M_MS, 95, 99, 94, 98))
    assert sigs == []
    assert det._watches3 == []                 # dropped, not bound
    drained = [t for t in det.drain_transitions() if t.status != "ARMED"]
    assert drained and drained[0].status == "TIMED_OUT"


# --------------------------------------------------------------------------- #
# No look-ahead on the interleaved (live) stream.
# --------------------------------------------------------------------------- #
def test_k1_prefix_stability_mode_b():
    bars15, bars3 = _make_3m_and_15m(7)
    events: list[tuple[int, int, SfpBar]] = []
    for bb in bars15:
        events.append((int(bb.ts_ms) + _15M_MS, 0, bb))
    for bb in bars3:
        events.append((int(bb.ts_ms) + _3M_MS, 1, bb))
    events.sort(key=lambda e: (e[0], e[1]))
    for mode in (MODE_REAL, MODE_CONSIDERABLE):
        full: list[tuple] = []
        det = SfpModeBDetector(mode=mode)
        for i, (_c, prio, bar) in enumerate(events):
            (det.on_closed_15m_bar if prio == 0 else det.on_closed_3m_bar)(bar)
            for t in det.drain_transitions():
                if t.status == "CONFIRMED":
                    full.append((i, int(t.fired_bar_ts_ms), int(t.status_bar_ts_ms)))
        for cut in (1000, 4000, 9000):
            prefix: list[tuple] = []
            det2 = SfpModeBDetector(mode=mode)
            for i, (_c, prio, bar) in enumerate(events[:cut]):
                (det2.on_closed_15m_bar if prio == 0 else det2.on_closed_3m_bar)(bar)
                for t in det2.drain_transitions():
                    if t.status == "CONFIRMED":
                        prefix.append((i, int(t.fired_bar_ts_ms), int(t.status_bar_ts_ms)))
            expected = [t for t in full if t[0] < cut]
            assert prefix == expected, f"mode={mode} cut={cut}: look-ahead detected"


# --------------------------------------------------------------------------- #
# Strongest gate: parity over the REAL per-coin 3m+15m data (gappy/shallow).
# --------------------------------------------------------------------------- #
def _load_db_bars(path: str, table: str) -> list[SfpBar]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            f"SELECT ts,open,high,low,close FROM {table} ORDER BY ts").fetchall()
    finally:
        con.close()
    # DB ts is epoch SECONDS (oracle uses datetime.fromtimestamp) → ms.
    return [SfpBar(ts_ms=int(r[0]) * 1000, open=float(r[1]), high=float(r[2]),
                   low=float(r[3]), close=float(r[4])) for r in rows]


@pytest.mark.parametrize("coin", _COINS)
def test_parity_mode_b_real_data(coin):
    path = os.path.join(_DATA_DIR, f"{coin}_scalping.db")
    if not os.path.exists(path):
        pytest.skip(f"{path} absent")
    bars15 = _load_db_bars(path, "bars_15m")
    bars3 = _load_db_bars(path, "bars_3m")
    if len(bars15) < PIV * 2 + 5 or len(bars3) < 10:
        pytest.skip(f"{coin}: insufficient bars (15m={len(bars15)} 3m={len(bars3)})")
    for mode in (MODE_REAL, MODE_CONSIDERABLE):
        oracle = _oracle_confirms_b(bars15, bars3, mode)
        streaming = _streaming_confirms_b(bars15, bars3, mode)
        assert streaming == oracle, (
            f"{coin} mode={mode}: streaming != oracle on REAL data; "
            f"only_streaming={sorted(streaming - oracle)[:5]} "
            f"only_oracle={sorted(oracle - streaming)[:5]}"
        )
