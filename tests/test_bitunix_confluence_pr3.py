"""Tests for the PR 3a additions to the BitUnix confluence score engine.

Pin the new opt-in code paths added by PR 3a:
  - `score_timeframes` filter (alert.tf in allowed set)
  - `factor_ttl_per_tf` per-TF TTL override
  - `pa_factors_in_score=False` removes PA from the score
  - `guards_in_score=False` removes rush/fall penalties

All defaults preserve pre-PR-3 behavior — these tests prove that, then
prove that explicit opt-in changes the math correctly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from trading_corp.agents.strategies.btc_accumulator import (
    AlertEvent,
    FactorConfig,
    GuardBracket,
    GuardConfig,
    PriceContext,
)
from trading_corp.agents.strategies.bitunix_confluence import (
    BitUnixConfluenceConfig,
    Side,
    Tier,
    evaluate_confluence_futures,
    filter_live_alerts_with_dedupe,
)


# ─── helpers ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _AlertWithTf:
    """Lightweight AlertEvent variant carrying a `tf` attribute for the
    PR 3a TF-filter tests. The score engine reads tf via getattr so it
    duck-types over both the legacy AlertEvent (no tf) and this one."""
    ts: datetime
    signal_name: str
    tf: str | None = None


def _config(
    *,
    score_timeframes: tuple[str, ...] | None = None,
    factor_ttl_per_tf: dict | None = None,
    pa_factors_in_score: bool = True,
    guards_in_score: bool = True,
    factor_ttl: int = 60,
) -> BitUnixConfluenceConfig:
    """Minimal config: one buy factor, one sell factor, default
    thresholds, optional PR 3a knobs."""
    factors = {
        "otter_buy": FactorConfig(name="otter_buy", weight=3, side="buy",
                                  ttl_minutes=factor_ttl),
        "otter_sell": FactorConfig(name="otter_sell", weight=3, side="sell",
                                   ttl_minutes=factor_ttl),
        "above_session_vwap": FactorConfig(
            name="above_session_vwap", weight=1, side="buy", ttl_minutes=0,
        ),
        "below_session_vwap": FactorConfig(
            name="below_session_vwap", weight=1, side="sell", ttl_minutes=0,
        ),
    }
    return BitUnixConfluenceConfig(
        enabled=True,
        min_score_to_fire=2,
        premium_threshold=10,
        standard_threshold=5,
        weak_threshold=2,
        cooldown_seconds=1800,
        dedupe_within_ttl=True,
        factors=factors,
        sell_on_rush=GuardConfig(
            window_minutes=60,
            brackets=(GuardBracket(upto_pct=5.0, penalty=-3),),
        ),
        buy_on_fall=GuardConfig(
            window_minutes=60,
            brackets=(GuardBracket(upto_pct=5.0, penalty=-3),),
        ),
        score_timeframes=score_timeframes,
        factor_ttl_per_tf=factor_ttl_per_tf or {},
        pa_factors_in_score=pa_factors_in_score,
        guards_in_score=guards_in_score,
    )


def _empty_pctx(**overrides) -> PriceContext:
    base = dict(
        current_price=100.0,
        pct_change_in_window_sell=0.0,
        pct_change_in_window_buy=0.0,
        above_session_vwap=False,
        below_session_vwap=False,
        higher_highs_4h=False,
        lower_lows_4h=False,
        volume_above_20bar_avg=False,
    )
    base.update(overrides)
    return PriceContext(**base)


# ─── score_timeframes filter ────────────────────────────────────────────


def test_default_score_timeframes_none_allows_all_tfs():
    """Backwards compat: when score_timeframes is None (default), every
    alert is eligible regardless of tf."""
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    alerts = [
        _AlertWithTf(ts=now, signal_name="otter_buy", tf="3m"),
        _AlertWithTf(ts=now, signal_name="otter_buy", tf="1d"),
        AlertEvent(ts=now, signal_name="otter_buy"),         # no tf attr
    ]
    cfg = _config()      # score_timeframes=None
    live = filter_live_alerts_with_dedupe(alerts, cfg, now)
    # Dedupe collapses to 1, but the FILTER step should let all 3 through
    # before dedupe — verify dedupe by checking the survivor
    assert len(live) == 1
    assert live[0].signal_name == "otter_buy"


def test_score_timeframes_filter_drops_alerts_outside_set():
    """Alerts whose tf is not in the allowed set are dropped — they hit
    the ledger but don't contribute to score."""
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    alerts = [
        _AlertWithTf(ts=now, signal_name="otter_buy", tf="3m"),
        _AlertWithTf(ts=now - timedelta(minutes=1), signal_name="otter_sell", tf="1d"),
    ]
    cfg = _config(score_timeframes=("3m",))
    live = filter_live_alerts_with_dedupe(alerts, cfg, now)
    assert len(live) == 1
    assert live[0].signal_name == "otter_buy"
    # The 1d sell alert was dropped — score should reflect buy only
    verdict = evaluate_confluence_futures(
        live_alerts=live, price_ctx=_empty_pctx(),
        config=cfg, now=now,
    )
    assert verdict.side == Side.BUY


