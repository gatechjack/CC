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


def test_status_banner_fresh_has_no_in_sync_claim():
    # FIX 3: the false unconditional "tile & panel in sync" string is GONE; the
    # fresh banner shows only the factual latest-decision line.
    from trading_corp.web.routes import _pmcc_status_banner
    banner = _pmcc_status_banner("robinhood_pmcc", "TSLA", state="fresh",
                                 age_h=0.0, source="expert")
    assert "in sync" not in banner
    assert "latest expert decision" in banner
    assert "Re-analyze" in banner        # control still present


# ── FIX 2: out-of-band tile-badge refresh on execute / Re-analyze ────────────

def _templates():
    """A Jinja2Templates pointed at the real templates dir (module-relative so
    the test doesn't depend on CWD)."""
    from pathlib import Path
    from starlette.templating import Jinja2Templates
    import trading_corp.web.routes as routes_mod
    tdir = Path(routes_mod.__file__).parent / "templates"
    return Jinja2Templates(directory=str(tdir))


def _deps(db_url):
    from types import SimpleNamespace
    return SimpleNamespace(
        db_url=db_url,
        pmcc_agent=SimpleNamespace(_cfg={"tile_status": {"staleness_hours": 8}}),
    )


def test_oob_tile_badge_fragment_reflects_executed_hold(db_url):
    # After an execute writes an 'executed' HOLD, the OOB fragment refreshes the
    # left-rail badge to HOLD without a reload: correct target id + oob marker.
    from datetime import datetime, timezone
    from trading_corp.web.routes import _pmcc_tile_badge_oob
    now_iso = datetime.now(timezone.utc).isoformat()   # fresh vs real now
    record_pmcc_decision("TSLA", status="hold", source="executed",
                         computed_at=now_iso, db_url=db_url)
    frag = _pmcc_tile_badge_oob(_templates(), _deps(db_url), "TSLA")
    assert 'id="pmcc-badge-TSLA"' in frag
    assert 'hx-swap-oob="true"' in frag
    assert "HOLD" in frag
    # not the stale/no-signal variants
    assert "awaiting scan" not in frag


def test_oob_tile_badge_fragment_no_signal_when_absent(db_url):
    # No record for the symbol -> OOB fragment carries the 'awaiting scan' badge
    # (still a valid refresh; HTMX no-ops if the row isn't in the DOM).
    from trading_corp.web.routes import _pmcc_tile_badge_oob
    frag = _pmcc_tile_badge_oob(_templates(), _deps(db_url), "NVDA")
    assert 'id="pmcc-badge-NVDA"' in frag and 'hx-swap-oob="true"' in frag
    assert "awaiting scan" in frag


def test_oob_helper_never_raises_on_bad_deps():
    # Best-effort contract: a broken deps object returns '' rather than blowing
    # up the execute/Re-analyze response.
    from trading_corp.web.routes import _pmcc_tile_badge_oob
    assert _pmcc_tile_badge_oob(None, object(), "TSLA") == ""


def test_badge_partial_renders_stale_state():
    # The 3rd badge state (fresh + none are covered by the OOB tests above).
    t = _templates()
    us = {"state": "stale", "stale_label": "stale", "source": "scan", "age_h": 9.0,
          "status_label": None, "urgency": "routine", "no_signal_label": "awaiting scan"}
    html = t.get_template("partials/_pmcc_badge.html").render(us=us)
    assert "stale" in html and "9h old" in html


def test_pmcc_row_and_badge_templates_compile():
    # Guards the template edits: both compile (Jinja parse) without error, and
    # the row wraps the badge in the OOB target id.
    t = _templates()
    t.get_template("partials/_pmcc_badge.html")
    src = (t.env.loader.get_source(t.env, "partials/pmcc_pair.html"))[0]
    assert 'id="pmcc-badge-{{ pair.underlying }}"' in src
    assert 'partials/_pmcc_badge.html' in src   # row includes the shared partial
