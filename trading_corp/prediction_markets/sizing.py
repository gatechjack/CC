"""Per-sub-division FLAT-CONTRACTS sizing -- the READ + the owner/admin-gated WRITE behind the /live sizing control
(2026-09-12). The engine reads `pm_subdivision.contracts` PER CYCLE (execution.py:544 via live_driver.py:1080), so a
change here takes effect on the next ~7s cycle with NO engine restart. This module is pm_web-SIDE ONLY -- the running
trading engine (live_driver / execution) never imports it. It owns the UI-side sizing WRITE + the pm_web-owned AUDIT
(pm_subdivision_sizing_audit, migration 022), written in ONE transaction alongside the pm_subdivision UPDATE, so the
engine never has to write the audit. The BOUNDS are a UI RULING (Jack 2026-09-12) defined here as constants -- NOT read
from engine config; the engine's per-order/daily USD caps are a separate, unrelated risk gate and out of scope."""
from __future__ import annotations

# R2 (Jack ruled 2026-09-12): a whole integer in [MIN, MAX], enforced server-side; the route turns a violation into a
# 400 naming the bound. Defined ONCE here (a UI ruling, not engine config).
CONTRACTS_MIN = 1
CONTRACTS_MAX = 50

_AUDIT_TABLE = "pm_subdivision_sizing_audit"


class SizingError(ValueError):
    """A bounds / sizing-mode violation. The route renders it as a 400 (bounds) or 409 (mode) with the message."""


def _commit(conn) -> None:
    if hasattr(conn, "commit"):
        conn.commit()


def _audit_present(conn) -> bool:
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (_AUDIT_TABLE,)).fetchone() is not None
    except Exception:
        return False


def _row(conn, account_id, category):
    try:
        return conn.execute("SELECT sizing_mode, contracts FROM pm_subdivision WHERE account_id=? AND category=?",
                            (account_id, category)).fetchone()
    except Exception:
        return None


def validate_contracts(n) -> int:
    """The one bounds gate (R2): a WHOLE integer in [CONTRACTS_MIN, CONTRACTS_MAX]. Raises SizingError naming the
    bound otherwise. A non-whole number (e.g. 2.5) is rejected -- contracts are whole."""
    try:
        f = float(n)
    except (TypeError, ValueError):
        raise SizingError("contracts must be a whole number")
    if f != int(f):
        raise SizingError("contracts must be a whole number (got %r)" % (n,))
    i = int(f)
    if i < CONTRACTS_MIN:
        raise SizingError("contracts must be at least %d (got %d)" % (CONTRACTS_MIN, i))
    if i > CONTRACTS_MAX:
        raise SizingError("contracts must be at most %d (got %d)" % (CONTRACTS_MAX, i))
    return i


def last_change(conn, account_id, category):
    """The most recent audit row for (account, category) -> {old, new, by, ts} or None (never changed / pre-022)."""
    if not _audit_present(conn):
        return None
    r = conn.execute(
        "SELECT old_contracts, new_contracts, changed_by, changed_ts FROM %s "
        "WHERE account_id=? AND category=? ORDER BY changed_ts DESC, id DESC LIMIT 1" % _AUDIT_TABLE,
        (account_id, category)).fetchone()
    return None if r is None else {"old": r[0], "new": r[1], "by": r[2], "ts": r[3]}


def recent_changes(conn, account_id, category, limit=5):
    """The last `limit` sizing changes (newest first) for the drawer footer (R4). Honest-empty [] if none / pre-022."""
    if not _audit_present(conn):
        return []
    rows = conn.execute(
        "SELECT old_contracts, new_contracts, changed_by, changed_ts FROM %s "
        "WHERE account_id=? AND category=? ORDER BY changed_ts DESC, id DESC LIMIT ?" % _AUDIT_TABLE,
        (account_id, category, int(limit))).fetchall()
    return [{"old": r[0], "new": r[1], "by": r[2], "ts": r[3]} for r in rows]


