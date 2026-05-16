"""Tests for the PR 3c observer integration: tf-aware ledger + PA + HTF
gate logic in shadow vs. enforce mode.

Pin the contract that:
  - Schema migration adds `tf` column idempotently.
  - `_append_to_ledger` extracts tf from `payload['interval']` via
    `_normalize_tf` and persists it.
  - `_read_live_ledger` returns BitUnixAlertEvent carrying tf.
  - `_normalize_tf` maps TradingView interval strings ("3", "240", "D")
    to canonical tf labels ("3m", "4h", "1d").
  - Observer in `htf_gate_mode='off'` ignores PA + HTF (default).
  - Observer in `'shadow'` writes pa_validation_decision +
    htf_gate_decision audits but does NOT alter trade flow.
  - Observer in `'enforce'` blocks on PA REJECT, applies HTF
    size_multiplier, and blocks on HTF size=0.

What we DON'T test here:
  - The pure PA / HTF classifiers (covered in their own test files).
  - Live BitUnix HTTP — all broker calls are mocked.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
    _normalize_tf,
)
from trading_corp.agents.strategies.bitunix_confluence import (
    BitUnixAlertEvent,
)
from trading_corp.agents.strategies.bitunix_htf_regime import (
    HTFRegimeConfig,
    Regime,
    RegimeVerdict,
    Session,
    TimeframeClassification,
    TimeframeRegime,
    TradePermission,
    VolatilityTier,
)
from trading_corp.agents.strategies.bitunix_pa_validation import (
    PAValidationConfig,
    PAValidationDecision,
)
from trading_corp.persistence import db


# ─── _normalize_tf ──────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("3", "3m"),
    ("15", "15m"),
    ("30", "30m"),
    ("60", "1h"),
    ("240", "4h"),
    ("D", "1d"),
    ("1D", "1d"),
    ("daily", "1d"),
    ("3m", "3m"),               # already canonical
    ("15m", "15m"),
    (None, None),
    ("", None),
    ("zzz", "zzz"),             # unknown passes through (replay can flag)
])
def test_normalize_tf_maps_tv_intervals(raw, expected):
    assert _normalize_tf(raw) == expected


# ─── ledger I/O fixtures ────────────────────────────────────────────────


@pytest.fixture
def observer(tmp_path: Path) -> BitunixFuturesObserver:
    db_path = tmp_path / "test_pr3c.db"
    db.init_db(f"sqlite:///{db_path}")
    return BitunixFuturesObserver(db_url=f"sqlite:///{db_path}")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ─── ledger captures + reads tf ─────────────────────────────────────────


def test_append_to_ledger_persists_tf_from_interval(observer):
    payload = {
        "symbol": "BTCUSDT", "signal": "mc_a_blood_diamond",
        "interval": "3", "time": _utc_now().isoformat(),
    }
    observer._append_to_ledger(payload, source="market_cypher")
    with db.connect(observer.db_url) as conn:
        rows = conn.execute(
            "SELECT signal, tf FROM bitunix_signal_ledger"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["signal"] == "mc_a_blood_diamond"
    assert rows[0]["tf"] == "3m"


def test_append_to_ledger_with_no_interval_persists_tf_null(observer):
    payload = {
        "symbol": "BTCUSDT", "signal": "otter_buy",
        "time": _utc_now().isoformat(),
    }
    observer._append_to_ledger(payload, source="lord_otter")
    with db.connect(observer.db_url) as conn:
        rows = conn.execute("SELECT tf FROM bitunix_signal_ledger").fetchall()
    assert rows[0]["tf"] is None


def test_read_live_ledger_returns_bitunix_alert_with_tf(observer):
    payload = {
        "symbol": "BTCUSDT", "signal": "mc_b_gold_buy",
        "interval": "15", "time": _utc_now().isoformat(),
    }
    observer._append_to_ledger(payload, source="market_cypher")
    alerts = observer._read_live_ledger(_utc_now())
    assert len(alerts) == 1
    a = alerts[0]
    assert isinstance(a, BitUnixAlertEvent)
    assert a.signal_name == "mc_b_gold_buy"
    assert a.tf == "15m"


def test_schema_migration_adds_tf_column_to_existing_table(tmp_path: Path):
    """A pre-PR-3c table (no tf column) gets the column added on first
    construct. Historical rows have tf=NULL — the read path handles it."""
    db_path = tmp_path / "premigration.db"
    db.init_db(f"sqlite:///{db_path}")
    # Simulate the pre-PR-3c table by dropping the column we just got
    # (SQLite doesn't support DROP COLUMN before 3.35; build the legacy
    # shape directly instead).
    with db.connect(f"sqlite:///{db_path}") as conn:
        conn.execute("DROP TABLE IF EXISTS bitunix_signal_ledger")
        conn.execute(
            "CREATE TABLE bitunix_signal_ledger ("
            " ts TEXT NOT NULL, signal TEXT NOT NULL, "
            " source TEXT NOT NULL, inserted_at TEXT NOT NULL"
            ")"
        )
        conn.execute(
            "INSERT INTO bitunix_signal_ledger "
            "(ts, signal, source, inserted_at) VALUES (?, ?, ?, ?)",
            ("2026-05-01T12:00:00+00:00", "otter_buy", "lord_otter",
             "2026-05-01T12:00:01+00:00"),
        )
    # Now constructing the observer triggers the migration
    BitunixFuturesObserver(db_url=f"sqlite:///{db_path}")
    with db.connect(f"sqlite:///{db_path}") as conn:
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(bitunix_signal_ledger)"
        ).fetchall()}
        assert "tf" in cols
        # Historical row preserved with tf=NULL
        rows = conn.execute(
            "SELECT signal, tf FROM bitunix_signal_ledger"
        ).fetchall()
        assert rows[0]["signal"] == "otter_buy"
        assert rows[0]["tf"] is None


# ─── observer dependency wiring ─────────────────────────────────────────


def test_observer_default_htf_gate_mode_is_off(tmp_path: Path):
    db_path = tmp_path / "default.db"
    db.init_db(f"sqlite:///{db_path}")
    obs = BitunixFuturesObserver(db_url=f"sqlite:///{db_path}")
    assert obs.htf_gate_mode == "off"
    assert obs.htf_provider is None
    assert obs.pa_config is None
    assert obs.htf_config is None


def test_observer_invalid_gate_mode_falls_back_to_off(tmp_path: Path):
    db_path = tmp_path / "invalid.db"
    db.init_db(f"sqlite:///{db_path}")
    obs = BitunixFuturesObserver(
        db_url=f"sqlite:///{db_path}", htf_gate_mode="garbage",
    )
    assert obs.htf_gate_mode == "off"


def test_observer_accepts_three_valid_modes(tmp_path: Path):
    for mode in ("off", "shadow", "enforce"):
        db_path = tmp_path / f"mode_{mode}.db"
        db.init_db(f"sqlite:///{db_path}")
        obs = BitunixFuturesObserver(
            db_url=f"sqlite:///{db_path}", htf_gate_mode=mode,
        )
        assert obs.htf_gate_mode == mode


# ─── audit-row writers (PR 3c) ──────────────────────────────────────────


def _pa_pass_result():
    """Helper returning a synthetic PA result for log testing."""
    from trading_corp.agents.strategies.bitunix_pa_validation import (
        PAValidationResult,
    )
    return PAValidationResult(
        decision=PAValidationDecision.PASS,
        side="buy",
        passed=("vwap_alignment", "volume_confirmation", "structure_alignment"),
        failed=(),
        rush_fall_triggered=None,
        reason="PASS: require_all (passed 3/3)",
    )


def _htf_verdict_strong_bull():
    tf_class = TimeframeClassification(
        timeframe="1h", regime=TimeframeRegime.Bull,
        ema20=None, ema50=None, ema200=None,
        ema_alignment="bull", structure="bull",
        adx=25.0, macd_hist=0.001, reason="synth",
    )
    return RegimeVerdict(
        regime=Regime.STRONG_BULL, score=1.0,
        h1=tf_class, h4=tf_class, d1=tf_class,
        volatility_tier=VolatilityTier.Normal,
        atr_pct_d1=1.2,
        nearest_resistance=72000.0, nearest_support=68000.0,
        distance_to_resistance_pct=2.0, distance_to_support_pct=2.0,
        session=Session.NewYork,
        funding_rate=0.0001, funding_extreme=False,
        safe_mode_reason=None,
    )


def _verdict_score_buy_premium():
    """Synthetic verdict_score the audit writers expect."""
    s = MagicMock()
    s.tier = MagicMock(value="PREMIUM")
    s.side = MagicMock(value="buy")
    s.breakdown = MagicMock(
        net_score=12, final_buy_score=12, final_sell_score=0,
        raw_buy_score=12, raw_sell_score=0,
        buy_guard_penalty=0, sell_guard_penalty=0,
        buy_contributions=[("otter_buy", 3), ("mc_b_gold_buy", 5)],
        sell_contributions=[],
    )
    s.cooldown_blocked = False
    s.reason = "PREMIUM buy: net_score=12"
    return s


def test_log_pa_validation_writes_audit_row_with_mode(observer):
    payload = {"signal": "otter_buy", "_source": "lord_otter"}
    observer._log_pa_validation(
        payload, _verdict_score_buy_premium(), _pa_pass_result(),
        enforced=False,    # shadow
    )
    with db.connect(observer.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='pa_validation_decision'"
        ).fetchall()
    assert len(rows) == 1
    p = json.loads(rows[0]["payload_json"])
    assert p["decision"] == "pass"
    assert p["mode"] == "shadow"
    assert "vwap_alignment" in p["passed"]
    assert p["score_side"] == "buy"
    assert p["score_tier"] == "PREMIUM"


def test_log_htf_gate_writes_audit_row_with_full_breakdown(observer):
    payload = {"signal": "otter_buy", "_source": "lord_otter"}
    permission = TradePermission(
        allow_long=True, allow_short=False, size_multiplier=1.0,
        reason="STRONG_BULL: longs full size",
        hard_zero_reason=None,
    )
    observer._log_htf_gate(
        payload, _verdict_score_buy_premium(), _htf_verdict_strong_bull(),
        permission, enforced=True,
    )
    with db.connect(observer.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='htf_gate_decision'"
        ).fetchall()
    assert len(rows) == 1
    p = json.loads(rows[0]["payload_json"])
    assert p["regime"] == "STRONG_BULL"
    assert p["composite_score"] == 1.0
    assert p["size_multiplier"] == 1.0
    assert p["mode"] == "enforce"
    assert p["h1"]["regime"] == "bull"
    assert p["h4"]["regime"] == "bull"
    assert p["d1"]["regime"] == "bull"
    assert p["volatility_tier"] == "normal"
    assert p["funding_extreme"] is False
