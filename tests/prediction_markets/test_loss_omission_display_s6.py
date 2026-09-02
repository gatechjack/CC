"""Stage 5 surfacing (2026-09-01): loss-omission % BESIDE win% on the Prospects LIST + the Analyze card, sourced from
the per-whale grounding cache Analyze populates. The load-bearing adversarial cases (Jack): (1) an UN-grounded whale
reads UNKNOWN, NEVER 0% (a 0 that means 'nobody checked' is the safety-check-that-stops-checking shape); (2) the
COVERAGE bound rides WITH the omission so '94% @ 96% cov' and '94% @ 31% cov (floor)' are not flattened into one
number; (3) a VERIFIED zero (grounded, a_only=0) is DISTINCT from unknown. Offline, PM DB only.
"""
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, farm

NOW = 1_700_000_000
CAND = "0xcandidatewhaleaaaaaaaaaaaaaaaaaaaaaaaa"


def _mk(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts) "
                     "VALUES('0xpinnedwhale','mlb',?,1,1,1)", (farm.PINNED,))   # makes 'mlb' an active tile
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    from trading_corp.prediction_markets.web.app import app
    cl = TestClient(app); cl.headers.update({"Remote-User": "jack"})
    return cl, p


def _add_cand(p, wallet, *, roi=0.2, n=60, name="Cand"):
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts,source) "
                     "VALUES(?, 'mlb', ?, 1,1,1,'search')", (wallet, farm.CANDIDATE))
        conn.execute("INSERT OR REPLACE INTO pm_whale(wallet,user_name,backfill_complete,last_refresh_ts) "
                     "VALUES(?,?,1,?)", (wallet, name, NOW))
        conn.execute("INSERT INTO pm_category_stats(wallet,category,n_resolved,roi,win_rate,net_realized_pnl,updated_ts) "
                     "VALUES(?, 'mlb', ?, ?, 0.90, 100.0, 1)", (wallet, n, roi))   # a 90% screen -> exactly the F-1 lie


def _ground_row(p, wallet, *, omission, coverage, a_only, trunc, hw=5, hl=52, ts=NOW, category="mlb"):
    with db.connect(p) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pm_loss_grounding_cache(wallet,category,honest_wins,honest_losses,a_only_losses,"
            "loss_omission_pct,coverage_pct,activity_truncated,n_activity_held_resolved,completeness,grounded_ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (wallet, category, hw, hl, a_only, omission, coverage, trunc, (a_only or 0) + hw + hl,
             "windowed(activity truncated -- a_only losses are a lower bound)" if trunc else
             "complete(activity exhausted within window)", ts))
        conn.commit()


# ── the migration + the pure cell logic (the unknown-vs-zero core) ──────────────────────────────────────
def test_schema_head_and_cache_table(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    assert db.SCHEMA_HEAD >= 17
    with db.connect(p) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pm_loss_grounding_cache'"
                            ).fetchone() is not None


def test_loss_omission_cell_none_is_unknown_never_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("PM_DB_PATH", str(tmp_path / "pm.db"))
    from trading_corp.prediction_markets.web import app as appmod
    assert appmod._loss_omission_cell(None, NOW) == {"known": False}   # UNKNOWN -- carries NO omission number at all
    e = {"loss_omission_pct": 0.94, "coverage_pct": 0.96, "a_only_losses": 47, "honest_wins": 5,
         "honest_losses": 52, "activity_truncated": 1, "grounded_ts": NOW - 2 * 86400}
    c = appmod._loss_omission_cell(e, NOW)
    assert c["known"] and c["omission_pct"] == 0.94 and c["coverage_pct"] == 0.96 and c["truncated"] is True
    assert c["floor"] is True and abs(c["age_days"] - 2.0) < 1e-6   # truncated -> floor
    # floor keys on truncation OR low coverage, independently:
    assert appmod._loss_omission_cell({"coverage_pct": 0.31, "activity_truncated": 0, "grounded_ts": NOW}, NOW)["floor"] is True
    assert appmod._loss_omission_cell({"coverage_pct": 0.98, "activity_truncated": 0, "grounded_ts": NOW}, NOW)["floor"] is False