def recent_fill_price(conn, account_id, category, n=20):
    """Average OUTCOME-LEG fill price over the last `n` FILLED ENTRY orders for this sub-division (the R3 estimate
    basis). None when there are NO fills yet -> the confirm says 'no fills yet to estimate from'. Journal-only
    (dry_run=0, filled, entries)."""
    try:
        rows = conn.execute(
            "SELECT fill_price FROM pm_subdivision_order WHERE account_id=? AND category=? AND dry_run=0 "
            "AND outcome_status='filled' AND is_exit=0 AND fill_price IS NOT NULL "
            "ORDER BY COALESCE(response_ts, submitted_ts, id) DESC LIMIT ?",
            (account_id, category, int(n))).fetchall()
    except Exception:
        return None
    ps = [float(r[0]) for r in rows if r[0] is not None]
    return (sum(ps) / len(ps)) if ps else None


def estimate_cost(conn, account_id, category, contracts):
    """R3 estimate -> (recent_avg_fill, per_copy_cost). per_copy_cost is None when there are no fills to estimate
    from (contracts x avg fill price otherwise)."""
    avg = recent_fill_price(conn, account_id, category)
    return (avg, (int(contracts) * avg) if avg is not None else None)


def read_sizing(conn, account_id, category, now_ts=None):
    """The /live sizing control's read (R4): current contracts + mode + last-change (who + age) + editability. None
    if the sub-division does not exist. `editable` is True ONLY when sizing_mode='contracts' -- the value the engine
    actually reads; on any other mode the number is UNREAD, so the control shows the mode read-only (never a change
    link that would be silently ignored)."""
    row = _row(conn, account_id, category)
    if row is None:
        return None
    mode = (row[0] or "fixed")
    contracts = 5 if row[1] is None else int(row[1])
    lc = last_change(conn, account_id, category)
    set_by = lc["by"] if lc else None
    set_age_sec = (int(now_ts) - int(lc["ts"])) if (lc and now_ts is not None and lc["ts"] is not None) else None
    return {"account_id": account_id, "category": category, "sizing_mode": mode, "contracts": contracts,
            "editable": (mode == "contracts"), "min": CONTRACTS_MIN, "max": CONTRACTS_MAX,
            "last_change": lc, "set_by": set_by, "set_age_sec": set_age_sec}


def set_contracts(conn, account_id, category, new_contracts, changed_by, now_ts) -> dict:
    """WRITE the flat-contracts count from pm_web (R2 bounds + R4 audit) in ONE transaction: validate the bound, read
    the current value, UPDATE pm_subdivision.contracts, and INSERT the audit row (who/from/to/when). Returns a result
    dict {ok, changed, old, new, ...}. FAIL-CLOSED: raises SizingError on an out-of-range value, a missing
    sub-division, or a non-'contracts' sizing_mode (the number would be UNREAD by the engine -> refusing is honest).
    Idempotent no-op when new == old (no audit row). AUTHZ (owner-or-admin to lower, admin to raise) is the ROUTE's
    gate, not this function's -- this is the data write, tested independently."""
    n = validate_contracts(new_contracts)
    row = _row(conn, account_id, category)
    if row is None:
        raise SizingError("no such sub-division: %s/%s" % (account_id, category))
    mode = (row[0] or "fixed")
    old = 5 if row[1] is None else int(row[1])
    if mode != "contracts":
        raise SizingError("sub-division %s/%s is on '%s' sizing, not 'contracts' -- the contract count is not read "
                          "(no change applied)" % (account_id, category, mode))
    if n == old:
        return {"ok": True, "changed": False, "old": old, "new": n,
                "account_id": account_id, "category": category}
    cur = conn.execute(
        "UPDATE pm_subdivision SET contracts=? WHERE account_id=? AND category=? AND sizing_mode='contracts'",
        (n, account_id, category))
    if cur.rowcount != 1:                                # concurrent mode-flip / row vanished -> do NOT audit a non-write
        _commit(conn)
        raise SizingError("sizing write did not apply (rowcount=%d) -- the sub-division may have changed mode"
                          % cur.rowcount)
    conn.execute(
        "INSERT INTO %s (account_id, category, old_contracts, new_contracts, changed_by, changed_ts) "
        "VALUES (?,?,?,?,?,?)" % _AUDIT_TABLE, (account_id, category, old, n, changed_by, int(now_ts)))
    _commit(conn)
    return {"ok": True, "changed": True, "old": old, "new": n, "account_id": account_id, "category": category}
