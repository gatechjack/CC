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
    the paper history survives. Idempotent: not-pinned -> a no-op with a reason."""
    wallet = (wallet or "").lower()
    cur = conn.execute(
        "UPDATE pm_watchlist SET status=?, updated_ts=? WHERE wallet=? AND category=? AND status=? AND active=1",
        (farm.CANDIDATE, now_ts, wallet, category, farm.PINNED))
    _commit(conn)
    if cur.rowcount:
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
    Creates the ATTACHMENT and NOTHING else (no order, no arm). JOINED ON CATEGORY: the sub-division lookup uses
    (account_id, category) and the pin lookup uses (wallet, category) -- the SAME category -- so a ufc whale can
    NEVER attach to an mlb sub-division. VALIDATES (app-layer, no FK): the sub-division exists+active AND the
    pair is pinned+active. IDEMPOTENT: a repeat attach reactivates/keeps ONE row (no duplicate)."""
    wallet = (wallet or "").lower()
    if not (_table_exists(conn, "pm_subdivision") and _table_exists(conn, "pm_subdivision_attachment")):
        return {"ok": False, "changed": False, "reason": "money_layer_not_migrated"}   # honest, never a 500
    sub = conn.execute("SELECT 1 FROM pm_subdivision WHERE account_id=? AND category=? AND active=1",
                       (account_id, category)).fetchone()
    if sub is None:
        return {"ok": False, "changed": False, "reason": "no_such_subdivision",
                "account_id": account_id, "category": category, "wallet": wallet}
    pin = conn.execute("SELECT 1 FROM pm_watchlist WHERE wallet=? AND category=? AND status=? AND active=1",
                       (wallet, category, farm.PINNED)).fetchone()
    if pin is None:                                     # not pinned in THIS category -> the category-join refuses
        return {"ok": False, "changed": False, "reason": "whale_not_pinned_in_category",
                "account_id": account_id, "category": category, "wallet": wallet}
    # report `changed` accurately (consistent with promote/demote): an already-ACTIVE attachment is a no-op.
    existing = conn.execute("SELECT active FROM pm_subdivision_attachment WHERE account_id=? AND category=? AND wallet=?",
                            (account_id, category, wallet)).fetchone()
    already = existing is not None and existing["active"] == 1
    # UPSERT keeps it concurrency-safe (ON CONFLICT collapses concurrent inserts to ONE row); the UPDATE omits
    # added_ts so a re-attach after detach PRESERVES the original attachment timestamp (reversibility: nothing lost).
    conn.execute(
        "INSERT INTO pm_subdivision_attachment (account_id, category, wallet, active, source, added_ts, removed_ts) "
        "VALUES (?,?,?,1,'promote_to_live',?,NULL) "
        "ON CONFLICT(account_id, category, wallet) DO UPDATE SET active=1, removed_ts=NULL",
        (account_id, category, wallet, now_ts))
    _commit(conn)
    return {"ok": True, "changed": (not already), "reason": ("already_attached" if already else "attached"),
            "account_id": account_id, "category": category, "wallet": wallet}


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
