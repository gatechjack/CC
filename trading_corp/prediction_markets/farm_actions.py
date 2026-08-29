"""Prediction Markets -- THE THREE FARM ACTIONS (Stage 3 R6) + promote-to-live's inverse.

The mutating half of the farm funnel. `farm.py` stays READ-ONLY; every status/attachment WRITE lives here, so
the read path and the write path are separable and independently testable. Writes ONLY the PM DB; imports NOTHING
engine-side and CANNOT reach the execution chokepoint -- promote-to-live creates an ATTACHMENT (a mapping row),
never an order (the order journal pm_subdivision_order is written only by the execution engine at placement time).

THE THREE-BASES INVARIANT is the load-bearing property here (a pair can sit on all three lists at once):
  * pm_watchlist is the FUNNEL (candidate|pinned status), NOT one of the three data bases. Promote/Demote flip
    ITS status and touch NOTHING else -- not the completed base (pm_category_stats/pm_closed_position), not the
    paper base (pm_paper_trade/pm_paper_category_stats), not the live base.
  * PROMOTE-TO-WATCHLIST (candidate->pinned): flips status only. Paper is seeded LATER by the poller
    (paper.poll_pinned), NOT here -- so this action writes ONLY pm_watchlist. INERT in prod until Search
    (Stage 4) populates candidates (there is nothing to promote until then); that is honest-empty, not broken.
  * DEMOTE (pinned->candidate): flips status only. ** pm_paper_trade rows SURVIVE (F-5) ** -- demote NEVER
    deletes/orphans paper; the display basis flips back to completed while the paper history stays reachable.
  * PROMOTE-TO-LIVE (attach a pinned pair to a sub-division): writes ONLY pm_subdivision_attachment. Joined ON
    CATEGORY (a ufc whale cannot attach to an mlb sub-division). Does NOT flip pm_watchlist (the pair stays
    pinned AND becomes live). Its inverse is DETACH (active=0, reversible).

Every action is IDEMPOTENT / safely repeatable: a double-submit is a no-op, never a second attachment or a
double flip. No action reads or writes a secret, a broker, or pm_subdivision_order.

Spec: reports/prediction_markets/STAGE3_PLAN_2026-08-28.md R6; PM_REBUILD_PLAN F-5.
"""
from __future__ import annotations

from . import farm   # PINNED / CANDIDATE vocab (single source of truth; read-only import)


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _commit(conn) -> None:
    if hasattr(conn, "commit"):
        conn.commit()


# ── funnel mutations (pm_watchlist.status) -- promote/demote are mutual inverses ─────────────────────
def promote_to_watchlist(conn, wallet: str, category: str, now_ts: int) -> dict:
    """Prospect -> Watchlist: candidate -> pinned. Flips pm_watchlist.status ONLY (paper is seeded later by the
    poller, not here). Idempotent: already-pinned or not-a-candidate -> a no-op with a reason. Only an ACTIVE
    (in-funnel) candidate is promotable."""
    wallet = (wallet or "").lower()
    cur = conn.execute(
        "UPDATE pm_watchlist SET status=?, pinned_ts=?, updated_ts=? "
        "WHERE wallet=? AND category=? AND status=? AND active=1",
        (farm.PINNED, now_ts, now_ts, wallet, category, farm.CANDIDATE))
    _commit(conn)
    if cur.rowcount:
        return {"ok": True, "changed": True, "reason": "promoted_to_watchlist", "wallet": wallet, "category": category}
    # nothing flipped: say WHY (already pinned vs not a candidate vs off-funnel/absent) -- honest, not a lie
    row = conn.execute("SELECT status, active FROM pm_watchlist WHERE wallet=? AND category=?",
                       (wallet, category)).fetchone()
    reason = ("already_pinned" if row and row["status"] == farm.PINNED
              else "off_funnel" if row and not row["active"]
              else "not_a_candidate")
    return {"ok": True, "changed": False, "reason": reason, "wallet": wallet, "category": category}


