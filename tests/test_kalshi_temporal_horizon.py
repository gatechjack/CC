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
