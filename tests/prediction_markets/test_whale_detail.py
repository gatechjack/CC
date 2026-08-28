"""CP2 Phase-3 whale-detail + drill-through web tests. FastAPI TestClient over a seeded PM DB.

Encodes the CP2-halt HARD BAR at the web layer: (1) every drill's on-page reconciliation banner reads
OK and the count matches the aggregate; (2) the ONE shared pm_position_rows.html renders IDENTICALLY for
the product drill (included in the page) and the diagnostics-shaped /positions partial call (parity,
checkpoint 3); (3) a NULL user_name renders the WALLET, never a blank/placeholder; the display name shows
with the wallet still visible. Plus route 200s, honest-empty, and the single_game clamp for fed.

Spec: reports/prediction_markets/P3_KICKOFF_2026-08-24.md (checkpoints 2-3).
"""
import re

from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, stats

NOW = 1_700_000_000
TWO_DAYS = 2 * 86400
A = "0x" + "aaaa".rjust(40, "0")   # named whale (Kickstand7), mlb, drillable structure
N = "0x" + "b1c9".rjust(40, "0")   # NO user_name -> must render the wallet (suffix avoids word collisions)


def _ins(conn, wallet, cid, oi, slug, *, won, avg, cost, rp, cat="mlb", suspect=0, reason=None):
    cols = ("wallet", "condition_id", "slug", "event_slug", "title", "category", "category_source",
            "outcome", "outcome_index", "avg_price", "total_bought", "cost_basis", "realized_pnl",
            "cur_price", "won", "pnl_suspect", "suspect_reason", "resolved_ts", "ingested_ts", "updated_ts")
    row = {c: None for c in cols}
    row.update(wallet=wallet, condition_id=cid, slug=slug, event_slug="mlb", title="game",
               category=cat, category_source="slug_prefix", outcome="Yes" if oi == 0 else "No",
               outcome_index=oi, avg_price=avg, total_bought=100.0, cost_basis=cost, realized_pnl=rp,
               cur_price=1.0 if won else 0.0, won=won, pnl_suspect=suspect, suspect_reason=reason,
               resolved_ts=NOW, ingested_ts=NOW, updated_ts=NOW)
    conn.execute("INSERT INTO pm_closed_position (%s) VALUES (%s)"
                 % (", ".join(cols), ", ".join("?" * len(cols))), [row[c] for c in cols])


def _whale(conn, wallet, name):
    conn.execute(
        "INSERT INTO pm_whale (wallet, user_name, first_seen_ts, last_backfill_ts, last_refresh_ts, "
        "backfill_complete, last_pulled, last_stored) VALUES (?,?,?,?,?,1,?,?)",
        (wallet, name, NOW - 90 * 86400, NOW - TWO_DAYS, NOW - TWO_DAYS, 8, 8))


def _seed(tmp_path, monkeypatch):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    db.init_db(p)
    with db.connect(p) as conn:
        _whale(conn, A, "Kickstand7")
        _whale(conn, N, None)                         # no display name
        # A / mlb: same hand-computed structure as test_drill_reconcile (n_resolved=6, wins=4,
        # n_two_sided=1, n_single_game=5, n_excluded=2, avg_win_price=0.75)
        _ins(conn, A, "m1", 0, "mlb-a-b-2026-01-01", won=1, avg=0.9, cost=90.0, rp=10.0)
        _ins(conn, A, "m2", 0, "mlb-c-d-2026-01-02", won=0, avg=0.4, cost=40.0, rp=-40.0)
        _ins(conn, A, "m3", 0, "mlb-2026-champion",  won=1, avg=0.8, cost=80.0, rp=20.0)
        _ins(conn, A, "m4", 0, "mlb-weirdprop",      won=1, avg=0.6, cost=60.0, rp=15.0)
        _ins(conn, A, "m5", 0, "mlb-e-f-2026-01-05", won=1, avg=0.7, cost=70.0, rp=30.0)
        _ins(conn, A, "m5", 1, "mlb-e-f-2026-01-05", won=0, avg=0.3, cost=30.0, rp=-30.0)
        _ins(conn, A, "q1", 0, "mlb-g-h-2026-01-06", won=0, avg=0.5, cost=50.0, rp=-500.0, suspect=1, reason="row_invariant")
        _ins(conn, A, "q2", 0, "mlb-2026-champion",  won=0, avg=0.5, cost=50.0, rp=-9.0, suspect=1, reason="no_cost_basis")
        # N / mlb: 3 one-sided scoreable rows so it ranks + appears
        for i in range(3):
            _ins(conn, N, f"n{i}", 0, f"mlb-x-y-2026-02-{10+i:02d}", won=1, avg=0.5, cost=50.0, rp=25.0)
        stats.rollup(conn, now_ts=NOW)
        stats.compute_scores(conn, now_ts=NOW, min_resolved=1)
    return p


def _client():
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def _pos_rows(html):
    """The position <tr> blocks (data-cid) from a render -- the shared-renderer output."""
    return re.findall(r"<tr[^>]*\bdata-cid=.*?</tr>", html, re.S)


# ── routes render ────────────────────────────────────────────────────────────────────────────────