def test_score_timeframes_set_drops_legacy_alerts_without_tf():
    """When the filter IS set, alerts with no tf attribute can't be
    verified as belonging to an allowed TF → drop."""
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    alerts = [AlertEvent(ts=now, signal_name="otter_buy")]
    cfg = _config(score_timeframes=("3m",))
    live = filter_live_alerts_with_dedupe(alerts, cfg, now)
    assert live == []


# ─── per-TF TTL override ────────────────────────────────────────────────


def test_per_tf_ttl_override_wins_over_factor_ttl():
    """Per-TF TTL of 30min should keep an alert from 25min ago (within
    30) while a factor-default TTL of 15min would drop it."""
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    alerts = [_AlertWithTf(
        ts=now - timedelta(minutes=25), signal_name="otter_buy", tf="3m",
    )]
    cfg = _config(
        factor_ttl=15,                               # legacy TTL = 15min
        factor_ttl_per_tf={"otter_buy": {"3m": 30}}, # per-TF override = 30min
    )
    live = filter_live_alerts_with_dedupe(alerts, cfg, now)
    assert len(live) == 1


def test_per_tf_ttl_falls_back_to_factor_ttl_when_unset():
    """If the per-TF dict has no entry for this (signal, tf), the
    factor's default ttl_minutes is used."""
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    # Alert age 20min, factor TTL 15min, no per-TF override
    alerts = [_AlertWithTf(
        ts=now - timedelta(minutes=20), signal_name="otter_buy", tf="3m",
    )]
    cfg = _config(
        factor_ttl=15,
        factor_ttl_per_tf={"otter_buy": {"15m": 60}},  # only 15m, not 3m
    )
    live = filter_live_alerts_with_dedupe(alerts, cfg, now)
    assert live == []      # 20min > factor_ttl=15min → expired


def test_per_tf_ttl_zero_means_no_expiry():
    """A per-TF TTL of 0 (or negative) means the alert never expires —
    it's always live once in the ledger."""
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    alerts = [_AlertWithTf(
        ts=now - timedelta(days=2), signal_name="otter_buy", tf="3m",
    )]
    cfg = _config(
        factor_ttl=15,
        factor_ttl_per_tf={"otter_buy": {"3m": 0}},
    )
    live = filter_live_alerts_with_dedupe(alerts, cfg, now)
    assert len(live) == 1


# ─── pa_factors_in_score opt-out ────────────────────────────────────────


def test_pa_factors_default_in_score_preserves_legacy_behavior():
    """Default pa_factors_in_score=True: above_session_vwap (weight 1)
    contributes to buy score."""
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    cfg = _config()      # default: pa in score
    pctx = _empty_pctx(above_session_vwap=True)
    verdict = evaluate_confluence_futures(
        live_alerts=[], price_ctx=pctx, config=cfg, now=now,
    )
    # Buy score should include the +1 PA contribution
    assert verdict.breakdown.raw_buy_score == 1


