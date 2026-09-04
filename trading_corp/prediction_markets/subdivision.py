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

Never selects a secret VALUE. `secret_ref` (a KeyVault NAME, not a value) is NEVER read here. `owner_identity` IS
read as of M4 (active_accounts/accounts_overview) -- it is a SCOPING field (the account's owning Authelia
username, consumed by web.authz.visible_account_ids), NOT a secret; the fail-closed account filter needs it.
"""
from __future__ import annotations


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _column_exists(conn, table: str, column: str) -> bool:
    """Is `column` present on `table`? Used so a SELECT can be DEFENSIVE about a column that a not-yet-deployed
    migration adds (e.g. owner_identity on pm_account) -- absent -> the caller substitutes NULL, never a 500.
    Tolerates the table being absent too (PRAGMA yields no rows)."""
    try:
        return any(str(r[1]) == column for r in conn.execute("PRAGMA table_info(%s)" % table).fetchall())
    except Exception:
        return False


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
    sub-division. Empty if pm_account is absent (pre-migration-010). Read-only; never selects a secret value.

    ** owner_identity IS selected here (M4) -- it is a SCOPING field (the Authelia username that OWNS the account),
    NOT a secret. authz.visible_account_ids keys the fail-closed account filter on it, so every path that decides
    visibility MUST carry it. `secret_ref` (a KeyVault NAME) is still NEVER selected -- that would be a value the
    credential-free pm_web has no business reading. owner_identity is defensive: COALESCE to NULL if the column
    predates its migration, and a NULL owner is admin-only downstream. **"""
    if not _table_exists(conn, "pm_account"):
        return []
    has_owner = _column_exists(conn, "pm_account", "owner_identity")
    owner_sel = "owner_identity" if has_owner else "NULL AS owner_identity"
    rows = conn.execute(
        "SELECT account_id, COALESCE(label, account_id) AS account_label, venue, " + owner_sel + " "
        "FROM pm_account WHERE active=1 ORDER BY account_label").fetchall()
    return [dict(r) for r in rows]


