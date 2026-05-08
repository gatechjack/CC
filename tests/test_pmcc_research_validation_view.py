"""Tests for the PMCC research-as-consultant validation view.

Pins the join behavior used by the 2026-05-02 → 2026-05-05 observation
period review surface on /research:

  research_candidate_recommendation_emitted   (engagement, candidates list)
    ⨝ research_candidate_acted_on / _skipped  (per-candidate division row)
    ⨝ proposed_order.status                   (downstream lifecycle)

Window starts at PMCC_OBSERVATION_PERIOD_START. Rows before that ts
must be excluded; non-PMCC engagements must be ignored.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from trading_corp.agents.logger import LoggerAgent
from trading_corp.persistence import db
from trading_corp.persistence.db import init_db
from trading_corp.web.routes import (
    PMCC_OBSERVATION_PERIOD_START,
    _build_pmcc_validation_view,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _deps(db_url: str) -> SimpleNamespace:
    return SimpleNamespace(logger_agent=LoggerAgent(db_url))


def _seed_audit(db_url: str, ts: str, actor: str, kind: str, payload: dict) -> None:
    """Insert a fully-formed audit_event row at a controlled timestamp.
    Bypasses LoggerAgent.log_event because that uses now_utc()."""
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event(ts, actor, kind, payload_json) "
            "VALUES(?,?,?,?)",
            (ts, actor, kind, json.dumps(payload)),
        )


def _seed_order(db_url: str, order_id: str, status: str, symbol: str = "AAPL") -> None:
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO proposed_order(id, ts, strategy, symbol, side, qty, "
            "order_type, limit_price, rationale, status, risk_reason, "
            "board_reason, fill_price, fill_ts, extra_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, "2026-05-02T13:00:00Z", "robinhood_pmcc", symbol,
             "buy", 1.0, "market", None, "test", status, None, None,
             None, None, "{}"),
        )


def _emit_payload(eid: str, candidates: list[dict]) -> dict:
    return {
        "engagement_id": eid,
        "requesting_division": "robinhood_pmcc",
        "product_type": "candidate_recommendation",
        "asset_class": "equity",
        "engagement_started_ts": "2026-05-02T13:00:00Z",
        "engagement_completed_ts": "2026-05-02T13:00:15Z",
        "product": {
            "engagement_id": eid,
            "requesting_division": "robinhood_pmcc",
            "asset_class": "equity",
            "candidates": candidates,
        },
        "cost_dollars": 0.0,
    }


def _candidate(symbol: str, conviction: str = "medium", fit: float = 0.5) -> dict:
    return {
        "symbol": symbol,
        "thesis": f"{symbol} fake thesis",
        "conviction": conviction,
        "fit_rationale": f"{symbol} fits",
        "fit_score": fit,
    }


# ── Tests ────────────────────────────────────────────────────────────────


def test_empty_state(tmp_db: str) -> None:
    init_db(tmp_db)
    view = _build_pmcc_validation_view(_deps(tmp_db))
    assert view["n_engagements"] == 0
    assert view["n_candidates"] == 0
    assert view["n_acted_on"] == 0
    assert view["n_skipped"] == 0
    assert view["n_board_approved_or_filled"] == 0
    assert view["n_filled"] == 0
    assert view["top_skip_reasons"] == []
    assert view["engagements"] == []
    assert view["observation_start"] == PMCC_OBSERVATION_PERIOD_START


def test_full_join_acted_on_skipped_no_outcome(tmp_db: str) -> None:
    """One PMCC engagement with 3 candidates: one acted_on (filled),
    one skipped (with reason), one with no division-side row (research
    returned a candidate that the per-symbol gate never even reached).
    Asserts the join surfaces all three correctly."""
    init_db(tmp_db)
    eid = str(uuid.uuid4())
    order_id = str(uuid.uuid4())

    # Engagement-side: 3 candidates returned by research
    _seed_audit(
        tmp_db, "2026-05-02T13:00:15Z", "research_firm",
        "research_candidate_recommendation_emitted",
        _emit_payload(eid, [
            _candidate("AAPL", conviction="high", fit=0.85),
            _candidate("MSFT", conviction="medium", fit=0.6),
            _candidate("NVDA", conviction="low", fit=0.3),
        ]),
    )
    # Division-side: AAPL acted on, MSFT skipped (earnings_within_buffer),
    # NVDA never gets a row (e.g., the loop crashed before reaching it —
    # shouldn't happen in practice but the view must still render)
    _seed_audit(
        tmp_db, "2026-05-02T13:00:16Z", "robinhood_pmcc",
        "research_candidate_acted_on",
        {"engagement_id": eid, "requesting_division": "robinhood_pmcc",
         "symbol": "AAPL", "candidate_index": 0, "fit_score": 0.85,
         "conviction": "high", "proposed_order_id": order_id},
    )
    _seed_audit(
        tmp_db, "2026-05-02T13:00:17Z", "robinhood_pmcc",
        "research_candidate_skipped",
        {"engagement_id": eid, "requesting_division": "robinhood_pmcc",
         "symbol": "MSFT", "candidate_index": 1, "fit_score": 0.6,
         "conviction": "medium", "reason": "earnings_within_buffer"},
    )
    # Order downstream: filled
    _seed_order(tmp_db, order_id, "filled", symbol="AAPL")

    view = _build_pmcc_validation_view(_deps(tmp_db))

    assert view["n_engagements"] == 1
    assert view["n_candidates"] == 3
    assert view["n_acted_on"] == 1
    assert view["n_skipped"] == 1
    assert view["n_board_approved_or_filled"] == 1
    assert view["n_filled"] == 1
    assert view["top_skip_reasons"] == [{"reason": "earnings_within_buffer", "count": 1}]
    assert len(view["engagements"]) == 1

    eng = view["engagements"][0]
    assert eng["engagement_id"] == eid
    assert eng["n_candidates"] == 3
    by_sym = {c["symbol"]: c for c in eng["candidates"]}

    assert by_sym["AAPL"]["status"] == "acted_on"
    assert by_sym["AAPL"]["order_status"] == "filled"
    assert by_sym["AAPL"]["proposed_order_id"] == order_id
    assert by_sym["AAPL"]["conviction"] == "high"
    assert by_sym["AAPL"]["fit_score"] == 0.85
    assert by_sym["AAPL"]["skip_reason"] is None

    assert by_sym["MSFT"]["status"] == "skipped"
    assert by_sym["MSFT"]["skip_reason"] == "earnings_within_buffer"
    assert by_sym["MSFT"]["order_status"] is None
    assert by_sym["MSFT"]["proposed_order_id"] is None

    assert by_sym["NVDA"]["status"] == "no_outcome"
    assert by_sym["NVDA"]["skip_reason"] is None
    assert by_sym["NVDA"]["order_status"] is None


def test_acted_on_with_pending_order_not_yet_filled(tmp_db: str) -> None:
    """Acted_on candidate where the proposed_order is still in an
    earlier lifecycle stage (board_approved but not filled). Counts
    in n_board_approved_or_filled but not in n_filled."""
    init_db(tmp_db)
    eid = str(uuid.uuid4())
    order_id = str(uuid.uuid4())
    _seed_audit(
        tmp_db, "2026-05-02T13:00:15Z", "research_firm",
        "research_candidate_recommendation_emitted",
        _emit_payload(eid, [_candidate("AAPL", conviction="high", fit=0.9)]),
    )
    _seed_audit(
        tmp_db, "2026-05-02T13:00:16Z", "robinhood_pmcc",
        "research_candidate_acted_on",
        {"engagement_id": eid, "requesting_division": "robinhood_pmcc",
         "symbol": "AAPL", "candidate_index": 0, "fit_score": 0.9,
         "conviction": "high", "proposed_order_id": order_id},
    )
    _seed_order(tmp_db, order_id, "board_approved")

    view = _build_pmcc_validation_view(_deps(tmp_db))
    assert view["n_acted_on"] == 1
    assert view["n_board_approved_or_filled"] == 1
    assert view["n_filled"] == 0
    cand = view["engagements"][0]["candidates"][0]
    assert cand["order_status"] == "board_approved"


def test_acted_on_with_rejected_order(tmp_db: str) -> None:
    """Acted_on candidate whose proposed_order was risk_rejected
    downstream — visible in order_status but NOT counted as approved."""
    init_db(tmp_db)
    eid = str(uuid.uuid4())
    order_id = str(uuid.uuid4())
    _seed_audit(
        tmp_db, "2026-05-02T13:00:15Z", "research_firm",
        "research_candidate_recommendation_emitted",
        _emit_payload(eid, [_candidate("AAPL")]),
    )
    _seed_audit(
        tmp_db, "2026-05-02T13:00:16Z", "robinhood_pmcc",
        "research_candidate_acted_on",
        {"engagement_id": eid, "requesting_division": "robinhood_pmcc",
         "symbol": "AAPL", "candidate_index": 0, "fit_score": 0.5,
         "conviction": "medium", "proposed_order_id": order_id},
    )
    _seed_order(tmp_db, order_id, "risk_rejected")

    view = _build_pmcc_validation_view(_deps(tmp_db))
    assert view["n_acted_on"] == 1
    assert view["n_board_approved_or_filled"] == 0
    assert view["n_filled"] == 0
    cand = view["engagements"][0]["candidates"][0]
    assert cand["order_status"] == "risk_rejected"


def test_out_of_window_rows_excluded(tmp_db: str) -> None:
    """Engagement timestamp BEFORE PMCC_OBSERVATION_PERIOD_START must
    not appear in the view."""
    init_db(tmp_db)
    eid_before = str(uuid.uuid4())
    eid_after = str(uuid.uuid4())
    _seed_audit(
        tmp_db, "2026-05-01T20:00:00Z", "research_firm",
        "research_candidate_recommendation_emitted",
        _emit_payload(eid_before, [_candidate("AAPL")]),
    )
    _seed_audit(
        tmp_db, "2026-05-02T13:00:00Z", "research_firm",
        "research_candidate_recommendation_emitted",
        _emit_payload(eid_after, [_candidate("MSFT")]),
    )
    view = _build_pmcc_validation_view(_deps(tmp_db))
    assert view["n_engagements"] == 1
    assert view["engagements"][0]["engagement_id"] == eid_after


def test_non_pmcc_engagement_ignored(tmp_db: str) -> None:
    """Engagements where requesting_division != 'robinhood_pmcc' must
    not be surfaced — this is the PMCC-specific validation view."""
    init_db(tmp_db)
    pmcc_eid = str(uuid.uuid4())
    other_eid = str(uuid.uuid4())
    _seed_audit(
        tmp_db, "2026-05-02T13:00:15Z", "research_firm",
        "research_candidate_recommendation_emitted",
        _emit_payload(pmcc_eid, [_candidate("AAPL")]),
    )
    # Same row shape but for lord_otter
    other_payload = _emit_payload(other_eid, [_candidate("BTC/USD")])
    other_payload["requesting_division"] = "lord_otter"
    other_payload["product"]["requesting_division"] = "lord_otter"
    _seed_audit(
        tmp_db, "2026-05-02T13:05:00Z", "research_firm",
        "research_candidate_recommendation_emitted",
        other_payload,
    )
    view = _build_pmcc_validation_view(_deps(tmp_db))
    assert view["n_engagements"] == 1
    assert view["engagements"][0]["engagement_id"] == pmcc_eid


def test_engagements_sorted_newest_first(tmp_db: str) -> None:
    init_db(tmp_db)
    eid_old = str(uuid.uuid4())
    eid_new = str(uuid.uuid4())
    _seed_audit(
        tmp_db, "2026-05-02T13:00:00Z", "research_firm",
        "research_candidate_recommendation_emitted",
        _emit_payload(eid_old, [_candidate("AAPL")]),
    )
    _seed_audit(
        tmp_db, "2026-05-03T09:00:00Z", "research_firm",
        "research_candidate_recommendation_emitted",
        _emit_payload(eid_new, [_candidate("MSFT")]),
    )
    view = _build_pmcc_validation_view(_deps(tmp_db))
    assert [e["engagement_id"] for e in view["engagements"]] == [eid_new, eid_old]


def test_skip_reasons_histogram_aggregates(tmp_db: str) -> None:
    """Skip reasons across engagements aggregate into a single
    histogram, sorted by count descending."""
    init_db(tmp_db)
    eid = str(uuid.uuid4())
    _seed_audit(
        tmp_db, "2026-05-02T13:00:15Z", "research_firm",
        "research_candidate_recommendation_emitted",
        _emit_payload(eid, [
            _candidate("A"), _candidate("B"), _candidate("C"),
            _candidate("D"), _candidate("E"),
        ]),
    )
    for sym, reason in [
        ("A", "earnings_within_buffer"),
        ("B", "earnings_within_buffer"),
        ("C", "earnings_within_buffer"),
        ("D", "no_qualifying_chain"),
        ("E", "no_qualifying_chain"),
    ]:
        _seed_audit(
            tmp_db, "2026-05-02T13:00:16Z", "robinhood_pmcc",
            "research_candidate_skipped",
            {"engagement_id": eid, "requesting_division": "robinhood_pmcc",
             "symbol": sym, "candidate_index": 0, "fit_score": 0.5,
             "conviction": "medium", "reason": reason},
        )
    view = _build_pmcc_validation_view(_deps(tmp_db))
    assert view["n_skipped"] == 5
    assert view["top_skip_reasons"] == [
        {"reason": "earnings_within_buffer", "count": 3},
        {"reason": "no_qualifying_chain", "count": 2},
    ]


def test_acted_on_without_order_id_surfaces_none_status(tmp_db: str) -> None:
    """If acted_on is logged without a proposed_order_id (defensive —
    shouldn't happen in practice), the candidate still surfaces with
    status='acted_on' and order_status=None."""
    init_db(tmp_db)
    eid = str(uuid.uuid4())
    _seed_audit(
        tmp_db, "2026-05-02T13:00:15Z", "research_firm",
        "research_candidate_recommendation_emitted",
        _emit_payload(eid, [_candidate("AAPL")]),
    )
    _seed_audit(
        tmp_db, "2026-05-02T13:00:16Z", "robinhood_pmcc",
        "research_candidate_acted_on",
        {"engagement_id": eid, "requesting_division": "robinhood_pmcc",
         "symbol": "AAPL", "candidate_index": 0, "fit_score": 0.5,
         "conviction": "high"},  # no proposed_order_id
    )
    view = _build_pmcc_validation_view(_deps(tmp_db))
    cand = view["engagements"][0]["candidates"][0]
    assert cand["status"] == "acted_on"
    assert cand["order_status"] is None
    assert cand["proposed_order_id"] is None
    assert view["n_acted_on"] == 1
    assert view["n_board_approved_or_filled"] == 0


def test_logger_events_since_excludes_older_rows(tmp_db: str) -> None:
    """Direct test on LoggerAgent.events_since — pins that ts < cutoff
    is excluded, ts >= cutoff is included."""
    init_db(tmp_db)
    _seed_audit(tmp_db, "2026-05-01T23:59:59Z", "x", "k", {})
    _seed_audit(tmp_db, "2026-05-02T00:00:00Z", "x", "k", {})
    _seed_audit(tmp_db, "2026-05-02T12:00:00Z", "x", "k", {})

    logger = LoggerAgent(tmp_db)
    events = logger.events_since("2026-05-02T00:00:00Z")
    assert len(events) == 2
    # newest-first ordering
    assert events[0]["ts"] == "2026-05-02T12:00:00Z"
    assert events[1]["ts"] == "2026-05-02T00:00:00Z"
