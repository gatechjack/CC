"""Tests for the whale-audit cache layer.

Covers cache_key construction, namespace isolation, hit/miss/TTL,
and roundtrip serialization via the frozen dataclass tree.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from trading_corp.agents.research.polymarket_whale_audit_cache import (
    AGENT_NAMESPACE,
    DEFAULT_TTL_SECONDS,
    KEY_PREFIX,
    cache_key,
    evict_audit,
    read_audit,
    write_audit,
)
from trading_corp.data.polymarket_whale_audit import (
    CategoryConcentrationReport, ClusteringReport, EdgeProfileReport,
    FlaggedDecision, RealizedPnLReport, SellFootprintReport,
    WhaleAuditReport,
)
from trading_corp.persistence.db import init_db, load_agent_state


@pytest.fixture
def db_url(tmp_path):
    """Fresh sqlite db per test. Schema init via init_db."""
    db_file = tmp_path / "test_trading_corp.db"
    url = f"sqlite:///{db_file}"
    init_db(url)
    return url


def _sample_report(
    wallet: str = "0xwhale",
    activity_max_ts: int = 1_700_000_000,
) -> WhaleAuditReport:
    """A minimal but complete WhaleAuditReport for round-trip tests."""
    return WhaleAuditReport(
        proxy_wallet=wallet.lower(),
        user_name="testwhale",
        activity_max_ts=activity_max_ts,
        activity_min_ts=activity_max_ts - 86400,
        n_raw_rows_examined=42,
        n_resolved_decisions=10,
        clustering=ClusteringReport(
            n_raw_fills=42, n_decisions=10, clustering_ratio=4.2,
            decisions_with_ge_5_fills=2,
            top_clusters_by_fill_count=(("0xabc", 0, 12), ("0xdef", 1, 8)),
        ),
        sell_footprint=SellFootprintReport(
            n_decisions_total=10, n_decisions_with_sells=3,
            n_round_trips=1, n_partial_sells=3, partial_sell_threshold=0.20,
            n_held_cleanly=7,
            top_flagged_by_inflation_usdc=(
                FlaggedDecision(
                    title="Big Inflation Decision",
                    condition_id_short="0xabc..",
                    outcome_index=0,
                    sum_buy_usdc=10000.0,
                    sum_sell_usdc=5000.0,
                    redeem_payout_usdc=5000.0,
                    sell_share=0.5,
                    is_round_trip=False,
                    is_winning_side=True,
                    realized_pnl=0.0,
                    held_to_resolution_pnl=5000.0,
                ),
            ),
        ),
        edge=EdgeProfileReport(
            n_decisions=10, avg_entry_price_decision_weighted=0.55,
            share_below_70=0.7, share_above_85=0.1,
            p25_entry=0.4, p50_entry=0.55, p75_entry=0.7,
        ),
        category=CategoryConcentrationReport(
            n_distinct_event_slugs=3,
            top_3_event_slugs=(("ev-1", 5), ("ev-2", 3), ("ev-3", 2)),
            largest_event_share=0.5,
        ),
        realized_pnl=RealizedPnLReport(
            realized_pnl_usdc=1234.56,
            held_to_resolution_pnl_usdc=2000.0,
            pnl_inflation_usdc=765.44,
            pnl_inflation_ratio=0.38,
            pnl_from_clean_holds_usdc=1000.0,
            pnl_from_partial_sells_usdc=234.56,
        ),
        partial_sell_threshold_used=0.20,
        verdict_narration="A test verdict line.",
        verdict_null_reason=None,
        llm_cost_usd=0.0013,
        llm_tokens_in=500,
        llm_tokens_out=50,
    )


# ── cache_key + namespace isolation ─────────────────────────────────────


def test_cache_key_format():
    assert cache_key("0xWHALE", 1_700_000_000) == "polymarket_whale_audit:0xwhale:1700000000"


def test_namespace_constants_are_isolated_from_promotion_slots():
    """The agent namespace must NOT match any promotion-side slot."""
    assert AGENT_NAMESPACE == "polymarket_whale_analyst"
    # Promotion slots all use agent='polymarket_copy_trader' — different namespace
    assert AGENT_NAMESPACE != "polymarket_copy_trader"
    # Key prefix is distinct from promotion-side keys
    assert KEY_PREFIX == "polymarket_whale_audit:"
    # Never starts with `watch_only_whales`, `selected_whales`, etc.
    for promo_key in (
        "watch_only_whales", "selected_whales", "pinned_whales", "metrics_epoch",
    ):
        assert not KEY_PREFIX.startswith(promo_key)


# ── round-trip ──────────────────────────────────────────────────────────


def test_write_then_read_returns_equivalent_report(db_url):
    report = _sample_report()
    write_audit(report, db_url=db_url)
    cached = read_audit(report.proxy_wallet, report.activity_max_ts, db_url=db_url)
    assert cached is not None
    assert cached.proxy_wallet == report.proxy_wallet
    assert cached.user_name == report.user_name
    assert cached.activity_max_ts == report.activity_max_ts
    assert cached.realized_pnl.realized_pnl_usdc == report.realized_pnl.realized_pnl_usdc
    assert cached.verdict_narration == report.verdict_narration
    # Nested tuples should be tuples again, not lists
    assert isinstance(cached.clustering.top_clusters_by_fill_count, tuple)
    assert isinstance(cached.sell_footprint.top_flagged_by_inflation_usdc, tuple)


def test_write_under_isolated_namespace(db_url):
    """Confirm the write hits agent='polymarket_whale_analyst', NOT
    'polymarket_copy_trader'."""
    report = _sample_report()
    write_audit(report, db_url=db_url)
    # Read via the persistence layer directly to confirm the namespace
    loaded = load_agent_state(
        "polymarket_whale_analyst",
        cache_key(report.proxy_wallet, report.activity_max_ts),
        db_url=db_url,
    )
    assert loaded is not None
    # And confirm NOTHING was written under polymarket_copy_trader
    loaded_promo = load_agent_state(
        "polymarket_copy_trader",
        cache_key(report.proxy_wallet, report.activity_max_ts),
        db_url=db_url,
    )
    assert loaded_promo is None


# ── miss + TTL ──────────────────────────────────────────────────────────


def test_read_returns_none_when_no_entry(db_url):
    assert read_audit("0xnonexistent", 999, db_url=db_url) is None


def test_read_returns_none_when_stale_and_evicts(db_url):
    report = _sample_report()
    write_audit(report, db_url=db_url)
    # Pretend "now" is far in the future — past the TTL
    future = datetime.now(timezone.utc) + timedelta(seconds=DEFAULT_TTL_SECONDS + 60)
    cached = read_audit(
        report.proxy_wallet, report.activity_max_ts,
        db_url=db_url, now=future,
    )
    assert cached is None
    # Stale entry should have been GC'd
    loaded = load_agent_state(
        "polymarket_whale_analyst",
        cache_key(report.proxy_wallet, report.activity_max_ts),
        db_url=db_url,
    )
    assert loaded is None


def test_read_within_ttl_succeeds(db_url):
    report = _sample_report()
    write_audit(report, db_url=db_url)
    near_future = datetime.now(timezone.utc) + timedelta(seconds=60)
    cached = read_audit(
        report.proxy_wallet, report.activity_max_ts,
        db_url=db_url, now=near_future,
    )
    assert cached is not None


# ── evict ───────────────────────────────────────────────────────────────


def test_evict_removes_specific_entry(db_url):
    report = _sample_report()
    write_audit(report, db_url=db_url)
    evict_audit(report.proxy_wallet, report.activity_max_ts, db_url=db_url)
    assert read_audit(
        report.proxy_wallet, report.activity_max_ts, db_url=db_url
    ) is None


def test_evict_is_idempotent(db_url):
    # Evicting a non-existent entry must not raise
    evict_audit("0xdoesnotexist", 999, db_url=db_url)


# ── self-invalidation via activity_max_ts ───────────────────────────────


def test_new_activity_max_ts_misses_old_cache_entry(db_url):
    """The cache key embeds activity_max_ts. A whale with new fills (later
    ts) auto-invalidates: same wallet, different ts → cache miss."""
    report_old = _sample_report(activity_max_ts=1_700_000_000)
    write_audit(report_old, db_url=db_url)
    # New activity max ts → different cache key → miss
    cached = read_audit(report_old.proxy_wallet, 1_700_000_001, db_url=db_url)
    assert cached is None
    # Old key still readable
    cached_old = read_audit(
        report_old.proxy_wallet, 1_700_000_000, db_url=db_url,
    )
    assert cached_old is not None