def get_subdivision(conn, account_id: str, category: str) -> dict | None:
    """One ACTIVE sub-division's config for its detail page, or None (not found / tables absent -> 404)."""
    if not _ready(conn):
        return None
    # NB: `s.contracts` needs migration 014 -- deploy the migration BEFORE this query ships (sizing_summary tolerates
    # its absence by defaulting to 5, but the SELECT itself would error pre-014).
    r = conn.execute(
        "SELECT s.account_id, s.category, s.label AS sub_label, s.market_types, s.sizing_mode, s.fixed_stake_usd, "
        "       s.contracts, s.per_order_usd_cap, s.daily_usd_cap, s.max_open_usd, s.max_orders_per_day, "
        "       s.max_slippage_cents, s.created_ts, COALESCE(a.label, s.account_id) AS account_label, a.venue "
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
    if mode == "contracts":
        n = sub.get("contracts")
        n = 5 if n is None else int(n)
        return ("flat contracts · %d contract%s per copy (read per cycle -- change the number, no restart; "
                "the copy costs %d x the contract price)" % (n, "" if n == 1 else "s", n))
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
    # WHALE ON EVERY ROW (2026-09-01): each order row carries `wallet` (the copied whale) -- confirmed present on
    # entries AND all close rows (settlement/opposed) on the box, 0 NULL. Join pm_whale for a display name; the
    # template renders `user_name or wallet` (matching the "Copies these whales" list). Defensive: no join if
    # pm_whale is somehow absent -> user_name NULL, never a 500.
    has_whale = _table_exists(conn, "pm_whale")
    name_sel = "w.user_name" if has_whale else "NULL AS user_name"
    join = "LEFT JOIN pm_whale w ON w.wallet = o.wallet " if has_whale else ""
    rows = conn.execute(
        "SELECT o.id, o.ticker, o.order_side, o.outcome_leg, o.is_exit, o.submitted_count, o.submitted_price, "
        "       o.time_in_force, o.outcome_status, o.fill_count, o.fill_price, o.remaining_count, o.fee, "
        "       o.error_detail, o.submitted_ts, o.response_ts, o.close_source, o.realized_pnl, o.won, o.settled_ts, "
        "       o.wallet, " + name_sel + " "   # close_source distinguishes a SETTLEMENT-close from a whale-EXIT
        "FROM pm_subdivision_order o " + join +   # (both is_exit=1); realized_pnl is the P&L a settlement books.
        "WHERE o.account_id = ? AND o.category = ? AND o.dry_run = 0 "
        "ORDER BY COALESCE(o.response_ts, o.submitted_ts) DESC, o.id DESC LIMIT ?",
        (account_id, category, int(limit))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["market_type"] = market_type_from_ticker(d.get("ticker"))
        out.append(d)
    return out


def live_order_count(conn, account_id: str, category: str) -> int:
    """The TOTAL count of REAL orders (dry_run=0) for this sub-division -- the SAME set live_orders() draws from,
    but UNCAPPED. Lets the page say honestly 'showing the latest N of M' when the journal exceeds live_orders'
    display LIMIT, so the tile count (also uncapped) and the visible table never diverge SILENTLY (no silent cap).
    0 if the journal table is absent. Read-only."""
    if not _table_exists(conn, "pm_subdivision_order"):
        return 0
    r = conn.execute(
        "SELECT COUNT(*) AS n FROM pm_subdivision_order WHERE account_id = ? AND category = ? AND dry_run = 0",
        (account_id, category)).fetchone()
    return int(r["n"]) if r is not None and r["n"] is not None else 0


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
        "      AND fill_count IS NOT NULL AND fill_count > 0 AND ticker IS NOT NULL",   # ticker guard MIRRORS boot_reconcile (no NULL-ticker phantom holding)
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


# ── WHALE ATTRIBUTION ON THE HELD TABLE + THE PER-WHALE LIVE-COPY RECORD (2026-09-01) ─────────────────────────
# Jack: "which whale is this trade from" + "is copying this whale actually working". SAME-SIDE STACKING is the
# design (ten whales one side = fifty contracts), so ONE ticker can carry copies from several whales -> the held
# view is keyed on (ticker, WALLET): one row PER WHALE per ticker, which is what answers the question. Attribution
# is by the wallet ON EACH ROW (entries AND closes carry it), NEVER a close->entry join -- a settlement with NULL
# cid/oidx (the first Cubs close, id=8) would be lost by a join but its wallet is present.

def live_positions_by_whale(conn, account_id: str, category: str) -> list[dict]:
    """CURRENTLY HELD, split PER (ticker, whale) -- the same journal-derived net as live_positions() but grouped by
    wallet too, so a ticker stacked by 2-3 whales shows one row each (answers 'which whale is this trade from').
    Net signed contracts = sum over that whale's FILLED rows of sign(leg)*sign(entry/exit)*fill_count (the
    boot_reconcile convention). A (ticker, wallet) whose net rounds to 0 is dropped (that whale's copy is flat).
    Read-only, journal-only. NB: summing these back over whales equals live_positions()'s per-ticker net (unless two
    whales sit on OPPOSITE legs of one ticker -- a pre-guard opposing pair -- where the per-whale rows are the honest
    view and the per-ticker net partially cancels)."""
    if not _table_exists(conn, "pm_subdivision_order"):
        return []
    has_whale = _table_exists(conn, "pm_whale")
    name_sel = "w.user_name" if has_whale else "NULL AS user_name"
    join = "LEFT JOIN pm_whale w ON w.wallet = o.wallet " if has_whale else ""
    rows = conn.execute(
        "SELECT o.ticker, o.wallet, " + name_sel + ", o.outcome_leg, o.is_exit, o.fill_count, o.fill_price, o.fee "
        "FROM pm_subdivision_order o " + join +
        "WHERE o.account_id = ? AND o.category = ? AND o.dry_run = 0 AND o.outcome_status = 'filled' "
        "      AND o.fill_count IS NOT NULL AND o.fill_count > 0 AND o.ticker IS NOT NULL",
        (account_id, category)).fetchall()
    agg: dict = {}
    for r in rows:
        d = dict(r)
        tk = d.get("ticker") or ""
        wal = d.get("wallet") or ""
        leg = str(d.get("outcome_leg") or "").lower()
        lsign = _LEG_SIGN.get(leg)
        if lsign is None:
            continue                      # unknown leg -> cannot sign it; skip rather than mis-sign
        esign = -1 if d.get("is_exit") else 1
        cnt = float(d.get("fill_count") or 0.0)
        price = float(d.get("fill_price") or 0.0)
        fee = float(d.get("fee") or 0.0)
        a = agg.setdefault((tk, wal), {"net": 0.0, "cost_yes": 0.0, "cost_no": 0.0, "fees": 0.0,
                                       "user_name": d.get("user_name")})
        a["net"] += lsign * esign * cnt
        a["cost_%s" % leg] += esign * price * cnt
        a["fees"] += fee
    out = []
    for (tk, wal), a in agg.items():
        net = a["net"]
        if abs(net) < 1e-9:
            continue
        held_leg = "yes" if net > 0 else "no"
        contracts = abs(net)
        cost = a["cost_yes"] if held_leg == "yes" else a["cost_no"]
        out.append({"ticker": tk, "wallet": wal, "user_name": a["user_name"],
                    "market_type": market_type_from_ticker(tk), "held_leg": held_leg, "contracts": contracts,
                    "cost_basis_usd": cost, "avg_price": (cost / contracts) if contracts else 0.0, "fees_usd": a["fees"]})
    out.sort(key=lambda x: (x["ticker"], x["wallet"]))
    return out


def live_copies_by_whale(conn, account_id: str, category: str, *, thin_floor: int = 10) -> list[dict]:
    """★ THE PER-WHALE LIVE-COPY RECORD -- REAL money placed on the venue for this sub-division, per whale. This is
    the ONE record that answers 'is copying this whale actually working for me': DISTINCT from the PAPER-TRADE record
    ('would it have') and the PROSPECT-screen record ('did it historically'), both of which key on the same whales
    but a DIFFERENT basis. LIVE COPIES ONLY.

    Attribution is by the WALLET ON EACH ROW (never a close->entry join -- see the module note; the first Cubs
    settlement id=8 has NULL cid/oidx and would be dropped by a join, but its wallet is present). HONESTY DISCIPLINE
    (same as the account P&L): realized / settled-W-L / SAMPLE / open-at-cost shown SEPARATELY; n is tiny per whale
    so the caller flags thin_sample. OPPOSED closes (the guard's flattens, realized_pnl NULL, won NULL) are counted
    SEPARATELY as `opposed_closed` and NEVER folded into realized or W/L -- they are guard-terminated, not settled
    outcomes. Read-only, journal-only. Includes every whale with >=1 filled row PLUS every currently-attached whale
    (so an attached-but-not-yet-copied whale shows 0s, and a detached whale that DID copy still shows its record)."""
    if not _table_exists(conn, "pm_subdivision_order"):
        return []
    has_whale = _table_exists(conn, "pm_whale")
    name_sel = "w.user_name" if has_whale else "NULL AS user_name"
    join = "LEFT JOIN pm_whale w ON w.wallet = o.wallet " if has_whale else ""
    rows = conn.execute(
        "SELECT o.wallet, " + name_sel + ", "
        "  SUM(CASE WHEN o.is_exit=0 THEN 1 ELSE 0 END) copies, "
        "  SUM(CASE WHEN o.is_exit=1 AND o.close_source='settlement' AND o.won=1 THEN 1 ELSE 0 END) settled_w, "
        "  SUM(CASE WHEN o.is_exit=1 AND o.close_source='settlement' AND o.won=0 THEN 1 ELSE 0 END) settled_l, "
        "  SUM(CASE WHEN o.is_exit=1 AND o.close_source='settlement' THEN 1 ELSE 0 END) n_settled, "
        "  SUM(CASE WHEN o.is_exit=1 AND o.close_source='opposed' THEN 1 ELSE 0 END) opposed_closed, "
        "  SUM(CASE WHEN o.is_exit=1 THEN 1 ELSE 0 END) n_closed, "
        "  COALESCE(SUM(CASE WHEN o.is_exit=1 THEN o.realized_pnl END), 0) realized "
        "FROM pm_subdivision_order o " + join +
        "WHERE o.account_id = ? AND o.category = ? AND o.dry_run = 0 AND o.outcome_status = 'filled' "
        "GROUP BY o.wallet", (account_id, category)).fetchall()
    rec: dict = {}
    for r in rows:
        d = dict(r); wal = d.get("wallet") or ""
        rec[wal] = {"wallet": wal, "user_name": d.get("user_name"), "attached": False,
                    "copies": int(d["copies"] or 0), "settled_w": int(d["settled_w"] or 0),
                    "settled_l": int(d["settled_l"] or 0), "n_settled": int(d["n_settled"] or 0),
                    "opposed_closed": int(d["opposed_closed"] or 0), "n_closed": int(d["n_closed"] or 0),
                    "realized_pnl": float(d["realized"] or 0.0),
                    "open_contracts": 0.0, "open_cost_usd": 0.0, "n_open": 0}
    # open-at-cost per whale, from the per-(ticker,wallet) held rows
    for h in live_positions_by_whale(conn, account_id, category):
        wal = h.get("wallet") or ""
        e = rec.setdefault(wal, {"wallet": wal, "user_name": h.get("user_name"), "attached": False, "copies": 0,
                                 "settled_w": 0, "settled_l": 0, "n_settled": 0, "opposed_closed": 0, "n_closed": 0,
                                 "realized_pnl": 0.0, "open_contracts": 0.0, "open_cost_usd": 0.0, "n_open": 0})
        e["n_open"] += 1
        e["open_contracts"] += float(h["contracts"]); e["open_cost_usd"] += float(h["cost_basis_usd"])
        if e.get("user_name") is None:
            e["user_name"] = h.get("user_name")
    # fold in currently-attached whales that have no rows yet (attached, 0 copies) + mark the attached flag
    for a in attached_whales(conn, account_id, category):
        wal = a.get("wallet") or ""
        e = rec.setdefault(wal, {"wallet": wal, "user_name": a.get("user_name"), "attached": True, "copies": 0,
                                 "settled_w": 0, "settled_l": 0, "n_settled": 0, "opposed_closed": 0, "n_closed": 0,
                                 "realized_pnl": 0.0, "open_contracts": 0.0, "open_cost_usd": 0.0, "n_open": 0})
        e["attached"] = True
        if e.get("user_name") is None:
            e["user_name"] = a.get("user_name")
    for e in rec.values():
        e["thin_sample"] = e["n_settled"] < int(thin_floor)
    # attached first, then by copies desc, then wallet
    return sorted(rec.values(), key=lambda x: (not x["attached"], -x["copies"], x["wallet"]))


# ── P&L / win-loss aggregation across sub-divisions (multi-account foundation, 2026-09-01) ──────────────────────
# REALIZED-ONLY basis (the credential-free basis, R2 ruling): pm_web holds no venue keys, so open positions are
# shown at COST (live_positions), NEVER marked-to-market -- a mark would need a live venue read pm_web cannot do.
# Realized P&L + win/loss come from the terminal-close rows the engine books (is_exit=1: settlement OR whale-exit
# OR opposed). Realized is booked ONLY at close (is_exit=1), so summing there is complete AND never double-counts
# an entry. Read-only, journal-only -- the standalone guard holds.

def subdivision_pnl(conn, account_id: str, category: str) -> dict:
    """Realized P&L + win/loss + open exposure for ONE sub-division. `realized_pnl`/`wins`/`losses`/`n_closed`
    from terminal closes (is_exit=1, dry_run=0, filled); a close with won IS NULL (e.g. a void) counts in
    n_closed but as neither win nor loss (honest). `n_open`/`open_contracts`/`open_cost_usd` from the
    journal-derived held positions, at COST. Zeroes if the order journal is absent (pre-migration-010)."""
    out = {"account_id": account_id, "category": category, "realized_pnl": 0.0,
           "wins": 0, "losses": 0, "n_closed": 0, "n_open": 0, "open_contracts": 0.0, "open_cost_usd": 0.0}
    if _table_exists(conn, "pm_subdivision_order"):
        r = conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) rp, "
            "       SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) wins, "
            "       SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END) losses, COUNT(*) n "
            "FROM pm_subdivision_order "
            "WHERE account_id = ? AND category = ? AND dry_run = 0 AND is_exit = 1 AND outcome_status = 'filled'",
            (account_id, category)).fetchone()
        out["realized_pnl"] = float(r["rp"] or 0.0)
        out["wins"] = int(r["wins"] or 0)
        out["losses"] = int(r["losses"] or 0)
        out["n_closed"] = int(r["n"] or 0)
    held = live_positions(conn, account_id, category)
    out["n_open"] = len(held)
    out["open_contracts"] = sum(float(h["contracts"]) for h in held)
    out["open_cost_usd"] = sum(float(h["cost_basis_usd"]) for h in held)
    return out


