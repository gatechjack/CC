"""Tests for the BitUnix snapshot-staleness health primitive
(gate (a) sub-item 2, 2026-05-30).

Covers the broker-side surface only: `_last_successful_snapshot_ts`,
`is_healthy()`, `_assert_snapshot_fresh()`, and YAML-threshold mtime cache.
The data_exec consumer handler is in test_data_exec_stale_snapshot.py;
the observer's pre-trade gate is in test_bitunix_observer_stale_snapshot.py.
"""
from __future__ import annotations

import time

import pytest

from trading_corp.brokers import bitunix as bx
from trading_corp.brokers.bitunix import BitunixBroker
from trading_corp.brokers.bitunix_exceptions import BitunixStaleSnapshot


def _make_broker(*, with_credentials: bool = True) -> BitunixBroker:
    if with_credentials:
        return BitunixBroker(api_key="k", api_secret="s")
    return BitunixBroker(api_key=None, api_secret=None)


# ---------------------------------------------------------------------------
# is_healthy() — fail-closed on first call (no snapshot yet)
# ---------------------------------------------------------------------------

def test_is_healthy_false_when_no_snapshot_yet():
    broker = _make_broker()
    assert broker._last_successful_snapshot_ts is None
    assert broker.is_healthy() is False


# ---------------------------------------------------------------------------
# is_healthy() — True directly after a successful snapshot
# ---------------------------------------------------------------------------

def test_is_healthy_true_after_recent_snapshot(monkeypatch):
    broker = _make_broker()
    # Simulate a successful snapshot having JUST returned.
    broker._last_successful_snapshot_ts = time.monotonic()
    # Threshold YAML read defaults to _DEFAULT_SNAPSHOT_STALENESS_S (60s).
    assert broker.is_healthy() is True


# ---------------------------------------------------------------------------
# is_healthy() — False when older than threshold
# ---------------------------------------------------------------------------

def test_is_healthy_false_when_older_than_threshold(monkeypatch):
    broker = _make_broker()
    # Force threshold lookup to 1 second and ts older than now-1.
    monkeypatch.setattr(broker, "_staleness_threshold_s", lambda: 1.0)
    broker._last_successful_snapshot_ts = time.monotonic() - 5.0
    assert broker.is_healthy() is False


# ---------------------------------------------------------------------------
# is_healthy() — boundary: ts exactly at threshold is still healthy
# ---------------------------------------------------------------------------

def test_is_healthy_boundary_at_threshold(monkeypatch):
    broker = _make_broker()
    monkeypatch.setattr(broker, "_staleness_threshold_s", lambda: 10.0)
    fixed_now = 1_000.0
    monkeypatch.setattr(bx.time, "monotonic", lambda: fixed_now)
    broker._last_successful_snapshot_ts = fixed_now - 10.0  # exactly threshold
    assert broker.is_healthy() is True
    broker._last_successful_snapshot_ts = fixed_now - 10.001  # just past
    assert broker.is_healthy() is False


# ---------------------------------------------------------------------------
# _assert_snapshot_fresh — no-op when fresh
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assert_snapshot_fresh_noop_when_fresh():
    broker = _make_broker()
    broker._last_successful_snapshot_ts = time.monotonic()
    await broker._assert_snapshot_fresh()  # no raise
    assert broker._halt_new_orders is False
    assert broker._halt_reason is None


# ---------------------------------------------------------------------------
# _assert_snapshot_fresh — raises + latches halt when stale
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assert_snapshot_fresh_raises_and_latches_when_stale(monkeypatch):
    broker = _make_broker()
    monkeypatch.setattr(broker, "_staleness_threshold_s", lambda: 1.0)
    broker._last_successful_snapshot_ts = time.monotonic() - 5.0

    with pytest.raises(BitunixStaleSnapshot) as ei:
        await broker._assert_snapshot_fresh()
    # Halt is latched BEFORE raising.
    assert broker._halt_new_orders is True
    assert broker._halt_reason is not None
    assert broker._halt_reason.startswith("snapshot_stale:")
    # Exception carries age + threshold so the operator-facing message can
    # render the magnitude of the staleness.
    assert ei.value.age_s >= 5.0
    assert ei.value.threshold_s == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _assert_snapshot_fresh — never-snapshotted case (age == inf)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assert_snapshot_fresh_never_snapshotted_is_infinite_age():
    broker = _make_broker()
    assert broker._last_successful_snapshot_ts is None
    with pytest.raises(BitunixStaleSnapshot) as ei:
        await broker._assert_snapshot_fresh()
    assert ei.value.age_s == float("inf")
    assert broker._halt_new_orders is True


