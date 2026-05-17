"""Tests for the PCT stale-entry pruner.

Covers the predicate fidelity from the 2026-05-16 03:29 UTC one-shot:
  - sell-side rows preserved
  - rows tied to a polymarket_round_trip via order_id OR entry_order_id preserved
  - fresh rows (< cutoff) preserved
  - dry-run never deletes
  - apply deletes only matches
  - max_rows caps the delete batch
  - audit row always written, regardless of dry-run vs apply
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest

from trading_corp.persistence import db as _db
from trading_corp.scripts.prune_stale_pct_entries import (
    AUDIT_KIND, ACTOR, KIND, prune,
)


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path):
    db_path = tmp_path / "prune_test.db"
    db_url = f"sqlite:///{db_path}"
    _db.init_db(db_url)
    return db_url, db_path


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _insert_audit(db_url, *, actor=ACTOR, kind=KIND, payload=None, ts=None):
    payload = payload or {}
    ts = ts or _iso(datetime.now(timezone.utc) - timedelta(days=2))
    with _db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) "
            "VALUES (?, ?, ?, ?)",
            (ts, actor, kind, json.dumps(payload)),
        )


def _insert_poly_round_trip(db_url, **overrides):
    """Minimal polymarket_round_trip insert matching the production schema."""
    row = {
        "order_id": "rt-1",
        "condition_id": "0xabc",
        "slug": "test-poly-market",
        "market_question": "Test?",
        "category": "politics",
        "series": "",
        "outcome_bet": "yes",
        "qty": 5.0,
        "entry_price": 0.40,
        "notional": 2.0,
        "entry_ts": _iso(datetime.now(timezone.utc) - timedelta(days=3)),
        "resolved_ts": _iso(datetime.now(timezone.utc) - timedelta(days=1)),
        "yes_won": 1,
        "won": 1,
        "realized_pnl": 3.0,
        "roi_pct": 150.0,
        "implied_at_entry": 0.4,
        "llm_prob": 0.5,
        "divergence_pct": 25.0,
        "extra_json": "{}",
    }
    row.update(overrides)
    cols = list(row.keys())
    sql = (
        f"INSERT INTO polymarket_round_trips ({','.join(cols)}) "
        f"VALUES ({','.join('?' for _ in cols)})"
    )
    with _db.connect(db_url) as conn:
        conn.execute(sql, [row[c] for c in cols])


def _count_pct_pending(db_url) -> int:
    with _db.connect(db_url) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM audit_event WHERE actor=? AND kind=?",
            (ACTOR, KIND),
        ).fetchone()[0]


def _count_audit_kind(db_url, kind: str) -> int:
    with _db.connect(db_url) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM audit_event WHERE kind=?", (kind,),
        ).fetchone()[0]


# ── tests ─────────────────────────────────────────────────────────────


def test_dry_run_never_deletes_even_with_matches(fresh_db):
    db_url, _ = fresh_db
    _insert_audit(db_url, payload={"side": "buy", "order_id": "stale-1"})
    _insert_audit(db_url, payload={"side": "buy", "order_id": "stale-2"})
    assert _count_pct_pending(db_url) == 2

    result = prune(db_url=db_url, apply=False)

    assert result["apply"] is False
    assert result["candidates"] == 2
    assert result["deleted"] == 0
    assert _count_pct_pending(db_url) == 2


def test_apply_deletes_matching_rows(fresh_db):
    db_url, _ = fresh_db
    _insert_audit(db_url, payload={"side": "buy", "order_id": "stale-1"})
    _insert_audit(db_url, payload={"side": "buy", "order_id": "stale-2"})

    result = prune(db_url=db_url, apply=True)

    assert result["candidates"] == 2
    assert result["deleted"] == 2
    assert _count_pct_pending(db_url) == 0


def test_sell_side_rows_preserved(fresh_db):
    """Sells are exits, never the source of stale BUYs."""
    db_url, _ = fresh_db
    _insert_audit(db_url, payload={"side": "buy", "order_id": "buy-1"})
    _insert_audit(db_url, payload={"side": "sell", "order_id": "sell-1"})

    result = prune(db_url=db_url, apply=True)

    assert result["deleted"] == 1
    assert _count_pct_pending(db_url) == 1
    with _db.connect(db_url) as conn:
        sides = [
            json.loads(r["payload_json"])["side"]
            for r in conn.execute(
                "SELECT payload_json FROM audit_event WHERE actor=? AND kind=?",
                (ACTOR, KIND),
            )
        ]
    assert sides == ["sell"]


def test_rows_paired_via_order_id_preserved(fresh_db):
    """An audit row whose order_id is in polymarket_round_trips.order_id
    is resolved — leave it alone."""
    db_url, _ = fresh_db
    _insert_poly_round_trip(db_url, order_id="paired-1")
    _insert_audit(db_url, payload={"side": "buy", "order_id": "paired-1"})
    _insert_audit(db_url, payload={"side": "buy", "order_id": "stale-1"})

    result = prune(db_url=db_url, apply=True)

    assert result["candidates"] == 1
    assert result["deleted"] == 1
    assert _count_pct_pending(db_url) == 1   # paired-1 survives


def test_rows_paired_via_entry_order_id_preserved(fresh_db):
    """K3-shape: round-trip exists with entry_order_id = audit's order_id."""
    db_url, _ = fresh_db
    _insert_poly_round_trip(
        db_url, order_id="exit-leg-1",
        extra_json='{"entry_order_id": "entry-1"}',
    )
    # K3 schema added entry_order_id as a real column — set via raw SQL.
    with _db.connect(db_url) as conn:
        try:
            conn.execute("ALTER TABLE polymarket_round_trips ADD COLUMN entry_order_id TEXT")
        except Exception:
            pass
        conn.execute(
            "UPDATE polymarket_round_trips SET entry_order_id=? WHERE order_id=?",
            ("entry-1", "exit-leg-1"),
        )
    _insert_audit(db_url, payload={"side": "buy", "order_id": "entry-1"})
    _insert_audit(db_url, payload={"side": "buy", "order_id": "stale-1"})

    result = prune(db_url=db_url, apply=True)

    assert result["candidates"] == 1
    assert result["deleted"] == 1
    assert _count_pct_pending(db_url) == 1   # entry-1 survives