def account_pnl(conn, account_id: str) -> dict:
    """Aggregate the above across ALL of an account's ACTIVE sub-divisions + carry the per-sub-division breakdown
    (the account page shows both the account total and a row per sub-division). Read-only; zeroed/empty if the
    money tables are absent (pre-010)."""
    cats = []
    if _ready(conn):
        cats = [dict(r)["category"] for r in conn.execute(
            "SELECT category FROM pm_subdivision WHERE account_id = ? AND active = 1 ORDER BY category",
            (account_id,)).fetchall()]
    breakdown = [subdivision_pnl(conn, account_id, c) for c in cats]
    return {
        "account_id": account_id, "n_subdivisions": len(breakdown),
        "realized_pnl": sum(b["realized_pnl"] for b in breakdown),
        "wins": sum(b["wins"] for b in breakdown), "losses": sum(b["losses"] for b in breakdown),
        "n_closed": sum(b["n_closed"] for b in breakdown), "n_open": sum(b["n_open"] for b in breakdown),
        "open_contracts": sum(b["open_contracts"] for b in breakdown),
        "open_cost_usd": sum(b["open_cost_usd"] for b in breakdown),
        "subdivisions": breakdown,
    }


def held_tickers(conn) -> list[str]:
    """Every DISTINCT ticker CURRENTLY HELD across ALL active sub-divisions (both accounts, every category) --
    journal-derived (live_positions), so a position that is open in ANY sub-division is represented exactly once.
    The mark poller derives the series to fetch from THIS, so it covers ATP/UFC/WTA the same as MLB instead of a
    hardcoded MLB list. Empty if the money tables are absent (pre-010). Read-only, journal-only."""
    if not _ready(conn):
        return []
    subs = conn.execute(
        "SELECT account_id, category FROM pm_subdivision WHERE active = 1 ORDER BY account_id, category").fetchall()
    seen: dict = {}
    for s in subs:
        d = dict(s)
        for h in live_positions(conn, d["account_id"], d["category"]):
            tk = h.get("ticker")
            if tk:
                seen[tk] = True
    return sorted(seen.keys())


def traded_series(conn) -> tuple:
    """The distinct Kalshi SERIES to poll for current marks, derived from every HELD ticker across all
    sub-divisions -- the poller's series list, so a non-MLB category we hold gets priced too (item 3). A Kalshi
    ticker is 'KX<SERIES>-<event>-<market>'; the series is the pre-'-' prefix. Empty tuple when nothing is held ->
    the poller falls back to its MLB default so a cold start still primes the MLB slate. Read-only."""
    out = set()
    for tk in held_tickers(conn):
        head = str(tk or "").split("-", 1)[0].strip().upper()
        if head:
            out.add(head)
    return tuple(sorted(out))


def accounts_overview(conn) -> list[dict]:
    """Every ACTIVE account with its aggregate realized P&L / win-loss / open exposure -- the /accounts landing.
    Read-only; empty if pm_account is absent (pre-010). Never selects a secret value."""
    out = []
    for a in active_accounts(conn):
        agg = account_pnl(conn, a["account_id"])
        agg["account_label"] = a.get("account_label")
        agg["venue"] = a.get("venue")
        agg["owner_identity"] = a.get("owner_identity")   # M4 scoping key -- the account filter reads this
        out.append(agg)
    return out
