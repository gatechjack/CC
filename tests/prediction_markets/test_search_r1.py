"""Stage 4 SEARCH -- RUNG 1 (the pure core). NO engine, NO live DB, NO network.

Covers the two places Jack pointed the adversarial review at:
  * THE WATERMARK (`page_new_rows` / `OutOfOrderPage`) -- an incremental stop that could SKIP a trade
    is this platform's worst failure class, so the newest-first precondition is ASSERTED, not assumed;
    the "silent skip" scenario is proven to RAISE instead of skipping.
  * THE SELECTION (`select_candidates`) -- N>=50 with the top-10 thin-sample fallback (Q1), 30d recency
    via the open-position proxy (Q2), the 15-category allowlist (Q4), cost-ROI rank NEVER win% (F-1),
    and every dropped row counted (never silent).
Plus migration 013 (pm_search_run) pure-DDL + schema-head, and structural R7-independence.
Spec: reports/prediction_markets/STAGE4_SEARCH_PLAN_2026-08-29.md.
"""
import inspect

import pytest

from trading_corp.prediction_markets import db, search

NOW = 2_000_000_000
DAY = 86_400
RECENT_TS = NOW - 10 * DAY     # inside a 30d window
OLD_TS = NOW - 100 * DAY       # outside a 30d window


# ════════════════════════════════════════════════ migration 013 ════════════════════════════════════════

def test_schema_head_is_14():
    # migration 014 (contracts column, R8 flat-contracts sizing) advanced the head from 13 -> 14; migration 013
    # (pm_search_run) is still present. (This assertion was stale from the sizing session; corrected here.)
    assert db.SCHEMA_HEAD == 14
    assert (13, db.MIGRATION_013) in db.MIGRATIONS


def test_migration_013_creates_pm_search_run(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pm_search_run'"
        ).fetchone() is not None
        cols = {r[1]: r for r in conn.execute("PRAGMA table_info(pm_search_run)")}
        assert set(cols) == {
            "run_id", "started_ts", "finished_ts", "leaderboard_category", "leaderboard_limit",
            "min_resolved", "recency_window_days", "thin_sample_target", "n_discovered",
            "n_backfilled", "n_candidates_written", "status", "summary", "params_json",
        }
        # started_ts NOT NULL; run_id is the PK (rowid alias)
        assert cols["started_ts"][3] == 1          # notnull flag
        assert cols["run_id"][5] == 1              # pk position
        # the started_ts listing index exists
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_pm_search_run_started'"
        ).fetchone() is not None


def test_migration_013_is_pure_ddl_empty(tmp_path):
    """PURE DDL: 013 creates the table EMPTY -- no data write (mirrors 009/010/011/012)."""
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_search_run").fetchone()[0] == 0


def test_upgrade_12_to_13_adds_search_run(tmp_path):
    """Applying 1..12 by hand leaves the DB at 12 with NO pm_search_run; applying migration 013 adds exactly that
    table (the 12->13 step is behaviour-neutral for every other table). (Formerly asserted a head of 13; migration
    014 later advanced the head, so this checks the 013 step directly and lets init_db reach SCHEMA_HEAD.)"""
    p = str(tmp_path / "pm.db")
    with db.connect(p) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
        for v, stmts in db.MIGRATIONS:
            if v > 12:
                continue
            conn.execute("BEGIN")
            for s in stmts:
                conn.execute(s)
            conn.execute("INSERT INTO schema_version(version) VALUES (?)", (v,))
            conn.execute("COMMIT")
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 12
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='pm_search_run'").fetchone() is None
    db.init_db(p)
    with db.connect(p) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == db.SCHEMA_HEAD
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='pm_search_run'").fetchone() is not None


