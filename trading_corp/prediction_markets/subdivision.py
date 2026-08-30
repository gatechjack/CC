"""Prediction Markets -- LIVE sub-division reads (Stage 3 R3). READ-ONLY: places nothing, arms nothing, reaches
NO order path (execution is R4+). Imports NOTHING beyond the sqlite connection it is handed -- no broker, no
secrets, no engine -- so pm_web stays standalone and structurally cannot place an order through this module.

A sub-division is an (account, category) pair (`pm_subdivision`) attached to a `pm_account`. This renders the LIVE
list (the top-of-hierarchy Account-Category tiles) and a per-sub-division page, INCLUDING the live-trade journal
(`pm_subdivision_order`, WRITTEN by the execution engine, READ here) and the journal-derived open positions. The
live list stays honest-empty until the engine actually fills a copy -- but the section IS wired to real data (it
was NOT in R3, when the order table was empty and the page hardcoded the empty state; the engine now trades).

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
    # n_live_trades = REAL orders placed (dry_run=0), so the tile hint stops hardcoding "no live trades yet" once
    # this sub-division has traded. Defensive: 0 when the order journal is absent (pre-010). Correlated scalar
    # subquery (one per row -- the sub-division list is tiny); NOT a fabricated field.
    has_ord = _table_exists(conn, "pm_subdivision_order")
    n_ord = ("(SELECT COUNT(*) FROM pm_subdivision_order o WHERE o.account_id = s.account_id "
             "AND o.category = s.category AND o.dry_run = 0) AS n_live_trades, " if has_ord else "0 AS n_live_trades, ")
    rows = conn.execute(
        "SELECT s.account_id, s.category, s.label AS sub_label, s.market_types, s.sizing_mode, s.fixed_stake_usd, "
        "       " + n_whales + n_ord + "s.created_ts, COALESCE(a.label, s.account_id) AS account_label, a.venue "
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


# ── LIVE-TRADE JOURNAL + JOURNAL-DERIVED POSITIONS (the real 'Live trades' section) ────────────────────────────
# pm_subdivision_order is the append-only order journal WRITTEN by the execution engine at placement time; pm_web
# only READS it (no broker, no order path -- the standalone guard holds). Everything below is read-only.

def market_type_from_ticker(ticker) -> str:
    """Derive the copied market type from the Kalshi series prefix (the token before the first '-'). Kalshi's MLB
    series follow a GAME/TOTAL/SPREAD suffix convention that holds across sports: KXMLBGAME -> moneyline,
    KXMLBTOTAL -> total, KXMLBSPREAD -> spread. Unknown series -> the raw series token, lower-cased (HONEST: a
    label we can't classify is shown verbatim, never mis-labelled). Pure string parse -- no DB, no matcher import
    (keeps pm_web standalone). The order row does NOT store market_type, so this is the ONE place it is derived."""
    series = str(ticker or "").upper().split("-", 1)[0]
    if not series:
        return "—"
    if "SPREAD" in series:
        return "spread"
    if "TOTAL" in series:
        return "total"
    if "GAME" in series or "MONEY" in series:
        return "moneyline"
    return series.lower()


def sizing_summary(sub) -> str:
    """Human sizing description that states BEHAVIOUR, not just the stored stake. Fixed sizing places
    max(1, floor(stake / price)) contracts per copy (brokers.kalshi_live.usd_to_contracts). A $0.01 stake floors
    to EXACTLY ONE contract at any tradable price (0.01..0.99), so the stored '$0.01/copy' is TRUE about the value
    but MISLEADING about cost -- the copy costs the fill price (e.g. $0.60), not a cent. This is a STAND-IN for the
    flat-contracts sizing mode still on the backlog (stated so it is not mistaken for the final design). Pure
    display -- no DB, no order path."""
    mode = (sub.get("sizing_mode") or "fixed")
    if mode != "fixed":
        return "%s (per-copy size set by the %s model)" % (mode, mode)
    stake = sub.get("fixed_stake_usd")
    if stake is None:
        return "fixed stake (unset) -- falls back to the code default at placement"
    stake = float(stake)
    # floor(stake/price) >= 2 needs stake >= 2*price >= 2*0.01 = 0.02; below that it is ALWAYS exactly 1 contract.
    if stake < 0.02:
        return ("fixed · 1 contract per copy (a $%.2f stake floors to a single contract at any tradable "
                "price; the cost is the fill price, not the stake). Stand-in for flat-contracts sizing (backlog)."
                % stake)
    return ("fixed · max(1, floor($%.2f / price)) contracts per copy (the $%.2f stake divided by the "
            "contract price, minimum one). Stand-in for flat-contracts sizing (backlog)." % (stake, stake))


def live_orders(conn, account_id: str, category: str, *, limit: int = 200) -> list[dict]:
    """The REAL live orders this sub-division has placed (dry_run=0), NEWEST FIRST -- the 'Live trades' table. R4
    dry-runs (logged-not-placed) are EXCLUDED: they are not live trades. Each row carries a derived `market_type`.
    Empty if the journal table is absent (pre-migration-010) -> honest-empty, never a 500. Read-only."""
    if not _table_exists(conn, "pm_subdivision_order"):
        return []
    rows = conn.execute(
        "SELECT id, ticker, order_side, outcome_leg, is_exit, submitted_count, submitted_price, time_in_force, "
        "       outcome_status, fill_count, fill_price, remaining_count, fee, error_detail, submitted_ts, response_ts "
        "FROM pm_subdivision_order "
        "WHERE account_id = ? AND category = ? AND dry_run = 0 "
        "ORDER BY COALESCE(response_ts, submitted_ts) DESC, id DESC LIMIT ?",
        (account_id, category, int(limit))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["market_type"] = market_type_from_ticker(d.get("ticker"))
        out.append(d)
    return out


_LEG_SIGN = {"yes": 1, "no": -1}


def live_positions(conn, account_id: str, category: str) -> list[dict]:
    """What this sub-division CURRENTLY HOLDS, DERIVED FROM THE FILLED ORDER JOURNAL -- NOT a live venue read
    (pm_web is standalone and calls no broker). Per ticker, net signed contracts = sum over FILLED rows of
    sign(leg) * sign(entry/exit) * fill_count, with sign(yes)=+1 / sign(no)=-1 and sign(entry)=+1 / sign(exit)=-1
    -- the SAME convention boot_reconcile uses (the engine's boot reconcile is what cross-checks this journal view
    against the exchange's get_positions). A ticker whose net rounds to 0 is FLAT and dropped (fully exited). The
    held leg is 'yes' when net>0, 'no' when net<0; contracts = |net|. cost_basis_usd = net cash on the held leg
    (entry fills add, exit fills subtract, at the outcome-leg fill price); avg_price = cost / contracts. fees_usd =
    total fees on the ticker. Read-only; reads ONLY the journal."""
    if not _table_exists(conn, "pm_subdivision_order"):
        return []
    rows = conn.execute(
        "SELECT ticker, outcome_leg, is_exit, fill_count, fill_price, fee "
        "FROM pm_subdivision_order "
        "WHERE account_id = ? AND category = ? AND dry_run = 0 AND outcome_status = 'filled' "
        "      AND fill_count IS NOT NULL AND fill_count > 0",
        (account_id, category)).fetchall()
    agg: dict = {}
    for r in rows:
        d = dict(r)
        tk = d.get("ticker") or ""
        leg = str(d.get("outcome_leg") or "").lower()
        lsign = _LEG_SIGN.get(leg)
        if lsign is None:
            continue                      # unknown leg -> cannot sign it; skip rather than guess (never mis-sign)
        esign = -1 if d.get("is_exit") else 1
        cnt = float(d.get("fill_count") or 0.0)
        price = float(d.get("fill_price") or 0.0)
        fee = float(d.get("fee") or 0.0)
        a = agg.setdefault(tk, {"net": 0.0, "cost_yes": 0.0, "cost_no": 0.0, "fees": 0.0})
        a["net"] += lsign * esign * cnt
        a["cost_%s" % leg] += esign * price * cnt     # cash on THIS leg (entry adds, exit subtracts)
        a["fees"] += fee
    out = []
    for tk, a in agg.items():
        net = a["net"]
        if abs(net) < 1e-9:
            continue                       # flat -> not currently held
        held_leg = "yes" if net > 0 else "no"
        contracts = abs(net)
        cost = a["cost_yes"] if held_leg == "yes" else a["cost_no"]
        avg = (cost / contracts) if contracts else 0.0
        out.append({"ticker": tk, "market_type": market_type_from_ticker(tk), "held_leg": held_leg,
                    "contracts": contracts, "cost_basis_usd": cost, "avg_price": avg, "fees_usd": a["fees"]})
    out.sort(key=lambda x: x["ticker"])
    return out
