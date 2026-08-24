"""CP2 Phase-3 HARD BAR: every drill's count reconciles with the aggregate cell it came from.

This is the Phase-3 analogue of the silent PK loss -- caught in a test, not in Jack's browser. The
fixture's aggregates are computed BY HAND in the comments so a drift in either the rollup or the drill
fails loudly. Encodes Jack's three adopted catches: two_sided/single_game are over ALL rows (structural,
not scoreable-filtered); two_sided reconciles on DISTINCT condition_ids (not raw rows); single_game
reuses classify_market_shape (the drill can never silently desync from n_single_game). Offline; tmp DB.

Spec: reports/prediction_markets/P3_KICKOFF_2026-08-24.md (reconciliation contract).
"""
from trading_corp.prediction_markets import db, positions, stats

NOW = 1_700_000_000
W = "0xwhale"


def _ins(conn, cid, oi, slug, *, won, avg, cost, rp, suspect=0, reason=None, cat="mlb"):
    cols = ("wallet", "condition_id", "slug", "event_slug", "title", "category", "category_source",
            "outcome", "outcome_index", "avg_price", "total_bought", "cost_basis", "realized_pnl",
            "cur_price", "won", "pnl_suspect", "suspect_reason", "resolved_ts", "ingested_ts", "updated_ts")
    row = {c: None for c in cols}
    row.update(wallet=W, condition_id=cid, slug=slug, event_slug="mlb", title="game",
               category=cat, category_source="slug_prefix", outcome="Yes" if oi == 0 else "No",
               outcome_index=oi, avg_price=avg, total_bought=100.0, cost_basis=cost, realized_pnl=rp,
               cur_price=1.0 if won else 0.0, won=won, pnl_suspect=suspect, suspect_reason=reason,
               resolved_ts=NOW, ingested_ts=NOW, updated_ts=NOW)
    conn.execute("INSERT INTO pm_closed_position (%s) VALUES (%s)"
                 % (", ".join(cols), ", ".join("?" * len(cols))), [row[c] for c in cols])


def _seed(tmp_path):
    """W / mlb, hand-computed aggregates:
      scoreable rows = 6 (m1,m2,m3,m4,m5a,m5b)   -> n_resolved = 6
      wins           = 4 (m1,m3,m4,m5a)          -> wins = 4;  avg_win_price = avg(.9,.8,.6,.7)=0.75
      quarantined    = 2 (q1,q2)                 -> n_excluded = 2
      two-sided cids = 1 (m5 on outcome 0 AND 1) -> n_two_sided = 1  (but 2 ROWS)
      single_game    = 5 (m1,m2,m5a,m5b,q1 dated; m3/q2 futures; m4 ambiguous) -> n_single_game = 5
    """
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_whale (wallet, user_name, backfill_complete) VALUES (?,?,1)", (W, "Whaley"))
        _ins(conn, "m1", 0, "mlb-a-b-2026-01-01", won=1, avg=0.9, cost=90.0, rp=10.0)    # single_game, won
        _ins(conn, "m2", 0, "mlb-c-d-2026-01-02", won=0, avg=0.4, cost=40.0, rp=-40.0)   # single_game, loss
        _ins(conn, "m3", 0, "mlb-2026-champion",  won=1, avg=0.8, cost=80.0, rp=20.0)    # FUTURES, won
        _ins(conn, "m4", 0, "mlb-weirdprop",      won=1, avg=0.6, cost=60.0, rp=15.0)    # ambiguous, won
        _ins(conn, "m5", 0, "mlb-e-f-2026-01-05", won=1, avg=0.7, cost=70.0, rp=30.0)    # two-sided leg A (won)
        _ins(conn, "m5", 1, "mlb-e-f-2026-01-05", won=0, avg=0.3, cost=30.0, rp=-30.0)   # two-sided leg B (loss)
        _ins(conn, "q1", 0, "mlb-g-h-2026-01-06", won=0, avg=0.5, cost=50.0, rp=-500.0,  # quarantined (single_game)
             suspect=1, reason="row_invariant")
        _ins(conn, "q2", 0, "mlb-2026-champion",  won=0, avg=0.5, cost=50.0, rp=-9.0,    # quarantined (futures)
             suspect=1, reason="no_cost_basis")
        stats.rollup(conn, now_ts=NOW)
    return p


def _agg(conn):
    return dict(conn.execute("SELECT * FROM pm_category_stats WHERE wallet=? AND category='mlb'", (W,)).fetchone())


