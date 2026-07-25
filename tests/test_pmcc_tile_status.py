"""Unified PMCC tile/expert decision record — precedence + freshness.

Covers the VERIFY list at the logic layer: expert overwrites scan; a later scan
does NOT overwrite a fresh manual expert; scan populates a stale/blank tile;
>8h -> STALE; no status this session -> NO SIGNAL; tile & expert agree when both
fresh (they read the same record); the 8h window is configurable.
"""
import pytest

import trading_corp.persistence.db as db
from trading_corp.agents.divisions._pmcc_status import (
    age_hours,
    classify_freshness,
    decision_key,
    load_decision,
    record_pmcc_decision,
    should_write,
)

T0 = "2026-07-24T10:00:00+00:00"
T_1H = "2026-07-24T11:00:00+00:00"   # +1h
T_8H = "2026-07-24T18:00:00+00:00"   # +8h (exactly the window)
T_9H = "2026-07-24T19:00:00+00:00"   # +9h (past the window)


# ── should_write: precedence (pure) ────────────────────────────────────────

def test_expert_always_overwrites():
    assert should_write(None, "expert", T0) is True
    assert should_write({"source": "scan", "computed_at": T0}, "expert", T_1H) is True
    # expert overwrites even a still-fresh expert (deliberate re-analyze)
    assert should_write({"source": "expert", "computed_at": T0}, "expert", T_1H) is True


def test_scan_populates_absent_or_scan_sourced():
    assert should_write(None, "scan", T0) is True
    assert should_write({"source": "scan", "computed_at": T0}, "scan", T_1H) is True


def test_scan_does_not_clobber_fresh_expert():
    fresh_expert = {"source": "expert", "computed_at": T0}
    assert should_write(fresh_expert, "scan", T_1H) is False   # 1h < 8h -> protected


def test_scan_repopulates_after_expert_ages_out():
    aged_expert = {"source": "expert", "computed_at": T0}
    assert should_write(aged_expert, "scan", T_9H) is True     # 9h >= 8h
    assert should_write(aged_expert, "scan", T_8H) is True     # exactly 8h -> stale


def test_precedence_window_is_configurable():
    fresh_expert = {"source": "expert", "computed_at": T0}
    # 1h-old expert is "stale" under a 0.5h window -> scan may repopulate.
    assert should_write(fresh_expert, "scan", T_1H, staleness_hours=0.5) is True
    # ...but protected under a 4h window.
    assert should_write(fresh_expert, "scan", T_1H, staleness_hours=4) is False


# ── classify_freshness: render state (pure) ─────────────────────────────────

def test_no_signal_when_absent_or_statusless():
    assert classify_freshness(None, T_1H) == "none"
    assert classify_freshness({}, T_1H) == "none"
    assert classify_freshness({"source": "scan"}, T_1H) == "none"   # no status


def test_fresh_within_window():
    rec = {"status": "hold", "computed_at": T0}
    assert classify_freshness(rec, T_1H) == "fresh"


def test_stale_past_window():
    rec = {"status": "hold", "computed_at": T0}
    assert classify_freshness(rec, T_9H) == "stale"
    assert classify_freshness(rec, T_8H) == "stale"               # >= is stale


def test_unparseable_ts_is_stale():
    assert classify_freshness({"status": "hold", "computed_at": "garbage"}, T_1H) == "stale"


def test_freshness_window_is_configurable():
    rec = {"status": "hold", "computed_at": T0}
    assert classify_freshness(rec, T_1H, staleness_hours=0.5) == "stale"
    assert classify_freshness(rec, T_1H, staleness_hours=4) == "fresh"


def test_age_hours():
    assert age_hours({"computed_at": T0}, T_9H) == pytest.approx(9.0)
    assert age_hours(None, T0) is None
    assert age_hours({"computed_at": "garbage"}, T0) is None


# ── record_pmcc_decision + load_decision: end-to-end via agent_state ─────────

@pytest.fixture
def db_url(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    db.init_db(url)
    return url


def test_scan_then_load_roundtrip(db_url):
    assert record_pmcc_decision("AAPL", status="hold", source="scan",
                                computed_at=T0, db_url=db_url) is True
    rec = load_decision("AAPL", db_url=db_url)
    assert rec["status"] == "hold" and rec["source"] == "scan"
    assert rec["symbol"] == "AAPL"
    # symbol-normalized key
    assert load_decision("aapl", db_url=db_url)["status"] == "hold"


def test_no_record_is_no_signal(db_url):
    # A symbol the scan aborted on (or pre-open, before any scan) -> no record.
    assert load_decision("NVDA", db_url=db_url) is None
    assert classify_freshness(load_decision("NVDA", db_url=db_url), T_1H) == "none"


def test_expert_overwrites_scan_then_scan_protected_then_repopulates(db_url):
    # scan writes -> expert overwrites -> fresh scan is BLOCKED -> aged scan repopulates.
    record_pmcc_decision("MSTR", status="watch", source="scan", computed_at=T0, db_url=db_url)
    assert record_pmcc_decision("MSTR", status="roll_short", source="expert",
                                computed_at=T0, db_url=db_url) is True
    assert load_decision("MSTR", db_url=db_url)["source"] == "expert"

    # A scheduled scan 1h later must NOT clobber the fresh manual expert.
    assert record_pmcc_decision("MSTR", status="hold", source="scan",
                                computed_at=T_1H, db_url=db_url) is False
    r = load_decision("MSTR", db_url=db_url)
    assert r["source"] == "expert" and r["status"] == "roll_short"

    # 9h later the expert has aged out -> the next scan repopulates.
    assert record_pmcc_decision("MSTR", status="hold", source="scan",
                                computed_at=T_9H, db_url=db_url) is True
    r = load_decision("MSTR", db_url=db_url)
    assert r["source"] == "scan" and r["status"] == "hold"


def test_scan_populates_stale_tile(db_url):
    # A stale scan record is freely overwritten by a newer scan.
    record_pmcc_decision("OPEN", status="watch", source="scan", computed_at=T0, db_url=db_url)
    assert record_pmcc_decision("OPEN", status="roll_short", source="scan",
                                computed_at=T_9H, db_url=db_url) is True
    assert load_decision("OPEN", db_url=db_url)["status"] == "roll_short"


def test_tile_and_expert_read_same_record(db_url):
    # Both surfaces load the SAME record -> identical status + freshness => agree.
    record_pmcc_decision("RKLB", status="roll_short_early", source="expert",
                         computed_at=T0, db_url=db_url,
                         urgency="urgent", confidence=0.9, summary="s", rationale="r")
    tile_rec = load_decision("RKLB", db_url=db_url)
    panel_rec = load_decision("RKLB", db_url=db_url)
    assert tile_rec["status"] == panel_rec["status"] == "roll_short_early"
    assert classify_freshness(tile_rec, T_1H) == classify_freshness(panel_rec, T_1H) == "fresh"
    # full analysis text persisted for the panel's stale-state render
    assert panel_rec["summary"] == "s" and panel_rec["rationale"] == "r"
    assert panel_rec["urgency"] == "urgent" and panel_rec["confidence"] == 0.9


def test_decision_key_normalizes_symbol():
    assert decision_key("aapl") == decision_key("AAPL") == "latest_decision:AAPL"
