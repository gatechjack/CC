"""Tests for the on-demand whale-audit dashboard endpoint.

POST /api/polymarket/watchlist/analyze/{proxy_wallet}

Covers:
  - returns rendered partial for a known wallet
  - cache hit shared between CLI and dashboard (verified by writing via
    `write_audit` directly, then asserting the endpoint returns the
    cached report — same namespace, same key)
  - null-verdict reasons RENDER readably in the partial (never blank)
  - one polymarket_whale_analyzed audit_event row emitted with
    source="dashboard" per call
  - NO promotion-relevant agent_state slot is written by the endpoint
    (asserted directly via load_agent_state pre/post)
  - invalid wallet format returns an error fragment, not a 500
"""
from __future__ import annotations

import dataclasses
import sys
import types
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from trading_corp.agents.polymarket_whale_analyst import NarrationResult
from trading_corp.agents.research.polymarket_whale_audit_cache import (
    AGENT_NAMESPACE, cache_key, write_audit,
)
from trading_corp.data.polymarket_data_api_client import (
    ActivityRow, LeaderboardEntry,
)
from trading_corp.data.polymarket_whale_audit import (
    CategoryConcentrationReport, ClusteringReport, EdgeProfileReport,
    FlaggedDecision, RealizedPnLReport, SellFootprintReport, WhaleAuditReport,
)
from trading_corp.persistence import db
from trading_corp.web import data as web_data
from trading_corp.web.app import WebDeps, create_app


# ── Stubs ────────────────────────────────────────────────────────────────


@dataclass
class _StubSnap:
    mode: str = "PAPER"
    dry_run: bool = False
    regime: str = "neutral"
    vix: float = 15.0
    health: Any = None
    equity_curve: list = None

    def __post_init__(self):
        if self.health is None:
            self.health = types.SimpleNamespace(
                brokers=[], scheduler=types.SimpleNamespace(last_run=None),
            )
        if self.equity_curve is None:
            self.equity_curve = []


async def _stub_build_command_center(deps):
    return _StubSnap(mode=deps.mode)


@pytest.fixture
def stub_snap(monkeypatch):
    monkeypatch.setattr(
        web_data, "build_command_center", _stub_build_command_center,
    )


# ── In-memory logger that records audit events ──────────────────────────


class _RecordingLogger:
    """Stand-in for LoggerAgent. Captures `log_event` calls so tests can
    assert which audit_event rows were emitted."""

    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def log_event(self, actor: str, kind: str, payload: dict) -> None:
        self.events.append((actor, kind, dict(payload)))


# ── Fake PolymarketDataAPIClient ────────────────────────────────────────


def _fake_activity_rows(wallet: str, n_buys: int = 5) -> list[ActivityRow]:
    """Build a small synthetic feed: n_buys winning BUYs on distinct cids."""
    rows = []
    for i in range(n_buys):
        rows.append(ActivityRow(
            proxy_wallet=wallet,
            timestamp=1_700_000_000 - i * 3600,
            condition_id=f"0xcid_{i:040x}"[:42],
            type="TRADE",
            size=100.0,
            usdc_size=50.0,
            transaction_hash=f"0xhash{i}",
            price=0.5,
            asset="",
            side="BUY",
            outcome_index=0,
            title=f"Test market {i}",
            slug=f"test-market-{i}",
            event_slug=f"event-{i}",
            outcome="Yes",
            name="testwhale",
        ))
    return rows


def _fake_resolutions(rows: list[ActivityRow]) -> dict[str, dict]:
    res = {}
    for r in rows:
        res[r.condition_id] = {
            "status": "resolved",
            "winning_outcome_index": 0,  # always wins in synthetic feed
        }
    return res


class _FakePolymarketClient:
    """Minimal stand-in for `PolymarketDataAPIClient` async context-manager.

    Returns the same canned activity for every wallet so the test is
    deterministic. The real client makes 4 kinds of calls during analyze:
    activity peek (limit=1), full activity walk, gamma resolutions,
    leaderboard top-50 — we serve all four from the canned set."""

    _CANNED_ROWS: dict[str, list[ActivityRow]] = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def fetch_activity(self, wallet, *, limit, offset):
        rows = self._CANNED_ROWS.get(wallet.lower(), [])
        # Page slicing
        sliced = rows[offset:offset + limit]
        return list(sliced)

    async def fetch_market_resolutions(self, condition_ids, *, chunk_size=50):
        # Resolve everything we know about; unknown cids absent
        out = {}
        for rows in self._CANNED_ROWS.values():
            for r in rows:
                if r.condition_id in condition_ids:
                    out[r.condition_id] = {
                        "status": "resolved",
                        "winning_outcome_index": 0,
                    }
        return out

    async def fetch_leaderboard(self, *, category, limit, offset):
        return []


