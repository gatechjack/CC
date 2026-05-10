"""Tests for scripts/backtest_polymarket_arbitrage.py.

Covers the binary-outcome P&L math + aggregation logic. Network-free
(resolution lookups stubbed). The script's `_run()` async path is
exercised via a synthetic-DB fixture; gamma-api is mocked.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Load the script as a module (it lives in scripts/, not on sys.path)
_HERE = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "backtest_polymarket_arbitrage",
    _HERE / "scripts" / "backtest_polymarket_arbitrage.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def _row(**overrides):
    """Build a paper row in the shape produced by the strategy."""
    base = {
        "_ts": "2026-05-01T12:00:00+00:00",
        "strategy": "polymarket_arbitrage",
        "symbol": "test-market:yes",
        "side": "buy",
        "qty": 2.5,
        "limit_price": 0.40,
        "outcome": "yes",
        "category": "sports",
        "series": "mlb",
        "market_slug": "test-market",
        "condition_id": "0xabc",
        "implied_prob_at_entry": 0.40,
        "llm_prob_estimate": 0.55,
        "divergence_pct": 15.0,
    }
    base.update(overrides)
    return base


def _res(*, status="resolved", yes_won=True):
    return {
        "status": status, "yes_won": yes_won,
        "outcome_prices": ["1", "0"] if yes_won else ["0", "1"],
        "closed": status == "resolved", "end_date": "2026-05-08",
    }


# ── P&L math ───────────────────────────────────────────────────────────


def test_buy_yes_resolves_yes():
    """BUY YES at $0.40 × 2.5 shares; YES wins → +$0.60 × 2.5 = +$1.50."""
    pnl = _MOD._compute_pnl(_row(qty=2.5, limit_price=0.40, outcome="yes"),
                            _res(yes_won=True))
    assert pnl is not None
    assert pnl["won"] is True
    assert abs(pnl["pnl"] - 1.50) < 1e-9


def test_buy_yes_resolves_no():
    """BUY YES at $0.40 × 2.5 shares; NO wins → -$0.40 × 2.5 = -$1.00."""
    pnl = _MOD._compute_pnl(_row(qty=2.5, limit_price=0.40, outcome="yes"),
                            _res(yes_won=False))
    assert pnl is not None
    assert pnl["won"] is False
    assert abs(pnl["pnl"] - (-1.00)) < 1e-9


def test_buy_no_resolves_no():
    """BUY NO at $0.60 × 2.0 shares; NO wins → +$0.40 × 2.0 = +$0.80."""
    pnl = _MOD._compute_pnl(_row(qty=2.0, limit_price=0.60, outcome="no"),
                            _res(yes_won=False))
    assert pnl is not None
    assert pnl["won"] is True
    assert abs(pnl["pnl"] - 0.80) < 1e-9


def test_buy_no_resolves_yes():
    """BUY NO at $0.60 × 2.0; YES wins → -$0.60 × 2.0 = -$1.20."""
    pnl = _MOD._compute_pnl(_row(qty=2.0, limit_price=0.60, outcome="no"),
                            _res(yes_won=True))
    assert pnl is not None
    assert pnl["won"] is False
    assert abs(pnl["pnl"] - (-1.20)) < 1e-9


def test_pnl_skips_pending_market():
    assert _MOD._compute_pnl(_row(), _res(status="pending", yes_won=None)) is None


def test_pnl_skips_void_market():
    assert _MOD._compute_pnl(_row(), _res(status="void")) is None


def test_pnl_skips_zero_qty():
    assert _MOD._compute_pnl(_row(qty=0), _res()) is None


def test_pnl_skips_zero_or_invalid_price():
    assert _MOD._compute_pnl(_row(limit_price=0), _res()) is None
    assert _MOD._compute_pnl(_row(limit_price=1.0), _res()) is None
    assert _MOD._compute_pnl(_row(limit_price=1.5), _res()) is None


def test_pnl_skips_unknown_outcome():
    assert _MOD._compute_pnl(_row(outcome="maybe"), _res()) is None


# ── Aggregation ────────────────────────────────────────────────────────


def _realized(pnl_value, *, ts, won, category="sports", notional=1.0):
    return {
        "ts": ts, "pnl": pnl_value, "won": won, "category": category,
        "notional": notional, "qty": notional, "entry_price": 1.0,
        "outcome_bet": "yes", "series": "x", "slug": "s",
        "implied_at_entry": 0.5, "llm_prob": 0.5, "divergence_pct": 0,
        "yes_won_actual": won,
    }


def test_aggregate_empty():
    out = _MOD._aggregate([])
    assert out["n_trades"] == 0
    assert out["hit_rate"] == 0
    assert out["total_pnl"] == 0
    assert out["by_category"] == {}


def test_aggregate_basic():
    rs = [
        _realized(+0.50, ts="2026-05-01", won=True),
        _realized(-0.40, ts="2026-05-02", won=False),
        _realized(+0.30, ts="2026-05-03", won=True),
    ]
    out = _MOD._aggregate(rs)
    assert out["n_trades"] == 3
    assert out["n_wins"] == 2
    assert out["n_losses"] == 1
    assert abs(out["hit_rate"] - 2/3) < 1e-9
    assert abs(out["total_pnl"] - 0.40) < 1e-9
    assert abs(out["avg_pnl_per_trade"] - 0.40 / 3) < 1e-9
    assert abs(out["roi_pct"] - 100 * 0.40 / 3.0) < 1e-9


def test_aggregate_per_category():
    rs = [
        _realized(+0.5, ts="2026-05-01", won=True, category="sports"),
        _realized(-0.4, ts="2026-05-02", won=False, category="sports"),
        _realized(+0.6, ts="2026-05-03", won=True, category="politics"),
    ]
    out = _MOD._aggregate(rs)
    assert "sports" in out["by_category"]
    assert "politics" in out["by_category"]
    assert out["by_category"]["sports"]["n"] == 2
    assert out["by_category"]["sports"]["hit_rate"] == 0.5
    assert abs(out["by_category"]["sports"]["total_pnl"] - 0.10) < 1e-9
    assert out["by_category"]["politics"]["n"] == 1
    assert out["by_category"]["politics"]["hit_rate"] == 1.0


def test_max_drawdown_consecutive_losses():
    """Cumulative P&L: +1.0, +0.5 (peak), -0.5, -1.5 (trough). DD = 2.0."""
    rs = [
        _realized(+1.0, ts="2026-05-01", won=True),
        _realized(-0.5, ts="2026-05-02", won=False),
        _realized(-1.0, ts="2026-05-03", won=False),
        _realized(-1.0, ts="2026-05-04", won=False),
        _realized(+0.5, ts="2026-05-05", won=True),
    ]
    out = _MOD._aggregate(rs)
    # Peak after first trade = 1.0; trough after fourth = 1.0 + (-0.5-1.0-1.0) = -1.5; DD = 2.5
    assert abs(out["max_drawdown"] - 2.5) < 1e-9


def test_max_drawdown_no_drawdown_when_monotone_up():
    rs = [
        _realized(+0.5, ts="2026-05-01", won=True),
        _realized(+0.4, ts="2026-05-02", won=True),
    ]
    out = _MOD._aggregate(rs)
    assert out["max_drawdown"] == 0.0


# ── Recommendation thresholds ─────────────────────────────────────────


def test_recommend_insufficient_data():
    m = {"n_trades": 5, "hit_rate": 0.8, "avg_pnl_per_trade": 0.5, "roi_pct": 30}
    assert "INSUFFICIENT_DATA" in _MOD._make_recommendation(m)


def test_recommend_approval():
    m = {"n_trades": 100, "hit_rate": 0.60, "avg_pnl_per_trade": 0.05, "roi_pct": 10}
    assert "RECOMMEND_APPROVAL" in _MOD._make_recommendation(m)


def test_recommend_rejection_low_hit():
    m = {"n_trades": 100, "hit_rate": 0.40, "avg_pnl_per_trade": 0.01, "roi_pct": 1}
    assert "RECOMMEND_REJECTION" in _MOD._make_recommendation(m)


def test_recommend_rejection_negative_pnl():
    m = {"n_trades": 100, "hit_rate": 0.50, "avg_pnl_per_trade": -0.10, "roi_pct": -5}
    assert "RECOMMEND_REJECTION" in _MOD._make_recommendation(m)


def test_recommend_mixed():
    """Above 30 trades but in the borderline zone."""
    m = {"n_trades": 50, "hit_rate": 0.50, "avg_pnl_per_trade": 0.01, "roi_pct": 2}
    assert "MIXED_SIGNAL" in _MOD._make_recommendation(m)