def demote_to_prospect(conn, wallet: str, category: str, now_ts: int) -> dict:
    """Watchlist -> Prospect: pinned -> candidate. Flips pm_watchlist.status ONLY. ** pm_paper_trade rows are
    PRESERVED (F-5) ** -- this function touches no paper table; the display basis flips back to completed while
    the paper history survives. Idempotent: not-pinned -> a no-op with a reason.

    ** DEMOTE-WITH-A-LIVE-ATTACHMENT (Jack's review case): REFUSE. ** A pair can be pinned AND live at once. A
    demote is a FUNNEL (paper-lane) action; it must NOT silently reach into the LIVE (real-money) plane and tear
    down attachments. So if the pair still has an ACTIVE live attachment, demote refuses (`attached_live_detach_
    first`) and names the attachments -- the operator detaches from live FIRST (CLI), then demotes. This enforces
    the invariant **live subset of pinned** (a prospect is never live-attached), which the execution engine can
    trust. (Alternative considered + rejected for first-live: CASCADE-detach on demote -- one click, but it lets a
    funnel action silently stop real-money copying; Jack can rule to switch.)"""
    wallet = (wallet or "").lower()
    # ATOMIC check-then-flip: the attachment check AND the status UPDATE run under ONE write lock, so a concurrent
    # promote_to_live CANNOT slip an attachment in between them (which would leave a CANDIDATE that is live-attached,
    # breaking live-subset-of-pinned). When the attachment table is absent (pre-migration-011) no attachment can
    # exist, so the plain UPDATE is safe without a transaction.
    if _table_exists(conn, "pm_subdivision_attachment"):
        conn.execute("BEGIN IMMEDIATE")
        try:
            att = conn.execute(
                "SELECT account_id, category FROM pm_subdivision_attachment WHERE wallet=? AND category=? AND active=1",
                (wallet, category)).fetchall()
            if att:
                conn.execute("ROLLBACK")
                return {"ok": False, "changed": False, "reason": "attached_live_detach_first",
                        "wallet": wallet, "category": category, "attachments": [dict(r) for r in att]}
            rc = conn.execute(
                "UPDATE pm_watchlist SET status=?, updated_ts=? WHERE wallet=? AND category=? AND status=? AND active=1",
                (farm.CANDIDATE, now_ts, wallet, category, farm.PINNED)).rowcount
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    else:
        rc = conn.execute(
            "UPDATE pm_watchlist SET status=?, updated_ts=? WHERE wallet=? AND category=? AND status=? AND active=1",
            (farm.CANDIDATE, now_ts, wallet, category, farm.PINNED)).rowcount
        _commit(conn)
    if rc:
        return {"ok": True, "changed": True, "reason": "demoted_to_prospect", "wallet": wallet, "category": category}
    row = conn.execute("SELECT status, active FROM pm_watchlist WHERE wallet=? AND category=?",
                       (wallet, category)).fetchone()
    reason = ("already_candidate" if row and row["status"] == farm.CANDIDATE
              else "off_funnel" if row and not row["active"]
              else "not_pinned")
    return {"ok": True, "changed": False, "reason": reason, "wallet": wallet, "category": category}


