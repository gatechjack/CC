"""Tests for the REDEEM-grounded realized-basis selection scorer.

Covers option (c) Phase 1:
  - `build_audit_report` populates the two new decision-unit fields
    (`n_winning_decisions`, `total_buy_usdc_resolved`)
  - `score_whale_from_audit` composite SHAPE = Wilson LCB × edge × category
    bonus, on decision-unit / realized inputs:
      * PLAIN decision-unit Wilson (no time-weighting — D2)
      * edge factor from realized ROI (D5; zero-denominator floor)
      * inflation exclusion gate, STRICTLY greater than threshold, with the
        exactly-at-threshold boundary KEPT (D4)
      * min-resolved gate
      * category bonus preserved (Rule-B)
  - the synthesized WhaleStats renders through the refresh details path (D3)
"""
from __future__ import annotations

from typing import Any

import pytest

from trading_corp.data.kalshi_whale_stats import _edge_factor, wilson_lcb_95
from trading_corp.data.polymarket_data_api_client import ActivityRow
from trading_corp.data.polymarket_whale_audit import (
    CategoryConcentrationReport,
    ClusteringReport,
    EdgeProfileReport,
    REDEEM_OUTCOME_INDEX_SENTINEL,
    RealizedPnLReport,
    SellFootprintReport,
    WhaleAuditReport,
    build_audit_report,
)
from trading_corp.data.polymarket_whale_stats import (
    DEFAULT_INFLATION_RATIO_THRESHOLD,
    DEFAULT_MIN_RESOLVED,
    score_whale_from_audit,
)


# ── helpers ──────────────────────────────────────────────────────────────


def _act(
    ts: int, cid: str, *, side: str = "BUY", oi: int = 0,
    price: float = 0.5, size: float = 100.0, type_: str = "TRADE",
    event_slug: str = "ev", title: str = "",
) -> ActivityRow:
    return ActivityRow(
        proxy_wallet="0xwhale", timestamp=ts, condition_id=cid, type=type_,
        size=size, usdc_size=size * price, transaction_hash=f"0xh{ts}",
        price=price, asset="", side=side, outcome_index=oi,
        title=title or f"market {cid}", slug=cid, event_slug=event_slug,
        outcome="Yes" if oi == 0 else "No", name="whale",
    )


def _redeem(ts: int, cid: str, size: float) -> ActivityRow:
    return ActivityRow(
        proxy_wallet="0xwhale", timestamp=ts, condition_id=cid, type="REDEEM",
        size=size, usdc_size=size, transaction_hash=f"0xr{ts}", price=0.0,
        asset="", side="", outcome_index=REDEEM_OUTCOME_INDEX_SENTINEL,
        title="", slug=cid, event_slug="ev", outcome="", name="whale",
    )


def _resolved(winning_outcome_index: int = 0) -> dict[str, Any]:
    return {"status": "resolved", "winning_outcome_index": winning_outcome_index}


def _report(
    *,
    n_resolved: int,
    n_winning: int,
    buy_usdc: float,
    realized_pnl: float,
    inflation_ratio: float = 0.0,
    user_name: str = "w",
    wallet: str = "0xwhale",
) -> WhaleAuditReport:
    """Build a WhaleAuditReport directly with the four fields the scorer
    actually reads (`n_resolved_decisions`, `n_winning_decisions`,
    `total_buy_usdc_resolved`, `realized_pnl.{realized_pnl_usdc,
    pnl_inflation_ratio}`); other sub-reports are filler."""
    return WhaleAuditReport(
        proxy_wallet=wallet, user_name=user_name,
        activity_max_ts=1_700_000_000, activity_min_ts=1_699_000_000,
        n_raw_rows_examined=200, n_resolved_decisions=n_resolved,
        clustering=ClusteringReport(
            n_raw_fills=200, n_decisions=n_resolved, clustering_ratio=1.0,
            decisions_with_ge_5_fills=0,
        ),
        sell_footprint=SellFootprintReport(
            n_decisions_total=n_resolved, n_decisions_with_sells=0,
            n_round_trips=0, n_partial_sells=0, partial_sell_threshold=0.20,
            n_held_cleanly=n_resolved, top_flagged_by_inflation_usdc=(),
        ),
        edge=EdgeProfileReport(
            n_decisions=n_resolved, avg_entry_price_decision_weighted=0.5,
            share_below_70=0.0, share_above_85=0.0,
            p25_entry=0.5, p50_entry=0.5, p75_entry=0.5,
        ),
        category=CategoryConcentrationReport(
            n_distinct_event_slugs=1, top_3_event_slugs=(), largest_event_share=1.0,
        ),
        realized_pnl=RealizedPnLReport(
            realized_pnl_usdc=realized_pnl,
            held_to_resolution_pnl_usdc=realized_pnl,
            pnl_inflation_usdc=0.0,
            pnl_inflation_ratio=inflation_ratio,
            pnl_from_clean_holds_usdc=realized_pnl,
            pnl_from_partial_sells_usdc=0.0,
        ),
        partial_sell_threshold_used=0.20,
        n_winning_decisions=n_winning,
        total_buy_usdc_resolved=buy_usdc,
    )