def test_fresh_rows_preserved(fresh_db):
    """Rows within the cutoff window must not be deleted."""
    db_url, _ = fresh_db
    fresh_ts = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    old_ts = _iso(datetime.now(timezone.utc) - timedelta(days=2))
    _insert_audit(db_url, payload={"side": "buy", "order_id": "fresh-1"}, ts=fresh_ts)
    _insert_audit(db_url, payload={"side": "buy", "order_id": "old-1"}, ts=old_ts)

    result = prune(db_url=db_url, apply=True, cutoff_hours=24)

    assert result["candidates"] == 1
    assert result["deleted"] == 1
    assert _count_pct_pending(db_url) == 1   # fresh-1 survives


def test_max_rows_caps_delete_batch(fresh_db):
    """If candidate count > max_rows, only max_rows are deleted."""
    db_url, _ = fresh_db
    for i in range(7):
        _insert_audit(db_url, payload={"side": "buy", "order_id": f"stale-{i}"})

    result = prune(db_url=db_url, apply=True, max_rows=3)

    assert result["candidates"] == 7
    assert result["deleted"] == 3
    assert _count_pct_pending(db_url) == 4


def test_non_pct_actors_untouched(fresh_db):
    """Other actors' audit rows must be invisible to the pruner."""
    db_url, _ = fresh_db
    _insert_audit(
        db_url, actor="kalshi_weather_arb",
        payload={"side": "buy", "order_id": "kw-1"},
    )
    _insert_audit(db_url, payload={"side": "buy", "order_id": "pct-1"})

    result = prune(db_url=db_url, apply=True)

    assert result["candidates"] == 1
    assert result["deleted"] == 1
    # The kalshi row is still there:
    with _db.connect(db_url) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM audit_event WHERE actor='kalshi_weather_arb'"
        ).fetchone()[0]
    assert n == 1


def test_audit_row_written_on_dry_run(fresh_db):
    db_url, _ = fresh_db
    _insert_audit(db_url, payload={"side": "buy", "order_id": "stale-1"})

    prune(db_url=db_url, apply=False)

    assert _count_audit_kind(db_url, AUDIT_KIND) == 1
    with _db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT payload_json FROM audit_event WHERE kind=?", (AUDIT_KIND,)
        ).fetchone()
    payload = json.loads(row["payload_json"])
    assert payload["candidates"] == 1
    assert payload["deleted"] == 0
    assert payload["dry_run"] is True
    assert payload["apply"] is False
    assert payload["division"] == "polymarket_copy_trading"
    assert payload["strategy"] == "pct_stale_pruner"


def test_audit_row_written_on_apply(fresh_db):
    db_url, _ = fresh_db
    _insert_audit(db_url, payload={"side": "buy", "order_id": "stale-1"})

    prune(db_url=db_url, apply=True)

    assert _count_audit_kind(db_url, AUDIT_KIND) == 1
    with _db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT payload_json FROM audit_event WHERE kind=?", (AUDIT_KIND,)
        ).fetchone()
    payload = json.loads(row["payload_json"])
    assert payload["candidates"] == 1
    assert payload["deleted"] == 1
    assert payload["dry_run"] is False
    assert payload["apply"] is True


def test_payload_without_side_treated_as_buy(fresh_db):
    """Pre-2026-05-14 PCT audit rows often lacked an explicit `side` —
    they're all BUY events. COALESCE(...,'buy') makes them visible."""
    db_url, _ = fresh_db
    _insert_audit(db_url, payload={"order_id": "no-side-1"})   # no 'side' key

    result = prune(db_url=db_url, apply=True)

    assert result["candidates"] == 1
    assert result["deleted"] == 1


def test_invalid_cutoff_hours_raises(fresh_db):
    db_url, _ = fresh_db
    with pytest.raises(ValueError):
        prune(db_url=db_url, cutoff_hours=0)


def test_invalid_max_rows_raises(fresh_db):
    db_url, _ = fresh_db
    with pytest.raises(ValueError):
        prune(db_url=db_url, max_rows=0)