# ── live-attachment mutations (pm_subdivision_attachment) -- promote-to-live + its inverse ────────────
def promote_to_live(conn, account_id: str, category: str, wallet: str, now_ts: int) -> dict:
    """Attach a PINNED (wallet, category) to the (account_id, category) sub-division -- the farm->money bridge.
    Creates the ATTACHMENT and, if the sub-division does not exist yet, AUTO-CREATES it -- ATOMICALLY, in ONE
    transaction (Jack ruling 1), so a failure never leaves an orphan sub-division with nothing attached. Creates
    NO order, arms nothing. The ACCOUNT is a credentialed entity (secret_ref) and MUST pre-exist -- we never
    auto-create a credential (`no_such_account` if absent). JOINED ON CATEGORY: the pin must be in THIS category
    and the sub-division is created AS (account, category), so a ufc whale can only ever create/attach a
    (account, ufc) sub-division -- NEVER land in an mlb one. IDEMPOTENT: a repeat attach keeps ONE row + reports
    changed=False; a re-attach after detach reactivates and PRESERVES the original added_ts."""
    wallet = (wallet or "").lower()
    base = {"account_id": account_id, "category": category, "wallet": wallet}
    if not (_table_exists(conn, "pm_account") and _table_exists(conn, "pm_subdivision")
            and _table_exists(conn, "pm_subdivision_attachment")):
        return {"ok": False, "changed": False, "reason": "money_layer_not_migrated", **base}   # honest, never a 500
    # EVERYTHING inside ONE BEGIN IMMEDIATE transaction: validate + auto-create + attach are ATOMIC. The write lock
    # is held for the whole op, so a concurrent account-deactivate / whale-demote / racing auto-create CANNOT slip
    # between the checks and the writes (no TOCTOU, no orphan sub-division). `created` comes from the ACTUAL insert
    # rowcount (accurate even under a race), NOT a stale pre-transaction read. UPSERT keeps the attach idempotent;
    # the UPDATE omits added_ts so a re-attach after detach PRESERVES the original attachment timestamp.
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT 1 FROM pm_account WHERE account_id=? AND active=1", (account_id,)).fetchone() is None:
            conn.execute("ROLLBACK")
            return {"ok": False, "changed": False, "reason": "no_such_account", **base}   # credentialed -> never auto-created
        if conn.execute("SELECT 1 FROM pm_watchlist WHERE wallet=? AND category=? AND status=? AND active=1",
                        (wallet, category, farm.PINNED)).fetchone() is None:              # category-join refuses a non-pin
            conn.execute("ROLLBACK")
            return {"ok": False, "changed": False, "reason": "whale_not_pinned_in_category", **base}
        att = conn.execute("SELECT active FROM pm_subdivision_attachment WHERE account_id=? AND category=? AND wallet=?",
                           (account_id, category, wallet)).fetchone()
        already = att is not None and att["active"] == 1
        created = False
        if conn.execute("SELECT 1 FROM pm_subdivision WHERE account_id=? AND category=? AND active=1",
                        (account_id, category)).fetchone() is None:
            # AUTO-CREATE with DDL-default config (market_types/sizing_mode); NULL caps -> code CONFIG_DEFAULTS; it
            # inherits the account's credentials at execution time. `created` = True only if THIS insert took effect.
            cur = conn.execute("INSERT OR IGNORE INTO pm_subdivision (account_id, category, active, created_ts) "
                               "VALUES (?,?,1,?)", (account_id, category, now_ts))
            created = (cur.rowcount or 0) > 0
        conn.execute(
            "INSERT INTO pm_subdivision_attachment (account_id, category, wallet, active, source, added_ts, removed_ts) "
            "VALUES (?,?,?,1,'promote_to_live',?,NULL) "
            "ON CONFLICT(account_id, category, wallet) DO UPDATE SET active=1, removed_ts=NULL",
            (account_id, category, wallet, now_ts))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"ok": True, "changed": (not already), "reason": ("already_attached" if already else "attached"),
            "created_subdivision": created, **base}


def detach_from_live(conn, account_id: str, category: str, wallet: str, now_ts: int) -> dict:
    """PROMOTE-TO-LIVE's inverse: detach a whale from a sub-division. REVERSIBLE (active=0 + removed_ts; the row
    survives so a later re-attach restores it). Idempotent: not-attached -> a no-op with a reason. This is the
    back-out for a wrong promote-to-live click; it is exposed via the CLI (works when pm_web is down)."""
    wallet = (wallet or "").lower()
    if not _table_exists(conn, "pm_subdivision_attachment"):
        return {"ok": False, "changed": False, "reason": "money_layer_not_migrated"}
    cur = conn.execute(
        "UPDATE pm_subdivision_attachment SET active=0, removed_ts=? "
        "WHERE account_id=? AND category=? AND wallet=? AND active=1",
        (now_ts, account_id, category, wallet))
    _commit(conn)
    return {"ok": True, "changed": bool(cur.rowcount), "reason": ("detached" if cur.rowcount else "not_attached"),
            "account_id": account_id, "category": category, "wallet": wallet}