@pytest.fixture
def patch_polymarket_client(monkeypatch):
    """Monkeypatch the PolymarketDataAPIClient class in its source
    module — the route imports it inline so we patch at source."""
    import trading_corp.data.polymarket_data_api_client as api_mod
    monkeypatch.setattr(api_mod, "PolymarketDataAPIClient", _FakePolymarketClient)


# ── Fake WhaleAnalyst (no real LLM) ─────────────────────────────────────


class _FakeAnalystOK:
    """Returns a successful narration."""
    def __init__(self, *args, **kwargs):
        pass

    async def narrate(self, report):
        return NarrationResult(
            narration="Test whale shows clean held positions and no inflation.",
            null_reason=None, cost_usd=0.0012,
            tokens_in=250, tokens_out=40,
        )


class _FakeAnalystNullReason:
    """Returns a null verdict with a specific reason — for testing each
    null-reason path renders in the partial."""
    def __init__(self, *args, reason="disabled_by_flag", **kwargs):
        self._reason = reason

    async def narrate(self, report):
        return NarrationResult(
            narration=None, null_reason=self._reason,
            cost_usd=0.0, tokens_in=0, tokens_out=0,
        )


# ── Test deps + app ─────────────────────────────────────────────────────


def _build_deps(tmp_db: str, logger: _RecordingLogger | None) -> WebDeps:
    return WebDeps(
        db_url=tmp_db,
        db_path=tmp_db.replace("sqlite:///", ""),
        mode="PAPER",
        logger_agent=logger,
        data_exec=None,
        trend_agent=None,
        portfolio=None,
        pmcc_agent=None,
        fidelity_agent=None,
        paper_broker=None,
        secrets=None,
        risk_agent=None,
        pending_registry=None,
    )


@pytest.fixture
def client(tmp_db, stub_snap, patch_polymarket_client):
    db.init_db(tmp_db)
    logger = _RecordingLogger()
    deps = _build_deps(tmp_db, logger)
    # Seed canned activity for the test wallet
    wallet = "0x" + "a" * 40
    _FakePolymarketClient._CANNED_ROWS[wallet.lower()] = _fake_activity_rows(wallet)
    app = create_app(deps)
    yield TestClient(app), deps, logger, wallet
    _FakePolymarketClient._CANNED_ROWS.clear()


# ── Tests ───────────────────────────────────────────────────────────────


def test_analyze_endpoint_returns_rendered_partial(client, monkeypatch):
    """Endpoint should render the partial template with the 6 sections."""
    client_, deps, logger, wallet = client
    monkeypatch.setattr(
        "trading_corp.agents.polymarket_whale_analyst.WhaleAnalyst", _FakeAnalystOK,
    )
    r = client_.post(f"/api/polymarket/watchlist/analyze/{wallet}")
    assert r.status_code == 200
    body = r.text
    # The 6 section headers from analyze_whale_result.html
    assert "Clustering" in body
    assert "Sell footprint" in body
    assert "Edge profile" in body
    assert "Category concentration" in body
    assert "Realized PnL" in body
    assert "Verdict" in body
    # Successful narration renders
    assert "clean held positions" in body
    # Partial wrapper class is present (for re-analyze targeting)
    assert "whale-audit-container" in body