# ── build_audit_report populates the new decision-unit fields ────────────


def test_build_audit_report_populates_winning_and_buy_usdc():
    """Two resolved decisions: cid_a (winning, oi=0, $50 cost) and cid_b
    (losing, oi=1 but winner is oi=0, $20 cost). n_winning=1, total buy USDC
    over resolved = $70."""
    rows = [
        _act(1000, "cid_a", oi=0, size=100.0, price=0.5),   # $50 buy, wins
        _redeem(1100, "cid_a", size=100.0),
        _act(999, "cid_b", oi=1, size=100.0, price=0.2),    # $20 buy, loses
    ]
    resolutions = {"cid_a": _resolved(0), "cid_b": _resolved(0)}
    report = build_audit_report(
        leaderboard_entry=None, activity_rows=rows, resolutions=resolutions,
        proxy_wallet="0xwhale",
    )
    assert report.n_resolved_decisions == 2
    assert report.n_winning_decisions == 1
    assert report.total_buy_usdc_resolved == pytest.approx(70.0)


def test_build_audit_report_unresolved_excluded_from_new_fields():
    rows = [
        _act(1000, "cid_a", oi=0, size=100.0, price=0.5),
        _redeem(1100, "cid_a", size=100.0),
        _act(999, "cid_open", oi=0, size=100.0, price=0.5),  # unresolved
    ]
    resolutions = {"cid_a": _resolved(0), "cid_open": {"status": "pending"}}
    report = build_audit_report(
        leaderboard_entry=None, activity_rows=rows, resolutions=resolutions,
        proxy_wallet="0xwhale",
    )
    assert report.n_resolved_decisions == 1
    assert report.n_winning_decisions == 1
    assert report.total_buy_usdc_resolved == pytest.approx(50.0)


# ── Wilson is plain decision-unit (D2: no time-weighting) ────────────────


def test_wilson_is_plain_decision_unit():
    report = _report(n_resolved=20, n_winning=15, buy_usdc=1000.0, realized_pnl=0.0)
    sw = score_whale_from_audit(report)
    assert sw.wilson_lcb == pytest.approx(wilson_lcb_95(15, 20))


# ── edge factor from realized ROI (D5) ───────────────────────────────────


def test_edge_factor_from_realized_roi():
    # realized +$300 on $1000 cost → ROI 0.30 → edge 1.30
    report = _report(n_resolved=20, n_winning=12, buy_usdc=1000.0, realized_pnl=300.0)
    sw = score_whale_from_audit(report)
    assert sw.edge_factor == pytest.approx(_edge_factor(0.30))
    assert sw.edge_factor == pytest.approx(1.30)
    # composite = wilson × edge × 1.0 (no category target)
    assert sw.composite_score == pytest.approx(wilson_lcb_95(12, 20) * 1.30)


def test_zero_buy_usdc_denominator_floors_roi_to_zero():
    # n>0 but no cost basis (e.g. SELL-only decisions) → ROI 0.0, edge 1.0, no crash
    report = _report(n_resolved=12, n_winning=8, buy_usdc=0.0, realized_pnl=50.0)
    sw = score_whale_from_audit(report)
    assert sw.edge_factor == pytest.approx(1.0)
    assert sw.composite_score == pytest.approx(wilson_lcb_95(8, 12))


# ── inflation gate (D4): strictly greater excludes; == kept ──────────────


def test_inflation_gate_keeps_exactly_at_threshold():
    report = _report(
        n_resolved=20, n_winning=15, buy_usdc=1000.0, realized_pnl=200.0,
        inflation_ratio=DEFAULT_INFLATION_RATIO_THRESHOLD,  # exactly 0.5
    )
    sw = score_whale_from_audit(report)
    assert sw.excluded is False
    assert "inflation" not in sw.exclusion_reason


def test_inflation_gate_excludes_strictly_above():
    report = _report(
        n_resolved=20, n_winning=15, buy_usdc=1000.0, realized_pnl=200.0,
        inflation_ratio=0.6,
    )
    sw = score_whale_from_audit(report)
    assert sw.excluded is True
    assert "inflation>0.5" in sw.exclusion_reason
    # ratio is surfaced for the dry-run gated-out list
    assert "0.60" in sw.exclusion_reason