def test_whale_detail_200_and_score_decomposition(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    r = _client().get(f"/whale/{A}/mlb")
    assert r.status_code == 200
    html = r.text
    assert "Why ranked here" in html and "wilson_lcb" in html and "edge" in html
    assert "net_roi" in html and "recency_weighted" in html


def test_whale_overview_200(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    r = _client().get(f"/whale/{A}")
    assert r.status_code == 200 and "mlb" in r.text


def test_unknown_wallet_is_honest_empty_not_500(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    r = _client().get("/whale/0xdeadbeef/mlb")
    assert r.status_code == 200 and 'data-empty="1"' in r.text


# ── display names (Jack's feedback item) ───────────────────────────────────────────────────────────

def test_display_name_shown_with_wallet_visible(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    html = _client().get(f"/whale/{A}/mlb").text
    assert "Kickstand7" in html                    # name for recognition
    assert A in html                               # wallet still present (truth) -- in title/href


def test_null_user_name_renders_wallet_never_placeholder(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    html = _client().get(f"/whale/{N}/mlb").text
    assert N[:10] in html                          # the wallet is shown
    assert "pm-waddr-only" in html                 # rendered via the no-name branch (wallet IS the label)
    assert "no display name" in html               # honest note, not a fabricated name
    # never a placeholder that reads like a name
    assert "Unknown" not in html and "Anonymous" not in html


# (phase 3) test_scoreboard_wallet_cell_links_to_whale_and_shows_name was REMOVED: the scoreboard PAGE is
# retired. The wallet-cell -> whale link + display-name behaviour now lives in the Prospects rows (whale_label
# links to /whale/...), covered by test_stage2_phase2.py; the name-display is covered by the /whale page tests above.


# ── reconciliation banner on the page (HARD BAR, checkpoint 2 at the web layer) ─────────────────────

def test_each_drill_reconciles_on_page(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    c = _client()
    expected = {"scoreable": 6, "won": 4, "two_sided": 1, "single_game": 5, "quarantined": 2}
    for drill, exp in expected.items():
        html = c.get(f"/whale/{A}/mlb/positions?drill={drill}").text
        assert 'data-recon-ok="1"' in html, f"{drill} did not reconcile: {html[:300]}"
        assert f'data-recon-expected="{exp}"' in html, f"{drill} expected {exp}"
        assert "MISMATCH" not in html


def test_two_sided_reconciles_on_distinct_cids_not_rows(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    html = _client().get(f"/whale/{A}/mlb/positions?drill=two_sided").text
    assert 'data-recon-ok="1"' in html and 'data-recon-expected="1"' in html   # 1 condition_id...
    assert "condition_ids" in html                                             # ...labeled as such
    assert len(_pos_rows(html)) == 2                                           # ...but 2 rows shown


# ── the ONE shared renderer: product drill == diagnostics-shaped call (parity, checkpoint 3) ────────

def test_shared_renderer_parity_page_vs_partial(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    c = _client()
    page = c.get(f"/whale/{A}/mlb?drill=quarantined").text          # partial included server-side in the page
    partial = c.get(f"/whale/{A}/mlb/positions?drill=quarantined").text   # the same renderer, standalone
    page_rows = _pos_rows(page)
    partial_rows = _pos_rows(partial)
    assert page_rows and page_rows == partial_rows                  # byte-identical rows -> cannot diverge


def test_mismatch_banner_is_loud_when_reconcile_fails(tmp_path, monkeypatch):
    # defence-in-depth: prove the renderer SHOWS a failure loudly (a real drill reconciles by construction,
    # so force a bad recon dict directly through the shared template).
    _seed(tmp_path, monkeypatch)
    from trading_corp.prediction_markets.web.app import templates
    out = templates.get_template("partials/pm_position_rows.html").render(
        rows=[{"condition_id": "x", "outcome_index": 0, "avg_price": 0.5, "total_bought": 100.0,
               "cost_basis": 50.0, "realized_pnl": 5.0, "won": 1, "pnl_suspect": 0, "resolved_ts": NOW}],
        recon={"aggregate": "n_resolved", "measure": "rows", "expected": 9, "actual": 1, "ok": False},
        drill_label="scoreable rows")
    assert "MISMATCH" in out and "pm-recon-bad" in out and 'data-recon-ok="0"' in out


def test_single_game_drill_not_offered_and_clamped_for_fed(tmp_path, monkeypatch):
    # fed has no single-game notion: the toolbar must not offer it, and ?drill=single_game clamps to scoreable
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    db.init_db(p)
    F = "0x0000000000000000000000000000000000000fed"
    with db.connect(p) as conn:
        _whale(conn, F, "FedWhale")
        for i in range(3):
            _ins(conn, F, f"f{i}", 0, f"fed-decision-{i}", won=1, avg=0.6, cost=60.0, rp=30.0, cat="fed")
        stats.rollup(conn, now_ts=NOW)
        stats.compute_scores(conn, now_ts=NOW, min_resolved=1)
    html = _client().get(f"/whale/{F}/fed?drill=single_game").text
    assert 'data-recon-ok="1"' in html                # clamped to scoreable -> reconciles (n_resolved=3)
    assert 'data-recon-expected="3"' in html
    assert ">single_game</a>" not in html             # the tab is not offered for fed