# ---------------------------------------------------------------------------
# Recovery: a new fresh snapshot reverses is_healthy() but the halt latch stays.
# Operator-clear via resume() — mirrors position-mode-mismatch recovery.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_halt_latch_persists_after_recovery_until_resume(monkeypatch):
    broker = _make_broker()
    monkeypatch.setattr(broker, "_staleness_threshold_s", lambda: 1.0)
    broker._last_successful_snapshot_ts = time.monotonic() - 5.0

    with pytest.raises(BitunixStaleSnapshot):
        await broker._assert_snapshot_fresh()
    assert broker._halt_new_orders is True

    # New successful snapshot: ts refreshes, is_healthy flips True.
    broker._last_successful_snapshot_ts = time.monotonic()
    assert broker.is_healthy() is True
    # …but the halt latch is sticky.
    assert broker._halt_new_orders is True
    assert broker._halt_reason is not None

    # Operator action clears the latch explicitly.
    broker.resume()
    assert broker._halt_new_orders is False
    assert broker._halt_reason is None


# ---------------------------------------------------------------------------
# _staleness_threshold_s — falls back to default on YAML read error
# ---------------------------------------------------------------------------

def test_staleness_threshold_defaults_when_yaml_missing(monkeypatch):
    broker = _make_broker()
    # Force the Path-resolution path to fail by patching pathlib.Path.stat.
    import pathlib
    orig_stat = pathlib.Path.stat

    def boom(self):
        raise FileNotFoundError("simulated")

    monkeypatch.setattr(pathlib.Path, "stat", boom)
    try:
        assert broker._staleness_threshold_s() == bx._DEFAULT_SNAPSHOT_STALENESS_S
    finally:
        monkeypatch.setattr(pathlib.Path, "stat", orig_stat)


# ---------------------------------------------------------------------------
# _staleness_threshold_s — reads the REAL config/strategies.yaml shipped on
# this branch (60s under bitunix_futures), then caches by mtime.
# ---------------------------------------------------------------------------

def test_staleness_threshold_reads_real_yaml_and_caches():
    broker = _make_broker()
    assert broker._staleness_threshold_cache is None

    value1 = broker._staleness_threshold_s()
    # config/strategies.yaml on this branch has the gate (a) sub-item 2 entry
    # at 60 — assert that's what we read.
    assert value1 == 60.0
    assert broker._staleness_threshold_cache is not None
    cached_mtime, cached_value = broker._staleness_threshold_cache
    assert cached_value == 60.0

    # Second call — same mtime → cache hit, same value (no re-read).
    value2 = broker._staleness_threshold_s()
    assert value2 == 60.0
    assert broker._staleness_threshold_cache[0] == cached_mtime


# ---------------------------------------------------------------------------
# _staleness_threshold_s — invalid value (≤0) falls back to default.
# Tested by directly poisoning the cache to bypass the YAML read path:
# verifies the value-validation branch inside the YAML-parse code.
# (Direct YAML-mutation tests would require editing the real config file,
# which would pollute branch state; the validation logic is straightforward
# Python — a unit assertion is sufficient.)
# ---------------------------------------------------------------------------

def test_staleness_threshold_validates_value_branch():
    # Probe the value-validation logic by exercising the inline check:
    # `if value <= 0: value = _DEFAULT_SNAPSHOT_STALENESS_S`.
    # We can't easily mutate the live YAML, but we can directly call the
    # branch via a stub.
    broker = _make_broker()

    # Patch the YAML read to return non-positive via simulated bx_block.
    # We monkeypatch yaml.safe_load to return a 0 value, then call the
    # method and assert the default is used.
    import yaml as _yaml
    orig_safe_load = _yaml.safe_load

    def fake_safe_load(f):
        return {"bitunix_futures": {"snapshot_staleness_threshold_seconds": 0}}

    _yaml.safe_load = fake_safe_load
    try:
        # Invalidate the cache to force a re-read.
        broker._staleness_threshold_cache = None
        assert broker._staleness_threshold_s() == bx._DEFAULT_SNAPSHOT_STALENESS_S
    finally:
        _yaml.safe_load = orig_safe_load


# ---------------------------------------------------------------------------
# is_healthy is a pure read — never calls _assert_snapshot_fresh (no side effects).
# ---------------------------------------------------------------------------

def test_is_healthy_has_no_side_effects(monkeypatch):
    broker = _make_broker()
    monkeypatch.setattr(broker, "_staleness_threshold_s", lambda: 1.0)
    broker._last_successful_snapshot_ts = time.monotonic() - 5.0  # stale
    # Calling is_healthy() should NOT latch the halt — the latch is
    # _assert_snapshot_fresh's job.
    assert broker.is_healthy() is False
    assert broker._halt_new_orders is False
    assert broker._halt_reason is None


# ---------------------------------------------------------------------------
# BitunixStaleSnapshot __repr__ contains diagnostic numbers
# ---------------------------------------------------------------------------

def test_stale_snapshot_message_carries_diagnostics():
    exc = BitunixStaleSnapshot(age_s=125.0, threshold_s=60.0)
    msg = str(exc)
    assert "125" in msg
    assert "60" in msg
    assert exc.age_s == 125.0
    assert exc.threshold_s == 60.0
