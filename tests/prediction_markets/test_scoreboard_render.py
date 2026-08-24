"""CP2 Phase-2 scoreboard render tests. Offline; FastAPI TestClient over a seeded PM DB + direct template
renders for edge branches. Encodes the Phase-2 bar: CLI/page flag PARITY, the three labelling requirements
(one-sided UPPER BOUND, two-sided GRAIN, single_game n/a for fed/unknown), honest-empty, and the STRUCTURAL
freshness stamp.

Spec: reports/prediction_markets/P2_PLAN.md §5.1, §6; P2_KICKOFF_2026-08-23.md (Phase-2 bar).
"""
import re
import time

import pytest
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, stats

NOW = 1_700_000_000            # fixed epoch for deterministic seeding/thresholds
TWO_DAYS = 2 * 86400


def _ins(conn, **kw):
    cols = ("wallet", "condition_id", "slug", "event_slug", "title", "category", "category_source",
            "outcome", "outcome_index", "avg_price", "total_bought", "cost_basis", "realized_pnl",
            "cur_price", "won", "pnl_suspect", "suspect_reason", "resolved_ts", "ingested_ts", "updated_ts")
    row = {c: None for c in cols}
    row.update(outcome_index=0, pnl_suspect=0, ingested_ts=NOW, updated_ts=NOW, resolved_ts=NOW)
    row.update(kw)
    conn.execute(
        "INSERT INTO pm_closed_position (%s) VALUES (%s)" % (", ".join(cols), ", ".join("?" * len(cols))),
        [row[c] for c in cols],
    )


def _whale(conn, wallet, *, complete=1, refresh_ts=NOW - TWO_DAYS):
    conn.execute(
        "INSERT INTO pm_whale (wallet, user_name, first_seen_ts, last_backfill_ts, last_refresh_ts, "
        "backfill_complete, last_pulled, last_stored) VALUES (?,?,?,?,?,?,?,?)",
        (wallet, wallet[-5:], NOW - 90 * 86400, refresh_ts, refresh_ts, complete, 12, 12),
    )


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    """3 whales: A = complete/chalk/one-sided/sports; B = complete/contested/two-sided+contaminated/fed;
    C = INCOMPLETE (backfill_complete=0). Returns (path, board) with board = query_scoreboard(net_roi)."""
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    db.init_db(p)
    A, B, C = "0x000000000000000000000000000000000000chal", \
              "0x0000000000000000000000000000000000000fed", \
              "0x000000000000000000000000000000000000part"
    with db.connect(p) as conn:
        _whale(conn, A); _whale(conn, B); _whale(conn, C, complete=0)
        # A: sports, 12 one-sided, chalk (win px 0.90), 11W/1L, dated single-game slugs
        for i in range(12):
            won = 0 if i == 11 else 1
            _ins(conn, wallet=A, condition_id=f"a{i}", category="sports", category_source="slug_prefix",
                 slug=f"nba-gsw-lal-2026-01-{10+i:02d}", event_slug="nba", title="GSW vs LAL",
                 avg_price=0.90, total_bought=100, cost_basis=90.0,
                 realized_pnl=(50.0 if won else -90.0), cur_price=(1.0 if won else 0.0), won=won)
        # B: fed, 10 one-sided + 1 two-sided condition (b10 held on outcome 0 AND 1) = 12 scoreable; contested
        for i in range(10):
            _ins(conn, wallet=B, condition_id=f"b{i}", category="fed", category_source="slug_prefix",
                 slug=f"fed-decision-{i}", event_slug="fed", title="Fed decision",
                 avg_price=0.60, total_bought=100, cost_basis=60.0, realized_pnl=30.0, cur_price=1.0, won=1)
        _ins(conn, wallet=B, condition_id="b10", outcome_index=0, category="fed", slug="fed-hike",
             event_slug="fed", title="Fed hike", avg_price=0.60, total_bought=100, cost_basis=60.0,
             realized_pnl=30.0, cur_price=1.0, won=1)
        _ins(conn, wallet=B, condition_id="b10", outcome_index=1, category="fed", slug="fed-hike",
             event_slug="fed", title="Fed hike", avg_price=0.60, total_bought=100, cost_basis=60.0,
             realized_pnl=-60.0, cur_price=0.0, won=0)
        # B: 2 QUARANTINED rows (pnl_suspect=1) -> 2/14 ~ 14% count -> data_quality 'contaminated'
        for j in (20, 21):
            _ins(conn, wallet=B, condition_id=f"b{j}", category="fed", slug=f"fed-bad-{j}", event_slug="fed",
                 title="bad", avg_price=0.60, total_bought=100, cost_basis=60.0, realized_pnl=-500.0,
                 cur_price=0.0, won=0, pnl_suspect=1, suspect_reason="row_invariant")
        # C: sports, 12 one-sided, neither chalk nor contested (0.75); INCOMPLETE backfill
        for i in range(12):
            _ins(conn, wallet=C, condition_id=f"c{i}", category="sports", slug=f"nfl-x-y-2026-02-{10+i:02d}",
                 event_slug="nfl", title="X vs Y", avg_price=0.75, total_bought=100, cost_basis=75.0,
                 realized_pnl=25.0, cur_price=1.0, won=1)
        stats.rollup(conn, now_ts=NOW)
        stats.compute_scores(conn, now_ts=NOW, min_resolved=10)
        board = stats.query_scoreboard(conn, routine="net_roi", min_resolved=10)
    return p, board, {"A": A, "B": B, "C": C}


