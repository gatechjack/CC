"""SFP watch-state emit + loop heartbeat (dashboard Tier-B source).

OBSERVE-ONLY: proves each lifecycle transition (ARMED/CONFIRMED/INVALIDATED/
TIMED_OUT) is buffered by the detector + persisted by the observer into
sfp_watch_state, that watch_id is idempotent on replay, that the heartbeat
writes, and that a persist failure is FAIL-SOFT (never raises, never touches the
returned signals — the trade decision path is independent of the emit).

The decision-path byte-identity proof (detector helpers unchanged) lives in the
deploy gates; here we prove the emit behaves + does not perturb signals.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from trading_corp.persistence import db
from trading_corp.agents.strategies.bitunix_sfp import (
    MODE_REAL, SfpBar, SfpDetector, SfpWatchTransition,
)
from trading_corp.agents.divisions.bitunix_sfp_observer import (
    BitunixSfpConfig, BitunixSfpObserver, DIVISION,
)


# ── detector fixtures (mirror test_bitunix_sfp_detector helpers) ──────────────
def _b(ts, o, h, l, c):
    return SfpBar(ts_ms=ts, open=o, high=h, low=l, close=c)


def _armed_real(**kw):
    """Detector with swing_low=90 armed (pivot_len=2). Returns (det, next_index)."""
    det = SfpDetector(mode=MODE_REAL, pivot_len=2, **kw)
    for bar in [_b(0, 100, 101, 100, 100), _b(1, 100, 101, 100, 100),
                _b(2, 95, 96, 90, 95), _b(3, 100, 101, 100, 100),
                _b(4, 100, 101, 100, 100)]:
        det.on_closed_bar(bar)
    assert det._swing_low == 90 and det._permit is True
    return det, 5


# ── DETECTOR: transition buffer is correct + does NOT perturb signals ─────────
def test_armed_transition_buffered():
    det, i = _armed_real()
    sigs = det.on_closed_bar(_b(i, 95, 96, 85, 95))     # SFP fires → ARMED
    ts = det.drain_transitions()
    assert sigs == []                                    # no signal yet (just armed)
    assert len(ts) == 1
    t = ts[0]
    assert t.status == "ARMED" and t.mode == MODE_REAL
    assert t.fired_bar_ts_ms == i and t.status_bar_ts_ms == i
    assert t.swept_level == 90 and t.swept_wick == 85
    # draining empties the buffer (write-only channel)
    assert det.drain_transitions() == []


def test_invalidated_transition_buffered():
    det, i = _armed_real()
    det.on_closed_bar(_b(i, 95, 96, 85, 95)); det.drain_transitions()   # ARMED (drained)
    sigs = det.on_closed_bar(_b(i + 1, 92, 93, 88, 89))  # close 89 < level 90 → invalid
    ts = det.drain_transitions()
    assert sigs == []
    assert [t.status for t in ts] == ["INVALIDATED"]
    assert ts[0].fired_bar_ts_ms == i and ts[0].status_bar_ts_ms == i + 1
    assert ts[0].swept_level == 90 and ts[0].swept_wick == 85


def test_timed_out_transition_buffered():
    det, i = _armed_real(watch_bars=3)
    det.on_closed_bar(_b(i, 95, 96, 85, 95)); det.drain_transitions()   # ARMED
    for k in range(i + 1, i + 4):
        det.on_closed_bar(_b(k, 95, 96, 94, 95))         # within window, no resolution
        assert det.drain_transitions() == []
    det.on_closed_bar(_b(i + 4, 95, 96, 94, 95))         # > window → timeout
    ts = det.drain_transitions()
    assert [t.status for t in ts] == ["TIMED_OUT"]
    assert ts[0].fired_bar_ts_ms == i and ts[0].status_bar_ts_ms == i + 4


def test_confirmed_transition_buffered_and_signal_unchanged():
    det, i = _armed_real(watch_bars=10)
    det.on_closed_bar(_b(i, 95, 96, 85, 95)); det.drain_transitions()   # ARMED
    det.on_closed_bar(_b(i + 1, 95, 95, 92, 93))         # bearish
    det.on_closed_bar(_b(i + 2, 93, 94, 91, 92))         # bearish → swing high = 96
    det.drain_transitions()
    sigs = det.on_closed_bar(_b(i + 3, 95, 98, 94, 97))  # BOS confirm
    ts = det.drain_transitions()
    # the trade signal is IDENTICAL to the no-emit detector (decision unchanged)
    assert len(sigs) == 1 and sigs[0].sfp_mode == MODE_REAL
    assert sigs[0].bos_ref_high == 96 and sigs[0].entry_bar_index == i + 4
    # and the CONFIRMED transition mirrors it
    assert [t.status for t in ts] == ["CONFIRMED"]
    assert ts[0].bos_ref_high == 96 and ts[0].entry_bar_index == i + 4


def test_draining_does_not_change_signals():
    """Feeding the SAME bars to a detector we DRAIN each bar vs one we NEVER drain
    yields identical signals — the buffer cannot influence signal generation."""
    seq = [_b(5, 95, 96, 85, 95), _b(6, 95, 95, 92, 93), _b(7, 93, 94, 91, 92),
           _b(8, 95, 98, 94, 97)]
    a, i = _armed_real(watch_bars=10)
    b, _ = _armed_real(watch_bars=10)
    sa, sb = [], []
    for bar in seq:
        sa += a.on_closed_bar(bar); a.drain_transitions()   # drains every bar
        sb += b.on_closed_bar(bar)                          # never drains
    assert [s.__dict__ for s in sa] == [s.__dict__ for s in sb]


# ── OBSERVER: persistence + idempotency + heartbeat + fail-soft ───────────────
def _obs(tmp_path):
    db_url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    db.init_db(db_url)
    cfg = BitunixSfpConfig(enabled=True, symbols=("BTC/USDT.P",))
    obs = BitunixSfpObserver(
        db_url=db_url, risk_agent=SimpleNamespace(), data_exec=SimpleNamespace(brokers={}),
        logger_agent=SimpleNamespace(log_event=lambda **k: 1, log_proposed_order=lambda o: None),
        config=cfg, bar_caches={},
    )
    return obs, db_url


def _rows(db_url):
    with db.connect(db_url) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM sfp_watch_state ORDER BY watch_id").fetchall()]


def _T(status, fired=1_700_000_000_000, **kw):
    base = dict(status=status, mode=MODE_REAL, fired_bar_ts_ms=fired,
                swept_level=90.0, swept_wick=85.0, bos_watch_level=96.0,
                status_bar_ts_ms=fired + 900_000)
    base.update(kw)
    return SfpWatchTransition(**base)


def test_ensure_schema_creates_table(tmp_path):
    obs, db_url = _obs(tmp_path)        # __init__ runs _ensure_watch_schema
    with db.connect(db_url) as conn:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "sfp_watch_state" in names


def test_armed_then_confirmed_upserts_one_row(tmp_path):
    obs, db_url = _obs(tmp_path)
    obs._emit_watch_transitions("BTCUSDT", [_T("ARMED")])
    r = _rows(db_url)
    assert len(r) == 1 and r[0]["status"] == "ARMED"
    assert r[0]["watch_id"] == f"BTCUSDT:{MODE_REAL}:1700000000000"
    assert r[0]["swept_level"] == 90.0 and r[0]["terminal_bar_ts"] is None
    armed_ts = r[0]["armed_ts"]
    # CONFIRMED for the same watch → same row, status updated, armed_ts preserved
    obs._emit_watch_transitions("BTCUSDT", [_T(
        "CONFIRMED", bos_watch_level=97.0, bos_ref_high=97.0, entry_bar_index=9)])
    r = _rows(db_url)
    assert len(r) == 1 and r[0]["status"] == "CONFIRMED"
    assert r[0]["terminal_bar_ts"] is not None and r[0]["armed_ts"] == armed_ts
    assert json.loads(r[0]["extra_json"])["bos_ref_high"] == 97.0


def test_watch_id_idempotent_on_replay(tmp_path):
    obs, db_url = _obs(tmp_path)
    # live: ARMED then CONFIRMED
    obs._emit_watch_transitions("BTCUSDT", [_T("ARMED")])
    obs._emit_watch_transitions("BTCUSDT", [_T("CONFIRMED", bos_ref_high=97.0, entry_bar_index=9)])
    # restart replay re-derives the SAME watch_id (ARMED + terminal again) → no dup
    obs._emit_watch_transitions("BTCUSDT", [_T("ARMED"),
                                            _T("CONFIRMED", bos_ref_high=97.0, entry_bar_index=9)])
    r = _rows(db_url)
    assert len(r) == 1 and r[0]["status"] == "CONFIRMED"


def test_recent_only_skips_ancient(tmp_path):
    obs, db_url = _obs(tmp_path)
    obs._emit_watch_transitions("BTCUSDT", [_T("ARMED", fired=1)], recent_only=True)  # ~1970
    assert _rows(db_url) == []                          # ancient → skipped
    import time
    now_ms = int(time.time() * 1000)
    obs._emit_watch_transitions("BTCUSDT", [_T("ARMED", fired=now_ms)], recent_only=True)
    assert len(_rows(db_url)) == 1                      # recent → persisted


def test_heartbeat_writes(tmp_path):
    obs, db_url = _obs(tmp_path)
    obs._write_heartbeat()
    loaded = db.load_agent_state(DIVISION, "loop_last_evaluated", db_url=db_url)
    assert loaded is not None and "ts" in loaded[0]


def test_emit_is_fail_soft(tmp_path, monkeypatch):
    obs, db_url = _obs(tmp_path)
    import trading_corp.agents.divisions.bitunix_sfp_observer as mod

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(mod.db, "connect", boom)
    # MUST NOT raise — a persist failure cannot affect trading
    obs._emit_watch_transitions("BTCUSDT", [_T("ARMED")])
    obs._write_heartbeat()
    # detector signal generation is independent of the emit (still works)
    det, i = _armed_real(watch_bars=10)
    assert det.on_closed_bar(_b(i, 95, 96, 85, 95)) == []   # arms, no crash
