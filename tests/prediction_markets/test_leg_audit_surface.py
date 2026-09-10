"""RUNG 1 (red-first) for the leg_audit REVIEW-surfacing follow-up.

A monitor that cries wolf gets ignored; a monitor whose output nobody reads is no monitor. These tests pin:
  (1) the canonical classifier maps every verdict to the RIGHT one of five states -- and the three surfaced states
      (inversion / soft / unevaluated) are DISTINCT, with an unknown/unreadable verdict failing SAFE to unevaluated
      (never a silent pass);
  (2) the read side agrees with the WRITE side -- classify(_audit_leg_independent(...)) lands where expected, so the
      two transforms cannot drift apart (the exact failure class we spent today fixing);
  (3) the reader surfaces a seeded REVIEW loudly and does NOT false-alarm on clean/legacy rows, and degrades honestly
      on a pre-migration schema.
"""
from __future__ import annotations

import sqlite3

from trading_corp.prediction_markets import leg_audit as LA
from trading_corp.prediction_markets import db
from trading_corp.prediction_markets.live_driver import _audit_leg_independent


# ── (1) canonical classifier: five distinct states, fail-safe on the unknown ────────────────
def test_classify_five_states_distinct():
    assert LA.classify_leg_audit("REVIEW:soccer_leg!=outcome:yes/No") == LA.STATE_INVERSION
    assert LA.classify_leg_audit("REVIEW:total_leg!=outcome:no/Over") == LA.STATE_INVERSION
    assert LA.classify_leg_audit("code_review:code_not_in_outcome:FAZE!<magic") == LA.STATE_SOFT
    assert LA.classify_leg_audit("unchecked") == LA.STATE_UNEVALUATED
    assert LA.classify_leg_audit("ok") == LA.STATE_CLEAN
    assert LA.classify_leg_audit("na") == LA.STATE_CLEAN
    assert LA.classify_leg_audit(None) == LA.STATE_LEGACY
    assert LA.classify_leg_audit("") == LA.STATE_UNEVALUATED   # non-null blank == unreadable -> surfaced, not dropped


def test_unknown_verdict_fails_safe_to_unevaluated_not_clean():
    """An unreadable audit is NOT a pass: an unrecognised non-null verdict must be UNEVALUATED, never CLEAN."""
    assert LA.classify_leg_audit("weird_future_value") == LA.STATE_UNEVALUATED
    assert LA.classify_leg_audit("REVIEWISH") == LA.STATE_INVERSION   # startswith REVIEW -> still loud (safe)


def test_legacy_is_distinct_from_unevaluated():
    """LEGACY (NULL, pre-021) must not be confused with UNEVALUATED (audit ran, could not decide)."""
    assert LA.classify_leg_audit(None) == LA.STATE_LEGACY
    assert LA.classify_leg_audit("unchecked") == LA.STATE_UNEVALUATED
    assert LA.STATE_LEGACY not in LA.SURFACED_STATES
    assert LA.STATE_UNEVALUATED in LA.SURFACED_STATES


# ── (2) read side agrees with the write side (no two-transform drift) ────────────────────────
def test_read_side_agrees_with_writer():
    T_LAFC = "KXMLSGAME-26SEP09LAFCNYRB-LAFC"
    T_TOT = "KXNFLTOTAL-26SEP13NODET-50"
    cases = [
        (("mls", "No", T_LAFC, "yes"), LA.STATE_INVERSION),   # soccer leg inversion
        (("mls", "No", T_LAFC, "no"), LA.STATE_CLEAN),        # soccer correct
        (("nfl", "Over", T_TOT, "no"), LA.STATE_INVERSION),   # total inversion
        (("nfl", "Over", T_TOT, "yes"), LA.STATE_CLEAN),      # total correct
        (("cs2", "magic", "KXCS2GAME-26SEP082300FAZEMGC-FAZE", "yes"), LA.STATE_SOFT),  # name code mismatch
        (("cs2", "magic", "KXCS2GAME-26SEP082300FAZEMGC-MGC", "yes"), LA.STATE_CLEAN),  # name code ok
        (("nfl", "New Orleans Saints", "KXNFLGAME-26SEP13NODET-NO", "yes"), LA.STATE_CLEAN),  # structural moneyline -> na
        (("cs2", "", "KXCS2GAME-26SEP082300FAZEMGC-MGC", "yes"), LA.STATE_UNEVALUATED),       # empty outcome -> unchecked
    ]
    for args, expected in cases:
        verdict = _audit_leg_independent(*args)
        assert LA.classify_leg_audit(verdict) == expected, (args, verdict, expected)


# ── (3) reader: surfaces REVIEW, no false alarm on clean/legacy, honest-empty pre-migration ──
def _seed(conn, rows):
    for i, (acct, cat, tk, leg, sig, audit, dry) in enumerate(rows, 1):
        conn.execute(
            "INSERT INTO pm_subdivision_order (account_id, category, ticker, outcome_leg, signal_outcome, "
            "leg_audit, is_exit, dry_run, outcome_status, response_ts) VALUES (?,?,?,?,?,?,0,?,?,?)",
            (acct, cat, tk, leg, sig, audit, dry, "filled", 1000 + i))
    conn.commit()


def _conn(p):
    c = sqlite3.connect(p); c.row_factory = sqlite3.Row; return c


