"""Engine-side DRIVER ROSTER (N2, per-account trading): WHICH (account, category) sub-divisions get a live
copy-trading task at engine boot, and HOW those per-account tasks are PLANNED from the roster.

main.py enumerates the roster here and spawns ONE `live_driver.scheduled_pm_live_loop` task per sub-division, each
with its OWN account's broker (keys resolved via `shard_snapshot_task.resolve_kalshi_keys`, secret_ref -> keypair,
fail-CLOSED). The driver loop body is already fully account-scoped (`WHERE account_id=?` throughout); this module is
the missing boot wiring that lets a SECOND account trade at all.

★ ENGINE-SIDE, NOT pm_web-safe. Unlike subdivision.py (deliberately credential-free), this DOES select
`pm_account.secret_ref` (a KeyVault NAME, never a value) -- the ENGINE needs it to resolve each account's keypair,
exactly as the M3 shard-snapshot block already does (main.py:1593). It reaches no order path and places nothing; it
only decides the roster. Never import it into pm_web.

★ THE ROSTER IS THE DATABASE, NOT CONFIG (Jack: "the driver reads the active sub-divisions and iterates"). A
sub-division turns ON by existing + `active=1` + having >=1 ACTIVE attachment, and OFF by losing its last active
attachment -- no engine edit, no yaml. The ATTACHMENT gate is load-bearing: an empty (unattached) sub-division must
NOT spawn a task, because the task boot-reconciles the WHOLE account before it reads attachments and could latch a
category that will never trade -- so a pinned-but-unattached whale does not make an account trade.

DEFENSIVE: tolerates the money-layer tables being absent (-> empty roster), never raising at boot.
"""
from __future__ import annotations

import logging

_LOG = logging.getLogger(__name__)


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _column_exists(conn, table: str, col: str) -> bool:
    # M4 (2026-09-03): tolerant read of the multi-category opt-in -- if pm_account.multi_category_ok is absent
    # (code precedes migration 019) we FAIL CLOSED to 0 (guard still refuses the 2nd category), never raise at boot.
    try:
        return any(r[1] == col for r in conn.execute("PRAGMA table_info(%s)" % table).fetchall())
    except Exception:
        return False


def active_driver_subdivisions(conn) -> list[dict]:
    """The DRIVER ROSTER: active sub-divisions on active accounts WITH >=1 active attachment. Returns a list of
    {account_id, category, secret_ref} dicts, deterministically ordered. Empty (never raises) if the money-layer
    tables are absent. This is a DEDICATED engine query -- NOT subdivision.list_subdivisions (which is a
    credential-free display query that never selects secret_ref and carries display fields a driver must not
    depend on)."""
    if not (_table_exists(conn, "pm_subdivision") and _table_exists(conn, "pm_account")):
        return []
    if not _table_exists(conn, "pm_subdivision_attachment"):
        # No attachment table (pre-011) -> the >=1-attachment gate cannot be applied. Fail SAFE to an EMPTY roster:
        # an ungated roster could spawn tasks for unattached sub-divisions (which boot-reconcile the whole account).
        # A driver that trades nothing is the safe degradation; the live box is post-011 so this is belt-and-braces.
        _LOG.warning("driver_roster: pm_subdivision_attachment absent -> EMPTY roster (fail-safe; 0 tasks)")
        return []
    # M4 (2026-09-03): carry the per-account multi_category_ok opt-in ON the roster row (TOLERANT: 0 if the column is
    # absent pre-019). plan_driver_tasks reads it to decide whether a 2nd category on the account is grouped or refused.
    optin_sel = "a.multi_category_ok AS multi_category_ok" if _column_exists(conn, "pm_account", "multi_category_ok") else "0 AS multi_category_ok"
    rows = conn.execute(
        "SELECT s.account_id AS account_id, s.category AS category, a.secret_ref AS secret_ref, " + optin_sel + " "
        "FROM pm_subdivision s "
        "JOIN pm_account a ON a.account_id = s.account_id AND a.active = 1 "
        "WHERE s.active = 1 "
        "  AND EXISTS (SELECT 1 FROM pm_subdivision_attachment at "
        "              WHERE at.account_id = s.account_id AND at.category = s.category AND at.active = 1) "
        "ORDER BY s.account_id, s.category").fetchall()
    return [{"account_id": r["account_id"], "category": r["category"], "secret_ref": r["secret_ref"],
             "multi_category_ok": int(r["multi_category_ok"] or 0)} for r in rows]


def plan_driver_tasks(roster: list[dict], accounts_with_keys) -> tuple[list[dict], list[dict]]:
    """PURE planning: given the roster and the set of account_ids whose keys RESOLVED, decide which tasks to SPAWN
    and which to SKIP (with reasons). Deterministic (roster is pre-ordered). Skip rules:
      - 'no_keys': the account's secret_ref did not resolve to a keypair -> fail-CLOSED (never trade on wrong keys).
      - 'second_subdivision_on_account': a 2nd category on an account that ALREADY has a spawned category, and the
        account is NOT opted in. N distinct accounts (one category each) are always safe; a 2nd CATEGORY on ONE
        account is safe ONLY under Option C (M1/M2/M3 all closed) + the per-account opt-in (M4). The account-scoped
        safeties that used to degrade -- the open_usd race (M1), latch_auth_failure scope (M2), whole-account
        boot-reconcile latch (M3) -- are now closed, so the guard opens BEHIND a fail-closed opt-in.
    ★ M4 (2026-09-03): a 2nd+ category is GROUPED (emitted, so main.py's by-account grouping puts it on the account's
    ONE Option-C task) IF AND ONLY IF the account is opted in via `pm_account.multi_category_ok=1` (carried on the
    roster row as `multi_category_ok`). OFF BY DEFAULT (DDL default 0; absent key -> 0 -> refuse): with no opt-in the
    behaviour is BYTE-IDENTICAL to before M4 -- one category per account, the 2nd refused. The relaxation is NEVER the
    default; an account gets a 2nd category only because a deliberate DB edit says so.
    Returns (spawn, skips): spawn = [{account_id, category}], skips = [{account_id, category, reason}]."""
    spawn: list[dict] = []
    skips: list[dict] = []
    seen_accounts: set = set()
    for row in roster:
        aid = row["account_id"]
        cat = row["category"]
        if aid not in accounts_with_keys:
            skips.append({"account_id": aid, "category": cat, "reason": "no_keys"})
            continue
        if aid in seen_accounts:
            if row.get("multi_category_ok"):                    # M4: opted-in -> GROUP onto the account's Option-C task
                spawn.append({"account_id": aid, "category": cat})
            else:                                               # fail-CLOSED default: refuse the 2nd category LOUDLY
                skips.append({"account_id": aid, "category": cat, "reason": "second_subdivision_on_account"})
            continue
        seen_accounts.add(aid)
        spawn.append({"account_id": aid, "category": cat})
    return spawn, skips
