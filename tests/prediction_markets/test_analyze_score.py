"""ANALYZE UPGRADE integration: build_pm_analysis attaches the deterministic WhaleScore, analyze_whale stores it in
pm_whale_score (migration 023), and _build_user_content presents the trust-flagged block. Uses a temp PM DB."""
import sqlite3

from trading_corp.prediction_markets import analyze, db, scoring


def _mkdb(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)                              # creates the schema incl migration 023 pm_whale_score
    conn = sqlite3.connect(p); conn.row_factory = sqlite3.Row
    return conn


def _cp(conn, wallet, cat, cid, pnl, cost, won, ts, title="m", oi=0, avg=0.6, suspect=0):
    tb = (cost / avg) if avg else 0.0          # total_bought is NOTIONAL; cost_basis = tb*avg
    conn.execute(
        "INSERT INTO pm_closed_position(wallet,condition_id,category,outcome_index,avg_price,total_bought,cost_basis,"
        "realized_pnl,cur_price,won,resolved_ts,pnl_suspect,title) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (wallet, cid, cat, oi, avg, tb, cost, pnl, (0.95 if won else 0.02), won, ts, suspect, title))


def test_build_pm_analysis_attaches_score_and_store_writes_pm_whale_score(tmp_path):
    conn = _mkdb(tmp_path)
    w = "0xabc"
    conn.execute("INSERT INTO pm_whale(wallet,user_name) VALUES (?,?)", (w, "TestWhale"))
    # a diversified winner: 40 wins @ +50 each (cost 60), 5 losses @ -60 -> net positive, low dominance
    for i in range(40):
        _cp(conn, w, "mlb", "cw%d" % i, 50.0, 60.0, 1, 1000 + i, "win%d" % i)
    for i in range(5):
        _cp(conn, w, "mlb", "cl%d" % i, -60.0, 60.0, 0, 2000 + i, "loss%d" % i)
    conn.commit()
    rep = analyze.build_pm_analysis(conn, w, "mlb", now_ts=9999, loss_grounding=None)
    assert isinstance(rep.score, dict)
    s = rep.score
    assert s["tier"] in (scoring.TIER_PROMOTE, scoring.TIER_WATCH, scoring.TIER_PASS, scoring.TIER_INSUFFICIENT)
    assert s["grounded"] is False and s["omission_pct"] is None          # ungrounded -> omission UNKNOWN, never zero
    assert s["tier"] == scoring.TIER_WATCH and "grounds clean" in s["reason"]   # ungrounded caps at WATCH
    assert s["dominance_net"] is not None and s["dominance_net"] < 0.30  # diversified
    assert s["dd_wins"] == 40 and s["dd_losses"] == 5                    # the drawdown-tell context
    # analyze_whale stores it (narrator OFF so no LLM); read pm_whale_score back
    analyze.analyze_whale(conn, w, "mlb", now_ts=9999, narrator_enabled=False)
    row = conn.execute("SELECT tier, grounded, sort_roi, dominance_net FROM pm_whale_score WHERE wallet=? AND category=?",
                       (w, "mlb")).fetchone()
    assert row is not None and row["tier"] == scoring.TIER_WATCH and row["grounded"] == 0


def test_unanalyzed_whale_has_no_score_row(tmp_path):
    conn = _mkdb(tmp_path)
    # never analyzed -> NO row -> the Prospects list reads NULL (not-analyzed), distinct from a PASS row
    row = conn.execute("SELECT * FROM pm_whale_score WHERE wallet=? AND category=?", ("0xnever", "mlb")).fetchone()
    assert row is None


class _Resp:
    def __init__(self, meta=None, model=None):
        self.response_metadata = meta or {}
        if model is not None:
            self.model = model


def test_extract_model_reads_the_real_response_not_the_config():
    # the deploy proof: a REAL Sonnet call surfaces claude-sonnet-4-6 from response_metadata (langchain uses either key)
    assert analyze._extract_model(_Resp(meta={"model": "claude-sonnet-4-6"})) == "claude-sonnet-4-6"
    assert analyze._extract_model(_Resp(meta={"model_name": "claude-sonnet-4-6"})) == "claude-sonnet-4-6"
    # a SILENT fallback must be VISIBLE: if the API answered as Haiku, _extract_model reports Haiku (post-check catches it)
    assert analyze._extract_model(_Resp(meta={"model": "claude-haiku-4-5-20251001"})) == "claude-haiku-4-5-20251001"
    # response omits a model -> None, and narrate falls back to the config so display never blanks
    assert analyze._extract_model(_Resp(meta={})) is None
    assert analyze._extract_model(_Resp(model="claude-sonnet-4-6")) == "claude-sonnet-4-6"


def test_user_content_carries_trust_flags_and_the_labelled_fiction():
    # a MIRAGE-shaped score dict (grounded, most losses dropped) -> the block flags it, not free prose
    rep = analyze.PMAnalysisReport(
        wallet="0xdef", category="mlb", user_name="Mirage", backfill_complete=True, n_total_rows=100,
        n_resolved=100, n_excluded=0, n_anomaly=0, wins=100, losses=0, win_rate=1.0, net_realized_pnl=5000.0,
        total_bought=0.0, cost_basis=6000.0, roi=0.73, roi_notional=None, avg_win_price=0.92, chalk=True,
        contested=False, n_condition_ids=100, two_sided_pct=0.0, onesided_roi=0.73, onesided_n=100,
        data_quality=None, dq_count_pct=0.0, dq_dollar_pct=0.0, data_state="ok", all_quarantined=False,
        min_resolved=30, rollup_n_resolved=None, reconciled=True, recon_note=None, generated_ts=0,
        skill_version="4",
        score={"tier": scoring.TIER_PASS, "reason": "mirage", "sort_roi": 0.73, "n_honest": 100, "grounded": True,
               "omission_pct": 0.94, "coverage_pct": 0.95, "omission_floor": False, "honest_roi": -0.05,
               "dominance_net": 0.01, "dominance_gross": 0.01, "largest_pnl": 100.0, "largest_title": "x",
               "two_sided_pct": 0.0, "avg_win_price": 0.92, "chalk": True, "dd_tell": 0.0, "dd_wins": 100,
               "dd_losses": 0, "copy_fills": 0, "copy_pnl": None})
    txt = analyze._build_user_content(rep)
    assert "TIER (already decided, final): PASS" in txt
    assert "[MIRAGE" in txt                                  # the omission flag is ON the number
    assert "LABELLED FICTION" in txt and "$0" in txt.replace("+", "")   # the drawdown-tell smell
    assert "EXACTLY ONE sentence" in txt                     # the brevity instruction is in-band