def test_reader_surfaces_review_and_separates_states(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p); conn = _conn(p)
    _seed(conn, [
        ("kalshi_jack", "mls", "KXMLSGAME-A-LAFC", "yes", "No", "REVIEW:soccer_leg!=outcome:yes/No", 0),   # inversion
        ("kalshi_jack", "cs2", "KXCS2GAME-B-FAZE", "yes", "magic", "code_review:code_not_in_outcome:FAZE!<magic", 0),  # soft
        ("kalshi_karen", "cs2", "KXCS2GAME-C-XX", "yes", "someorg", "unchecked", 0),   # unevaluated
        ("kalshi_karen", "nfl", "KXNFLGAME-D-NO", "yes", "New Orleans Saints", "na", 0),  # clean -> not surfaced
        ("kalshi_jack", "nfl", "KXNFLTOTAL-E-50", "yes", "Over", "ok", 0),               # clean -> not surfaced
        ("kalshi_jack", "mlb", "KXMLBGAME-F-NYY", "yes", None, None, 0),                 # legacy (NULL) -> not surfaced
        ("kalshi_jack", "mls", "KXMLSGAME-G-LAFC", "yes", "No", "REVIEW:soccer_leg!=outcome:yes/No", 1),   # dry_run -> excluded
    ])
    s = LA.read_leg_audit_reviews(conn)
    assert s["schema_ok"] is True
    assert s["counts"][LA.STATE_INVERSION] == 1
    assert s["counts"][LA.STATE_SOFT] == 1
    assert s["counts"][LA.STATE_UNEVALUATED] == 1
    assert s["total_surfaced"] == 3     # ok/na/legacy/dry_run all excluded
    assert s["rows"][0]["state"] == LA.STATE_INVERSION or any(r["state"] == LA.STATE_INVERSION for r in s["rows"])
    # every surfaced row carries a human state label distinct from the others
    labels = {r["state"]: r["state_label"] for r in s["rows"]}
    assert len({v for v in labels.values()}) == len(labels)
    # account scoping
    s_jack = LA.read_leg_audit_reviews(conn, account_ids=["kalshi_jack"])
    assert s_jack["counts"][LA.STATE_UNEVALUATED] == 0   # karen's unevaluated excluded
    assert s_jack["total_surfaced"] == 2


def test_reader_no_false_alarm_when_all_clean(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p); conn = _conn(p)
    _seed(conn, [
        ("kalshi_jack", "nfl", "KXNFLGAME-A-NO", "yes", "New Orleans Saints", "na", 0),
        ("kalshi_jack", "nfl", "KXNFLTOTAL-B-50", "yes", "Over", "ok", 0),
        ("kalshi_jack", "mlb", "KXMLBGAME-C-NYY", "yes", None, None, 0),   # legacy
    ])
    s = LA.read_leg_audit_reviews(conn)
    assert s["total_surfaced"] == 0 and s["rows"] == []


def test_reader_honest_empty_on_pre_migration_schema(tmp_path):
    p = str(tmp_path / "old.db"); conn = _conn(p)
    conn.execute("CREATE TABLE pm_subdivision_order (id INTEGER PRIMARY KEY, account_id TEXT, category TEXT, dry_run INTEGER)")
    conn.commit()
    s = LA.read_leg_audit_reviews(conn)
    assert s["schema_ok"] is False and s["total_surfaced"] == 0


def test_reader_empty_account_scope_surfaces_nothing(tmp_path):
    """A viewer scoped to ZERO accounts ([]) must see NOTHING -- [] must NEVER fall through to all-accounts (a leak).
    None (the operator/CLI runner) stays unscoped and sees everything. This pins the cross-account-leak fix."""
    p = str(tmp_path / "pm.db"); db.init_db(p); conn = _conn(p)
    _seed(conn, [("kalshi_jack", "mls", "KXMLSGAME-A-LAFC", "yes", "No", "REVIEW:soccer_leg!=outcome:yes/No", 0)])
    unscoped = LA.read_leg_audit_reviews(conn)                    # None -> operator, sees it
    scoped_none = LA.read_leg_audit_reviews(conn, account_ids=[]) # [] -> viewer owns no account, sees nothing
    assert unscoped["total_surfaced"] == 1
    assert scoped_none["schema_ok"] is True
    assert scoped_none["total_surfaced"] == 0 and scoped_none["rows"] == []


def test_reader_inversion_never_truncated_below_cap(tmp_path):
    """An OLDER inversion must survive the row cap ahead of a wall of newer soft/unevaluated rows (severity-first),
    and the honest count still reflects everything even when rows are capped."""
    p = str(tmp_path / "pm.db"); db.init_db(p); conn = _conn(p)
    rows = [("kalshi_jack", "mls", "KXMLSGAME-OLD-LAFC", "yes", "No", "REVIEW:soccer_leg!=outcome:yes/No", 0)]  # oldest ts
    for i in range(60):
        rows.append(("kalshi_jack", "cs2", "KXCS2-%d" % i, "yes", "x", "unchecked", 0))   # 60 NEWER unevaluated
    _seed(conn, rows)   # _seed stamps response_ts = 1000+i in order -> inversion is the OLDEST
    s = LA.read_leg_audit_reviews(conn, limit=50)
    assert s["counts"][LA.STATE_INVERSION] == 1
    assert s["total_surfaced"] == 61 and s["shown"] == 50    # count honest; rows capped
    assert s["rows"][0]["state"] == LA.STATE_INVERSION       # inversion leads, never truncated
    assert any(r["state"] == LA.STATE_INVERSION for r in s["rows"])