def test_cache_hit_shared_with_cli(client, monkeypatch):
    """A cached report from the CLI path (write_audit directly) should
    be served by the dashboard endpoint without re-running the LLM —
    proves the cache namespace is genuinely shared, not two namespaces
    that happen to look alike."""
    client_, deps, logger, wallet = client

    # Pre-seed the cache as if the CLI had already run
    pre_cached_report = WhaleAuditReport(
        proxy_wallet=wallet.lower(),
        user_name="cli-cached-whale",
        activity_max_ts=1_700_000_000,  # matches first canned row's ts
        activity_min_ts=1_700_000_000 - 14400,
        n_raw_rows_examined=5, n_resolved_decisions=5,
        clustering=ClusteringReport(5, 5, 1.0, 0, ()),
        sell_footprint=SellFootprintReport(5, 0, 0, 0, 0.20, 5, ()),
        edge=EdgeProfileReport(5, 0.5, 1.0, 0.0, 0.5, 0.5, 0.5),
        category=CategoryConcentrationReport(5, (("event-0", 1),), 0.2),
        realized_pnl=RealizedPnLReport(
            250.0, 250.0, 0.0, 0.0, 250.0, 0.0,
        ),
        partial_sell_threshold_used=0.20,
        verdict_narration="CACHED VERDICT — from a prior CLI run",
        verdict_null_reason=None,
        llm_cost_usd=0.0011, llm_tokens_in=240, llm_tokens_out=35,
    )
    write_audit(pre_cached_report, db_url=deps.db_url)

    # Patch the analyst to a "loud" analyst so we can detect if it was
    # called (it must NOT be — cache hit should short-circuit)
    called = {"narrate": False}

    class _LoudAnalyst:
        def __init__(self, *a, **kw): pass
        async def narrate(self, report):
            called["narrate"] = True
            return NarrationResult(
                narration="FRESH (should not appear)", null_reason=None,
                cost_usd=0.005, tokens_in=999, tokens_out=999,
            )

    monkeypatch.setattr(
        "trading_corp.agents.polymarket_whale_analyst.WhaleAnalyst", _LoudAnalyst,
    )

    r = client_.post(f"/api/polymarket/watchlist/analyze/{wallet}")
    assert r.status_code == 200
    # The CACHED verdict must render — proving the cache was used
    assert "CACHED VERDICT" in r.text
    assert "cli-cached-whale" in r.text
    assert "CACHE HIT" in r.text
    # The analyst must NOT have been called
    assert called["narrate"] is False, "analyst was invoked despite cache hit"


@pytest.mark.parametrize("null_reason,expected_text", [
    ("disabled_by_flag", "verdict disabled"),
    ("llm_unavailable", "LLM unavailable"),
    ("daily_cap_hit", "daily LLM cost cap reached"),
    ("llm_error", "LLM call errored"),
])
def test_null_verdict_reasons_render_readably(
    client, monkeypatch, null_reason, expected_text,
):
    """Every null_reason value must produce a readable line in the
    partial — never a blank verdict section."""
    client_, deps, logger, wallet = client
    def _make_null_analyst(*args, **kwargs):
        return _FakeAnalystNullReason(reason=null_reason)
    monkeypatch.setattr(
        "trading_corp.agents.polymarket_whale_analyst.WhaleAnalyst", _make_null_analyst,
    )
    r = client_.post(f"/api/polymarket/watchlist/analyze/{wallet}?force=1")
    assert r.status_code == 200
    body = r.text
    # The reason text appears (operator never sees a silent None)
    assert expected_text in body, (
        f"null_reason={null_reason!r} expected text {expected_text!r} not in body"
    )
    # The "verdict not emitted" preamble is also present
    assert "verdict not emitted" in body


def test_audit_event_emitted_with_dashboard_source(client, monkeypatch):
    """Endpoint should emit exactly one polymarket_whale_analyzed
    audit_event per call with source='dashboard'."""
    client_, deps, logger, wallet = client
    monkeypatch.setattr(
        "trading_corp.agents.polymarket_whale_analyst.WhaleAnalyst", _FakeAnalystOK,
    )
    r = client_.post(f"/api/polymarket/watchlist/analyze/{wallet}")
    assert r.status_code == 200
    # Find the analyzed event
    matching = [
        (actor, kind, payload) for (actor, kind, payload) in logger.events
        if kind == "polymarket_whale_analyzed"
    ]
    assert len(matching) == 1, (
        f"expected 1 analyzed event, got {len(matching)}: "
        f"{[k for _, k, _ in logger.events]}"
    )
    actor, kind, payload = matching[0]
    assert actor == "polymarket_copy_trader"
    assert payload["source"] == "dashboard"
    assert payload["wallet"] == wallet.lower()
    assert payload["verdict_emitted"] is True
    assert payload["llm_cost_usd"] > 0
    assert payload["llm_tokens_in"] == 250
    assert payload["llm_tokens_out"] == 40
    assert payload["cache_hit"] is False
    assert "duration_ms" in payload