def test_inflation_gate_keeps_below():
    report = _report(
        n_resolved=20, n_winning=15, buy_usdc=1000.0, realized_pnl=200.0,
        inflation_ratio=0.49,
    )
    sw = score_whale_from_audit(report)
    assert sw.excluded is False


def test_custom_inflation_threshold():
    report = _report(
        n_resolved=20, n_winning=15, buy_usdc=1000.0, realized_pnl=200.0,
        inflation_ratio=0.31,
    )
    # default 0.5 keeps it; a tightened 0.30 gate excludes it
    assert score_whale_from_audit(report).excluded is False
    sw = score_whale_from_audit(report, inflation_threshold=0.30)
    assert sw.excluded is True
    assert "inflation>0.3" in sw.exclusion_reason


# ── min-resolved gate ─────────────────────────────────────────────────────


def test_min_resolved_gate_excludes_but_keeps_score():
    report = _report(n_resolved=5, n_winning=4, buy_usdc=500.0, realized_pnl=100.0)
    sw = score_whale_from_audit(report, min_resolved=10)
    assert sw.excluded is True
    assert sw.exclusion_reason == "resolved<10"
    # composite still computed (NOT zeroed) so the dry-run can show would-be rank
    assert sw.composite_score > 0.0


def test_zero_resolved_decisions_excluded_score_zero():
    report = _report(n_resolved=0, n_winning=0, buy_usdc=0.0, realized_pnl=0.0)
    sw = score_whale_from_audit(report)
    assert sw.excluded is True
    assert sw.exclusion_reason == "no_resolved_decisions"
    assert sw.composite_score == 0.0


def test_both_gates_fire_reason_is_joined():
    report = _report(
        n_resolved=5, n_winning=4, buy_usdc=500.0, realized_pnl=100.0,
        inflation_ratio=0.9,
    )
    sw = score_whale_from_audit(report, min_resolved=10)
    assert sw.excluded is True
    assert "resolved<10" in sw.exclusion_reason
    assert "inflation>0.5" in sw.exclusion_reason


# ── category bonus preserved (Rule-B) ────────────────────────────────────


def test_category_bonus_applied_for_target_match():
    report = _report(n_resolved=20, n_winning=15, buy_usdc=1000.0, realized_pnl=200.0)
    base = score_whale_from_audit(report, target_category=None)
    boosted = score_whale_from_audit(
        report, target_category="Sports", whale_categories=("Sports",),
    )
    assert base.category_bonus == pytest.approx(1.0)
    assert boosted.category_bonus == pytest.approx(1.5)
    assert boosted.composite_score == pytest.approx(base.composite_score * 1.5)


def test_category_bonus_absent_without_match():
    report = _report(n_resolved=20, n_winning=15, buy_usdc=1000.0, realized_pnl=200.0)
    sw = score_whale_from_audit(
        report, target_category="Crypto", whale_categories=("Sports",),
    )
    assert sw.category_bonus == pytest.approx(1.0)


# ── D3: synthesized WhaleStats renders through the details path ──────────


def test_synthesized_stats_renders_through_details_path():
    """The ScoredWhale.stats synthesized by score_whale_from_audit must flow
    through the refresh's print/details field accesses + format specifiers
    without error (D3)."""
    report = _report(n_resolved=20, n_winning=15, buy_usdc=1000.0, realized_pnl=300.0)
    sw = score_whale_from_audit(report)
    # The exact attribute reads the refresh details dict performs:
    d = {
        "closed_positions_count": sw.stats.closed_positions_count,
        "wins": sw.stats.wins,
        "win_rate": round(sw.stats.win_rate, 3),
        "avg_pnl_per_contract_usdc": round(sw.stats.avg_pnl_per_contract, 4),
        "wilson_lcb": round(sw.wilson_lcb, 4),
        "edge_factor": round(sw.edge_factor, 3),
        "category_bonus": round(sw.category_bonus, 2),
        "composite_score": round(sw.composite_score, 4),
    }
    # win_rate is the decision win rate
    assert d["win_rate"] == pytest.approx(0.75)
    assert d["closed_positions_count"] == 20
    assert d["wins"] == 15
    # mimic _print_human's format specifiers — must not raise
    line = (
        f"{d['composite_score']:>7.4f} | {d['wilson_lcb']:>7.4f} | "
        f"{d['avg_pnl_per_contract_usdc']:>+8.4f} | {d['win_rate']:>5.2f} | "
        f"{d['closed_positions_count']:>4}"
    )
    assert line
