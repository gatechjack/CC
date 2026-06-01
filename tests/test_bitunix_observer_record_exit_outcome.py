"""Tests for `_record_exit_outcome` — canonical exit-side writer.

Commit 2 of Stage-1 Session N+2 Phase 3 — the exit-side mirror of
`_record_placement_outcome`. Validates:

- Paper-mode call stamps `extra["result_source"]="paper_replay_bars"`.
- Live-mode call stamps `extra["result_source"]="live_broker_truth"`.
- `result_*` columns updated on the row.
- `extra_json` merge preserves prior fields (broker_order_id from
  Path C revert, score_path, etc.) — load-bearing for the reconciler
  + audit lineage.
- `exit_outcome_recorded` audit row written with rich payload.
- `leg` parameter routes into `extra["exit_leg"]` for multi-leg trades.
- `fill_event` parameter routes into both `extra["exit_broker_order_id"]`
  AND the audit payload's `fill_event` block.
- DB-write failures are swallowed (logged, not re-raised) — the exit
  helper must never crash the consumer loop.

Session A scope: this commit ADDS the helper. Consumers (replay loop
for paper, `_execute_live_exits` for live) wire in commits 3+ and
Session B.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
)
from trading_corp.agents.logger import LoggerAgent
from trading_corp.persistence import db
from trading_corp.persistence.models import FillEvent


# ─── fixtures ───────────────────────────────────────────────────────────


def _make_observer(tmp_path: Path) -> tuple[BitunixFuturesObserver, str]:
    db_path = tmp_path / "record_exit.db"
    db_url = f"sqlite:///{db_path}"
    db.init_db(db_url)

    logger_agent = LoggerAgent(db_url=db_url)
    obs = BitunixFuturesObserver(
        db_url=db_url,
        risk_agent=MagicMock(),
        data_exec=MagicMock(),
        logger_agent=logger_agent,
        execution_mode="paper",
    )
    return obs, db_url


def _seed_pending_row(
    db_url: str,
    order_id: str,
    *,
    extra: dict | None = None,
) -> None:
    """Seed a paper_trade_record row in `result IS NULL` state — the
    shape the exit helper updates."""
    extra_json = json.dumps(extra) if extra else None
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
                7200, None, extra_json,
            ),
        )


# ─── paper-mode stamp ───────────────────────────────────────────────────


def test_paper_mode_stamps_result_source_paper_replay_bars(tmp_path):
    obs, db_url = _make_observer(tmp_path)
    _seed_pending_row(db_url, "ord-p1")
    obs._record_exit_outcome(
        order_id="ord-p1",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
        actual_pnl_dollars=1.0,
        actual_r_multiple=2.0,
        bars_to_resolution=60,
        is_live=False,
    )
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT extra_json FROM paper_trade_record WHERE order_id=?",
            ("ord-p1",),
        ).fetchone()
    extra = json.loads(row["extra_json"])
    assert extra["result_source"] == "paper_replay_bars"


# ─── live-mode stamp ────────────────────────────────────────────────────


def test_live_mode_stamps_result_source_live_broker_truth(tmp_path):
    obs, db_url = _make_observer(tmp_path)
    _seed_pending_row(db_url, "ord-l1", extra={"execution_mode": "live"})
    fill = FillEvent(
        order_id="bx-exit-1", symbol="BTCUSDT", side="sell",
        qty=0.001, price=81_000.0, ts="2026-06-01T11:00:00+00:00",
        venue="bitunix",
    )
    obs._record_exit_outcome(
        order_id="ord-l1",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
        actual_pnl_dollars=1.0,
        actual_r_multiple=2.0,
        bars_to_resolution=60,
        is_live=True,
        fill_event=fill,
    )
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT extra_json FROM paper_trade_record WHERE order_id=?",
            ("ord-l1",),
        ).fetchone()
    extra = json.loads(row["extra_json"])
    assert extra["result_source"] == "live_broker_truth"


# ─── result columns updated ─────────────────────────────────────────────


def test_updates_result_columns(tmp_path):
    obs, db_url = _make_observer(tmp_path)
    _seed_pending_row(db_url, "ord-r1")
    obs._record_exit_outcome(
        order_id="ord-r1",
        result="loss",
        result_ts="2026-06-01T12:00:00+00:00",
        result_price=79_500.0,
        actual_pnl_dollars=-0.5,
        actual_r_multiple=-1.0,
        bars_to_resolution=120,
        is_live=False,
    )
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT result, result_ts, result_price, "
            "       actual_pnl_dollars, actual_r_multiple, "
            "       bars_to_resolution "
            "FROM paper_trade_record WHERE order_id=?",
            ("ord-r1",),
        ).fetchone()
    assert row["result"] == "loss"
    assert row["result_ts"] == "2026-06-01T12:00:00+00:00"
    assert row["result_price"] == 79_500.0
    assert row["actual_pnl_dollars"] == -0.5
    assert row["actual_r_multiple"] == -1.0
    assert row["bars_to_resolution"] == 120


# ─── extra_json merge preserves prior fields ────────────────────────────


def test_extra_json_merge_preserves_path_c_fields(tmp_path):
    """Load-bearing: Path C entries stamp `execution_mode` +
    `broker_order_id` on row creation. The exit helper must merge,
    not overwrite — otherwise the reconciler loses its link back to
    broker truth."""
    obs, db_url = _make_observer(tmp_path)
    _seed_pending_row(db_url, "ord-m1", extra={
        "execution_mode": "live",
        "broker_order_id": "bx-entry-42",
        "tier": "PREMIUM",
    })
    obs._record_exit_outcome(
        order_id="ord-m1",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
        is_live=True,
    )
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT extra_json FROM paper_trade_record WHERE order_id=?",
            ("ord-m1",),
        ).fetchone()
    extra = json.loads(row["extra_json"])
    assert extra["execution_mode"] == "live"
    assert extra["broker_order_id"] == "bx-entry-42"
    assert extra["tier"] == "PREMIUM"
    assert extra["result_source"] == "live_broker_truth"


def test_extra_json_merge_accepts_extra_json_updates_kwarg(tmp_path):
    """Multi-leg classifier returns `extra_json_updates` (filled_legs,
    current_sl) on close-out. The helper merges them into the row."""
    obs, db_url = _make_observer(tmp_path)
    _seed_pending_row(db_url, "ord-u1", extra={"tier": "STANDARD"})
    obs._record_exit_outcome(
        order_id="ord-u1",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
        is_live=False,
        extra_json_updates={
            "filled_legs": ["tp1", "tp2", "tp3"],
            "current_sl": 80_500.0,
        },
    )
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT extra_json FROM paper_trade_record WHERE order_id=?",
            ("ord-u1",),
        ).fetchone()
    extra = json.loads(row["extra_json"])
    assert extra["tier"] == "STANDARD"
    assert extra["filled_legs"] == ["tp1", "tp2", "tp3"]
    assert extra["current_sl"] == 80_500.0
    assert extra["result_source"] == "paper_replay_bars"


# ─── audit row written ──────────────────────────────────────────────────


def test_writes_exit_outcome_recorded_audit(tmp_path):
    obs, db_url = _make_observer(tmp_path)
    _seed_pending_row(db_url, "ord-a1")
    obs._record_exit_outcome(
        order_id="ord-a1",
        result="expired",
        result_ts="2026-06-01T14:00:00+00:00",
        result_price=80_100.0,
        actual_pnl_dollars=0.0,
        actual_r_multiple=0.2,
        bars_to_resolution=240,
        is_live=False,
    )
    with db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='exit_outcome_recorded' AND actor='bitunix_futures'"
        ).fetchall()
    assert len(rows) == 1
    p = json.loads(rows[0]["payload_json"])
    assert p["order_id"] == "ord-a1"
    assert p["strategy"] == "bitunix_futures"
    assert p["division"] == "bitunix_futures"
    assert p["result"] == "expired"
    assert p["result_source"] == "paper_replay_bars"
    assert p["is_live"] is False


# ─── leg parameter ──────────────────────────────────────────────────────


def test_leg_param_stamped_into_extra_and_audit(tmp_path):
    obs, db_url = _make_observer(tmp_path)
    _seed_pending_row(db_url, "ord-leg1")
    obs._record_exit_outcome(
        order_id="ord-leg1",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
        is_live=False,
        leg="tp2",
    )
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT extra_json FROM paper_trade_record WHERE order_id=?",
            ("ord-leg1",),
        ).fetchone()
        audit = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='exit_outcome_recorded'"
        ).fetchone()
    assert json.loads(row["extra_json"])["exit_leg"] == "tp2"
    assert json.loads(audit["payload_json"])["leg"] == "tp2"


# ─── fill_event parameter (live mode) ───────────────────────────────────


def test_fill_event_stamped_into_extra_and_audit(tmp_path):
    obs, db_url = _make_observer(tmp_path)
    _seed_pending_row(db_url, "ord-fe1")
    fill = FillEvent(
        order_id="bx-exit-99",
        symbol="BTCUSDT", side="sell", qty=0.001,
        price=81_234.0, ts="2026-06-01T11:30:00+00:00",
        venue="bitunix",
    )
    obs._record_exit_outcome(
        order_id="ord-fe1",
        result="win",
        result_ts="2026-06-01T11:30:00+00:00",
        result_price=81_234.0,
        is_live=True,
        fill_event=fill,
    )
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT extra_json FROM paper_trade_record WHERE order_id=?",
            ("ord-fe1",),
        ).fetchone()
        audit = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='exit_outcome_recorded'"
        ).fetchone()
    extra = json.loads(row["extra_json"])
    assert extra["exit_broker_order_id"] == "bx-exit-99"
    audit_p = json.loads(audit["payload_json"])
    assert audit_p["fill_event"]["order_id"] == "bx-exit-99"
    assert audit_p["fill_event"]["price"] == 81_234.0
    assert audit_p["fill_event"]["venue"] == "bitunix"


# ─── failure-mode: DB error is swallowed ────────────────────────────────


def test_row_update_failure_is_swallowed(tmp_path, monkeypatch):
    """DB hiccup must NOT propagate from the helper. The exit happened
    (at the broker for live, at the classifier for paper); a transient
    write failure shouldn't crash the consumer loop. The reconciler
    catches persistent divergences."""
    obs, db_url = _make_observer(tmp_path)
    # Don't seed the row → UPDATE matches 0 rows. The helper should
    # still complete without raising. We additionally monkeypatch
    # db.connect to raise to simulate a real DB outage.

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated db outage")

    # Patch db.connect (used inside _record_exit_outcome) to raise.
    from trading_corp.agents.divisions import bitunix_futures_observer as obs_mod
    monkeypatch.setattr(obs_mod.db, "connect", _boom)

    # Should not raise.
    obs._record_exit_outcome(
        order_id="ord-x1",
        result="loss",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=79_500.0,
        is_live=False,
    )


def test_audit_failure_is_swallowed(tmp_path, monkeypatch):
    """Same swallow discipline applies to the audit-write step."""
    obs, db_url = _make_observer(tmp_path)
    _seed_pending_row(db_url, "ord-x2")

    def _boom_log(**_kwargs):
        raise RuntimeError("logger went down")

    monkeypatch.setattr(obs.logger_agent, "log_event", _boom_log)
    # Should not raise.
    obs._record_exit_outcome(
        order_id="ord-x2",
        result="win",
        result_ts="2026-06-01T11:00:00+00:00",
        result_price=81_000.0,
        is_live=False,
    )
    # And the row update still landed.
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT result FROM paper_trade_record WHERE order_id=?",
            ("ord-x2",),
        ).fetchone()
    assert row["result"] == "win"
