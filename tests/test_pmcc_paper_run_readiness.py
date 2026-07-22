"""Tests for the PMCC paper-run readiness gate.

Mirrors the readiness tests in `tests/test_paper_run_tooling.py`. Runs against the
repo's real production config (the script's job is verifying those configs +
gate wiring are valid); the network SOFT checks are skipped in the sandbox.
"""
from __future__ import annotations

from trading_corp.persistence import db
from trading_corp.scripts.pmcc_paper_run_readiness import (
    KNOWN_LIMITATIONS,
    ReadinessReport,
    _format_report,
    run_readiness_checks,
)


def test_pmcc_readiness_all_blocking_pass_on_production_config():
    """Against the repo's actual production config + wiring, every blocking check
    passes. The SOFT VIX/earnings checks are skipped (no network in the sandbox)."""
    report = run_readiness_checks(skip_network=True)
    failed = [c for c in report.checks if not c.ok and c.blocking]
    assert failed == [], (
        "blocking readiness checks failed:\n"
        + "\n".join(f"  [{c.name}] {c.detail}" for c in failed)
    )
    assert report.all_blocking_passed is True


def test_pmcc_readiness_formatter_and_known_limitations_block():
    """The formatter labels every BLOCK row, emits READY when all blocking pass,
    and ALWAYS prints the KNOWN LIMITATIONS block (accepted state, not a warning
    that toggles) with every limitation title."""
    report = run_readiness_checks(skip_network=True)
    text = _format_report(report)
    assert "PMCC (Poor-Man's Covered Call) - Paper-Run Readiness Check" in text
    assert "[BLOCK]" in text
    assert "READY" in text
    assert "NOT READY" not in text
    assert "auto_execute is FALSE" in text        # the paper-mode invariant is a BLOCK row
    assert "KNOWN LIMITATIONS" in text
    for title, _desc in KNOWN_LIMITATIONS:
        assert title in text


def test_pmcc_readiness_handles_db_path_override(tmp_path):
    """A non-production db_url routes the agent_state + audit_event checks to a
    fresh schema'd database and still passes all blocking checks."""
    from trading_corp.persistence.db import SCHEMA
    test_db = f"sqlite:///{(tmp_path / 'probe.db').as_posix()}"
    with db.connect(test_db) as conn:
        conn.executescript(SCHEMA)
    report = run_readiness_checks(db_url=test_db, skip_network=True)
    assert report.all_blocking_passed is True
