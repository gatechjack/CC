"""Tests for the UI-rebuild live-state writes (2026-08-14, Track A).

Covers the RungStore write surface that feeds the /mace read model:
  - set_live_state: upsert per-rung mark/spot into mace_rung_live (INSERT OR
    REPLACE; None -> NULL; latest write wins).
  - promote_open entry_atm_iv (A3): the durable per-rung entry ATM IV is written
    ONLY when available; a None must NOT clobber the column to a bogus value.

The _manage_one loop-write and the A4 snapshot widen are covered in
test_mace_manager_live_write.py (they need the manager + a fake port).
"""
from __future__ import annotations

import sqlite3
from datetime import date

from trading_corp.mace import execution as ex
from trading_corp.mace.domain import CondorSpec, RUNG_OPEN
from trading_corp.persistence import db as dbmod

SESSION = date(2026, 8, 10)
EXPIRY = date(2026, 9, 18)
SPEC = CondorSpec("SPY", EXPIRY, 585.0, 582.0, 615.0, 618.0, 3.0)
RUNG_ID = SPEC.rung_id(SESSION)
ISO_WK = "2026-W33"


def _conn():
    """In-memory DB carrying the FULL migrated schema — SCHEMA plus the
    _maybe_add_column migrations init_db runs (here: mace_rung.entry_atm_iv).
    Reuses the real migration helper so the test can never drift from prod."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(dbmod.SCHEMA)
    dbmod._maybe_add_column(c, "mace_rung", "entry_atm_iv", "REAL")
    return c


def _live_row(conn, rung_id):
    return conn.execute(
        "SELECT rung_id, symbol, mark, spot, ts FROM mace_rung_live WHERE rung_id=?",
        (rung_id,)).fetchone()


# ── set_live_state ──────────────────────────────────────────────────────────

def test_set_live_state_roundtrip():
    store = ex.RungStore(_conn())
    store.set_live_state(RUNG_ID, "SPY", 0.71, 778.08, "2026-08-14T18:00:00Z")
    r = _live_row(store.conn, RUNG_ID)
    assert r is not None
    assert r["symbol"] == "SPY"
    assert r["mark"] == 0.71
    assert r["spot"] == 778.08
    assert r["ts"] == "2026-08-14T18:00:00Z"


def test_set_live_state_upsert_latest_wins():
    store = ex.RungStore(_conn())
    store.set_live_state(RUNG_ID, "SPY", 0.78, 777.0, "2026-08-14T18:00:00Z")
    store.set_live_state(RUNG_ID, "SPY", 0.71, 778.08, "2026-08-14T18:05:00Z")
    rows = store.conn.execute(
        "SELECT rung_id FROM mace_rung_live WHERE rung_id=?", (RUNG_ID,)).fetchall()
    assert len(rows) == 1                       # INSERT OR REPLACE — one row per rung
    r = _live_row(store.conn, RUNG_ID)
    assert r["mark"] == 0.71 and r["ts"].endswith("18:05:00Z")


def test_set_live_state_none_stored_as_null():
    """Unpriceable mark / missing spot -> NULL (the view renders '—'), never a
    fabricated number. The row still exists with a ts (freshness marker)."""
    store = ex.RungStore(_conn())
    store.set_live_state(RUNG_ID, "SPY", None, None, "2026-08-14T18:00:00Z")
    r = _live_row(store.conn, RUNG_ID)
    assert r is not None
    assert r["mark"] is None and r["spot"] is None
    assert r["ts"] == "2026-08-14T18:00:00Z"


# ── promote_open entry_atm_iv (A3) ──────────────────────────────────────────

def _submit(store):
    store.insert_submitting(RUNG_ID, SPEC, 1, entry_ts="2026-08-10T13:30:00Z",
                            entry_iso_week=ISO_WK, max_risk_usd=207.0)


def _rung_row(conn):
    return conn.execute(
        "SELECT status, credit_actual, entry_atm_iv FROM mace_rung WHERE rung_id=?",
        (RUNG_ID,)).fetchone()


def test_promote_open_writes_entry_iv_when_present():
    store = ex.RungStore(_conn())
    _submit(store)
    store.promote_open(RUNG_ID, credit_actual=0.93, entry_order_id="O1",
                       entry_ts="2026-08-10T13:31:00Z", entry_atm_iv=0.142)
    r = _rung_row(store.conn)
    assert r["status"] == RUNG_OPEN
    assert r["credit_actual"] == 0.93
    assert r["entry_atm_iv"] == 0.142


def test_promote_open_none_iv_leaves_null():
    """IV unavailable on the fill tick -> entry_atm_iv stays NULL (never a bogus
    0). The rung still promotes normally; the T+0 payoff falls back to daily IV."""
    store = ex.RungStore(_conn())
    _submit(store)
    store.promote_open(RUNG_ID, credit_actual=0.91, entry_order_id="O2",
                       entry_ts="2026-08-10T13:31:00Z", entry_atm_iv=None)
    r = _rung_row(store.conn)
    assert r["status"] == RUNG_OPEN
    assert r["credit_actual"] == 0.91
    assert r["entry_atm_iv"] is None


def test_promote_open_default_iv_is_none():
    """Back-compat: the crash-recovery drain path calls promote_open WITHOUT
    entry_atm_iv — it must default to None (no bogus write), not error."""
    store = ex.RungStore(_conn())
    _submit(store)
    store.promote_open(RUNG_ID, credit_actual=0.90, entry_order_id="O3",
                       entry_ts="2026-08-10T13:31:00Z")
    r = _rung_row(store.conn)
    assert r["status"] == RUNG_OPEN
    assert r["entry_atm_iv"] is None
