"""PMCC pricing fix (2026-08-04) — three coupled changes from the root-cause
investigation of the uniform "can't be priced right now" failure:

  FIX 1  `_passes_liquidity` no longer applies a standalone `vol < min_avg_volume`
         (50) floor. It was subsumed by Liveness (OI>=100 OR vol>=500) and only ever
         wrongly rejected OI-established strikes with thin intraday prints — firing on
         EVERY held name off-hours (vol=0) and at the open.
  FIX 2  the MANUAL build paths (Re-analyze + refresh-pricing) short-circuit to a
         specific "market closed" state when `market_regular_open()` is false — never
         building a priced Approve off stale overnight quotes.
  FIX 3  the empty-orders path surfaces the SPECIFIC abort reason (from the
         `_last_roll_abort` stash `_audit_roll_abort` sets even in preview) instead of
         the conflated "market closed, illiquid, or a sparse chain" fallback.

No real API/LLM calls.
"""
from __future__ import annotations

import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_corp.agents.divisions.pmcc_robinhood import (
    PMCCAgent,
    _select_weekly_strike,
)


# ---------------------------------------------------------------------------
# Fixtures — a real PMCCAgent with the prod liquidity DEFAULTS
# (min_open_interest=100, oi_bypass_min_volume=500, max_bid_ask_spread_pct=0.10).
# ---------------------------------------------------------------------------

