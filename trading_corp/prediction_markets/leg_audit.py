"""Read-side surfacing for the pm_subdivision_order.leg_audit verdict (Rung-3 observability, follow-up 2026-09-10).

The write side (`live_driver._audit_leg_independent`) stamps every real order with an INDEPENDENT re-derivation of
the chosen (ticker, leg). Until now nothing READ it back -- a check whose output nobody reads is a check that has
silently stopped checking. This module is the read side: a single CANONICAL classifier (so the runner and the UI
never diverge into two transforms) + a reader that aggregates the rows for a page-top strip.

★ THE THREE NON-CLEAN STATES ARE DISTINCT -- an unreadable audit is NOT a pass:
  INVERSION   (leg_audit LIKE 'REVIEW%')      -- soccer Yes/No or total Over/Under leg disagreed with the outcome. LOUD.
  SOFT        (leg_audit LIKE 'code_review%')  -- name-family (cs2/tennis/ufc) ticker CODE not a subsequence of the org.
  UNEVALUATED (leg_audit == 'unchecked' or an unknown verdict) -- the audit RAN but could not decide. Fail-safe: shown,
              never treated as a pass.
And the clean / out-of-scope:
  CLEAN       (leg_audit in {'ok','na'})       -- 'ok' verified; 'na' = no independent leg check applies (structural
              moneyline/spread are code-anchored in the matcher). Not a concern; NOT surfaced.
  LEGACY      (leg_audit IS NULL)              -- pre-migration-021 rows (the audit did not exist yet) or dry-run. This
              is DISTINCT from UNEVALUATED: legacy = before-the-check; unevaluated = the-check-could-not-decide.

Standalone by construction: stdlib only, no engine imports -> safe for the pm_web standalone-imports guard.
"""
from __future__ import annotations

STATE_INVERSION = "inversion"      # REVIEW: a real leg/side inversion -- the loud one
STATE_SOFT = "soft"                # code_review: name-family code-vs-outcome soft flag
STATE_UNEVALUATED = "unevaluated"  # unchecked / unknown verdict -- audit could not decide (NOT a pass)
STATE_CLEAN = "clean"              # ok | na
STATE_LEGACY = "legacy"            # NULL leg_audit (pre-021 / dry-run) -- out of the audit's scope

# the three states a page-top strip surfaces, most-severe first
SURFACED_STATES = (STATE_INVERSION, STATE_SOFT, STATE_UNEVALUATED)

_STATE_LABEL = {
    STATE_INVERSION: "LEG INVERSION",
    STATE_SOFT: "code review",
    STATE_UNEVALUATED: "unevaluated (could not check)",
    STATE_CLEAN: "clean",
    STATE_LEGACY: "legacy (pre-audit)",
}


def classify_leg_audit(leg_audit):
    """CANONICAL: map a raw pm_subdivision_order.leg_audit value to one of the STATE_* constants. Fail-safe: a
    NON-NULL verdict we do not recognise is UNEVALUATED (could-not-decide), never CLEAN -- an unreadable audit is
    not a pass. A NULL verdict is LEGACY (the audit did not run because the row predates migration 021)."""
    if leg_audit is None:
        return STATE_LEGACY
    v = str(leg_audit).strip()
    if v == "":
        return STATE_UNEVALUATED   # non-null but blank == unreadable, NOT pre-audit -> could-not-decide (never dropped)
    if v.startswith("REVIEW"):
        return STATE_INVERSION
    if v.startswith("code_review"):
        return STATE_SOFT
    if v == "unchecked":
        return STATE_UNEVALUATED
    if v in ("ok", "na"):
        return STATE_CLEAN
    return STATE_UNEVALUATED   # unknown non-null verdict -> could-not-decide, fail-safe (never a silent pass)


def state_label(state):
    return _STATE_LABEL.get(state, state)


def read_leg_audit_reviews(conn, account_ids=None, limit=50):
    """Aggregate the SURFACED (inversion / soft / unevaluated) leg_audit rows for the given accounts (all if None).
    Reads pm_subdivision_order (dry_run=0). Legacy (NULL) rows are EXCLUDED -- they predate the audit and are not a
    finding. Returns {counts:{state:n}, total_surfaced:int, by_account:{aid:{state:n}}, rows:[...] newest-first}.

    Degrades honestly on a pre-migration schema: if the leg_audit column is absent (schema < 21) it returns an
    empty summary with schema_ok=False rather than raising (mirrors the tile readers' honest-empty contract)."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pm_subdivision_order)")]
    if "leg_audit" not in cols:
        return {"schema_ok": False, "counts": {}, "total_surfaced": 0, "by_account": {}, "rows": [], "shown": 0}
    where = "dry_run=0 AND leg_audit IS NOT NULL"
    params = []
    if account_ids is not None:          # None = unscoped (operator / CLI runner). A list = scope to exactly these.
        if not account_ids:              # [] = the viewer can see NO account -> surface nothing. NEVER fall through
            return {"schema_ok": True, "counts": {s: 0 for s in SURFACED_STATES},   # to all-accounts (that is a leak).
                    "total_surfaced": 0, "by_account": {}, "rows": [], "shown": 0}
        where += " AND account_id IN (%s)" % ",".join("?" * len(account_ids))
        params.extend(account_ids)
    q = ("SELECT id, account_id, category, ticker, outcome_leg, signal_outcome, signal_slug, leg_audit, "
         "outcome_status, response_ts FROM pm_subdivision_order WHERE %s ORDER BY response_ts DESC, id DESC" % where)
    counts = {s: 0 for s in SURFACED_STATES}
    by_account = {}
    _sev = {s: i for i, s in enumerate(SURFACED_STATES)}   # inversion(0) < soft(1) < unevaluated(2)
    surfaced = []
    for r in conn.execute(q, params):
        st = classify_leg_audit(r["leg_audit"])
        if st not in SURFACED_STATES:
            continue
        counts[st] += 1                  # counts are over ALL matches -> the headline stays honest even when capped
        by_account.setdefault(r["account_id"], {s: 0 for s in SURFACED_STATES})[st] += 1
        surfaced.append((_sev[st], -(r["response_ts"] or 0), -(r["id"] or 0), {
            "id": r["id"], "account": r["account_id"], "category": r["category"], "ticker": r["ticker"],
            "outcome_leg": r["outcome_leg"], "signal_outcome": r["signal_outcome"], "signal_slug": r["signal_slug"],
            "leg_audit": r["leg_audit"], "state": st, "state_label": state_label(st),
            "outcome_status": r["outcome_status"], "response_ts": r["response_ts"],
        }))
    # severity-first, then newest, then id -- so an INVERSION is NEVER truncated below the cap by a wall of soft/uneval.
    surfaced.sort(key=lambda t: (t[0], t[1], t[2]))
    rows = [t[3] for t in surfaced[:limit]]
    return {
        "schema_ok": True,
        "counts": counts,
        "total_surfaced": sum(counts.values()),
        "by_account": by_account,
        "rows": rows,
        "shown": len(rows),              # < total_surfaced when capped -> the UI/runner say "showing N of M"
    }