def _client():
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def _row_html(html, wallet):
    """Return the <tr>...</tr> block whose wallet cell holds `wallet`."""
    for m in re.finditer(r"<tr\b.*?</tr>", html, re.S):
        if wallet in m.group(0):
            return m.group(0)
    return ""


# ── the shared-deriver PARITY bar (#1) ────────────────────────────────────────────────────────────

def test_flag_parity_page_equals_cli(seeded):
    path, board, _ = seeded
    html = _client().get("/").text
    page_flags = sorted(re.findall(r'data-flag="([^"]+)"', html))
    expected = sorted(f for r in board for f in stats.scoreboard_flags(r))
    assert page_flags == expected, f"page {page_flags} != derived {expected}"
    # ... and the CLI report emits the very same tokens (one shared deriver => cannot diverge)
    cli = stats.format_report(board, fmt="table")
    for tok in expected:
        assert tok in cli, f"CLI report missing flag token {tok!r}"


def test_incomplete_backfill_not_ranked(seeded):
    _, _, w = seeded
    html = _client().get("/").text
    row = _row_html(html, w["C"])
    assert 'data-flag="INCOMPLETE-NOT-RANKED"' in row
    assert "pm-row-unranked" in row
    assert "not ranked" in row               # no score snapshot for an incomplete wallet


def test_contaminated_flag_present(seeded):
    _, _, w = seeded
    row = _row_html(_client().get("/").text, w["B"])
    assert re.search(r'data-flag="CONTAMINATED\(cnt\d+%/\$\d+%\)"', row), row[:400]


# ── the three labelling requirements ──────────────────────────────────────────────────────────────

def test_onesided_labeled_upper_bound(seeded):
    _, board, w = seeded
    row = _row_html(_client().get("/").text, w["A"])
    assert "bound" in row.lower()            # the ↑bound tag renders
    assert "UPPER BOUND" in row              # the caveat is in the cell title, spelled out
    a = next(r for r in board if r["wallet"] == w["A"])
    assert a["onesided_roi"] is not None and a["onesided_is_upper_bound"] == 1


def test_two_sided_grain_labeled(seeded):
    html = _client().get("/").text
    assert "per (wallet, category)" in html  # grain label so nobody 'corrects' vs the per-wallet §13A(j) figure


def test_single_game_na_for_fed_but_pct_for_sports(seeded):
    _, _, w = seeded
    html = _client().get("/").text
    fed_row = _row_html(html, w["B"])
    sports_row = _row_html(html, w["A"])
    # single-game% column: fed -> n/a (NEVER 0%); sports (ranked, one-sided) -> a real percentage, no n/a anywhere
    assert "n/a" in fed_row
    assert "n/a" not in sports_row
    assert re.search(r"\d+%", sports_row)


# ── honest-empty + notional/cost framing ──────────────────────────────────────────────────────────

def test_honest_empty_no_fabricated_zero(seeded):
    html = _client().get("/?category=doesnotexist").text
    assert 'data-empty="1"' in html
    assert "<tbody" not in html               # no table body, no zero row


def test_two_sided_honest_empty_when_no_condition_ids(seeded):
    # degenerate row (n_condition_ids=0) must render '—', never a fabricated 0%
    from trading_corp.prediction_markets.web.app import templates
    row = {"wallet": "0xz", "category": "sports", "n_resolved": 5, "win_rate": 0.5, "roi": 0.1,
           "roi_notional": 0.1, "onesided_roi": None, "onesided_n": None, "n_condition_ids": 0,
           "two_sided_pct": 0.0, "single_game_pct": None, "avg_win_price": 0.5, "chalk": False,
           "contested": False, "net_realized_pnl": 10.0, "score": 0.2, "backfill_complete": 1, "flags": []}
    out = templates.get_template("partials/pm_scoreboard_table.html").render(
        board=[row], refresh=stats.refresh_band_state(NOW - TWO_DAYS, NOW))
    cell = re.search(r"<tr\b.*?</tr>", out, re.S).group(0)
    # the two-sided% cell shows the em-dash, not "0%"
    assert "—" in cell


def test_cost_is_ranked_notional_is_comparison_only(seeded):
    html = _client().get("/").text
    assert "roi&nbsp;(cost)" in html or "roi (cost)" in html
    assert "comparison only" in html          # notional explicitly demoted
    assert "cost-based ROI" in html


# ── freshness: structural + threshold logic ────────────────────────────────────────────────────────

def test_refresh_band_is_structural(seeded):
    # even the EMPTY render carries the freshness stamp -- you cannot get rows without it
    html = _client().get("/?category=doesnotexist").text
    assert "data-refresh-band=" in html
    assert "data-refresh-band=" in _client().get("/").text


def test_refresh_band_state_thresholds():
    assert stats.refresh_band_state(NOW - 2 * 86400, NOW)["band"] == "green"    # 2d
    assert stats.refresh_band_state(NOW - 10 * 86400, NOW)["band"] == "amber"   # 10d
    assert stats.refresh_band_state(NOW - 20 * 86400, NOW)["band"] == "red"     # 20d
    none_band = stats.refresh_band_state(None, NOW)
    assert none_band["band"] == "red" and none_band["note"]                     # missing => red + honest note


def test_scoreboard_orders_ranked_before_unranked(seeded):
    # query_scoreboard puts scored rows (A,B) before NULL-score (C) -- server-side order, no client sort
    _, board, w = seeded
    order = [r["wallet"] for r in board]
    assert order.index(w["C"]) == len(order) - 1
