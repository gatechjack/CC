"""Horizon-cap tests for kalshi_temporal_bucket_arb._detect_temporal_violations.

The `max_horizon_days` cap (default 60) must drop any market resolving beyond
the cutoff, so no temporal PAIR's late leg locks capital too long. Pruning the
market list is sufficient because a pair needs late >= early, so if the late
leg is within the cutoff the early leg is too. Regression guard for the
2026-07-07 finding: 93% of live temporal proposals resolved >60 days out (some
in 2035), which is exactly what this cap removes.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from trading_corp.agents.strategies import kalshi_temporal_bucket_arb as tb


def _mkt(ticker, iso_date, yes_ask):
    # subtitle is the ISO date string; parse_subtitle_date reads it directly.
    return SimpleNamespace(ticker=ticker, subtitle=iso_date, yes_ask=yes_ask)


def _event(markets):
    return SimpleNamespace(
        event_ticker="KXTEST", title="Test temporal event", markets=markets,
    )


def test_no_cutoff_returns_violation():
    # P(early)=0.90 > P(late)=0.50 -> 40c edge; no horizon cap applied.
    ev = _event([
        _mkt("KX-EARLY", "2026-08-01", 0.90),
        _mkt("KX-LATE", "2026-08-20", 0.50),
    ])
    opps = tb._detect_temporal_violations(ev, min_edge_cents=4.0, horizon_cutoff=None)
    assert len(opps) == 1
    assert opps[0].late_date == date(2026, 8, 20)


def test_cutoff_drops_pair_with_far_late_leg():
    # Same near early leg, but the LATE leg resolves well past the cutoff ->
    # the whole pair must be dropped (it would lock capital past the horizon).
    ev = _event([
        _mkt("KX-EARLY", "2026-08-01", 0.90),
        _mkt("KX-LATE", "2027-06-01", 0.50),
    ])
    opps = tb._detect_temporal_violations(
        ev, min_edge_cents=4.0, horizon_cutoff=date(2026, 8, 15)
    )
    assert opps == []


def test_cutoff_keeps_near_pair_and_excludes_far_market():
    # Two near markets form a valid pair; a far-future (2035) market must never
    # be used as a leg. This is the direct regression guard for the 2035 rows.
    ev = _event([
        _mkt("KX-A", "2026-08-01", 0.90),
        _mkt("KX-B", "2026-08-20", 0.50),
        _mkt("KX-C", "2035-12-31", 0.10),
    ])
    opps = tb._detect_temporal_violations(
        ev, min_edge_cents=4.0, horizon_cutoff=date(2026, 9, 1)
    )
    assert len(opps) == 1
    assert opps[0].early_ticker == "KX-A"
    assert opps[0].late_ticker == "KX-B"
    assert date(2035, 12, 31) not in {o.late_date for o in opps}


# ── bucket guards (>=2 legs + expected_expiration horizon) ──────────────

_NOW = date(2026, 7, 7)
_CUTOFF = date(2026, 9, 5)   # ~60 days out


def _bmkt(ticker, yes_ask, exp_iso):
    return SimpleNamespace(
        ticker=ticker, subtitle="", yes_ask=yes_ask,
        expected_expiration_time=exp_iso,
    )


def _bevent(markets):
    return SimpleNamespace(event_ticker="KXBUCKET", title="Bucket event", markets=markets)


def test_bucket_requires_two_legs():
    # A single-leg "bucket" is not an arb, even with a fat edge.
    ev = _bevent([_bmkt("KX-1", 0.40, "2026-08-01T00:00:00Z")])
    assert tb._detect_bucket_violations(ev, 5.0, _CUTOFF, _NOW) is None


def test_bucket_within_horizon_emits():
    # 2 legs, sum=0.80 -> edge 0.20; both expire within the cutoff.
    ev = _bevent([
        _bmkt("KX-1", 0.40, "2026-08-01T00:00:00Z"),
        _bmkt("KX-2", 0.40, "2026-08-15T00:00:00Z"),
    ])
    opp = tb._detect_bucket_violations(ev, 5.0, _CUTOFF, _NOW)
    assert opp is not None
    assert len(opp.legs) == 2


def test_bucket_drops_far_future_expiration():
    ev = _bevent([
        _bmkt("KX-1", 0.40, "2026-08-01T00:00:00Z"),
        _bmkt("KX-2", 0.40, "2027-06-01T00:00:00Z"),   # beyond cutoff
    ])
    assert tb._detect_bucket_violations(ev, 5.0, _CUTOFF, _NOW) is None


def test_bucket_drops_past_expiration_stuck():
    # NBER-style: expiration already past but market still open -> stuck.
    ev = _bevent([
        _bmkt("KX-1", 0.40, "2026-05-01T00:00:00Z"),
        _bmkt("KX-2", 0.40, "2026-06-01T00:00:00Z"),
    ])
    assert tb._detect_bucket_violations(ev, 5.0, _CUTOFF, _NOW) is None


def test_bucket_drops_missing_expiration():
    ev = _bevent([
        _bmkt("KX-1", 0.40, "2026-08-01T00:00:00Z"),
        _bmkt("KX-2", 0.40, None),   # no expiration -> can't verify horizon
    ])
    assert tb._detect_bucket_violations(ev, 5.0, _CUTOFF, _NOW) is None
