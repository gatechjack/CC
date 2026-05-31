"""Tests for the Stage-1 paper-mode header badge resolver.

The badge surfaces three signals to the operator on every dashboard page:
  • bitunix_futures execution_mode (paper/live/unwired/unknown)
  • deployed git SHA (short, or 'unknown')
  • live-since label ('just now' / '47m' / '2h 14m' / '3d 4h')

It reads from `request.app.state` at template-render time. These tests
pin the data-shape contract that `base.html` consumes via the Jinja
global `stage1_badge(request)`.
"""
from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

import pytest

from trading_corp.web.app import _format_live_since, _stage1_badge_data


def _mk_request(*, obs=None, git_sha="unknown", live_since_utc=None):
    """Build the minimal request shape `_stage1_badge_data` reads."""
    deps = types.SimpleNamespace(bitunix_observer=obs) if obs is not None else None
    state = types.SimpleNamespace(
        deps=deps,
        git_sha=git_sha,
        live_since_utc=live_since_utc,
    )
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state))


# ── _format_live_since ────────────────────────────────────────────────────

def test_format_live_since_just_now():
    now = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(seconds=30)
    assert _format_live_since(start, now) == "just now"


def test_format_live_since_minutes_only():
    now = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(minutes=47)
    assert _format_live_since(start, now) == "47m"


def test_format_live_since_hours_and_minutes():
    now = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(hours=2, minutes=14)
    assert _format_live_since(start, now) == "2h 14m"


def test_format_live_since_hours_only_when_round():
    now = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(hours=3)
    assert _format_live_since(start, now) == "3h"


def test_format_live_since_days_and_hours():
    now = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(days=3, hours=4)
    assert _format_live_since(start, now) == "3d 4h"


def test_format_live_since_days_only_when_round():
    now = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(days=5)
    assert _format_live_since(start, now) == "5d"


# ── _stage1_badge_data ────────────────────────────────────────────────────

def test_badge_paper_mode_with_sha_and_live_since():
    """Happy path: bitunix observer wired in paper mode + GIT_SHA set."""
    obs = types.SimpleNamespace(execution_mode="paper")
    live_since = datetime.now(timezone.utc) - timedelta(hours=2, minutes=14)
    req = _mk_request(
        obs=obs,
        git_sha="7352f8ff6cf7ba6abcc1ef45037b8a81d7715f1f",
        live_since_utc=live_since,
    )

    result = _stage1_badge_data(req)

    assert result["execution_mode"] == "paper"
    assert result["git_sha"] == "7352f8f"  # short SHA, 7 chars
    assert result["division"] == "bitunix_futures"
    assert "h" in result["live_since_label"]  # at least an "h"
    assert result["live_since_iso"].endswith("Z")


def test_badge_live_mode_renders_live_token():
    obs = types.SimpleNamespace(execution_mode="live")
    req = _mk_request(
        obs=obs,
        git_sha="abcdef0123456789",
        live_since_utc=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    result = _stage1_badge_data(req)
    assert result["execution_mode"] == "live"
    assert result["git_sha"] == "abcdef0"


def test_badge_observer_missing_returns_unwired():
    """When deps.bitunix_observer is None, the badge must not crash."""
    deps = types.SimpleNamespace(bitunix_observer=None)
    state = types.SimpleNamespace(
        deps=deps,
        git_sha="unknown",
        live_since_utc=datetime.now(timezone.utc),
    )
    req = types.SimpleNamespace(app=types.SimpleNamespace(state=state))
    result = _stage1_badge_data(req)
    assert result["execution_mode"] == "unwired"
    assert result["git_sha"] == "unknown"


def test_badge_deps_entirely_missing_returns_unwired():
    """When app.state has no deps at all (early init / test env)."""
    state = types.SimpleNamespace(
        deps=None,
        git_sha="unknown",
        live_since_utc=datetime.now(timezone.utc),
    )
    req = types.SimpleNamespace(app=types.SimpleNamespace(state=state))
    result = _stage1_badge_data(req)
    assert result["execution_mode"] == "unwired"


def test_badge_unknown_sha_passes_through():
    """GIT_SHA env not set → app.state.git_sha = 'unknown' → render literal."""
    obs = types.SimpleNamespace(execution_mode="paper")
    req = _mk_request(
        obs=obs,
        git_sha="unknown",
        live_since_utc=datetime.now(timezone.utc),
    )
    result = _stage1_badge_data(req)
    assert result["git_sha"] == "unknown"


def test_badge_execution_mode_missing_attr_returns_unknown():
    """Observer wired but lacks execution_mode — defensive path."""
    obs = types.SimpleNamespace()  # no execution_mode attr
    req = _mk_request(
        obs=obs,
        git_sha="abc1234",
        live_since_utc=datetime.now(timezone.utc),
    )
    result = _stage1_badge_data(req)
    assert result["execution_mode"] == "unknown"


def test_badge_live_since_missing_renders_dash():
    """app.state.live_since_utc absent → label and iso both '—'."""
    obs = types.SimpleNamespace(execution_mode="paper")
    req = _mk_request(obs=obs, git_sha="unknown", live_since_utc=None)
    result = _stage1_badge_data(req)
    assert result["live_since_label"] == "—"
    assert result["live_since_iso"] == "—"