def test_report_loss_is_floor_property(tmp_path):
    """The report's loss_is_floor recomputes from stored fields (survives the cache round-trip) and keys on truncation
    OR low coverage -- NOT truncation alone."""
    import dataclasses as dc
    from trading_corp.prediction_markets import analyze, db
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as c:
        base = analyze.build_pm_analysis(c, "0xw", "mlb", now_ts=NOW)          # ungrounded base report
    assert dc.replace(base, loss_grounded=False).loss_is_floor is False
    assert dc.replace(base, loss_grounded=True, loss_completeness="complete(x)", loss_coverage_pct=0.98).loss_is_floor is False
    assert dc.replace(base, loss_grounded=True, loss_completeness="complete(x)", loss_coverage_pct=0.31).loss_is_floor is True
    assert dc.replace(base, loss_grounded=True, loss_completeness="windowed(lower bound)", loss_coverage_pct=1.0).loss_is_floor is True


# ── the Prospects LIST render ───────────────────────────────────────────────────────────────────────────
def test_prospects_unknown_never_zero(monkeypatch, tmp_path):
    """A candidate never Analyzed has NO grounding row -> its win% cell reads UNKNOWN and its win% is caveated; it must
    NOT render a 0% omission (the worst display: a 0 that means 'nobody looked')."""
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND)                                     # never Analyzed -> no cache row
    html = cl.get("/farm/mlb").text
    assert "pm-omit-unknown" in html and "omission&nbsp;unknown" in html
    assert "pm-winpct-caveated" in html                   # the 90% win% is visibly not-to-be-trusted
    assert "pm-omit-ok" not in html and "verified" not in html   # NO fabricated "0% verified" for an unchecked whale
    assert "pm-omit-bad" not in html                      # and no fabricated omission figure


def test_prospects_material_omission_carries_coverage_not_flattened(monkeypatch, tmp_path):
    """Two whales at the SAME 94% omission but different coverage must render as DIFFERENT claims -- the low-coverage
    windowed one is marked a FLOOR. The omission rides in the win% cell (beside the number it corrupts)."""
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, "0xhicov", roi=0.30, n=60, name="HiCov")
    _add_cand(p, "0xlocov", roi=0.20, n=60, name="LoCov")
    _ground_row(p, "0xhicov", omission=0.94, coverage=0.96, a_only=47, trunc=0)
    _ground_row(p, "0xlocov", omission=0.94, coverage=0.31, a_only=47, trunc=0)
    html = cl.get("/farm/mlb").text
    assert "94%&nbsp;losses" in html                      # the omission figure beside win%
    assert "@96%cov" in html and "@31%cov" in html        # coverage carried -> two claims NOT flattened
    assert "pm-omit-bad" in html


def test_prospects_low_coverage_is_a_floor_even_when_not_truncated(monkeypatch, tmp_path):
    """The floor marker must key on LOW COVERAGE, not truncation alone: a whale whose /activity did NOT hit the page
    ceiling but only re-found 31% of its closed era is a FLOOR (older losers lie beyond the window), not a full
    measurement. (Regression for the review's Attack #3.)"""
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND)
    _ground_row(p, CAND, omission=0.94, coverage=0.31, a_only=47, trunc=0)   # UNtruncated, but under-covered
    html = cl.get("/farm/mlb").text
    assert "@31%cov" in html and "pm-omit-floor" in html and "(floor)" in html


def test_prospects_well_covered_untruncated_is_not_a_floor(monkeypatch, tmp_path):
    """The mirror: a well-covered (>=90%), untruncated omission is a MEASUREMENT, not a floor -- no floor marker."""
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND)
    _ground_row(p, CAND, omission=0.60, coverage=0.98, a_only=30, trunc=0)
    html = cl.get("/farm/mlb").text
    assert "@98%cov" in html and "pm-omit-bad" in html
    assert "pm-omit-floor" not in html and "(floor)" not in html


def test_prospects_verified_zero_is_distinct_from_unknown(monkeypatch, tmp_path):
    """A GROUNDED whale with no missing losses shows a VERIFIED 0% (known-clean), which is NOT the unknown state."""
    cl, p = _mk(monkeypatch, tmp_path)
    _add_cand(p, CAND)
    _ground_row(p, CAND, omission=0.0, coverage=1.0, a_only=0, trunc=0, hw=40, hl=20)
    html = cl.get("/farm/mlb").text
    assert "pm-omit-ok" in html and "0%&nbsp;verified" in html   # grounded zero = VERIFIED clean
    assert "pm-omit-unknown" not in html                        # distinct from 'nobody checked'
    assert "pm-winpct-caveated" not in html                     # a verified-clean win% is NOT caveated
