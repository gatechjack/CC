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
    """All ACTIVE sub-divisions as LIVE tiles (joined to their account). Empty list if the tables are absent
    (pre-migration-010) or empty. Carries NO live-trade data (P3 not built)."""
    if not _ready(conn):
        return []
    rows = conn.execute(
        "SELECT s.account_id, s.category, s.label AS sub_label, s.market_types, s.sizing_mode, "
        "       s.fixed_stake_usd, s.created_ts, COALESCE(a.label, s.account_id) AS account_label, a.venue "
        "FROM pm_subdivision s LEFT JOIN pm_account a ON a.account_id = s.account_id "
        "WHERE s.active = 1 ORDER BY account_label, s.category").fetchall()
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
def subdivisions_for_category(conn, category: str) -> list[dict]:
    """The ACTIVE sub-divisions whose category matches `category` -- the valid promote-to-LIVE targets for a
    pinned pair in that category (the category-join, surfaced to the UI). Empty if the tables are absent
    (pre-migration-010) or none match. Read-only."""
    if not _ready(conn):
        return []
    rows = conn.execute(
        "SELECT s.account_id, s.category, COALESCE(a.label, s.account_id) AS account_label "
        "FROM pm_subdivision s LEFT JOIN pm_account a ON a.account_id = s.account_id "
        "WHERE s.category = ? AND s.active = 1 ORDER BY account_label", (category,)).fetchall()
    return [dict(r) for r in rows]


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
