"""Render-layer of the unified PMCC decision: the tile helper (web/data.py) and
the Expert-panel helper (web/routes.py) read the SAME record, so they agree; the
tile transitions NO SIGNAL -> fresh once the post-open scan writes.
"""
import pytest

import trading_corp.persistence.db as db
from trading_corp.agents.divisions._pmcc_status import load_decision, record_pmcc_decision
from trading_corp.web.data import _build_pmcc_tile_status
from trading_corp.web.routes import _pmcc_analysis_from_record

T0 = "2026-07-24T10:00:00+00:00"
T_1H = "2026-07-24T11:00:00+00:00"
T_9H = "2026-07-24T19:00:00+00:00"


@pytest.fixture
def db_url(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    db.init_db(url)
    return url


def test_tile_and_panel_agree_from_same_record(db_url):
    record_pmcc_decision("HOOD", status="roll_short", source="scan", computed_at=T_1H,
                         db_url=db_url, urgency="elevated", confidence=0.8,
                         summary="s", rationale="r")
    now = "2026-07-24T12:00:00+00:00"  # +1h from the record -> fresh
    tile = _build_pmcc_tile_status("HOOD", db_url=db_url, now=now,
                                   cfg={"staleness_hours": 8})
    panel = _pmcc_analysis_from_record(load_decision("HOOD", db_url=db_url))
    assert tile["state"] == "fresh"
    # Same underlying decision -> tile label is the panel action, just display-cased.
    assert panel.action == "roll_short"
    assert tile["status_label"] == "ROLL SHORT" == panel.action.upper().replace("_", " ")
    assert tile["urgency"] == panel.urgency == "elevated"


def test_tile_no_signal_then_scan_populates(db_url):
    cfg = {"staleness_hours": 8, "no_signal_label": "awaiting scan"}
    now = "2026-07-24T11:30:00+00:00"
    before = _build_pmcc_tile_status("SMR", db_url=db_url, now=now, cfg=cfg)
    assert before["state"] == "none" and before["status_label"] is None
    assert before["no_signal_label"] == "awaiting scan"
    # post-open scan writes -> tile is populated + fresh
    record_pmcc_decision("SMR", status="hold", source="scan", computed_at=T_1H, db_url=db_url)
    after = _build_pmcc_tile_status("SMR", db_url=db_url, now=now, cfg=cfg)
    assert after["state"] == "fresh" and after["status_label"] == "HOLD"


def test_tile_stale_render(db_url):
    record_pmcc_decision("TSLA", status="watch", source="scan", computed_at=T0, db_url=db_url)
    tile = _build_pmcc_tile_status("TSLA", db_url=db_url, now=T_9H,
                                   cfg={"staleness_hours": 8, "stale_label": "stale"})
    assert tile["state"] == "stale"
    assert tile["stale_label"] == "stale"
    assert tile["age_h"] == pytest.approx(9.0)


def test_no_signal_panel_and_reader_render(db_url):
    # The reader helper produces a no-signal panel when there's no record...
    from trading_corp.web.routes import _pmcc_no_signal_panel, _pmcc_status_banner
    panel = _pmcc_no_signal_panel("robinhood_pmcc", "NVDA", {"no_signal_label": "awaiting scan"})
    assert "awaiting scan" in panel and "Re-analyze" in panel
    # ...and a stale banner carries the age + a Re-analyze control.
    banner = _pmcc_status_banner("robinhood_pmcc", "TSLA", state="stale",
                                 age_h=9.0, source="scan")
    assert "stale as of 9h" in banner and "Re-analyze" in banner