def test_rollup_matches_hand_computed_aggregates(tmp_path):
    # sanity-anchor the fixture: if the rollup ever changes these, the reconciliation asserts below still
    # hold (they compare drill-to-rollup) but this pins the ABSOLUTE numbers so the fixture stays meaningful.
    with db.connect(_seed(tmp_path)) as conn:
        a = _agg(conn)
    assert a["n_resolved"] == 6 and a["wins"] == 4 and a["n_excluded"] == 2
    assert a["n_two_sided"] == 1 and a["n_single_game"] == 5 and a["n_futures_like"] == 2
    assert abs(a["avg_win_price"] - 0.75) < 1e-9


def test_scoreable_drill_reconciles_n_resolved(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        rows = positions.drill_rows(conn, W, "mlb", "scoreable")
        rec = positions.reconcile(conn, W, "mlb", "scoreable", rows)
    assert rec["ok"] and rec["expected"] == 6 and rec["actual"] == 6
    assert all(r["pnl_suspect"] == 0 for r in rows)


def test_won_drill_reconciles_wins_AND_avg_win_price(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        a = _agg(conn)
        rows = positions.drill_rows(conn, W, "mlb", "won")
        rec = positions.reconcile(conn, W, "mlb", "won", rows)
        avg = positions.won_avg_price(rows)
    assert rec["ok"] and rec["expected"] == 4 and rec["actual"] == 4        # row count == wins
    assert all(r["won"] == 1 and r["pnl_suspect"] == 0 for r in rows)
    assert abs(avg - a["avg_win_price"]) < 1e-9                             # AND the mean == avg_win_price


def test_quarantined_drill_reconciles_n_excluded(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        rows = positions.drill_rows(conn, W, "mlb", "quarantined")
        rec = positions.reconcile(conn, W, "mlb", "quarantined", rows)
    assert rec["ok"] and rec["expected"] == 2 and rec["actual"] == 2
    assert all(r["pnl_suspect"] == 1 and r["suspect_reason"] for r in rows)  # each carries its reason


def test_two_sided_drill_reconciles_on_DISTINCT_condition_ids_not_rows(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        rows = positions.drill_rows(conn, W, "mlb", "two_sided")
        rec = positions.reconcile(conn, W, "mlb", "two_sided", rows)
    # BOTH legs of m5 are returned (2 rows) but it reconciles on DISTINCT condition_ids (= 1 = n_two_sided).
    assert len(rows) == 2 and {r["condition_id"] for r in rows} == {"m5"}
    assert rec["measure"] == "distinct_cids" and rec["expected"] == 1 and rec["actual"] == 1 and rec["ok"]
    # a naive raw-row measure would read 2 and FALSE-fail -- the catch Jack adopted as spec
    assert len(rows) != rec["expected"]


def test_single_game_drill_reuses_classifier_and_reconciles(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        rows = positions.drill_rows(conn, W, "mlb", "single_game")
        rec = positions.reconcile(conn, W, "mlb", "single_game", rows)
        cids = {r["condition_id"] for r in rows}
    assert rec["ok"] and rec["expected"] == 5 and rec["actual"] == 5
    # the drill APPLIED the classifier: futures (m3, q2) EXCLUDED; a quarantined single_game (q1) INCLUDED
    # (single_game is structural over ALL rows, not scoreable-filtered).
    assert "m3" not in cids and "q2" not in cids
    assert "q1" in cids and {"m1", "m2", "m5"} <= cids


def test_single_game_over_all_rows_not_scoreable_filtered(tmp_path):
    # explicit guard for the structural-vs-scoreable subtlety: q1 is quarantined yet counts as single_game
    with db.connect(_seed(tmp_path)) as conn:
        rows = positions.drill_rows(conn, W, "mlb", "single_game")
    assert any(r["condition_id"] == "q1" and r["pnl_suspect"] == 1 for r in rows)


def test_all_drill_returns_every_row(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        rows = positions.drill_rows(conn, W, "mlb", "all")
    assert len(rows) == 8                                                    # 6 scoreable + 2 quarantined


def test_score_decomposition_exposes_both_routines(tmp_path):
    p = _seed(tmp_path)
    with db.connect(p) as conn:
        stats.compute_scores(conn, now_ts=NOW, min_resolved=1)
        dec = positions.score_decomposition(conn, W, "mlb")
    assert set(dec) == {"net_roi", "recency_weighted"}
    for routine, d in dec.items():
        # score == wilson_lcb x edge_factor (the 'why ranked' identity is auditable on the page)
        assert abs(d["score"] - d["wilson_lcb"] * d["edge_factor"]) < 1e-9
        assert d["params"].get("excludes_suspect") is True
