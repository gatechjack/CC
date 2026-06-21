"""Tests for the PEAD dashboard observability persistence
(`persistence/pead_observability.py` + the two SCHEMA tables)."""
from __future__ import annotations

import pytest

from trading_corp.persistence import pead_observability as obs
from trading_corp.persistence.db import init_db


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(url)  # creates data_feed_status + scan_evaluation (idempotent SCHEMA)
    return url


# ── feed status ────────────────────────────────────────────────────────────
def test_feed_status_upsert_load_and_down_preserves_last_ok(db):
    obs.upsert_feed_status("eodhd", "live", ok=True, detail="ok", db_url=db)
    s = obs.load_feed_status(db)
    assert s["eodhd"]["status"] == "live"
    assert s["eodhd"]["last_ok_ts"] is not None

    obs.upsert_feed_status("eodhd", "down", detail="boom", db_url=db)
    s = obs.load_feed_status(db)
    assert s["eodhd"]["status"] == "down"
    assert s["eodhd"]["last_ok_ts"] is not None      # preserved across a down write
    assert s["eodhd"]["detail"] == "boom"


def test_feed_status_coerces_unknown_state_to_down(db):
    obs.upsert_feed_status("eodhd", "weird", db_url=db)
    assert obs.load_feed_status(db)["eodhd"]["status"] == "down"


# ── scan-rejection tally ───────────────────────────────────────────────────
def test_scan_tally_reconciles_scanned_minus_qualified(db):
    sess = "2026-06-21T12:00:00+00:00"
    obs.insert_scan_evaluation(sess, "AAA", "passed", db_url=db)
    obs.insert_scan_evaluation(sess, "BBB", "passed", db_url=db)
    obs.insert_scan_evaluation(sess, "CCC", "rejected", reason_code="below-min-cap", db_url=db)
    obs.insert_scan_evaluation(sess, "DDD", "rejected", reason_code="below-min-cap", db_url=db)
    obs.insert_scan_evaluation(sess, "EEE", "rejected", reason_code="financial/utility", db_url=db)

    t = obs.scan_rejection_tally(db_url=db)
    assert t["session_ts"] == sess
    assert (t["scanned"], t["qualified"], t["rejected"]) == (5, 2, 3)
    assert t["by_reason"] == {"below-min-cap": 2, "financial/utility": 1}
    # INVARIANT the dashboard relies on:
    assert t["scanned"] - t["qualified"] == t["rejected"] == sum(t["by_reason"].values())


def test_scan_tally_empty_is_graceful(db):
    assert obs.scan_rejection_tally(db_url=db) == {
        "session_ts": None, "scanned": 0, "qualified": 0, "rejected": 0, "by_reason": {},
    }


def test_latest_session_is_newest(db):
    obs.insert_scan_evaluation("2026-06-20T00:00:00+00:00", "X", "passed", db_url=db)
    obs.insert_scan_evaluation("2026-06-21T00:00:00+00:00", "Y", "rejected",
                               reason_code="guidance-cut", db_url=db)
    assert obs.latest_scan_session(db) == "2026-06-21T00:00:00+00:00"
    t = obs.scan_rejection_tally(db_url=db)   # defaults to latest
    assert t["session_ts"] == "2026-06-21T00:00:00+00:00"
    assert (t["scanned"], t["rejected"]) == (1, 1)