@pytest.fixture
def _pmcc_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "strategies.yaml"
    p.write_text(
        "robinhood_pmcc:\n"
        "  enabled: true\n"
        "  auto_execute: false\n"
        "  universe_source: positions\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def _risk_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "risk.yaml"
    p.write_text(
        "global:\n"
        "  per_trade_risk_pct: 0.015\n"
        "pmcc:\n"
        "  short_call_target_delta: 0.30\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def agent(_pmcc_yaml: Path, _risk_yaml: Path) -> PMCCAgent:
    return PMCCAgent(strategies_yaml=_pmcc_yaml, risk_yaml=_risk_yaml)


def _opt(*, bid, ask, oi, vol, strike=340.0, delta=0.33) -> dict:
    return {
        "bid": bid, "ask": ask, "open_interest": oi, "volume": vol,
        "strike_price": strike, "delta": delta, "mark_price": (bid + ask) / 2,
        "expiration_date": "2026-08-12", "dte": 8,
        "option_id": f"opt_{strike}",
    }


# ===========================================================================
# FIX 1 — _passes_liquidity: the volume floor is gone; liveness + spread remain.
# ===========================================================================

def test_opening_rotation_high_oi_low_vol_now_passes(agent: PMCCAgent):
    """THE FIX: the live TSLA δ0.32 target ($340, OI 209) with ZERO intraday volume
    — the pre-market / opening-rotation case that used to fail `vol < 50` — now
    passes. Established by OI; needs no intraday prints."""
    ok, reason = agent._passes_liquidity(_opt(bid=4.90, ask=5.05, oi=209, vol=0))
    assert ok, reason
    # And a mid-rotation strike with 40 lots (< the old 50 floor) also passes now.
    ok2, _ = agent._passes_liquidity(_opt(bid=4.90, ask=5.05, oi=209, vol=40))
    assert ok2


def test_regression_high_oi_high_vol_still_passes(agent: PMCCAgent):
    """The in-RTH pick (OI 209, vol 910, tight spread) still passes — no regression."""
    ok, reason = agent._passes_liquidity(_opt(bid=4.90, ask=5.05, oi=209, vol=910))
    assert ok, reason


def test_thin_strike_still_fails_liveness_not_loosened(agent: PMCCAgent):
    """A genuinely thin strike (OI<100 AND vol<500 — the live $345) still fails the
    LIVENESS gate. Liveness was NOT loosened by removing the volume floor."""
    ok, reason = agent._passes_liquidity(_opt(bid=3.70, ask=3.80, oi=73, vol=454))
    assert not ok
    assert "OI=" in reason and "AND" in reason        # liveness bound, not the floor
    # A phantom / untraded strike (OI 0, vol 0) still fails liveness.
    ok0, reason0 = agent._passes_liquidity(_opt(bid=0.05, ask=0.10, oi=0, vol=0))
    assert not ok0 and "OI=" in reason0


def test_wide_spread_still_fails(agent: PMCCAgent):
    """Spread is the remaining per-contract backstop: OI-established but a 40%
    spread still fails (stale/untradeable)."""
    ok, reason = agent._passes_liquidity(_opt(bid=1.00, ask=1.50, oi=500, vol=1000))
    assert not ok
    assert "spread=" in reason


def test_no_ask_still_fails(agent: PMCCAgent):
    """No ask (can't buy back / untradeable) still fails even when liquid by OI."""
    ok, reason = agent._passes_liquidity(_opt(bid=1.00, ask=0.0, oi=500, vol=1000))
    assert not ok
    assert "no ask" in reason


def test_oi_bypass_fresh_strike_preserved(agent: PMCCAgent):
    """A FRESH near-dated strike (low OI 5) qualifies purely on volume>=500 — the
    oi_bypass path is preserved, and with the floor gone nothing downstream trips it."""
    ok, reason = agent._passes_liquidity(_opt(bid=4.90, ask=5.05, oi=5, vol=800))
    assert ok, reason


def test_filter_liquid_keeps_established_low_vol_and_picker_selects_it(agent: PMCCAgent):
    """End-to-end selection: the OI-established δ0.32 low-volume strike now SURVIVES
    `_filter_liquid` and is the one `_select_weekly_strike` picks for target δ0.30.
    Before the fix it was dropped (vol<50) and the picker would have chosen the
    further-out $350 — the wrong strike."""
    chain = [
        _opt(bid=4.90, ask=5.05, oi=209, vol=0, strike=340.0, delta=0.33),   # target, vol=0
        _opt(bid=3.70, ask=3.80, oi=73, vol=454, strike=345.0, delta=0.27),  # thin → liveness fail
        _opt(bid=2.77, ask=2.82, oi=452, vol=2478, strike=350.0, delta=0.21),  # liquid, further OTM
    ]
    liquid = agent._filter_liquid(chain, "TSLA")
    strikes = {o["strike_price"] for o in liquid}
    assert 340.0 in strikes            # THE FIX — established low-vol strike survives
    assert 345.0 not in strikes        # thin strike still dropped (liveness)
    assert 350.0 in strikes
    best = _select_weekly_strike(liquid, 0.30)
    assert best is not None
    assert best["strike_price"] == 340.0   # closest to δ0.30 → the correct pick


# ===========================================================================
# FIX 3 — the abort reason is stashed (even in preview) and surfaced specifically.
# ===========================================================================

def test_audit_roll_abort_preview_persists_reason_but_writes_no_row(agent: PMCCAgent):
    """Preview abort: NO audit row (invariant preserved) but `_last_roll_abort` IS
    stashed so the render can explain WHY — the forensic gap the investigation hit."""
    rows: list = []
    agent._audit_division = lambda kind, payload: rows.append((kind, payload))  # type: ignore
    agent._audit_roll_abort(
        reason="sparse_chain_no_weekly", symbol="TSLA", missing_leg="new_short",
        diag={"reason": "no_liquid_weekly_contracts", "considered": 8, "liquid": 0,
              "failed_by_gate": {"spread": 8}},
        preview=True,
    )
    assert rows == []                                        # preview → no audit row
    assert agent._last_roll_abort is not None                # …but the reason persists
    assert agent._last_roll_abort["reason"] == "sparse_chain_no_weekly"


def test_last_roll_abort_reason_sparse_liquidity_symbol_scoped(agent: PMCCAgent):
    agent._last_roll_abort = {
        "reason": "sparse_chain_no_weekly", "symbol": "TSLA",
        "chain_state": {"reason": "no_liquid_weekly_contracts", "considered": 8,
                        "failed_by_gate": {"spread": 8}},
    }
    why = agent.last_roll_abort_reason("TSLA")
    assert why is not None
    assert "candidate" in why and "liquidity gate" in why
    assert "8" in why
    # Symbol-scoped: a stale abort from another name never leaks into this card.
    assert agent.last_roll_abort_reason("HOOD") is None


def test_last_roll_abort_reason_distinguishes_cases(agent: PMCCAgent):
    # empty chain
    agent._last_roll_abort = {"reason": "sparse_chain_no_weekly", "symbol": "T",
                              "chain_state": {"reason": "no_future_expiry_dates"}}
    assert "chain empty" in agent.last_roll_abort_reason("T")
    # no roll-out expiry
    agent._last_roll_abort = {"reason": "sparse_chain_no_weekly", "symbol": "T",
                              "chain_state": {"reason": "no_rollout_weekly"}}
    assert "rolls out" in agent.last_roll_abort_reason("T")
    # earnings proceed-gate (not a sparse chain)
    agent._last_roll_abort = {"reason": "earnings_window", "symbol": "T",
                              "chain_state": {}}
    assert "earnings" in agent.last_roll_abort_reason("T")
    # net-debit credit gate
    agent._last_roll_abort = {"reason": "net_debit_roll", "symbol": "T",
                              "chain_state": {}}
    assert "debit" in agent.last_roll_abort_reason("T")


def test_last_roll_abort_reason_none_when_no_abort(agent: PMCCAgent):
    agent._last_roll_abort = None
    assert agent.last_roll_abort_reason("TSLA") is None


def test_market_closed_extras_shape():
    from trading_corp.web import pmcc_pricing
    extras = pmcc_pricing.market_closed_extras()
    assert extras["estimate"] is None            # no build → no stale-quote pricing
    assert extras["earnings"] is None
    assert "market closed" in extras["estimate_reason"]
    assert "9:30" in extras["estimate_reason"]


# ===========================================================================
# FIX 2 (+ FIX 3) — route-level: manual paths short-circuit when market closed,
# and surface a specific reason when the market is open but the build is empty.
# ===========================================================================

from fastapi.testclient import TestClient          # noqa: E402
from trading_corp.persistence import db            # noqa: E402
from trading_corp.web.app import WebDeps, create_app  # noqa: E402
from trading_corp.agents.divisions import _pmcc_status  # noqa: E402


class _Broker:
    paper = True


class _DataExec:
    def __init__(self):
        self.brokers = {"robinhood_pmcc": _Broker()}


class _Logger:
    def log_event(self, actor=None, kind=None, payload=None):
        pass


class _PMCC:
    """Stub PMCC agent for the route tests. Counts build calls so a test can prove
    NO build happened, and exposes a canned abort reason for the open-empty case."""

    def __init__(self, abort_reason=None):
        self._cfg = {"tile_status": {}}
        self.analyze_calls = 0
        self.propose_calls = 0
        self._abort_reason = abort_reason

    async def analyze_symbol(self, broker, sym, regime="unknown"):
        self.analyze_calls += 1
        return types.SimpleNamespace(
            action="roll_short_early", urgency="attention", confidence=0.8,
            summary="short is deep ITM", rationale="roll it", warnings=[],
            target_delta=0.32, target_dte=7,
            target_delta_low=0.28, target_delta_high=0.36,
        )

    async def propose_orders_for_pair(self, broker, sym, analysis, *, preview=False):
        self.propose_calls += 1
        return []          # empty build → a gate aborted

    def last_roll_abort_reason(self, symbol=None):
        return self._abort_reason


def _client(pmcc, tmp_db):
    db.init_db(tmp_db)
    deps = WebDeps(
        db_url=tmp_db, db_path=tmp_db.replace("sqlite:///", ""), mode="PAPER",
        logger_agent=_Logger(), data_exec=_DataExec(),
        trend_agent=types.SimpleNamespace(
            read=lambda: types.SimpleNamespace(regime="neutral")),
        portfolio=None, pmcc_agent=pmcc, fidelity_agent=None, paper_broker=None,
        secrets=None, risk_agent=None,
    )
    return TestClient(create_app(deps)), deps


def _store_decision(tmp_db, sym="TSLA"):
    _pmcc_status.record_pmcc_decision(
        sym, status="roll_short_early", source="expert",
        computed_at=datetime.now(timezone.utc).isoformat(), db_url=tmp_db,
        urgency="attention", confidence=0.8, summary="s", rationale="r",
        warnings=[], target_delta_low=0.28, target_delta_high=0.36, target_dte=7,
    )


def test_reanalyze_market_closed_short_circuits_no_build(tmp_db, monkeypatch):
    """FIX 2: market closed → Re-analyze shows the market-closed state and NEVER calls
    propose_orders_for_pair (no build off stale quotes, no priced Approve)."""
    from trading_corp.web import pmcc_pricing
    monkeypatch.setattr(pmcc_pricing, "market_regular_open", lambda *a, **k: False)
    pmcc = _PMCC()
    client, _ = _client(pmcc, tmp_db)
    r = client.get("/division/robinhood_pmcc/pair-analysis/TSLA", params={"force": "1"})
    assert r.status_code == 200
    assert pmcc.analyze_calls == 1                 # judgment still refreshed
    assert pmcc.propose_calls == 0                 # …but NO build attempted
    assert "market closed" in r.text and "9:30" in r.text
    assert "Approve &amp; Execute" not in r.text   # no priced Approve on stale data


def test_refresh_pricing_market_closed_short_circuits_no_price(tmp_db, monkeypatch):
    """FIX 2: market closed → refresh-pricing shows the market-closed state and NEVER
    calls price_and_stash (no stale-quote pull)."""
    from trading_corp.web import pmcc_pricing

    def _boom(*a, **k):
        raise AssertionError("price_and_stash must NOT be called when market is closed")

    monkeypatch.setattr(pmcc_pricing, "market_regular_open", lambda *a, **k: False)
    monkeypatch.setattr(pmcc_pricing, "price_and_stash", _boom)
    pmcc = _PMCC()
    client, _ = _client(pmcc, tmp_db)   # inits the DB (agent_state table) first…
    _store_decision(tmp_db)             # …then store the decision to price against
    r = client.post("/division/robinhood_pmcc/pair/TSLA/refresh-pricing")
    assert r.status_code == 200
    assert "market closed" in r.text and "9:30" in r.text
    assert "Approve &amp; Execute" not in r.text


def test_reanalyze_open_empty_build_shows_specific_reason(tmp_db, monkeypatch):
    """FIX 3: market OPEN but the build comes back empty → the panel shows the
    SPECIFIC abort reason, not the conflated fallback, and offers no priced Approve."""
    from trading_corp.web import pmcc_pricing
    monkeypatch.setattr(pmcc_pricing, "market_regular_open", lambda *a, **k: True)
    pmcc = _PMCC(abort_reason="8 candidate strike(s) fetched, all failed the liquidity gate")
    client, _ = _client(pmcc, tmp_db)
    r = client.get("/division/robinhood_pmcc/pair-analysis/TSLA", params={"force": "1"})
    assert r.status_code == 200
    assert pmcc.propose_calls == 1                 # build WAS attempted (market open)
    assert "candidate" in r.text and "liquidity gate" in r.text
    assert "market closed, illiquid, or a sparse chain" not in r.text  # not the fallback
    assert "Approve &amp; Execute" not in r.text
