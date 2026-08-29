"""Prediction Markets -- LIVE sub-division reads (Stage 3 R3). READ-ONLY: places nothing, arms nothing, reaches
NO order path (execution is R4+). Imports NOTHING beyond the sqlite connection it is handed -- no broker, no
secrets, no engine -- so pm_web stays standalone and structurally cannot place an order through this module.

A sub-division is an (account, category) pair (`pm_subdivision`) attached to a `pm_account`. This renders the LIVE
list (the top-of-hierarchy Account-Category tiles) and a per-sub-division page. Live TRADES/STATS are P3 (tables
not built) -- so the live list is honest-empty by construction in R3.

DEFENSIVE BY DESIGN: `pm_account` / `pm_subdivision` are created by migration 010, which may NOT be deployed yet
(live schema 9). Every read tolerates the tables being ABSENT -> honest-empty (no sub-divisions), never a 500. So
R3 can deploy on a pm_web restart INDEPENDENTLY of the migration-010 deploy; tiles appear once 010 is live AND an
account/sub-division exists. Tile-on-CREATE (Jack's ruling): a sub-division row's mere existence yields a tile,
before it ever trades -- so the empty state reads as information ("created, never traded"), not as an error.

Never selects a secret VALUE. `secret_ref` (a KeyVault NAME, not a value) and `owner_identity` (P3) are not read
here -- the read-only tile needs neither.
"""
from __future__ import annotations


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _ready(conn) -> bool:
    """Both money-layer tables present? (False pre-migration-010 -> honest-empty, never an error.)"""
    return _table_exists(conn, "pm_subdivision") and _table_exists(conn, "pm_account")


def list_subdivisions(conn) -> list[dict]:
    """The VISIBLE sub-divisions as LIVE tiles. VISIBILITY = has >=1 ACTIVE attachment (Jack ruling 3), which
    RECONCILES with tile-on-create: auto-create always attaches, so a just-created sub-division has an attachment
    and shows IMMEDIATELY (tile-on-create honored, keyed on attachments not on trades); it drops off the dashboard
    only when its LAST attachment is detached -- but the ROW PERSISTS (ruling 2: sub-divisions are PERMANENT for
    lifetime stats; the detail page stays reachable by URL, see get_subdivision). `n_whales` = active attachments.
    Empty list if the tables are absent. Read-only.

    ** SUB-DIVISIONS ARE PERMANENT (ruling 2): there is deliberately NO delete + NO garbage-collection of an empty
    one. A future agent must NOT 'clean up' a sub-division with 0 active attachments -- it is FILED (hidden from
    the dashboard by the visibility gate) precisely so its lifetime stats survive. **"""
    if not _ready(conn):
        return []
    has_att = _table_exists(conn, "pm_subdivision_attachment")
    # visibility gate = >=1 active attachment. If the attachment table is absent (a transient pre-011 deploy), it
    # cannot be applied -> show all active sub-divisions (R3 fallback); post-011 the gate is real.
    join = ("LEFT JOIN (SELECT account_id, category, COUNT(*) n FROM pm_subdivision_attachment WHERE active=1 "
            "GROUP BY account_id, category) at ON at.account_id=s.account_id AND at.category=s.category "
            if has_att else "")
    n_whales = "COALESCE(at.n, 0) AS n_whales, " if has_att else "0 AS n_whales, "
    where_visible = "AND COALESCE(at.n, 0) > 0 " if has_att else ""
    rows = conn.execute(
        "SELECT s.account_id, s.category, s.label AS sub_label, s.market_types, s.sizing_mode, s.fixed_stake_usd, "
        "       " + n_whales + "s.created_ts, COALESCE(a.label, s.account_id) AS account_label, a.venue "
        "FROM pm_subdivision s LEFT JOIN pm_account a ON a.account_id = s.account_id " + join +
        "WHERE s.active = 1 " + where_visible + "ORDER BY account_label, s.category").fetchall()
    return [dict(r) for r in rows]


def active_accounts(conn) -> list[dict]:
    """The ACTIVE accounts = the promote-to-LIVE TARGETS. Because promote-to-live AUTO-CREATES the (account,
    category) sub-division on demand (ruling 1), the operator promotes to an ACCOUNT, not to a pre-existing
    sub-division. Empty if pm_account is absent (pre-migration-010). Read-only; never selects a secret value."""
    if not _table_exists(conn, "pm_account"):
        return []
    rows = conn.execute(
        "SELECT account_id, COALESCE(label, account_id) AS account_label, venue "
        "FROM pm_account WHERE active=1 ORDER BY account_label").fetchall()
    return [dict(r) for r in rows]


def get_subdivision(conn, account_id: str, category: str) -> dict | None:
    """One ACTIVE sub-division's config for its detail page, or None (not found / tables absent -> 404)."""
    if not _ready(conn):
        return None
    r = conn.execute(
        "SELECT s.account_id, s.category, s.label AS sub_label, s.market_types, s.sizing_mode, s.fixed_stake_usd, "
        "       s.per_order_usd_cap, s.daily_usd_cap, s.max_open_usd, s.max_orders_per_day, s.max_slippage_cents, "
        "       s.created_ts, COALESCE(a.label, s.account_id) AS account_label, a.venue "
        "FROM pm_subdivision s LEFT JOIN pm_account a ON a.account_id = s.account_id "
        "WHERE s.account_id = ? AND s.category = ? AND s.active = 1", (account_id, category)).fetchone()
    return dict(r) if r is not None else None


# ── R6 attachment reads (which whales a sub-division copies) -- READ-ONLY; defensive if the R6 table is absent ─
def attached_whales(conn, account_id: str, category: str) -> list[dict]:
    """The ACTIVE whales a sub-division copies (its attachments), joined to their display name -- the read-only
    'copies these whales' list on the sub-division page. Empty if the R6 attachment table is absent
    (pre-migration-011) or none attached -> honest-empty, never a 500. Places/arms/reaches NOTHING."""
    if not _table_exists(conn, "pm_subdivision_attachment"):
        return []
    rows = conn.execute(
        "SELECT at.wallet, at.category, at.added_ts, w.user_name "
        "FROM pm_subdivision_attachment at LEFT JOIN pm_whale w ON w.wallet = at.wallet "
        "WHERE at.account_id = ? AND at.category = ? AND at.active = 1 "
        "ORDER BY (w.user_name IS NULL), w.user_name COLLATE NOCASE, at.wallet", (account_id, category)).fetchall()
    return [dict(r) for r in rows]