def test_pa_factors_excluded_when_flag_false():
    """When pa_factors_in_score=False, PA factors are NOT added to score
    (they're handled by the new bitunix_pa_validation gate instead)."""
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    cfg = _config(pa_factors_in_score=False)
    pctx = _empty_pctx(
        above_session_vwap=True, higher_highs_4h=True, volume_above_20bar_avg=True,
    )
    verdict = evaluate_confluence_futures(
        live_alerts=[], price_ctx=pctx, config=cfg, now=now,
    )
    # No signal alerts + PA excluded → score should be 0
    assert verdict.breakdown.raw_buy_score == 0
    assert verdict.breakdown.raw_sell_score == 0


# ─── guards_in_score opt-out ────────────────────────────────────────────


def test_guards_default_apply_to_score():
    """Default guards_in_score=True: a 6% drop applies the buy_on_fall
    penalty (-3 at >5% bracket)."""
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    alerts = [AlertEvent(ts=now, signal_name="otter_buy")]
    cfg = _config()      # default: guards in score
    pctx = _empty_pctx(pct_change_in_window_buy=-6.0)
    verdict = evaluate_confluence_futures(
        live_alerts=alerts, price_ctx=pctx, config=cfg, now=now,
    )
    assert verdict.breakdown.buy_guard_penalty == -3
    # raw=3, penalty=-3, final=0 → SKIP because below min_score_to_fire
    assert verdict.breakdown.final_buy_score == 0


def test_guards_excluded_when_flag_false():
    """When guards_in_score=False, rush/fall don't penalize the score
    (they're handled by the new PA validation gate as hard rejects)."""
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    alerts = [AlertEvent(ts=now, signal_name="otter_buy")]
    cfg = _config(guards_in_score=False)
    pctx = _empty_pctx(pct_change_in_window_buy=-10.0)    # huge drop
    verdict = evaluate_confluence_futures(
        live_alerts=alerts, price_ctx=pctx, config=cfg, now=now,
    )
    assert verdict.breakdown.buy_guard_penalty == 0
    assert verdict.breakdown.final_buy_score == 3         # raw, no penalty


# ─── from_dict YAML parse ───────────────────────────────────────────────


def test_from_dict_parses_new_pr3a_keys():
    raw = {
        "scoring": {
            "enabled": True,
            "min_score_to_fire": 5,
            "tier_thresholds": {"premium": 10, "standard": 5, "weak": 3},
            "cooldown_seconds": 1800,
            "dedupe_within_ttl": True,
            "factors": {
                "mc_b_gold_buy": {
                    "weight": 5, "side": "buy", "ttl_minutes": 15,
                    "ttl_per_tf": {"3m": 15, "15m": 45, "30m": 90},
                },
            },
            "guards": {},
            "score_timeframes": ["3m", "15m", "30m"],
            "pa_factors_in_score": False,
            "guards_in_score": False,
        },
    }
    cfg = BitUnixConfluenceConfig.from_dict(raw)
    assert cfg.score_timeframes == ("3m", "15m", "30m")
    assert cfg.pa_factors_in_score is False
    assert cfg.guards_in_score is False
    assert cfg.factor_ttl_per_tf == {
        "mc_b_gold_buy": {"3m": 15, "15m": 45, "30m": 90},
    }


def test_from_dict_omitting_new_keys_preserves_legacy_defaults():
    """Existing YAML (no PR 3a keys) → defaults preserve current
    behavior. This is the contract that PR 3a is a code-only no-op."""
    raw = {
        "scoring": {
            "enabled": True,
            "min_score_to_fire": 8,
            "tier_thresholds": {"premium": 12, "standard": 8, "weak": 5},
            "cooldown_seconds": 1800,
            "dedupe_within_ttl": True,
            "factors": {
                "otter_buy": {"weight": 3, "side": "buy", "ttl_minutes": 15},
            },
        },
    }
    cfg = BitUnixConfluenceConfig.from_dict(raw)
    assert cfg.score_timeframes is None
    assert cfg.pa_factors_in_score is True
    assert cfg.guards_in_score is True
    assert cfg.factor_ttl_per_tf == {}
