"""RUNG 3 -- observability: persist the whale's signal-time side + an INDEPENDENT post-fill leg audit.

The audit found 188 of 234 real entries UNVERIFIABLE because we stored signal_id but not the whale's
outcome/slug at copy time -- so a wrong-side fill could never be told apart from a whale flip post-hoc.
Migration 021 adds signal_outcome / signal_slug / leg_audit to pm_subdivision_order; _record_order
persists the copy INTENT and an INDEPENDENT re-derivation of (ticker, leg) that shares no transform
with the matcher (the no-leg-lens net -- previously "Decision.leg is NEVER re-derived, nothing downstream
compares the whale outcome to the chosen side").
"""
from __future__ import annotations

import sqlite3

from trading_corp.prediction_markets import db
from trading_corp.prediction_markets.live_driver import _audit_leg_independent


# ── migration 021 ──────────────────────────────────────────────────────────────────────────
def test_schema_head_is_21_and_contiguous():
    vers = sorted(v for v, _ in db.MIGRATIONS)
    assert vers == list(range(1, db.SCHEMA_HEAD + 1)), vers
    assert db.SCHEMA_HEAD == 21, db.SCHEMA_HEAD


def test_migration_021_adds_columns_and_is_idempotent(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    db.init_db(p)   # idempotent -- second run must not raise or double-apply
    con = sqlite3.connect(p)
    ver = con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    cols = [r[1] for r in con.execute("PRAGMA table_info(pm_subdivision_order)")]
    con.close()
    assert ver == 21, ver
    for c in ("signal_outcome", "signal_slug", "leg_audit"):
        assert c in cols, (c, cols)


# ── the independent leg audit (pure, no matcher transform shared) ────────────────────────────
def test_audit_name_family_code_matches_outcome_is_ok():
    assert _audit_leg_independent("cs2", "magic", "KXCS2GAME-26SEP082300FAZEMGC-MGC", "yes") == "ok"
    assert _audit_leg_independent("atp", "Arthur Rinderknech", "KXATPMATCH-26SEP04MEDRIN-RIN", "yes") == "ok"


def test_audit_name_family_wrong_code_flags_code_review():
    """If the magic->FaZe class ever slipped past the matcher, the audit trail records it (soft, persisted)."""
    v = _audit_leg_independent("cs2", "magic", "KXCS2GAME-26SEP082300FAZEMGC-FAZE", "yes")
    assert v.startswith("code_review:"), v


def test_audit_name_family_team_liquid_is_soft_not_alarm():
    """Team Liquid->TL is a legit non-subsequence: 'code_review' (persisted), NOT a REVIEW alarm."""
    v = _audit_leg_independent("cs2", "Liquid", "KXCS2GAME-26SEP101300TLNAVI-TL", "yes")
    assert v.startswith("code_review:") and not v.startswith("REVIEW"), v


def test_audit_soccer_no_with_yes_leg_is_REVIEW():
    """The unambiguous leg inversion the audit exists to catch: whale 'No' but we recorded leg 'yes'."""
    v = _audit_leg_independent("mls", "No", "KXMLSGAME-26SEP09LAFCNYRB-LAFC", "yes")
    assert v.startswith("REVIEW:soccer_leg"), v


def test_audit_soccer_no_with_no_leg_is_ok():
    assert _audit_leg_independent("mls", "No", "KXMLSGAME-26SEP09LAFCNYRB-LAFC", "no") == "ok"


def test_audit_total_over_with_no_leg_is_REVIEW():
    v = _audit_leg_independent("nfl", "Over", "KXNFLTOTAL-26SEP13NODET-50", "no")
    assert v.startswith("REVIEW:total_leg"), v


def test_audit_total_over_with_yes_leg_is_ok():
    assert _audit_leg_independent("nfl", "Over", "KXNFLTOTAL-26SEP13NODET-50", "yes") == "ok"


def test_audit_structural_moneyline_is_na():
    """Structural moneyline/spread are code-anchored in the matcher (KXNFLGAME) -> audit is 'na' here."""
    assert _audit_leg_independent("nfl", "New Orleans Saints", "KXNFLGAME-26SEP13NODET-NO", "yes") == "na"


def test_audit_empty_outcome_is_unchecked():
    assert _audit_leg_independent("cs2", "", "KXCS2GAME-26SEP082300FAZEMGC-MGC", "yes") == "unchecked"
