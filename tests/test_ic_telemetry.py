"""Synthetic-archetype tests for IC telemetry queries.

Coverage matrix the user asked for in step 13:
  - Winner with no adjustment
  - Loser with no adjustment
  - Winner with adjustment
  - Loser with adjustment
  - Unfilled scan (combo proposed but never filled — appears in
    scan_telemetry filters)
  - Filtered scan by EACH reason (ivr_below_30, vix_above_30,
    macro_halt, term_structure_backwardated, ex_dividend_window)
  - Slippage events (positive — better than limit, exact match)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from trading_corp.agents.ic_telemetry import (
    adjustment_outcome_stats,
    combo_pnl_report,
    combo_slippage_stats,
    scan_filter_counters,
    win_rate_by_ivr,
)
from trading_corp.persistence import db


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _ensure_schema(db_url: str) -> None:
    from trading_corp.persistence.db import SCHEMA
    with db.connect(db_url) as conn:
        conn.executescript(SCHEMA)


def _insert_combo_legs(
    db_url: str,
    *,
    combo_id: str,
    symbol: str = "SPY",
    division: str = "robinhood_joint",
    strategy: str = "robinhood_joint_iron_condor",
    open_prices: tuple[float, float, float, float] = (0.60, 0.20, 0.65, 0.20),
    close_prices: tuple[float, float, float, float] | None = (0.20, 0.05, 0.25, 0.05),
    ivr_at_entry: float | None = 45.0,
    opened_ts: str = "2026-05-15T15:00:00",
    closed_ts: str = "2026-06-01T15:00:00",
) -> None:
    """Insert 4 open legs (+ optionally 4 close legs) for one combo.

    open_prices: (short_put, long_put, short_call, long_call) — used as
    avg_price. Per step-7 signed-qty: buys=+qty, sells=-qty.
    Cashflow = -qty * avg_price * 100.
    """
    rows = []
    legs_open = [
        ("short_put",  "sell", "put",  430.0, open_prices[0], -1.0),
        ("long_put",   "buy",  "put",  427.0, open_prices[1], +1.0),
        ("short_call", "sell", "call", 470.0, open_prices[2], -1.0),
        ("long_call",  "buy",  "call", 473.0, open_prices[3], +1.0),
    ]
    for role, side, otype, strike, mark, signed_qty in legs_open:
        ex = {
            "combo_id": combo_id, "combo_role": role,
            "combo_direction": "credit",
            "is_option": True, "is_combo_leg": True,
            "underlying": symbol, "option_type": otype,
            "strike": strike, "position_effect": "open",
            "strategy": strategy, "division": division,
        }
        if ivr_at_entry is not None:
            ex["ic_underlying_iv_rank_at_entry"] = ivr_at_entry
        rows.append((division, symbol, signed_qty, mark, opened_ts, json.dumps(ex)))

    if close_prices is not None:
        legs_close = [
            ("short_put",  "buy",  "put",  430.0, close_prices[0], +1.0),
            ("long_put",   "sell", "put",  427.0, close_prices[1], -1.0),
            ("short_call", "buy",  "call", 470.0, close_prices[2], +1.0),
            ("long_call",  "sell", "call", 473.0, close_prices[3], -1.0),
        ]
        for role, side, otype, strike, mark, signed_qty in legs_close:
            ex = {
                "combo_id": combo_id, "combo_role": role,
                "combo_direction": "debit",
                "is_option": True, "is_combo_leg": True,
                "underlying": symbol, "option_type": otype,
                "strike": strike, "position_effect": "close",
                "strategy": strategy, "division": division,
            }
            rows.append((division, symbol, signed_qty, mark, closed_ts, json.dumps(ex)))

    with db.connect(db_url) as conn:
        conn.executemany(
            "INSERT INTO position(account, symbol, qty, avg_price, opened_ts, extra_json) "
            "VALUES(?,?,?,?,?,?)",
            rows,
        )


def _insert_lifecycle_audit(
    db_url: str,
    *,
    combo_id: str,
    ts: str,
    ivr_at_entry: float = 45.0,
    credit_at_entry: float = 1.20,
    contracts: int = 1,
    adjustment_count: int = 0,
    realized_pnl_per_share: float,
    close_kind: str = "profit_target",
    strategy: str = "robinhood_joint_iron_condor",
    symbol: str = "SPY",
) -> None:
    payload = {
        "combo_id": combo_id, "symbol": symbol,
        "ivr_at_entry": ivr_at_entry,
        "credit_at_entry": credit_at_entry,
        "contracts": contracts,
        "adjustment_count": adjustment_count,
        "realized_pnl_per_share": realized_pnl_per_share,
        "realized_pnl_dollars": realized_pnl_per_share * 100 * contracts,
        "close_kind": close_kind,
    }
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event(ts, actor, kind, payload_json) "
            "VALUES(?,?,?,?)",
            (ts, strategy, "ic_lifecycle_closed", json.dumps(payload)),
        )


def _insert_combo_filled_audit(
    db_url: str,
    *,
    ts: str,
    combo_id: str,
    direction: str = "credit",
    net_limit: float = 1.20,
    net_actual: float = 1.30,
    strategy: str = "robinhood_joint_iron_condor",
    division: str = "robinhood_joint",
) -> None:
    payload = {
        "combo_id": combo_id,
        "strategy": strategy, "division": division,
        "direction": direction,
        "net_limit_price": net_limit,
        "net_actual": net_actual,
        "actual_vs_limit_slippage_dollars": abs(net_actual - net_limit),
        "leg_count": 4,
        "legs": [],
    }
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event(ts, actor, kind, payload_json) VALUES(?,?,?,?)",
            (ts, "data_exec", "combo_filled", json.dumps(payload)),
        )


def _write_scan_telemetry(
    db_url: str,
    *,
    by_day: dict[str, dict],
    strategy: str = "robinhood_joint_iron_condor",
) -> None:
    state = {
        "open_ics": {},
        "circuit_breaker": {
            "consecutive_losses": 0, "recent_pnl": [],
            "paused_until": None, "drawdown_hwm": None,
        },
        "scan_telemetry": by_day,
    }
    db.set_agent_state(strategy, "state", state, db_url=db_url)


# ---------------------------------------------------------------------------
# combo_pnl_report — archetype matrix
# ---------------------------------------------------------------------------


def test_pnl_report_classifies_winner_loser_and_open(tmp_db):
    _ensure_schema(tmp_db)
    # Winner, no adjustment: collected 1.20 credit, closed for 0.50 debit
    # → realized = $1.20 - $0.50 = $0.70 per share = $70 dollars.
    _insert_combo_legs(
        tmp_db, combo_id="winner-1",
        open_prices=(0.60, 0.20, 0.65, 0.15),   # net credit = 0.90
        close_prices=(0.20, 0.05, 0.25, 0.05),  # net debit = 0.35
        # realized = +0.55 × 100 = +$55
    )
    # Loser, no adjustment: 1.20 credit collected, closed for 2.40 debit → -$120
    _insert_combo_legs(
        tmp_db, combo_id="loser-1",
        open_prices=(0.60, 0.20, 0.65, 0.15),   # net credit 0.90
        close_prices=(1.50, 0.10, 1.40, 0.20),  # net debit 2.60
        # realized = +0.90 - 2.60 = -1.70 × 100 = -$170
    )
    # Still-open combo: no close legs.
    _insert_combo_legs(
        tmp_db, combo_id="open-1",
        open_prices=(0.55, 0.20, 0.55, 0.20),
        close_prices=None,
    )
    report = combo_pnl_report(db_url=tmp_db)
    by_id = {c["combo_id"]: c for c in report["combos"]}

    assert by_id["winner-1"]["status"] == "realized"
    assert by_id["winner-1"]["net_pnl"] == pytest.approx(55.0)
    assert by_id["loser-1"]["status"] == "realized"
    assert by_id["loser-1"]["net_pnl"] == pytest.approx(-170.0)
    assert by_id["open-1"]["status"] == "open"

    s = report["summary"]
    assert s["realized_count"] == 2
    assert s["win_count"] == 1
    assert s["loss_count"] == 1
    assert s["win_rate"] == 0.5
    assert s["mean_win"] == pytest.approx(55.0)
    assert s["mean_loss"] == pytest.approx(-170.0)
    assert s["expectancy"] == pytest.approx(0.5 * 55.0 + 0.5 * -170.0)
    assert s["open_count"] == 1
    assert s["total_realized"] == pytest.approx(-115.0)


def test_pnl_report_filters_by_strategy(tmp_db):
    _ensure_schema(tmp_db)
    _insert_combo_legs(tmp_db, combo_id="ic-1",
                       strategy="robinhood_joint_iron_condor",
                       open_prices=(0.50, 0.10, 0.50, 0.10),
                       close_prices=(0.20, 0.05, 0.20, 0.05))
    _insert_combo_legs(tmp_db, combo_id="other-1",
                       strategy="fidelity_options",
                       open_prices=(1.0, 0.5, 1.0, 0.5),
                       close_prices=None)
    report = combo_pnl_report(
        strategy="robinhood_joint_iron_condor", db_url=tmp_db,
    )
    ids = {c["combo_id"] for c in report["combos"]}
    assert ids == {"ic-1"}


def test_pnl_report_filters_by_date_range(tmp_db):
    _ensure_schema(tmp_db)
    _insert_combo_legs(tmp_db, combo_id="old", opened_ts="2026-01-10T15:00:00",
                       closed_ts="2026-02-01T15:00:00")
    _insert_combo_legs(tmp_db, combo_id="recent",
                       opened_ts="2026-05-10T15:00:00",
                       closed_ts="2026-06-01T15:00:00")
    report = combo_pnl_report(
        start_ts="2026-05-01T00:00:00",
        end_ts="2026-06-30T00:00:00",
        db_url=tmp_db,
    )
    ids = {c["combo_id"] for c in report["combos"]}
    assert "recent" in ids
    assert "old" not in ids


# ---------------------------------------------------------------------------
# win_rate_by_ivr
# ---------------------------------------------------------------------------


def test_win_rate_by_ivr_buckets_correctly(tmp_db):
    _ensure_schema(tmp_db)
    # 3 in 30-40 bucket (2 wins, 1 loss); 2 in 50-60 (2 losses).
    _insert_lifecycle_audit(tmp_db, combo_id="a", ts="2026-05-01T00:00:00",
                            ivr_at_entry=35.0, realized_pnl_per_share=+0.40)
    _insert_lifecycle_audit(tmp_db, combo_id="b", ts="2026-05-02T00:00:00",
                            ivr_at_entry=38.0, realized_pnl_per_share=+0.20)
    _insert_lifecycle_audit(tmp_db, combo_id="c", ts="2026-05-03T00:00:00",
                            ivr_at_entry=39.0, realized_pnl_per_share=-0.80)
    _insert_lifecycle_audit(tmp_db, combo_id="d", ts="2026-05-04T00:00:00",
                            ivr_at_entry=52.0, realized_pnl_per_share=-1.00)
    _insert_lifecycle_audit(tmp_db, combo_id="e", ts="2026-05-05T00:00:00",
                            ivr_at_entry=58.0, realized_pnl_per_share=-0.60)
    report = win_rate_by_ivr(db_url=tmp_db)

    by_label = {b["label"]: b for b in report["buckets"]}
    assert by_label["30-40"]["count"] == 3
    assert by_label["30-40"]["win_count"] == 2
    assert by_label["30-40"]["loss_count"] == 1
    assert by_label["30-40"]["win_rate"] == pytest.approx(2/3)

    assert by_label["50-60"]["count"] == 2
    assert by_label["50-60"]["win_rate"] == 0.0   # no wins
    assert by_label["50-60"]["mean_pnl_dollars"] == pytest.approx(-80.0)

    assert by_label["40-50"]["count"] == 0
    assert by_label["40-50"]["win_rate"] is None

    assert report["total_closed"] == 5


def test_win_rate_by_ivr_handles_missing_ivr(tmp_db):
    _ensure_schema(tmp_db)
    _insert_lifecycle_audit(tmp_db, combo_id="x", ts="2026-05-01T00:00:00",
                            ivr_at_entry=None, realized_pnl_per_share=+0.50)
    # ts column on audit_event requires NOT NULL; payload's ivr_at_entry is None.
    report = win_rate_by_ivr(db_url=tmp_db)
    assert report["unbucketed_count"] == 1


# ---------------------------------------------------------------------------
# adjustment_outcome_stats — winner / loser × adjust / no-adjust
# ---------------------------------------------------------------------------


def test_adjustment_outcome_compares_adjusted_vs_unadjusted(tmp_db):
    _ensure_schema(tmp_db)
    # Winner no adjust:
    _insert_lifecycle_audit(tmp_db, combo_id="w0", ts="2026-05-01T00:00:00",
                            adjustment_count=0, realized_pnl_per_share=+0.60)
    # Loser no adjust:
    _insert_lifecycle_audit(tmp_db, combo_id="l0", ts="2026-05-02T00:00:00",
                            adjustment_count=0, realized_pnl_per_share=-1.20)
    # Winner WITH adjustment:
    _insert_lifecycle_audit(tmp_db, combo_id="w1", ts="2026-05-03T00:00:00",
                            adjustment_count=1, realized_pnl_per_share=+0.20)
    # Loser WITH adjustment:
    _insert_lifecycle_audit(tmp_db, combo_id="l1", ts="2026-05-04T00:00:00",
                            adjustment_count=1, realized_pnl_per_share=-0.80)
    report = adjustment_outcome_stats(db_url=tmp_db)

    adj = report["adjusted"]
    unadj = report["unadjusted"]

    assert adj["count"] == 2
    assert adj["win_count"] == 1
    assert adj["loss_count"] == 1
    assert adj["win_rate"] == 0.5
    assert adj["mean_pnl"] == pytest.approx((20 - 80) / 2)        # = -30
    assert adj["total_pnl"] == pytest.approx(-60.0)

    assert unadj["count"] == 2
    assert unadj["win_count"] == 1
    assert unadj["loss_count"] == 1
    assert unadj["mean_pnl"] == pytest.approx((60 - 120) / 2)     # = -30
    assert unadj["total_pnl"] == pytest.approx(-60.0)


def test_adjustment_outcome_empty_bucket_returns_zeros(tmp_db):
    _ensure_schema(tmp_db)
    # Only an unadjusted entry; adjusted bucket should be empty/zero.
    _insert_lifecycle_audit(tmp_db, combo_id="w0", ts="2026-05-01T00:00:00",
                            adjustment_count=0, realized_pnl_per_share=+0.50)
    report = adjustment_outcome_stats(db_url=tmp_db)
    assert report["adjusted"]["count"] == 0
    assert report["adjusted"]["mean_pnl"] is None
    assert report["unadjusted"]["count"] == 1


# ---------------------------------------------------------------------------
# scan_filter_counters — filtered by each reason
# ---------------------------------------------------------------------------


def test_scan_filter_counters_aggregates_each_reason(tmp_db):
    _ensure_schema(tmp_db)
    # All 5 documented reasons appearing across 2 days.
    by_day = {
        "2026-05-15": {
            "SPY": {"total": 5, "by_reason": {
                "ivr_below_30": 3, "vix_above_30": 2,
            }},
            "QQQ": {"total": 2, "by_reason": {
                "ex_dividend_window": 2,
            }},
        },
        "2026-05-16": {
            "SPY": {"total": 3, "by_reason": {
                "macro_halt": 1, "term_structure_backwardated": 2,
            }},
            "IWM": {"total": 1, "by_reason": {
                "ivr_below_30": 1,
            }},
        },
    }
    _write_scan_telemetry(tmp_db, by_day=by_day)

    # Full history
    report = scan_filter_counters(db_url=tmp_db)
    assert report["total_filtered"] == 5 + 2 + 3 + 1
    totals = report["totals_by_reason"]
    assert totals["ivr_below_30"] == 3 + 1
    assert totals["vix_above_30"] == 2
    assert totals["ex_dividend_window"] == 2
    assert totals["macro_halt"] == 1
    assert totals["term_structure_backwardated"] == 2

    # Single-day filter
    only_15 = scan_filter_counters(date_iso="2026-05-15", db_url=tmp_db)
    assert only_15["total_filtered"] == 5 + 2
    assert only_15["totals_by_reason"]["ivr_below_30"] == 3
    assert "macro_halt" not in only_15["totals_by_reason"]


def test_scan_filter_counters_empty_when_no_state(tmp_db):
    _ensure_schema(tmp_db)
    report = scan_filter_counters(db_url=tmp_db)
    assert report == {"by_day": {}, "totals_by_reason": {}, "total_filtered": 0}


# ---------------------------------------------------------------------------
# combo_slippage_stats
# ---------------------------------------------------------------------------


def test_combo_slippage_stats_distribution(tmp_db):
    _ensure_schema(tmp_db)
    # 5 fills: 0.18, 0.05, 0.00 (exact), 0.30, 0.10
    _insert_combo_filled_audit(tmp_db, ts="2026-05-15T15:00:00",
                               combo_id="a", net_limit=1.00, net_actual=1.18)
    _insert_combo_filled_audit(tmp_db, ts="2026-05-15T16:00:00",
                               combo_id="b", net_limit=1.00, net_actual=1.05)
    _insert_combo_filled_audit(tmp_db, ts="2026-05-15T17:00:00",
                               combo_id="c", net_limit=1.20, net_actual=1.20)
    _insert_combo_filled_audit(tmp_db, ts="2026-05-15T18:00:00",
                               combo_id="d", net_limit=0.80, net_actual=1.10)
    _insert_combo_filled_audit(tmp_db, ts="2026-05-15T19:00:00",
                               combo_id="e", net_limit=1.00, net_actual=1.10)
    report = combo_slippage_stats(db_url=tmp_db)
    s = report["summary"]
    assert s["n"] == 5
    # Slippages: 0.18, 0.05, 0.00, 0.30, 0.10  → mean = 0.126
    assert s["mean_slippage"] == pytest.approx(0.126)
    assert s["max_slippage"] == pytest.approx(0.30)
    assert s["median_slippage"] == pytest.approx(0.10)


def test_combo_slippage_filters_by_strategy(tmp_db):
    _ensure_schema(tmp_db)
    _insert_combo_filled_audit(tmp_db, ts="2026-05-15T15:00:00",
                               combo_id="ic", strategy="robinhood_joint_iron_condor",
                               net_limit=1.00, net_actual=1.10)
    _insert_combo_filled_audit(tmp_db, ts="2026-05-15T16:00:00",
                               combo_id="other", strategy="other_strategy",
                               net_limit=1.00, net_actual=1.50)
    report = combo_slippage_stats(
        strategy="robinhood_joint_iron_condor", db_url=tmp_db,
    )
    assert report["summary"]["n"] == 1
    assert report["events"][0]["combo_id"] == "ic"


# ---------------------------------------------------------------------------
# End-to-end archetype matrix — all 4 winner/loser × adjust/no-adjust
# combos plus filtered scans by each reason + slippage events.
# ---------------------------------------------------------------------------


def test_full_archetype_matrix_round_trip(tmp_db):
    _ensure_schema(tmp_db)
    # 4 closed combo lifecycles (one of each archetype).
    _insert_lifecycle_audit(tmp_db, combo_id="w-noadj",
                            ts="2026-05-01T00:00:00",
                            ivr_at_entry=42.0, adjustment_count=0,
                            realized_pnl_per_share=+0.45,
                            close_kind="profit_target")
    _insert_lifecycle_audit(tmp_db, combo_id="l-noadj",
                            ts="2026-05-02T00:00:00",
                            ivr_at_entry=55.0, adjustment_count=0,
                            realized_pnl_per_share=-1.50,
                            close_kind="hard_stop")
    _insert_lifecycle_audit(tmp_db, combo_id="w-adj",
                            ts="2026-05-03T00:00:00",
                            ivr_at_entry=33.0, adjustment_count=1,
                            realized_pnl_per_share=+0.15,
                            close_kind="profit_target")
    _insert_lifecycle_audit(tmp_db, combo_id="l-adj",
                            ts="2026-05-04T00:00:00",
                            ivr_at_entry=68.0, adjustment_count=1,
                            realized_pnl_per_share=-0.50,
                            close_kind="force_close_dte")

    # Scan filter counters covering every documented reason.
    _write_scan_telemetry(tmp_db, by_day={
        "2026-05-01": {
            "SPY": {"total": 1, "by_reason": {"ivr_below_30": 1}},
            "QQQ": {"total": 1, "by_reason": {"vix_above_30": 1}},
            "IWM": {"total": 1, "by_reason": {"macro_halt": 1}},
            "GLD": {"total": 1, "by_reason": {"ex_dividend_window": 1}},
            "TLT": {"total": 1, "by_reason": {"term_structure_backwardated": 1}},
        },
    })

    # Slippage: one exact, one favorable (positive slip).
    _insert_combo_filled_audit(tmp_db, ts="2026-05-01T15:00:00",
                               combo_id="w-noadj", net_limit=1.00, net_actual=1.00)
    _insert_combo_filled_audit(tmp_db, ts="2026-05-03T15:00:00",
                               combo_id="w-adj", net_limit=1.00, net_actual=1.10)

    # 1. P&L: NOT exercised here (no position rows inserted); the
    # lifecycle-audit-only path serves win_rate_by_ivr + adjustment
    # which are the per-combo realized stats.

    # 2. Win rate by IVR
    ivr = win_rate_by_ivr(db_url=tmp_db)
    by_label = {b["label"]: b for b in ivr["buckets"]}
    assert by_label["30-40"]["count"] == 1
    assert by_label["40-50"]["count"] == 1
    assert by_label["50-60"]["count"] == 1
    assert by_label["60-70"]["count"] == 1
    assert ivr["total_closed"] == 4

    # 3. Adjustment outcome
    adj = adjustment_outcome_stats(db_url=tmp_db)
    assert adj["adjusted"]["count"] == 2
    assert adj["unadjusted"]["count"] == 2

    # 4. Scan filter counters — every reason captured.
    scan = scan_filter_counters(db_url=tmp_db)
    assert scan["total_filtered"] == 5
    assert set(scan["totals_by_reason"].keys()) == {
        "ivr_below_30", "vix_above_30", "macro_halt",
        "ex_dividend_window", "term_structure_backwardated",
    }

    # 5. Slippage
    slip = combo_slippage_stats(db_url=tmp_db)
    assert slip["summary"]["n"] == 2
    assert slip["summary"]["max_slippage"] == pytest.approx(0.10)
    assert slip["summary"]["mean_slippage"] == pytest.approx(0.05)
