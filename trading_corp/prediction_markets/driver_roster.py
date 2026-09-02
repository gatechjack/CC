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
    rows = conn.execute(
        "SELECT s.account_id AS account_id, s.category AS category, a.secret_ref AS secret_ref "
        "FROM pm_subdivision s "
        "JOIN pm_account a ON a.account_id = s.account_id AND a.active = 1 "
        "WHERE s.active = 1 "
        "  AND EXISTS (SELECT 1 FROM pm_subdivision_attachment at "
        "              WHERE at.account_id = s.account_id AND at.category = s.category AND at.active = 1) "
        "ORDER BY s.account_id, s.category").fetchall()
    return [{"account_id": r["account_id"], "category": r["category"], "secret_ref": r["secret_ref"]} for r in rows]


def plan_driver_tasks(roster: list[dict], accounts_with_keys) -> tuple[list[dict], list[dict]]:
    """PURE planning: given the roster and the set of account_ids whose keys RESOLVED, decide which tasks to SPAWN
    and which to SKIP (with reasons). Deterministic (roster is pre-ordered). Two skip rules:
      - 'no_keys': the account's secret_ref did not resolve to a keypair -> fail-CLOSED (never trade on wrong keys).
      - 'second_subdivision_on_account': a 2nd sub-division on an account that ALREADY has a spawned task. N
        DISTINCT accounts (one category each) are safe; a 2nd CATEGORY on ONE account silently degrades three
        account-scoped safeties -- the account-level open_usd cap has a within-cycle over-place race between the
        two same-account tasks; latch_auth_failure is called with only the caller's category so a 401 leaves the
        sibling POSTing on dead auth; a full-account KALSHI_ONLY boot-reconcile mismatch latches only the categories
        that have tasks. FILED, unbuilt. This guard REFUSES the 2nd LOUDLY so a config/DB edit cannot land the
        unsafe case silently.
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
            skips.append({"account_id": aid, "category": cat, "reason": "second_subdivision_on_account"})
            continue
        seen_accounts.add(aid)
        spawn.append({"account_id": aid, "category": cat})
    return spawn, skips
