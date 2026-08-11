"""Phase-1 tests: MACE economic-event calendar (seeds/rule/manual, idempotent).

Seeds are checked against the real config/macro_calendar.yaml (also guards that
file); the LPR rule is checked with an injected `today` for determinism.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from trading_corp.mace import calendar as cal
from trading_corp.persistence import db

ROOT = Path(__file__).resolve().parents[1]
MACRO_YAML = ROOT / "config" / "macro_calendar.yaml"


@pytest.fixture
def conn(tmp_path):
    url = f"sqlite:///{(tmp_path / 'mace.db').as_posix()}"
    with db.connect(url) as c:
        c.executescript(db.SCHEMA)
        yield c


# ── write primitives ────────────────────────────────────────────────────

def test_add_event_idempotent(conn):
    assert cal.add_event(conn, event_type="OPEC", event_date="2026-09-07",
                         source=cal.SOURCE_MANUAL, symbol_scope="USO") is True
    assert cal.add_event(conn, event_type="OPEC", event_date="2026-09-07",
                         source=cal.SOURCE_MANUAL, symbol_scope="USO") is False
    rows = cal.list_events(conn, source="manual")
    assert len(rows) == 1 and rows[0]["event_type"] == "OPEC"


def test_add_event_normalizes_case(conn):
    cal.add_event(conn, event_type="opec", event_date=date(2026, 9, 7),
                  source=cal.SOURCE_MANUAL, symbol_scope="uso")
    r = cal.list_events(conn)[0]
    assert r["event_type"] == "OPEC" and r["symbol_scope"] == "USO"


def test_add_event_bad_source_raises(conn):
    with pytest.raises(ValueError):
        cal.add_event(conn, event_type="X", event_date="2026-01-01", source="bogus")


def test_remove_event(conn):
    cal.add_event(conn, event_type="OPEC", event_date="2026-09-07",
                  source=cal.SOURCE_MANUAL, symbol_scope="USO")
    assert cal.remove_event(conn, event_type="OPEC", event_date="2026-09-07",
                            symbol_scope="USO") == 1
    assert cal.list_events(conn) == []


# ── seeds ────────────────────────────────────────────────────────────────

def test_seed_from_macro_counts(conn):
    rep = cal.seed_from_macro_calendar(conn, MACRO_YAML)
    assert rep["by_type"] == {"FOMC": 8, "CPI": 12, "NFP": 12}
    assert rep["inserted"] == 32 and rep["unclassified"] == 0


def test_seed_idempotent(conn):
    cal.seed_from_macro_calendar(conn, MACRO_YAML)
    rep2 = cal.seed_from_macro_calendar(conn, MACRO_YAML)
    assert rep2["inserted"] == 0 and rep2["skipped"] == 32


def test_seed_scope_and_source(conn):
    cal.seed_from_macro_calendar(conn, MACRO_YAML)
    fomc = cal.list_events(conn, event_type="FOMC")
    assert len(fomc) == 8
    assert all(r["symbol_scope"] == "ALL" and r["source"] == "seed" for r in fomc)


def test_classify_macro():
    assert cal._classify_macro("FOMC Rate Decision (Jan 27-28)") == "FOMC"
    assert cal._classify_macro("US CPI - Dec 2025") == "CPI"
    assert cal._classify_macro("US Employment Situation (NFP) - Dec") == "NFP"
    assert cal._classify_macro("Random Fed speaker") is None


def test_macro_ts_to_et_date():
    assert cal._macro_ts_to_et_date("2026-09-16T18:00:00Z") == date(2026, 9, 16)
    assert cal._macro_ts_to_et_date("2026-08-12T12:30:00Z") == date(2026, 8, 12)


# ── LPR rule ─────────────────────────────────────────────────────────────

def test_roll_weekend_forward():
    assert cal._roll_weekend_forward(date(2026, 8, 20)) == date(2026, 8, 20)   # Thu
    assert cal._roll_weekend_forward(date(2026, 9, 20)) == date(2026, 9, 21)   # Sun->Mon
    assert cal._roll_weekend_forward(date(2027, 2, 20)) == date(2027, 2, 22)   # Sat->Mon


def test_generate_lpr_weekend_rolls(conn):
    rep = cal.generate_lpr_fix_rule(conn, today=date(2026, 8, 1), months=13)
    assert rep["inserted"] == 13
    d = set(rep["dates"])
    assert {"2026-08-20", "2026-09-21", "2026-12-21", "2027-02-22"} <= d
    rows = cal.list_events(conn, event_type="LPR_FIX")
    assert len(rows) == 13 and all(r["source"] == "rule" for r in rows)


def test_generate_lpr_idempotent(conn):
    cal.generate_lpr_fix_rule(conn, today=date(2026, 8, 1), months=3)
    rep = cal.generate_lpr_fix_rule(conn, today=date(2026, 8, 1), months=3)
    assert rep["inserted"] == 0 and rep["skipped"] == 3


# ── read filters + weekly refresh ────────────────────────────────────────

def test_list_events_filters(conn):
    cal.seed_from_macro_calendar(conn, MACRO_YAML)
    cal.generate_lpr_fix_rule(conn, today=date(2026, 8, 1), months=13)
    aug = cal.list_events(conn, start="2026-08-01", end="2026-08-31")
    types = {r["event_type"] for r in aug}
    assert {"CPI", "NFP", "LPR_FIX"} <= types
    only_rule = cal.list_events(conn, source="rule")
    assert only_rule and all(r["event_type"] == "LPR_FIX" for r in only_rule)


def test_weekly_refresh_preserves_manual(conn):
    rep = cal.weekly_refresh(conn, MACRO_YAML, today=date(2026, 8, 1), lpr_months=13)
    assert rep["seed"]["inserted"] == 32 and rep["lpr"]["inserted"] == 13
    cal.add_event(conn, event_type="OPEC", event_date="2026-09-07",
                  source=cal.SOURCE_MANUAL)
    rep2 = cal.weekly_refresh(conn, MACRO_YAML, today=date(2026, 8, 1), lpr_months=13)
    assert rep2["seed"]["inserted"] == 0                 # already seeded
    assert cal.list_events(conn, event_type="OPEC")      # manual row untouched
