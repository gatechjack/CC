"""Tests for the polymarket_copy_trading metrics-epoch helpers.

`_get_polymarket_metrics_epoch` reads agent_state(polymarket_copy_trader,
metrics_epoch), validates the stored value parses as an ISO-8601
timestamp, returns the string verbatim or None on any invalid/missing
case. The returned value is f-string-interpolated into SQL by
`_polymarket_cutoff_clause`, so the validation is the injection gate —
not the write path's trust.

Covers:
  - epoch-set → clause emits the WHERE fragment
  - epoch unset (slot absent) → returns None → clause emits ''
  - epoch set to JSON null (set_agent_state(..., None)) → returns None
  - non-ISO string ('not-a-date') → returns None + warns
  - injection-shaped string ("'; DROP TABLE …; --") → returns None
  - non-string value (int) → returns None
  - DELETE-based unset (canonical reversibility path) → returns None
  - Clause column parameterization for audit_event (a.ts + JSON-extracted
    division) renders without quoting issues
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from trading_corp.persistence import db
from trading_corp.web.data import (
    _get_polymarket_metrics_epoch,
    _polymarket_cutoff_clause,
)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    p = tmp_path / "metrics_epoch.db"
    url = f"sqlite:///{p.as_posix()}"
    db.init_db(db_url=url)
    return url


# ── _polymarket_cutoff_clause: pure-function tests ────────────────────────


def test_cutoff_clause_none_returns_empty_string():
    assert _polymarket_cutoff_clause(None) == ""


def test_cutoff_clause_empty_string_returns_empty_string():
    # Defensive: empty string is falsy → no-op path, same as None.
    assert _polymarket_cutoff_clause("") == ""


def test_cutoff_clause_round_trips_default_column():
    out = _polymarket_cutoff_clause("2026-05-23T06:23:09+00:00")
    assert out == (
        " AND NOT (division='polymarket_copy_trading' "
        "AND entry_ts < '2026-05-23T06:23:09+00:00')"
    )


def test_cutoff_clause_audit_event_column_parameterization():
    """audit_event surface uses a.ts + JSON-extracted division — the
    helper must accept both and render a valid WHERE fragment."""
    out = _polymarket_cutoff_clause(
        "2026-05-23T06:23:09+00:00",
        ts_col="a.ts",
        div_col="COALESCE(json_extract(a.payload_json,'$.division'),'polymarket_arbitrage')",
    )
    assert "a.ts < '2026-05-23T06:23:09+00:00'" in out
    assert "json_extract(a.payload_json,'$.division')" in out
    assert "polymarket_copy_trading" in out


def test_cutoff_clause_round_trips_value_verbatim():
    """The clause embeds the value as-passed; no normalization. Validation
    is `_get_polymarket_metrics_epoch`'s job, not the clause's."""
    out = _polymarket_cutoff_clause("2026-05-23T06:23:09.123456+00:00")
    assert "2026-05-23T06:23:09.123456+00:00" in out


# ── _get_polymarket_metrics_epoch: full validation matrix ─────────────────


def test_epoch_helper_missing_slot_returns_none(db_url):
    # No agent_state row at all.
    assert _get_polymarket_metrics_epoch(db_url) is None


def test_epoch_helper_valid_iso_returns_string_verbatim(db_url):
    db.set_agent_state(
        "polymarket_copy_trader", "metrics_epoch",
        "2026-05-23T06:23:09+00:00", db_url=db_url,
    )
    assert _get_polymarket_metrics_epoch(db_url) == "2026-05-23T06:23:09+00:00"


def test_epoch_helper_iso_with_microseconds(db_url):
    db.set_agent_state(
        "polymarket_copy_trader", "metrics_epoch",
        "2026-05-23T06:23:09.123456+00:00", db_url=db_url,
    )
    assert (
        _get_polymarket_metrics_epoch(db_url)
        == "2026-05-23T06:23:09.123456+00:00"
    )


def test_epoch_helper_naive_iso_accepted(db_url):
    """datetime.fromisoformat accepts naive ISO; we don't impose tz-aware
    here. Validation is purely syntactic typecheck."""
    db.set_agent_state(
        "polymarket_copy_trader", "metrics_epoch",
        "2026-05-23T06:23:09", db_url=db_url,
    )
    assert _get_polymarket_metrics_epoch(db_url) == "2026-05-23T06:23:09"


def test_epoch_helper_non_iso_string_rejected(db_url, caplog):
    db.set_agent_state(
        "polymarket_copy_trader", "metrics_epoch",
        "not-a-date", db_url=db_url,
    )
    with caplog.at_level(logging.WARNING):
        result = _get_polymarket_metrics_epoch(db_url)
    assert result is None
    assert any("metrics_epoch" in r.message for r in caplog.records)


def test_epoch_helper_injection_attempt_rejected(db_url, caplog):
    """The single most important validation case: a value attempting to
    break out of the SQL literal MUST be rejected by the ISO parse, not
    silently passed into the clause."""
    payload = "'; DROP TABLE polymarket_round_trips; --"
    db.set_agent_state(
        "polymarket_copy_trader", "metrics_epoch", payload, db_url=db_url,
    )
    with caplog.at_level(logging.WARNING):
        result = _get_polymarket_metrics_epoch(db_url)
    assert result is None
    # And the clause must therefore be empty too — no SQL ever sees the payload.
    assert _polymarket_cutoff_clause(result) == ""


def test_epoch_helper_non_string_rejected(db_url):
    """Numeric value at the slot → reject. Reasonable defense against
    bad write paths that coerce-then-store."""
    db.set_agent_state(
        "polymarket_copy_trader", "metrics_epoch", 12345, db_url=db_url,
    )
    assert _get_polymarket_metrics_epoch(db_url) is None


def test_epoch_helper_json_null_returns_none(db_url):
    """set_agent_state(..., None) writes JSON null. load_agent_state
    returns (None, ts) — the helper must treat the inner None as unset.
    This is the fallback unset path; the canonical path is DELETE."""
    db.set_agent_state(
        "polymarket_copy_trader", "metrics_epoch", None, db_url=db_url,
    )
    assert _get_polymarket_metrics_epoch(db_url) is None


def test_epoch_helper_empty_string_returns_none(db_url):
    db.set_agent_state(
        "polymarket_copy_trader", "metrics_epoch", "", db_url=db_url,
    )
    assert _get_polymarket_metrics_epoch(db_url) is None


def test_epoch_helper_targeted_delete_canonical_unset(db_url):
    """The canonical reversibility path: targeted DELETE returns to
    no-op. Set the slot, verify it returns the value, DELETE the row,
    verify the helper returns None."""
    db.set_agent_state(
        "polymarket_copy_trader", "metrics_epoch",
        "2026-05-23T06:23:09+00:00", db_url=db_url,
    )
    assert _get_polymarket_metrics_epoch(db_url) is not None

    # Targeted DELETE — the documented unset path
    with db.connect(db_url) as conn:
        conn.execute(
            "DELETE FROM agent_state WHERE agent='polymarket_copy_trader' "
            "AND key='metrics_epoch'"
        )

    assert _get_polymarket_metrics_epoch(db_url) is None


def test_epoch_helper_clause_round_trip_through_helper(db_url):
    """End-to-end: set epoch via agent_state → helper returns value →
    clause renders correctly. Proves the validation gate doesn't corrupt
    the value en route to the SQL."""
    db.set_agent_state(
        "polymarket_copy_trader", "metrics_epoch",
        "2026-05-23T06:23:09+00:00", db_url=db_url,
    )
    epoch = _get_polymarket_metrics_epoch(db_url)
    clause = _polymarket_cutoff_clause(epoch)
    assert clause == (
        " AND NOT (division='polymarket_copy_trading' "
        "AND entry_ts < '2026-05-23T06:23:09+00:00')"
    )
