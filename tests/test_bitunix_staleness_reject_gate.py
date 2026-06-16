"""Tests for the C staleness-reject gate (bar-interval-aware entry freshness).

Investigation reports/2026-06-16_entry_latency_investigation.md found late
entries are caused by an event-loop FREEZE: the 00:38 trade entered ~11.5 min
after its bar (loop frozen 00:25:19→00:38:34) and stopped out. The C gate
REJECTS an entry whose originating bar is older than (bar_interval + margin)
so a freeze- or webhook-retry-delayed alert can't open a stale, adverse entry.

Covered:
  - decision matrix (`_staleness_verdict`): fresh allowed / stale rejected /
    interval-aware threshold (3m vs 15m, NOT a fixed constant) / configurable
    margin / config OFF = no gating / fail-open on unparseable payload.
  - integration: a stale alert is rejected before propose/place and emits the
    `entry_rejected_stale_bar` audit (the 00:38-trade-prevention case); a fresh
    alert passes the gate.
  - exits are NEVER staleness-gated (`_execute_live_exits` still acts).
  - the webhook 20-min anti-replay window is untouched (replay path intact).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import trading_corp.agents.divisions.bitunix_futures_observer as obs_mod
from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
    _interval_to_seconds,
)
from trading_corp.agents.logger import LoggerAgent
from trading_corp.persistence import db
from trading_corp.persistence.models import FillEvent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _payload(*, age_seconds: float, interval: str = "3", **over) -> dict:
    bar_open = _now() - timedelta(seconds=age_seconds)
    p = {
        "signal": "spoon_bull",
        "symbol": "BTC/USDT.P",
        "price": 66_000.0,
        "time": bar_open.isoformat(),
        "interval": interval,
    }
    p.update(over)
    return p


# ─────────────────────────── interval mapping ───────────────────────────


def test_interval_to_seconds_scales_with_bar():
    assert _interval_to_seconds("3") == 180.0
    assert _interval_to_seconds("15") == 900.0
    assert _interval_to_seconds("60") == 3_600.0
    assert _interval_to_seconds("240") == 14_400.0
    assert _interval_to_seconds("1D") == 86_400.0
    assert _interval_to_seconds("D") == 86_400.0
    assert _interval_to_seconds("30S") == 30.0
    # fail-open inputs → None (gate skips rather than blocks)
    assert _interval_to_seconds(None) is None
    assert _interval_to_seconds("") is None
    assert _interval_to_seconds("foo") is None


# ─────────────────────── _staleness_verdict matrix ───────────────────────


def _obs(tmp_path, *, enabled: bool, margin: float = 120.0) -> BitunixFuturesObserver:
    db_url = f"sqlite:///{tmp_path / 'sv.db'}"
    db.init_db(db_url)
    return BitunixFuturesObserver(
        db_url=db_url,
        staleness_gate_enabled=enabled,
        staleness_margin_seconds=margin,
    )


def test_fresh_3m_allowed(tmp_path):
    obs = _obs(tmp_path, enabled=True)
    # age 60s < 180+120 = 300s → fresh
    is_stale, info = obs._staleness_verdict(_payload(age_seconds=60), _now())
    assert is_stale is False
    assert info is None


def test_stale_3m_rejected_like_the_00_38_trade(tmp_path):
    obs = _obs(tmp_path, enabled=True)
    # 692s = the real stale alert age; 692 > 300 → REJECT
    is_stale, info = obs._staleness_verdict(_payload(age_seconds=692), _now())
    assert is_stale is True
    assert info["interval_seconds"] == 180.0
    assert info["threshold_seconds"] == 300.0
    assert 690 <= info["age_seconds"] <= 695


def test_interval_aware_not_a_fixed_constant(tmp_path):
    obs = _obs(tmp_path, enabled=True)
    now = _now()
    # 692s is STALE on a 3m bar (threshold 300s) ...
    stale_3m, _ = obs._staleness_verdict(_payload(age_seconds=692, interval="3"), now)
    assert stale_3m is True
    # ... but FRESH on a 15m bar (threshold 900+120 = 1020s). The threshold
    # tracks the interval — it is NOT the 3m constant.
    fresh_15m, info15 = obs._staleness_verdict(
        _payload(age_seconds=692, interval="15"), now,
    )
    assert fresh_15m is False
    assert info15 is None
    # And a 15m bar IS rejected past its own (larger) threshold.
    stale_15m, info = obs._staleness_verdict(
        _payload(age_seconds=1_100, interval="15"), now,
    )
    assert stale_15m is True
    assert info["interval_seconds"] == 900.0
    assert info["threshold_seconds"] == 1_020.0


def test_margin_is_configurable(tmp_path):
    obs = _obs(tmp_path, enabled=True, margin=300.0)  # 3m threshold = 180+300 = 480s
    now = _now()
    assert obs._staleness_verdict(_payload(age_seconds=400), now)[0] is False
    assert obs._staleness_verdict(_payload(age_seconds=500), now)[0] is True


def test_config_off_never_gates(tmp_path):
    obs = _obs(tmp_path, enabled=False)
    # absurdly old bar, gate OFF → not stale (current pre-C behavior)
    is_stale, info = obs._staleness_verdict(_payload(age_seconds=1_000_000), _now())
    assert is_stale is False
    assert info is None


def test_default_observer_gate_off(tmp_path):
    # An observer constructed WITHOUT the staleness args (every existing
    # caller/test, and the offline backtester path) keeps current behavior.
    db_url = f"sqlite:///{tmp_path / 'def.db'}"
    db.init_db(db_url)
    obs = BitunixFuturesObserver(db_url=db_url)
    assert obs.staleness_gate_enabled is False
    assert obs._staleness_verdict(_payload(age_seconds=99_999), _now())[0] is False


def test_fail_open_on_unparseable_payload(tmp_path):
    obs = _obs(tmp_path, enabled=True)
    now = _now()
    # missing interval, missing time, garbage interval → skip (don't block)
    assert obs._staleness_verdict({"time": _now().isoformat()}, now)[0] is False
    assert obs._staleness_verdict({"interval": "3"}, now)[0] is False
    assert obs._staleness_verdict(
        {"time": "not-a-ts", "interval": "3"}, now,
    )[0] is False


# ─────────────────────── integration: entry path ────────────────────────


def _wired(tmp_path, *, enabled: bool, margin: float = 120.0):
    db_url = f"sqlite:///{tmp_path / 'wired.db'}"
    db.init_db(db_url)

    risk_agent = MagicMock()
    rv = MagicMock()
    rv.verdict = "approve"
    rv.reason = "ok"
    rv.new_qty = None
    risk_agent.evaluate.return_value = rv

    snap = MagicMock()
    snap.positions = []  # equity left as a MagicMock → float() raises →
    # observer's error_snapshot branch returns cleanly (snapshot WAS awaited).
    broker = MagicMock()
    broker.snapshot = AsyncMock(return_value=snap)

    data_exec = MagicMock()
    data_exec.brokers = {"bitunix_futures": broker}
    data_exec.place = AsyncMock()
    data_exec.flatten_division = AsyncMock()

    logger_agent = MagicMock()

    obs = BitunixFuturesObserver(
        db_url=db_url,
        risk_agent=risk_agent,
        data_exec=data_exec,
        logger_agent=logger_agent,
        staleness_gate_enabled=enabled,
        staleness_margin_seconds=margin,
    )
    return obs, data_exec, broker, logger_agent


def _patch_score_premium(monkeypatch, obs):
    """Force the score path to PREMIUM with empty ledger so the test reaches
    the gate without depending on bias/CVD/ledger machinery."""
    bd = SimpleNamespace(
        net_score=8.0, final_buy_score=8.0, final_sell_score=0.0,
        raw_buy_score=8.0, raw_sell_score=0.0,
        buy_guard_penalty=0.0, sell_guard_penalty=0.0,
        buy_contributions={}, sell_contributions={},
    )
    verdict = SimpleNamespace(
        tier=obs_mod._ScoreTier.PREMIUM, side=obs_mod._ScoreSide.BUY,
        breakdown=bd, cooldown_blocked=False, reason="test",
    )
    monkeypatch.setattr(obs_mod, "filter_live_alerts_with_dedupe", lambda *a, **k: [])
    monkeypatch.setattr(obs_mod, "evaluate_confluence_futures", lambda *a, **k: verdict)
    monkeypatch.setattr(obs, "_read_live_ledger", lambda now: [])


def _stale_audits(logger_agent):
    return [
        c for c in logger_agent.log_event.call_args_list
        if c.kwargs.get("kind") == "entry_rejected_stale_bar"
    ]


@pytest.mark.asyncio
async def test_stale_entry_rejected_and_audited(tmp_path, monkeypatch):
    obs, data_exec, broker, logger_agent = _wired(tmp_path, enabled=True)
    _patch_score_premium(monkeypatch, obs)

    await obs._score_and_maybe_propose_locked(
        _payload(age_seconds=692), source="market_cypher",
    )

    # 1) dedicated audit emitted with the diagnostic fields
    audits = _stale_audits(logger_agent)
    assert len(audits) == 1
    ap = audits[0].kwargs["payload"]
    assert ap["interval_seconds"] == 180.0
    assert ap["threshold_seconds"] == 300.0
    assert ap["age_seconds"] >= 690
    assert ap["source"] == "market_cypher"
    # 2) short-circuited BEFORE any broker work / placement
    broker.snapshot.assert_not_awaited()
    data_exec.place.assert_not_called()
    # 3) the score-decision trail records the rejection reason
    with db.connect(obs.db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event WHERE kind='bitunix_score_decided'"
        ).fetchall()
    outcomes = [json.loads(r["payload_json"])["outcome"] for r in rows]
    assert "skipped_stale_bar" in outcomes


@pytest.mark.asyncio
async def test_fresh_entry_passes_the_gate(tmp_path, monkeypatch):
    obs, data_exec, broker, logger_agent = _wired(tmp_path, enabled=True)
    _patch_score_premium(monkeypatch, obs)

    await obs._score_and_maybe_propose_locked(
        _payload(age_seconds=60), source="market_cypher",
    )

    # gate did NOT reject ...
    assert _stale_audits(logger_agent) == []
    # ... and the flow proceeded PAST the gate (snapshot is downstream of it)
    broker.snapshot.assert_awaited()


@pytest.mark.asyncio
async def test_gate_off_does_not_reject_stale_entry(tmp_path, monkeypatch):
    # Config OFF → current behavior: a stale alert is NOT rejected by C.
    obs, data_exec, broker, logger_agent = _wired(tmp_path, enabled=False)
    _patch_score_premium(monkeypatch, obs)

    await obs._score_and_maybe_propose_locked(
        _payload(age_seconds=99_999), source="market_cypher",
    )
    assert _stale_audits(logger_agent) == []
    broker.snapshot.assert_awaited()  # not short-circuited by C


# ─────────────────────── exits are NEVER gated ──────────────────────────


def _seed_live_row(db_url: str, order_id: str, *, broker_order_id: str) -> None:
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record ("
            " order_id, ts, strategy, division, symbol, side, qty, "
            " entry_reference_price, stop_price, tp_price, "
            " max_hold_seconds, result, extra_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                order_id, "2026-06-01T10:00:00+00:00",
                "bitunix_futures", "bitunix_futures",
                "BTCUSDT", "buy", 0.001,
                80_000.0, 79_500.0, 81_000.0,
                7200, None,
                json.dumps({"execution_mode": "live", "broker_order_id": broker_order_id}),
            ),
        )


@pytest.mark.asyncio
async def test_exit_is_never_staleness_gated(tmp_path):
    """An exit must always act — even with the gate ON and even if the gate
    would scream 'stale'. Exits don't carry a signal bar and never consult
    `_staleness_verdict`."""
    db_url = f"sqlite:///{tmp_path / 'exit.db'}"
    db.init_db(db_url)
    fill = FillEvent(
        order_id="bx-exit-1", symbol="BTCUSDT", side="sell",
        qty=0.001, price=81_000.0, ts="2026-06-01T11:00:00+00:00", venue="bitunix",
    )
    data_exec = MagicMock()
    data_exec.place = AsyncMock(return_value=fill)
    telegram = MagicMock()
    telegram.push = AsyncMock(return_value=True)
    logger_agent = LoggerAgent(db_url=db_url)

    obs = BitunixFuturesObserver(
        db_url=db_url,
        risk_agent=MagicMock(),
        data_exec=data_exec,
        logger_agent=logger_agent,
        telegram_channel=telegram,
        execution_mode="live",
        staleness_gate_enabled=True,   # gate ON
    )
    # Belt-and-suspenders: even if the gate would say "stale", the exit acts.
    obs._staleness_verdict = lambda payload, now: (True, {"note": "forced"})
    _seed_live_row(obs.db_url, "ord-tp1", broker_order_id="bx-entry-7")

    ok = await obs._execute_live_exits(
        order_id="ord-tp1",
        symbol="BTCUSDT",
        entry_side="buy",
        qty=0.001,
        exit_kind="tp1",
        parent_broker_order_id="bx-entry-7",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
        actual_pnl_dollars=1.0,
        actual_r_multiple=2.0,
    )

    assert ok is True
    data_exec.place.assert_awaited_once()
    # reduce_only close was placed → exit executed, not staleness-rejected
    assert data_exec.place.await_args.kwargs.get("reduce_only") is True


# ─────────────────────── replay path is untouched ───────────────────────


def test_webhook_anti_replay_window_unchanged():
    # C must NOT repurpose/break the 20-min anti-replay window in webhooks.py.
    from trading_corp.web.webhooks import _REPLAY_WINDOW_SEC
    assert _REPLAY_WINDOW_SEC == 1200
