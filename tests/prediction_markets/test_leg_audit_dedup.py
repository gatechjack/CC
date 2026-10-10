"""Leg-audit banner dedup + FILLED/NO_FILL headline split (display-layer fix 2026-10-10).

The /live banner surfaced 19 rows that were ALL the same verdict (code_review:code_not_in_outcome:TS!<Spirit)
across 2 tickers -- 2 filled + 17 no-fill. The fix: count FILLED (positions at risk) separately from NO_FILL
(attempts) in the headline, and COLLAPSE only NO_FILL rows (one line per (ticker,leg,outcome) with a count).

★ FILLED rows NEVER collapse -- each filled position is money at risk and keeps its own line/id/status.
★ NO_FILL rows are GROUPED, never suppressed/hidden.
Expected values are HAND-COMPUTED from the fixture, never read back from the function under test.
"""
import sqlite3

from trading_corp.prediction_markets import db
from trading_corp.prediction_markets import leg_audit as LA

V = "code_review:code_not_in_outcome:TS!<Spirit"   # the live soft verdict (all 19 current rows)


def _conn(p):
    c = sqlite3.connect(p); c.row_factory = sqlite3.Row; return c


def _seed(conn, rows):
    # rows: (account, category, ticker, leg, signal_outcome, leg_audit, outcome_status)
    for i, (acct, cat, tk, leg, sig, audit, status) in enumerate(rows, 1):
        conn.execute(
            "INSERT INTO pm_subdivision_order (account_id, category, ticker, outcome_leg, signal_outcome, "
            "leg_audit, is_exit, dry_run, outcome_status, response_ts) VALUES (?,?,?,?,?,?,0,0,?,?)",
            (acct, cat, tk, leg, sig, audit, status, 1000 + i))
    conn.commit()


def test_dedup_collapses_nofill_only_and_splits_filled(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p); conn = _conn(p)
    T1 = "KXCS2GAME-26OCT091200TSFAL-TS"    # 1 FILLED (the TSFAL Spirit position)
    T2 = "KXCS2GAME-26OCT101300TSMOUZ-TS"   # 1 FILLED + 17 NO_FILL (the TSMOUZ run)
    rows = [("kalshi_jack", "cs2", T1, "yes", "Spirit", V, "filled"),
            ("kalshi_jack", "cs2", T2, "yes", "Spirit", V, "filled")]
    rows += [("kalshi_jack", "cs2", T2, "yes", "Spirit", V, "no_fill") for _ in range(17)]
    _seed(conn, rows)
    s = LA.read_leg_audit_reviews(conn)
    # HAND-COMPUTED (19 rows = 2 filled + 17 no_fill; 2 tickers):
    assert s["total_surfaced"] == 19                           # raw population UNCHANGED (every flagged order)
    assert s["status_counts"] == {"filled": 2, "no_fill": 17}  # the headline split
    assert s["total_lines"] == 3                               # 2 filled lines + 1 collapsed NO_FILL tuple
    assert len(s["rows"]) == 3
    byk = {(r["ticker"], r["filled"]): r for r in s["rows"]}
    assert byk[(T1, True)]["dup_count"] == 1                   # filled line -> never collapsed
    assert byk[(T2, True)]["dup_count"] == 1                   # the OTHER filled line stays separate
    assert byk[(T2, False)]["dup_count"] == 17                 # the 17 no-fill attempts collapsed into ONE line
    filled_ids = sorted(r["id"] for r in s["rows"] if r["filled"])
    assert len(filled_ids) == 2 and filled_ids[0] != filled_ids[1]   # two distinct filled ids, not merged


def test_two_filled_rows_same_tuple_do_not_merge(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p); conn = _conn(p)
    T = "KXCS2GAME-26OCT101300TSMOUZ-TS"
    _seed(conn, [("kalshi_jack", "cs2", T, "yes", "Spirit", V, "filled"),
                 ("kalshi_jack", "cs2", T, "yes", "Spirit", V, "filled")])   # SAME (ticker,leg,outcome), BOTH filled
    s = LA.read_leg_audit_reviews(conn)
    assert s["total_surfaced"] == 2 and s["status_counts"] == {"filled": 2, "no_fill": 0}
    assert s["total_lines"] == 2 and len(s["rows"]) == 2        # NOT collapsed -> two separate filled lines
    assert all(r["dup_count"] == 1 for r in s["rows"])


def test_all_nofill_collapse_to_one_line_never_suppressed(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p); conn = _conn(p)
    T = "KXCS2GAME-26OCT101300TSMOUZ-TS"
    _seed(conn, [("kalshi_jack", "cs2", T, "yes", "Spirit", V, "no_fill") for _ in range(17)])
    s = LA.read_leg_audit_reviews(conn)
    assert s["total_surfaced"] == 17 and s["status_counts"] == {"filled": 0, "no_fill": 17}
    assert s["total_lines"] == 1 and len(s["rows"]) == 1        # GROUPED into one line, NOT hidden
    assert s["rows"][0]["dup_count"] == 17 and s["rows"][0]["filled"] is False


def test_cap_keeps_inversion_line_and_counts_stay_honest(tmp_path):
    # an OLDER inversion (filled) must survive the cap ahead of a wall of newer NO_FILL soft lines; counts honest
    p = str(tmp_path / "pm.db"); db.init_db(p); conn = _conn(p)
    rows = [("kalshi_jack", "mls", "KXMLSGAME-OLD-LAFC", "yes", "No", "REVIEW:soccer_leg!=outcome:yes/No", "filled")]
    rows += [("kalshi_jack", "cs2", "KXCS2-%d" % i, "yes", "x", "code_review:code_not_in_outcome:A!<b", "no_fill")
             for i in range(60)]   # 60 DISTINCT-ticker no_fill lines -> 60 distinct lines, all newer
    _seed(conn, rows)
    s = LA.read_leg_audit_reviews(conn, limit=50)
    assert s["total_surfaced"] == 61            # honest over all matches
    assert s["total_lines"] == 61               # 1 inversion + 60 distinct no_fill tuples (none collapse together)
    assert s["shown"] == 50                     # capped to 50 lines
    assert s["rows"][0]["state"] == LA.STATE_INVERSION   # inversion leads, never truncated by the soft wall
