"""Tests for the kalshi_weather_arb sizing + ensemble/nowcast pipeline.

Network-free. Exercises:
  - `kelly_fraction` math at edge cases (no edge, full conviction, boundary)
  - `_SpendCounter` accumulator semantics
  - `_compute_kelly_usd` clamping ladder (per-market < per-day < per-city < min)
  - `_query_today_spend` reads + categorises audit history correctly
  - Open-Meteo ensemble σ derivation (mean/std on a stub payload)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from trading_corp.agents.strategies import kalshi_weather_arb as kw
from trading_corp.agents.strategies._weather_math import kelly_fraction
from trading_corp.data.open_meteo_client import EnsembleObservation
from trading_corp.persistence import db as _db


# ── kelly_fraction math ────────────────────────────────────────────────


def test_kelly_no_edge_returns_zero():
    # Model says 50% on a 50¢ market → no edge → full Kelly = 0
    assert kelly_fraction(0.50, 0.50) == 0.0


def test_kelly_full_conviction_clamps_to_one():
    # Model says 100% — full Kelly = 1 (bet entire bankroll)
    assert kelly_fraction(1.0, 0.20) == 1.0


def test_kelly_wrong_side_returns_zero():
    # Model says 30% on a 50¢ market — wrong side, full Kelly is negative
    assert kelly_fraction(0.30, 0.50) == 0.0


def test_kelly_typical_edge():
    # p=0.6 on a 50¢ market: b=1, f* = (0.6*1 - 0.4)/1 = 0.2
    assert kelly_fraction(0.60, 0.50) == pytest.approx(0.20)


def test_kelly_undefined_prices():
    assert kelly_fraction(0.6, 0.0) == 0.0
    assert kelly_fraction(0.6, 1.0) == 0.0
    assert kelly_fraction(0.0, 0.5) == 0.0


# ── _SpendCounter ──────────────────────────────────────────────────────


def test_spend_counter_accumulates():
    c = kw._SpendCounter(total_usd=0.0, per_city_usd={})
    c.add(city="NYC", usd=5.0)
    c.add(city="CHI", usd=3.0)
    c.add(city="NYC", usd=2.0)
    assert c.total_usd == pytest.approx(10.0)
    assert c.per_city_usd == {"NYC": 7.0, "CHI": 3.0}


def test_spend_counter_seeded():
    # Seeded from audit history — successive adds compound on top
    c = kw._SpendCounter(total_usd=20.0, per_city_usd={"NYC": 12.0, "LAX": 8.0})
    c.add(city="NYC", usd=3.0)
    assert c.total_usd == pytest.approx(23.0)
    assert c.per_city_usd["NYC"] == pytest.approx(15.0)
    assert c.per_city_usd["LAX"] == pytest.approx(8.0)


# ── _compute_kelly_usd: clamping ladder ────────────────────────────────


def _agent_with_sizing(tmp_path, sizing: dict, **extra) -> kw.KalshiWeatherArbAgent:
    """Build an agent with a sizing block stubbed directly into
    `_strat_cfg`, bypassing strategies.yaml so tests stay hermetic."""
    a = kw.KalshiWeatherArbAgent(db_url=None)
    cfg = {"sizing": sizing}
    cfg.update(extra)
    a._strat_cfg = cfg
    return a


def test_kelly_per_market_cap_dominates(tmp_path):
    # Bankroll $500, kelly_fraction=0.25, full kelly=20% → kelly_target=$25
    # per_market cap = 5% × $500 = $25 → no shrink
    # If kelly were 80%, target = $100 → per_market caps at $25
    a = _agent_with_sizing(tmp_path, {
        "mode": "kelly_fractional",
        "kelly_fraction": 0.25,
        "min_usd": 1.0,
        "max_per_market_pct": 5.0,
        "max_per_day_pct": 25.0,
        "max_per_city_pct": 15.0,
    })
    spend = kw._SpendCounter(total_usd=0.0, per_city_usd={})
    # p=0.8 on 50¢ market: b=1, full = (0.8 - 0.2)/1 = 0.6
    # kelly_target = 500 * 0.25 * 0.6 = 75; clamped by per_market $25
    usd, meta = a._compute_kelly_usd(
        prob_outcome=0.8, share_price=0.5, account_equity=500.0,
        city_code="NYC", spend=spend,
    )
    assert usd == pytest.approx(25.0)
    assert meta["applied_cap"] == "per_market"
    assert meta["kelly_full_pct"] == pytest.approx(60.0)
    assert meta["kelly_fraction_used"] == 0.25


def test_kelly_per_day_cap_dominates(tmp_path):
    # Bankroll $500. Day cap = 25% × $500 = $125. Already spent $120 today.
    # Remaining = $5. Kelly target large → clamps to $5.
    a = _agent_with_sizing(tmp_path, {
        "mode": "kelly_fractional",
        "kelly_fraction": 0.25,
        "min_usd": 1.0,
        "max_per_market_pct": 5.0,
        "max_per_day_pct": 25.0,
        "max_per_city_pct": 15.0,
    })
    spend = kw._SpendCounter(total_usd=120.0, per_city_usd={"CHI": 120.0})
    usd, meta = a._compute_kelly_usd(
        prob_outcome=0.8, share_price=0.5, account_equity=500.0,
        city_code="NYC", spend=spend,
    )
    assert usd == pytest.approx(5.0)
    assert meta["applied_cap"] == "per_day"


def test_kelly_per_city_cap_dominates(tmp_path):
    # City cap = 15% × $500 = $75. NYC already spent $73 today. Remaining $2.
    # Per-day budget still has headroom, per-market is $25 — city caps.
    a = _agent_with_sizing(tmp_path, {
        "mode": "kelly_fractional",
        "kelly_fraction": 0.25,
        "min_usd": 1.0,
        "max_per_market_pct": 5.0,
        "max_per_day_pct": 25.0,
        "max_per_city_pct": 15.0,
    })
    spend = kw._SpendCounter(total_usd=73.0, per_city_usd={"NYC": 73.0})
    usd, meta = a._compute_kelly_usd(
        prob_outcome=0.8, share_price=0.5, account_equity=500.0,
        city_code="NYC", spend=spend,
    )
    assert usd == pytest.approx(2.0)
    assert meta["applied_cap"] == "per_city"


def test_kelly_min_usd_floor_zeros_size(tmp_path):
    # Edge tiny → kelly_target tiny → below $1 floor → returned size = 0
    a = _agent_with_sizing(tmp_path, {
        "mode": "kelly_fractional",
        "kelly_fraction": 0.25,
        "min_usd": 1.0,
        "max_per_market_pct": 5.0,
        "max_per_day_pct": 25.0,
        "max_per_city_pct": 15.0,
    })
    spend = kw._SpendCounter(total_usd=0.0, per_city_usd={})
    # p=0.52 on 0.50 market: b=1, full = (0.52 - 0.48)/1 = 0.04
    # kelly_target = 500 * 0.25 * 0.04 = $5 → above floor.
    # Force below floor with tiny equity:
    usd, meta = a._compute_kelly_usd(
        prob_outcome=0.52, share_price=0.50, account_equity=50.0,
        city_code="NYC", spend=spend,
    )
    # kelly_target = 50 * 0.25 * 0.04 = 0.50; below $1 → zero
    assert usd == 0.0
    assert "below min_usd" in meta["cap_reason"]


def test_fixed_usd_mode_still_supported(tmp_path):
    a = _agent_with_sizing(tmp_path, {
        "mode": "fixed_usd",
        "fixed_amount": 1.0,
    })
    spend = kw._SpendCounter(total_usd=0.0, per_city_usd={})
    usd, meta = a._compute_kelly_usd(
        prob_outcome=0.8, share_price=0.5, account_equity=500.0,
        city_code="NYC", spend=spend,
    )
    assert usd == pytest.approx(1.0)
    assert meta["applied_cap"] == "fixed_usd"


# ── _query_today_spend reads audit history ─────────────────────────────


def test_query_today_spend_sums_by_city(tmp_path):
    db_path = tmp_path / "weather_spend_test.db"
    db_url = f"sqlite:///{db_path}"
    _db.init_db(db_url)

    now = datetime.now(timezone.utc)

    def _insert(payload: dict, ts: str):
        with _db.connect(db_url) as conn:
            conn.execute(
                "INSERT INTO audit_event(ts, actor, kind, payload_json) "
                "VALUES(?, ?, ?, ?)",
                (ts, "kalshi_weather_arb", "would_have_placed", json.dumps(payload)),
            )

    today_ts = now.replace(hour=12).isoformat()
    yesterday_ts = (now.replace(hour=12) - _td(days=1)).isoformat()

    # Today, NYC, $10 ($2 × 5 shares)
    _insert(
        {"ticker": "KXHIGHNYC-26MAY15-T80", "qty": 5.0, "limit_price": 2.0},
        today_ts,
    )
    # Today, CHI, $6
    _insert(
        {"ticker": "KXHIGHCHI-26MAY15-T70", "qty": 3.0, "limit_price": 2.0},
        today_ts,
    )
    # Today, NYC again, $4
    _insert(
        {"ticker": "KXLOWNYC-26MAY15-T40", "qty": 2.0, "limit_price": 2.0},
        today_ts,
    )
    # Yesterday — must NOT count
    _insert(
        {"ticker": "KXHIGHLAX-26MAY14-T80", "qty": 100.0, "limit_price": 1.0},
        yesterday_ts,
    )
    # Today but wrong actor — must NOT count
    with _db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event(ts, actor, kind, payload_json) VALUES(?, ?, ?, ?)",
            (today_ts, "kalshi_crypto_arb", "would_have_placed",
             json.dumps({"ticker": "KXBTC-26MAY15-T70000", "qty": 10.0,
                         "limit_price": 0.5})),
        )

    a = kw.KalshiWeatherArbAgent(db_url=db_url)
    total, per_city = a._query_today_spend(now=now)
    assert total == pytest.approx(20.0)
    assert per_city == {"NYC": pytest.approx(14.0), "CHI": pytest.approx(6.0)}


# ── ensemble σ math on a hand-built EnsembleObservation ────────────────


def test_ensemble_observation_std_with_three_members():
    obs = EnsembleObservation(
        target_iso="2026-05-15T18:00:00+00:00",
        members=[70.0, 72.0, 74.0],
        models=["gfs_global", "icon_global", "ecmwf_ifs04"],
    )
    assert obs.n_members == 3
    assert obs.mean_f == pytest.approx(72.0)
    # std of [70, 72, 74] = 2.0 (sample std)
    assert obs.std_f == pytest.approx(2.0)


def test_ensemble_observation_std_single_member_returns_zero():
    obs = EnsembleObservation(
        target_iso="2026-05-15T18:00:00+00:00",
        members=[70.0], models=["gfs_global"],
    )
    assert obs.std_f == 0.0


# ── helpers ────────────────────────────────────────────────────────────


def _td(**kwargs):
    from datetime import timedelta
    return timedelta(**kwargs)