def test_migrations_idempotent_at_13(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    db.init_db(p)
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == len(db.MIGRATIONS)
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == db.SCHEMA_HEAD


def test_pm_search_run_roundtrip_defaults_and_autoincrement(tmp_path):
    # run_id is a rowid alias (autoincrements); the three counters DEFAULT 0; finished_ts NULL until done.
    # Also exercises the pm_watchlist.search_run_id FK-satisfiability (an INTEGER run_id to point at).
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_search_run (started_ts) VALUES (?)", (NOW,))
        row = conn.execute(
            "SELECT run_id, n_discovered, n_backfilled, n_candidates_written, finished_ts, status "
            "FROM pm_search_run").fetchone()
    assert isinstance(row["run_id"], int) and row["run_id"] > 0
    assert row["n_discovered"] == 0 and row["n_backfilled"] == 0 and row["n_candidates_written"] == 0
    assert row["finished_ts"] is None and row["status"] is None


def test_pm_search_run_started_ts_not_null(tmp_path):
    import sqlite3
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO pm_search_run (leaderboard_category) VALUES ('Sports')")


# ════════════════════════════════════════════════ watermark ════════════════════════════════════════════

class _RawCP:
    """A raw /closed-positions row exposes `.timestamp` (ingest maps it to resolved_ts)."""
    def __init__(self, timestamp):
        self.timestamp = timestamp


def _rows(*ts):
    return [{"resolved_ts": t} for t in ts]


def test_full_mode_takes_all_never_stops_no_order_check():
    # backfill_complete=False -> FULL mode: take everything, never stop, order irrelevant (unsorted OK).
    page = _rows(100, 900, 300, 0)   # deliberately unsorted
    d = search.page_new_rows(page, watermark_ts=500, backfill_complete=False)
    assert d.new_rows == page and d.stop is False


def test_full_mode_when_no_watermark():
    page = _rows(900, 800)
    d = search.page_new_rows(page, watermark_ts=None, backfill_complete=True)
    assert d.new_rows == page and d.stop is False
    # a zero/negative watermark is also "no trustworthy watermark" -> full mode
    d0 = search.page_new_rows(page, watermark_ts=0, backfill_complete=True)
    assert d0.new_rows == page and d0.stop is False


def test_incremental_stops_when_crossing_watermark():
    # newest-first page straddling the watermark -> take >= wm, stop (older rows already stored)
    page = _rows(1500, 1200, 1000, 900, 800)
    d = search.page_new_rows(page, watermark_ts=1000, backfill_complete=True)
    assert [r["resolved_ts"] for r in d.new_rows] == [1500, 1200, 1000]   # boundary 1000 re-included
    assert d.stop is True


def test_incremental_whole_page_new_keeps_paging():
    # every row >= wm -> all new, DO NOT stop (more new rows may be on the next page)
    page = _rows(2000, 1800, 1500, 1200)
    d = search.page_new_rows(page, watermark_ts=1000, backfill_complete=True)
    assert len(d.new_rows) == 4 and d.stop is False


def test_incremental_boundary_row_reincluded_idempotent():
    # a market co-resolved in the SAME second as the watermark must be re-fetched, never skipped
    page = _rows(1000, 1000, 999)
    d = search.page_new_rows(page, watermark_ts=1000, backfill_complete=True)
    assert [r["resolved_ts"] for r in d.new_rows] == [1000, 1000]
    assert d.stop is True


def test_incremental_unreadable_ts_row_reincluded_not_dropped():
    # Finding 3: an unreadable (None/0) resolution ts must NOT be silently dropped in incremental mode --
    # its recency is unknown, so re-include it (idempotent upsert), and don't stop on ambiguity alone.
    page = [{"resolved_ts": 1500}, {"resolved_ts": 1200}, {"resolved_ts": None}]
    d = search.page_new_rows(page, watermark_ts=1000, backfill_complete=True)
    assert d.new_rows == page            # all three, including the unreadable-ts row (never dropped)
    assert d.stop is False               # no REAL below-watermark row -> don't stop on ambiguity


def test_incremental_unreadable_ts_kept_while_real_below_wm_stops():
    # a REAL below-watermark row (950) triggers stop; the unreadable-ts row (0) is still captured, and
    # the already-stored 950 is correctly not re-listed as new.
    page = [{"resolved_ts": 1500}, {"resolved_ts": 950}, {"resolved_ts": 0}]
    d = search.page_new_rows(page, watermark_ts=1000, backfill_complete=True)
    assert {r["resolved_ts"] for r in d.new_rows} == {1500, 0}
    assert d.stop is True


def test_incremental_seam_out_of_order_raises():
    # INTER-PAGE (the CRITICAL from review): this page's first row (1600) is NEWER than the previous
    # page's minimum (1200) -> a page-seam inversion. Each page is internally descending, so an intra-page
    # check alone would MISS it and a prior early-stop could skip these newer rows. The seam check raises.
    page = _rows(1600, 1500, 1000)
    with pytest.raises(search.OutOfOrderPage):
        search.page_new_rows(page, watermark_ts=500, backfill_complete=True, prev_min_ts=1200)


def test_incremental_seam_same_second_two_sided_ok():
    # two-sided legs / co-resolved markets share a ts and may straddle a page boundary: the seam is
    # NON-strict, so first-row == prev-page-min does NOT raise.
    page = _rows(1200, 1200, 1100)
    d = search.page_new_rows(page, watermark_ts=1000, backfill_complete=True, prev_min_ts=1200)
    assert [r["resolved_ts"] for r in d.new_rows] == [1200, 1200, 1100] and d.stop is False


def test_incremental_seam_ok_when_globally_descending():
    page = _rows(1200, 1100, 900)
    d = search.page_new_rows(page, watermark_ts=1000, backfill_complete=True, prev_min_ts=1300)
    assert [r["resolved_ts"] for r in d.new_rows] == [1200, 1100] and d.stop is True


def test_incremental_reads_raw_timestamp_attr():
    page = [_RawCP(1500), _RawCP(1000), _RawCP(500)]
    d = search.page_new_rows(page, watermark_ts=1000, backfill_complete=True)
    assert [r.timestamp for r in d.new_rows] == [1500, 1000] and d.stop is True


def test_out_of_order_page_raises_not_silent_skip():
    """THE ADVERSARIAL CASE. watermark=1000, page=[900, 2000, 1500] (NOT newest-first). A naive
    'stop at the first row < watermark' would stop on 900 at index 0 and SKIP 2000 & 1500 -- two
    trades NEWER than the watermark, silently lost. We ASSERT descending and RAISE instead."""
    page = _rows(900, 2000, 1500)
    with pytest.raises(search.OutOfOrderPage):
        search.page_new_rows(page, watermark_ts=1000, backfill_complete=True)


def test_sorted_variant_of_same_rows_captures_the_new_ones():
    # positive control: the SAME timestamps, correctly newest-first, DO capture the > wm rows
    page = _rows(2000, 1500, 900)
    d = search.page_new_rows(page, watermark_ts=1000, backfill_complete=True)
    assert [r["resolved_ts"] for r in d.new_rows] == [2000, 1500] and d.stop is True


def test_out_of_order_only_asserted_in_incremental_mode():
    # the same unsorted page in FULL mode must NOT raise (order is irrelevant when we keep it all)
    page = _rows(900, 2000, 1500)
    d = search.page_new_rows(page, watermark_ts=None, backfill_complete=False)
    assert d.new_rows == page and d.stop is False


def test_watermark_without_complete_backfill_is_full_mode():
    # a watermark exists but the wallet was never fully backfilled -> DO NOT trust it (partial history);
    # full-page, and do not assert order (so an unsorted partial page cannot raise here)
    page = _rows(900, 2000, 1500)
    d = search.page_new_rows(page, watermark_ts=1000, backfill_complete=False)
    assert d.new_rows == page and d.stop is False


def test_empty_page():
    d = search.page_new_rows([], watermark_ts=1000, backfill_complete=True)
    assert d.new_rows == [] and d.stop is False


# ════════════════════════════════════════════════ selection ════════════════════════════════════════════

def _stat(wallet, category, n_resolved, roi, *, last_resolved_ts=RECENT_TS, has_open=False, win_rate=None):
    return search.WalletCategoryStat(
        wallet=wallet, category=category, n_resolved=n_resolved, roi=roi,
        last_resolved_ts=last_resolved_ts, has_open_position=has_open, win_rate=win_rate,
    )


def _sel(stats, **kw):
    return search.select_candidates(stats, now_ts=NOW, **kw)


def test_allowlist_excludes_non_ruled_categories():
    stats = [
        _stat("w1", "mlb", 60, 0.20),
        _stat("w2", "cbb", 60, 0.50),      # excluded category
        _stat("w3", "unknown", 99, 0.99),  # excluded category
        _stat("w4", "nascar", 80, 0.40),   # derivable but NOT in the 15
    ]
    res = _sel(stats)
    got = {c.category for c in res.candidates}
    assert got == {"mlb"}
    assert res.excluded[search.EX_CATEGORY_NOT_ALLOWED] == 3


def test_recency_gate_dormant_excluded_open_position_and_settled_pass():
    stats = [
        _stat("dorm", "mlb", 60, 0.30, last_resolved_ts=OLD_TS, has_open=False),   # dormant -> out
        _stat("open", "mlb", 60, 0.20, last_resolved_ts=OLD_TS, has_open=True),    # open proxy -> in
        _stat("sett", "mlb", 60, 0.10, last_resolved_ts=RECENT_TS, has_open=False),# settled recent -> in
    ]
    res = _sel(stats)
    by_w = {c.wallet: c for c in res.candidates}
    assert set(by_w) == {"open", "sett"}
    assert by_w["open"].recent_reason == "open_position"
    assert by_w["sett"].recent_reason == "settled_recent"
    assert res.excluded[search.EX_NOT_RECENT] == 1


def test_recency_boundary_exactly_at_window_is_recent():
    at_edge = NOW - 30 * DAY
    res = _sel([_stat("edge", "mlb", 60, 0.1, last_resolved_ts=at_edge)])
    assert [c.wallet for c in res.candidates] == ["edge"]
    just_out = NOW - 30 * DAY - 1
    res2 = _sel([_stat("out", "mlb", 60, 0.1, last_resolved_ts=just_out)])
    assert res2.candidates == []
    assert res2.excluded[search.EX_NOT_RECENT] == 1


def test_normal_returns_all_qualifiers_no_cap_no_thin_flag():
    # 12 mlb qualifiers (>= 10) -> ALL returned (Q3 no top-K cap), none thin-sample, ranked by roi desc
    stats = [_stat(f"q{i:02d}", "mlb", 50 + i, 0.30 - i * 0.01) for i in range(12)]
    res = _sel(stats)
    assert len(res.candidates) == 12
    assert all(not c.thin_sample for c in res.candidates)
    assert "mlb" not in res.thin_sample_categories
    rois = [c.roi for c in res.candidates]
    assert rois == sorted(rois, reverse=True)
    assert [c.rank_in_category for c in res.candidates] == list(range(1, 13))
    assert res.excluded[search.EX_BELOW_FALLBACK_CAP] == 0


def test_non_fallback_category_counts_sub_floor_drops():
    # 10 qualifiers (>= target) + 3 recent sub-50 -> qualifiers returned, the 3 subs counted (not silent)
    stats = [_stat(f"q{i}", "mlb", 60, 0.30 - i * 0.01) for i in range(10)]
    stats += [_stat("s1", "mlb", 10, 0.99), _stat("s2", "mlb", 20, 0.98), _stat("s3", "mlb", 5, 0.97)]
    res = _sel(stats)
    assert len(res.candidates) == 10 and all(not c.thin_sample for c in res.candidates)
    assert "mlb" not in res.thin_sample_categories
    assert res.excluded[search.EX_BELOW_MIN_RESOLVED] == 3
    assert res.excluded[search.EX_BELOW_FALLBACK_CAP] == 0


def test_selection_accounting_is_complete():
    # discovered eligible rows == candidates + every excluded bucket (no row vanishes uncounted)
    stats = [
        _stat("a", "mlb", 60, 0.2), _stat("b", "mlb", 10, 0.9),          # 1 qual + 1 sub (fallback: both surface)
        _stat("c", "cbb", 60, 0.5),                                       # allowlist reject
        _stat("d", "mlb", 60, 0.3, last_resolved_ts=OLD_TS),             # dormant
        _stat("e", "mlb", 99, None),                                      # no roi
    ]
    res = _sel(stats)
    total_excluded = sum(res.excluded.values())
    assert len(res.candidates) + total_excluded == len(stats)


def test_thin_sample_fallback_top_target_flags_sub_floor():
    qual = [_stat("q1", "mlb", 60, 0.30), _stat("q2", "mlb", 55, 0.10), _stat("q3", "mlb", 80, 0.05)]
    subs = [
        _stat("s1", "mlb", 10, 0.40), _stat("s2", "mlb", 20, 0.35), _stat("s3", "mlb", 5, 0.25),
        _stat("s4", "mlb", 30, 0.20), _stat("s5", "mlb", 15, 0.15), _stat("s6", "mlb", 40, 0.12),
        _stat("s7", "mlb", 8, 0.08), _stat("s8", "mlb", 12, 0.02), _stat("s9", "mlb", 3, -0.05),
        _stat("s10", "mlb", 7, -0.10), _stat("s11", "mlb", 25, -0.20),
    ]
    res = _sel(qual + subs)                       # 3 qualifiers < 10 -> fallback, top 10 of 14 eligible
    assert "mlb" in res.thin_sample_categories
    assert [c.wallet for c in res.candidates] == \
        ["s1", "s2", "q1", "s3", "s4", "s5", "s6", "q2", "s7", "q3"]   # by cost-ROI desc
    thin = {c.wallet for c in res.candidates if c.thin_sample}
    assert thin == {"s1", "s2", "s3", "s4", "s5", "s6", "s7"}          # sub-50 flagged; q1/q2/q3 not
    assert {"q1", "q2", "q3"}.isdisjoint(thin)
    assert res.excluded[search.EX_BELOW_FALLBACK_CAP] == 4             # s8,s9,s10,s11 dropped, counted


def test_fallback_caps_at_target_even_with_large_pool():
    stats = [_stat(f"s{i:02d}", "golf", 10, 0.50 - i * 0.01) for i in range(20)]   # 20 recent sub-50
    res = _sel(stats)
    assert len(res.candidates) == 10
    assert all(c.thin_sample for c in res.candidates)
    assert res.excluded[search.EX_BELOW_FALLBACK_CAP] == 10
    assert "golf" in res.thin_sample_categories


def test_fallback_fewer_than_target_returns_all_no_manufacturing():
    stats = [_stat("g1", "golf", 10, 0.3), _stat("g2", "golf", 20, 0.2), _stat("g3", "golf", 5, 0.1)]
    res = _sel(stats)
    assert [c.wallet for c in res.candidates] == ["g1", "g2", "g3"]
    assert all(c.thin_sample for c in res.candidates)
    assert res.excluded[search.EX_BELOW_FALLBACK_CAP] == 0


def test_ranks_on_cost_roi_never_win_rate():
    # A has the far better win% but worse cost-ROI; ranking MUST prefer B (higher roi)
    a = _stat("A", "mlb", 60, 0.05, win_rate=0.90)
    b = _stat("B", "mlb", 60, 0.30, win_rate=0.40)
    res = _sel([a, b])
    assert [c.wallet for c in res.candidates] == ["B", "A"]
    assert res.candidates[0].win_rate == 0.40   # carried for display, did not drive the rank


def test_roi_none_excluded_not_ranked():
    stats = [_stat("has", "mlb", 60, 0.2), _stat("none", "mlb", 99, None)]
    res = _sel(stats)
    assert [c.wallet for c in res.candidates] == ["has"]
    assert res.excluded[search.EX_NO_COST_ROI] == 1


def test_roi_nan_or_inf_excluded_like_none():
    # non-finite roi is out of db.py contract but would silently scramble the sort -> treat like None
    stats = [
        _stat("ok", "mlb", 60, 0.2),
        _stat("nan", "mlb", 60, float("nan")),
        _stat("inf", "mlb", 60, float("inf")),
    ]
    res = _sel(stats)
    assert [c.wallet for c in res.candidates] == ["ok"]
    assert res.excluded[search.EX_NO_COST_ROI] == 2


def test_category_normalized_before_allowlist():
    # uppercase / whitespace categories must normalize to the canonical lowercase before the allowlist,
    # else every real category would fall through to category_not_allowed and NOTHING would surface.
    stats = [
        _stat("u", "MLB", 60, 0.3),
        _stat("w", "  mlb  ", 60, 0.2),
        _stat("x", "Ufc", 60, 0.1),
    ]
    res = _sel(stats)
    assert {c.category for c in res.candidates} == {"mlb", "ufc"}
    assert res.excluded[search.EX_CATEGORY_NOT_ALLOWED] == 0


def test_nine_qualifiers_falls_back_ten_does_not():
    # the exact off-by-one witness: 9 qualifiers -> fallback (thin-sample); 10 -> normal (no fallback)
    nine = [_stat(f"q{i}", "mlb", 60, 0.30 - i * 0.01) for i in range(9)]
    assert "mlb" in _sel(nine).thin_sample_categories
    ten = [_stat(f"q{i}", "mlb", 60, 0.30 - i * 0.01) for i in range(10)]
    assert "mlb" not in _sel(ten).thin_sample_categories


def test_fallback_qualifier_ranked_out_of_top_target():
    # a fallback category where the 2 qualifiers have the WORST cost-ROI: 10 higher-ROI sub-50 rows fill
    # the top-10, both qualifiers rank OUT -> counted in below_fallback_cap, accounting still balances.
    quals = [_stat("qlo1", "mlb", 60, -0.50), _stat("qlo2", "mlb", 80, -0.60)]   # qualifiers, awful ROI
    subs = [_stat(f"s{i:02d}", "mlb", 10, 0.40 - i * 0.01) for i in range(12)]    # 12 sub-50, better ROI
    res = _sel(quals + subs)                        # 2 qualifiers < 10 -> fallback
    assert "mlb" in res.thin_sample_categories
    assert len(res.candidates) == 10
    assert all(c.thin_sample for c in res.candidates)               # all 10 chosen are sub-50
    assert "qlo1" not in {c.wallet for c in res.candidates}         # the qualifiers ranked out
    assert "qlo2" not in {c.wallet for c in res.candidates}
    assert res.excluded[search.EX_BELOW_FALLBACK_CAP] == 4          # 14 eligible - 10 chosen
    assert len(res.candidates) + sum(res.excluded.values()) == 14


def test_non_default_params_plumb_through():
    # override min_resolved + thin_sample_target: with floor=100 and target=3, an n=60 row is sub-floor
    stats = [_stat("a", "mlb", 60, 0.3), _stat("b", "mlb", 150, 0.2)]
    res = search.select_candidates(stats, now_ts=NOW, min_resolved=100, thin_sample_target=3)
    by_w = {c.wallet: c for c in res.candidates}
    assert by_w["a"].thin_sample is True and by_w["b"].thin_sample is False


def test_tie_break_more_evidence_then_wallet():
    same_roi = [
        _stat("bbb", "mlb", 50, 0.2),
        _stat("aaa", "mlb", 80, 0.2),   # more evidence -> ranks first
        _stat("aaa2", "mlb", 80, 0.2),  # same roi & n as aaa -> wallet asc: 'aaa2' vs 'bbb'
    ]
    res = _sel(same_roi)
    order = [c.wallet for c in res.candidates]
    assert order.index("aaa") < order.index("aaa2") < order.index("bbb")


def test_recency_respected_inside_fallback():
    # 2 recent + 5 dormant sub-50 golf -> fallback surfaces ONLY the 2 recent; dormant counted not_recent
    recent = [_stat("r1", "golf", 10, 0.30), _stat("r2", "golf", 20, 0.20)]
    dormant = [_stat(f"d{i}", "golf", 10, 0.90, last_resolved_ts=OLD_TS) for i in range(5)]
    res = _sel(recent + dormant)
    assert {c.wallet for c in res.candidates} == {"r1", "r2"}
    assert res.excluded[search.EX_NOT_RECENT] == 5
    assert res.excluded[search.EX_BELOW_FALLBACK_CAP] == 0


def test_multi_category_grouped_and_sorted():
    stats = [
        _stat("m1", "mlb", 60, 0.2), _stat("u1", "ufc", 60, 0.5),
        _stat("m2", "mlb", 60, 0.4), _stat("u2", "ufc", 60, 0.1),
    ]
    res = _sel(stats)
    # categories alpha, then rank within: mlb(m2,m1), ufc(u1,u2)
    assert [(c.category, c.wallet) for c in res.candidates] == \
        [("mlb", "m2"), ("mlb", "m1"), ("ufc", "u1"), ("ufc", "u2")]
    assert all(c.rank_in_category in (1, 2) for c in res.candidates)


def test_allowlist_has_exactly_15_and_excludes_the_probed():
    assert len(search.CATEGORY_ALLOWLIST) == 15
    for c in ("mlb", "nba", "nfl", "nhl", "wnba", "epl", "ucl", "soccer",
              "atp", "wta", "tennis", "cs2", "golf", "ufc", "fed"):
        assert c in search.CATEGORY_ALLOWLIST
    for c in ("cbb", "fifwc", "nascar", "unknown"):
        assert c not in search.CATEGORY_ALLOWLIST


def test_loss_omission_caveat_names_the_bias():
    cav = search.LOSS_OMISSION_CAVEAT
    assert isinstance(cav, str) and cav
    low = cav.lower()
    assert "cost-roi" in low and "screen" in low and "under-report" in low


# ════════════════════════════════════════════ R7-independence ══════════════════════════════════════════

def test_search_does_not_import_order_path():
    """Structural: Stage 4 SEARCH must not IMPORT the order path (R7 separate + untouched).

    Parse real import statements via AST -- NOT a substring scan (a raw `"arm" in src` false-matches
    'farm', and a text scan also misses a dynamic import). We check the last dotted segment of each
    imported module against the forbidden set, so 'trading_corp.prediction_markets.arm' is caught while
    the word 'farm' in a comment is not."""
    import ast

    forbidden = {"execution", "arm", "live_driver", "kalshi_live", "brokers", "kalshi", "boot_reconcile"}
    tree = ast.parse(inspect.getsource(search))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.update(a.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.update(node.module.split("."))
            for a in node.names:
                imported.add(a.name)
    leaked = imported & forbidden
    assert not leaked, f"search.py must not import the order path; leaked: {sorted(leaked)}"