def test_no_promotion_slot_written_by_endpoint(client, monkeypatch):
    """CRITICAL invariant: the analyze endpoint MUST NOT write to any
    promotion-relevant slot. Asserted directly via load_agent_state
    before and after the call."""
    client_, deps, logger, wallet = client
    monkeypatch.setattr(
        "trading_corp.agents.polymarket_whale_analyst.WhaleAnalyst", _FakeAnalystOK,
    )
    # Capture baseline state of every protected slot
    protected_slots = (
        ("polymarket_copy_trader", "watch_only_whales"),
        ("polymarket_copy_trader", "selected_whales"),
        ("polymarket_copy_trader", "pinned_whales"),
        ("polymarket_copy_trader", "metrics_epoch"),
    )
    pre = {
        (agent, key): db.load_agent_state(agent, key, db_url=deps.db_url)
        for agent, key in protected_slots
    }
    # Run the endpoint
    r = client_.post(f"/api/polymarket/watchlist/analyze/{wallet}")
    assert r.status_code == 200
    # Re-read every protected slot — must be byte-identical to baseline
    post = {
        (agent, key): db.load_agent_state(agent, key, db_url=deps.db_url)
        for agent, key in protected_slots
    }
    for slot, pre_value in pre.items():
        post_value = post[slot]
        assert post_value == pre_value, (
            f"protected slot {slot} was mutated by analyze endpoint: "
            f"pre={pre_value!r} post={post_value!r}"
        )
    # Conversely, the AUDIT NAMESPACE must have been written (sanity:
    # confirms the cache layer is actually doing its job)
    audit_slot = db.load_agent_state(
        AGENT_NAMESPACE,
        cache_key(wallet, 1_700_000_000),  # most-recent ts in canned feed
        db_url=deps.db_url,
    )
    assert audit_slot is not None, "audit cache namespace should be written"


def test_invalid_wallet_returns_error_fragment_not_500(client):
    """Bad wallet shape should produce a render-able error, not a 500."""
    client_, _, _, _ = client
    r = client_.post("/api/polymarket/watchlist/analyze/not_a_wallet")
    assert r.status_code == 200  # render-able error, not HTTP error
    assert "invalid wallet format" in r.text


def test_force_evicts_cache_and_recomputes(client, monkeypatch):
    """?force=1 should bypass the cache."""
    client_, deps, logger, wallet = client

    # Seed cache
    pre_cached = WhaleAuditReport(
        proxy_wallet=wallet.lower(), user_name="stale-cache",
        activity_max_ts=1_700_000_000, activity_min_ts=1_700_000_000 - 14400,
        n_raw_rows_examined=5, n_resolved_decisions=5,
        clustering=ClusteringReport(5, 5, 1.0, 0, ()),
        sell_footprint=SellFootprintReport(5, 0, 0, 0, 0.20, 5, ()),
        edge=EdgeProfileReport(5, 0.5, 1.0, 0.0, 0.5, 0.5, 0.5),
        category=CategoryConcentrationReport(5, (("event-0", 1),), 0.2),
        realized_pnl=RealizedPnLReport(100.0, 100.0, 0.0, 0.0, 100.0, 0.0),
        partial_sell_threshold_used=0.20,
        verdict_narration="STALE — should be replaced",
        verdict_null_reason=None,
        llm_cost_usd=0.001, llm_tokens_in=100, llm_tokens_out=20,
    )
    write_audit(pre_cached, db_url=deps.db_url)

    monkeypatch.setattr(
        "trading_corp.agents.polymarket_whale_analyst.WhaleAnalyst", _FakeAnalystOK,
    )
    r = client_.post(f"/api/polymarket/watchlist/analyze/{wallet}?force=1")
    assert r.status_code == 200
    # The stale verdict must NOT appear — force should have evicted it
    assert "STALE" not in r.text
    # The fresh verdict from _FakeAnalystOK should appear
    assert "clean held positions" in r.text
