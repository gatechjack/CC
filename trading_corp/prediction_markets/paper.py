"""Paper-trading poller + adjudicator for the Prediction Markets farm league (CP3a).

Standalone farm-league logic. Reads the datastore (`db`) and derives categories (`category`); the
read-only Polymarket client is INJECTED (`poll_pinned(..., client=...)`). Does NOT edit `ingest.py`
(off-limits) and NEVER writes the legacy DB. The engine is untouched.

TWO BIASES (labelled here + in the schema; surfaced in the UI in CP3b):
  1. `entry_observed_ts` is OBSERVATION time (+/- the poll interval), NOT a fill ts. `/positions` carries
     no fill timestamp; the poller stamps the time it SAW the position open. Recency basis is observation.
  2. Same-poll open-and-close is INVISIBLE -> BIAS-UP: a whale who enters and exits inside one interval
     never appears, so the misses skew toward fast round-trips / quick losses and the paper record reads
     BETTER than reality. Asserted, not measured (see CP3A report cross-check).

STALE vs RESOLVED is a two-phase adjudication (addendum 1): a vanished position is NOT classified on the
disappearance (a `/positions` row drops on BOTH whale-exit AND market settle). The poller marks it
`pending_adjudication`; `adjudicate()` (off the weekly `/closed-positions` refresh) decides
`closed`(resolution) vs `stale`(whale_exit) deterministically -- biases DOWN (a whale exit never books
paper P&L).

Spec: reports/prediction_markets/P2_PLAN.md 5.2 (as amended 2026-08-24) + CP3A_CONTAMINATION_GATE.md.
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .category import derive_category_from_slug

# size comparison tolerance for scale-in/reduction detection (sizes are contract counts; a real
# scale-in moves size by >= ~1 contract, JSON float noise is ~1e-12, so 1e-6 cleanly separates them).
_SIZE_EPS = 1e-6

# Round-number /positions counts that signal an un-paginated API page cap (Ruling H). fetch_positions is
# un-paginated + live-shared (NOT edited); a poll returning EXACTLY one of these is flagged as cap_suspect
# so the tell reads itself instead of relying on someone eyeballing per-whale counts.
_CAP_SIGNATURES = {50, 100, 250, 500}

# pm_paper_config code DEFAULTS -- a missing pm_paper_config table/row degrades HONESTLY to these rather
# than erroring (the migration also seeds them; these are the safety net on a read path).
CONFIG_DEFAULTS: dict[str, float] = {
    "poll_interval_sec": 300.0,     # 5 min
    "grace_window_sec": 172800.0,   # 48 h adjudication grace (addendum 1)
    "size_basis": 100.0,            # fixed paper stake (contracts/shares; e7 -- NOT the whale's size, NOT dollars)
}


class PaperError(RuntimeError):
    """Base error for the paper farm-league poller/adjudicator."""


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def get_config(conn, key: str) -> float:
    """Numeric config value from pm_paper_config, else the code DEFAULT. Tolerates a missing table/row
    (degrades honestly; never creates the table on a read path)."""
    default = CONFIG_DEFAULTS.get(key, 0.0)
    try:
        row = conn.execute("SELECT value FROM pm_paper_config WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return default                         # table absent -> honest default
    if row is None or row[0] is None:
        return default
    return _f(row[0], default)


# ---- /positions PositionRow field access -----------------------------------------------------------
# The shared client's PositionRow (trading_corp/data/polymarket_data_api_client.py) parses only a subset
# of fields into attributes; outcomeIndex / redeemable / curPrice / endDate land in `.extra`. Read them
# from there (a plain object with those attributes also works, for tests/fixtures).

def _extra(p) -> dict:
    ex = getattr(p, "extra", None)
    return ex if isinstance(ex, dict) else {}


def pos_outcome_index(p) -> int:
    return _i(_extra(p).get("outcomeIndex", getattr(p, "outcome_index", 0)), 0)


def pos_end_date(p) -> str:
    return str(_extra(p).get("endDate", getattr(p, "end_date", "")) or "")


def is_genuinely_open(p) -> bool:
    """OR-filter (ruling D1): a `/positions` row is RESOLVED-UNREDEEMED (EXCLUDE) if `redeemable` is true
    OR `curPrice` sits at a settled bound (<=0 or >=1). Genuine opens have 0 < curPrice < 1 and redeemable
    false. Bias-DOWN by construction: over-excluding drops a genuine open (allowed); under-excluding books
    a phantom entry on a settled position (forbidden) -- the asymmetry is why the guard is OR (not AND) and
    uses <=/>= bounds. The redeemable<=>curPrice biconditional held on n=3 only, so BOTH are checked.
    A missing curPrice coerces to 0.0 -> treated as settled (over-exclude, bias-down)."""
    ex = _extra(p)
    if bool(ex.get("redeemable")):
        return False
    cur = _f(ex.get("curPrice", getattr(p, "cur_price", 0.0)), 0.0)
    if cur <= 0.0 or cur >= 1.0:
        return False
    return True


# ---- the poller ------------------------------------------------------------------------------------

async def poll_pinned(conn, *, client, now_ts: int | None = None,
                      poll_interval_sec: int | None = None, size_basis: float | None = None) -> dict:
    """Poll `/positions` for PINNED whales (pm_watchlist status='pinned') and capture genuinely-open
    positions as paper entries. Per whale: fetch once, filter to genuinely-open, then ROUTE each position
    to its tier-1 derived category. A position whose derived category is in the whale's pinned set is
    captured under that category; one that is NOT is counted + logged in `skipped` (Ruling F -- an invisible
    exclusion is survivorship even when expected empty). Per-whale isolation: a fetch error leaves that
    whale's pairs UN-polled, so their `pm_roster.last_polled_ts` stays stale (Ruling G -- absence is never
    the signal). Idempotent via the open guard (a leg with an OPEN row is never re-captured).

    Under the reversed C2.4 every category a migrated whale trades is pinned, so the category filter is near
    a no-op today (`skipped` ~0); it still matters once the farm league pins single-category whales, which is
    exactly why a non-zero `skipped` -- a position deriving to a category the whale is NOT pinned in -- is a
    tier-1 derivation surprise worth seeing."""
    now = now_ts if now_ts is not None else int(time.time())
    interval = int(poll_interval_sec if poll_interval_sec is not None else get_config(conn, "poll_interval_sec"))
    basis = float(size_basis if size_basis is not None else get_config(conn, "size_basis"))

    by_wallet: dict[str, set] = defaultdict(set)
    for r in conn.execute("SELECT wallet, category FROM pm_watchlist WHERE status = 'pinned'").fetchall():
        by_wallet[(r["wallet"] or "").lower()].add(r["category"])

    per_pair: list[dict] = []
    skipped: list[dict] = []          # F: genuinely-open positions whose derived category is NOT pinned for the whale
    cap_suspects: list[dict] = []     # H: round-number /positions counts
    errors: list[dict] = []
    for wallet, pinned_cats in by_wallet.items():
        try:
            positions = await client.fetch_positions(wallet)
        except Exception as e:                                 # per-whale isolation; this whale's pairs stay UN-polled (G)
            errors.append({"wallet": wallet, "categories": sorted(pinned_cats), "error": repr(e)[:200]})
            continue
        n_returned = len(positions)
        if n_returned in _CAP_SIGNATURES:                      # H: cap signature reads itself
            cap_suspects.append({"wallet": wallet, "positions_returned": n_returned})
        genuinely_open = [p for p in positions if is_genuinely_open(p)]
        by_cat: dict[str, list] = defaultdict(list)
        for p in genuinely_open:
            pc, _s = derive_category_from_slug(getattr(p, "event_slug", "") or "", getattr(p, "slug", "") or "")
            if pc in pinned_cats:
                by_cat[pc].append(p)
            else:                                              # F: outside the whale's pinned set -- log with slug
                skipped.append({"wallet": wallet, "condition_id": str(getattr(p, "condition_id", "") or ""),
                                "derived_category": pc, "slug": str(getattr(p, "slug", "") or "")})
        for category in sorted(pinned_cats):                   # EVERY pinned category (empty -> polled, found nothing)
            res = _process_category(conn, wallet, category, by_cat.get(category, []), now, interval, basis)
            res["positions_returned"] = n_returned
            res["genuinely_open"] = len(genuinely_open)
            conn.execute("UPDATE pm_roster SET last_polled_ts = ? WHERE wallet = ? AND category = ?",
                         (now, wallet, category))              # G: mark this pair ACTUALLY polled
            per_pair.append(res)
        if hasattr(conn, "commit"):
            conn.commit()

    totals = _sum_totals(per_pair)
    totals["n_skipped_category"] = len(skipped)
    totals["cap_suspects"] = len(cap_suspects)
    totals["errors"] = len(errors)
    return {"per_pair": per_pair, "skipped": skipped, "cap_suspects": cap_suspects, "errors": errors,
            "totals": totals, "now_ts": now, "poll_interval_sec": interval, "size_basis": basis}


def _process_category(conn, wallet: str, category: str, positions: list, now: int,
                      interval: int, basis: float) -> dict:
    # `positions` is already filtered to genuinely-open AND this pinned category (routed by poll_pinned).
    cat_pos: dict[tuple, Any] = {
        (str(getattr(p, "condition_id", "") or ""), pos_outcome_index(p)): p for p in positions}

    open_trades = {
        (r["condition_id"], _i(r["outcome_index"], 0)): r
        for r in conn.execute(
            "SELECT * FROM pm_paper_trade WHERE wallet=? AND category=? AND status='open'",
            (wallet, category)).fetchall()
    }

    captured = adds = reductions = touched = vanished = 0
    for key, p in cat_pos.items():
        row = open_trades.get(key)
        if row is None:
            _insert_entry(conn, wallet, category, p, now, interval, basis)   # open guard: no OPEN row -> NEW entry
            captured += 1
            continue
        prior = row["last_observed_size"]
        if prior is None:
            prior = row["whale_size_at_observation"]
        prior = _f(prior, 0.0)
        cur = _f(getattr(p, "size", 0.0), 0.0)
        pk = (row["condition_id"], row["outcome_index"], row["entry_observed_ts"])
        if cur > prior + _SIZE_EPS:                                          # scale-in: NOT a new entry (addendum 3)
            conn.execute(
                "UPDATE pm_paper_trade SET n_observed_adds = n_observed_adds + 1, last_add_observed_ts=?, "
                "last_observed_size=?, last_observed_ts=?, updated_ts=? "
                "WHERE wallet=? AND condition_id=? AND outcome_index=? AND entry_observed_ts=?",
                (now, cur, now, now, wallet, *pk))
            adds += 1
        elif cur < prior - _SIZE_EPS:                                       # partial whale exit -> log, no status change
            conn.execute(
                "UPDATE pm_paper_trade SET n_observed_reductions = n_observed_reductions + 1, "
                "last_reduction_observed_ts=?, last_observed_size=?, last_observed_ts=?, updated_ts=? "
                "WHERE wallet=? AND condition_id=? AND outcome_index=? AND entry_observed_ts=?",
                (now, cur, now, now, wallet, *pk))
            reductions += 1
        else:                                                                # unchanged -> touch last-seen
            conn.execute(
                "UPDATE pm_paper_trade SET last_observed_size=?, last_observed_ts=?, updated_ts=? "
                "WHERE wallet=? AND condition_id=? AND outcome_index=? AND entry_observed_ts=?",
                (cur, now, now, wallet, *pk))
            touched += 1

    for key, row in open_trades.items():
        if key not in cat_pos:                                              # vanished pre-resolution -> pending (addendum 1)
            conn.execute(
                "UPDATE pm_paper_trade SET status='pending_adjudication', exit_observed_ts=?, updated_ts=? "
                "WHERE wallet=? AND condition_id=? AND outcome_index=? AND entry_observed_ts=?",
                (now, now, wallet, row["condition_id"], row["outcome_index"], row["entry_observed_ts"]))
            vanished += 1

    # loudness (the ingest `pulled == stored` analogue): every genuinely-open pinned-category position is
    # EITHER newly captured OR matched an already-open row -- no silent drop.
    accounted = captured + adds + reductions + touched
    if accounted != len(cat_pos):
        raise PaperError("poller drop: wallet=%s category=%s in_category=%d accounted=%d"
                         % (wallet, category, len(cat_pos), accounted))

    return {"wallet": wallet, "category": category, "polled": True, "in_category": len(cat_pos),
            "captured": captured, "adds": adds, "reductions": reductions, "touched": touched,
            "vanished": vanished, "open_after": _count_open(conn, wallet, category)}


def _insert_entry(conn, wallet: str, category: str, p, now: int, interval: int, basis: float) -> None:
    avg = _f(getattr(p, "avg_price", 0.0), 0.0)
    size = _f(getattr(p, "size", 0.0), 0.0)
    conn.execute(
        "INSERT INTO pm_paper_trade ("
        "wallet, category, condition_id, outcome_index, slug, event_slug, title, outcome, side, "
        "entry_observed_ts, entry_price_avg_at_observation, whale_size_at_observation, size_basis, "
        "cost_basis, poll_interval_sec, entry_basis, market_end_date, last_observed_size, last_observed_ts, "
        "status, source, opened_ts, updated_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'BUY', ?, ?, ?, ?, ?, ?, 'positions_observation', ?, ?, ?, "
        "'open', 'poller', ?, ?)",
        (wallet, category, str(getattr(p, "condition_id", "") or ""), pos_outcome_index(p),
         str(getattr(p, "slug", "") or ""), str(getattr(p, "event_slug", "") or ""),
         str(getattr(p, "title", "") or ""), str(getattr(p, "outcome", "") or ""),
         now, avg, size, basis, basis * avg, interval, pos_end_date(p), size, now, now, now))


def _count_open(conn, wallet: str, category: str) -> int:
    return conn.execute("SELECT COUNT(1) FROM pm_paper_trade WHERE wallet=? AND category=? AND status='open'",
                        (wallet, category)).fetchone()[0]


def _sum_totals(per_pair: list[dict]) -> dict:
    keys = ("in_category", "captured", "adds", "reductions", "touched", "vanished")
    out = {k: sum(_i(r.get(k), 0) for r in per_pair) for k in keys}
    out["pairs"] = len(per_pair)
    return out


# ---- the adjudicator (runs off the EXISTING weekly /closed-positions refresh, NOT the poller) --------

class PaperSubsetError(PaperError):
    """C2.3: a PINNED-paper whale is NOT in the weekly /closed-positions refresh set, so its
    pending_adjudication rows could never resolve (silent limbo -- a working-looking system that quietly
    never closes). Raised to FAIL LOUD; never warn-and-continue."""


def assert_pinned_subset_of_refresh(conn) -> dict:
    """C2.3 subset assertion: every PINNED-paper wallet must be in the weekly refresh set
    (pm_roster WHERE active=1 -- Ruling B's refresh source). If any pinned wallet is not refreshed, its
    vanished positions would sit in pending_adjudication forever. FAIL LOUD naming the offenders; never
    warn-and-continue. Read-only + idempotent; returns the membership report (also used at seed time)."""
    pinned = sorted({(r["wallet"] or "").lower() for r in conn.execute(
        "SELECT DISTINCT wallet FROM pm_watchlist WHERE status='pinned'").fetchall()})
    refreshed = {(r["wallet"] or "").lower() for r in conn.execute(
        "SELECT DISTINCT wallet FROM pm_roster WHERE active=1").fetchall()}
    unrefreshed = [w for w in pinned if w not in refreshed]
    report = {"n_pinned": len(pinned), "n_refreshed": len(refreshed), "unrefreshed": unrefreshed}
    if unrefreshed:
        raise PaperSubsetError(
            "PINNED-but-UNREFRESHED wallet(s) -- their pending_adjudication rows would never resolve; "
            "add them to pm_roster (active=1) or unpin: %s" % unrefreshed)
    return report


def _parse_end_date(s) -> int | None:
    """Parse a /positions endDate (ISO date or datetime, optional trailing 'Z') to a UTC unix ts.
    None if missing/unparseable -> the caller treats 'cannot prove we are past resolution' as NOT stale."""
    if not s:
        return None
    t = str(s).strip().replace("Z", "+00:00")
    dt = None
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        try:
            dt = datetime.fromisoformat(t[:10])          # date-only fallback ("2026-09-01")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _past_grace(market_end_date, now: int, grace: int) -> bool:
    ts = _parse_end_date(market_end_date)
    if ts is None:
        return False                                      # cannot prove past-resolution -> stay pending (never guess stale)
    return now >= ts + grace


def _paper_realized(row, won: int) -> float:
    """PAPER realized P&L from resolution, on OUR fixed size_basis (NOT the whale's realized_pnl):
    a won leg pays $1/contract -> size_basis - cost_basis; a lost leg pays 0 -> -cost_basis."""
    basis = _f(row["size_basis"], 0.0)
    cost = _f(row["cost_basis"], 0.0)
    return (basis - cost) if won else (-cost)


def adjudicate(conn, *, now_ts: int | None = None, grace_window_sec: int | None = None) -> dict:
    """Resolve pending_adjudication paper trades off the EXISTING weekly /closed-positions data
    (pm_closed_position), NOT off the poller (addendum 1, two-phase):
      - a matching pm_closed_position (wallet, condition_id, outcome_index) exists -> status='closed',
        close_source='resolution', book paper realized_pnl + won, import the 3A pnl_suspect parity.
      - no match AND we are past market_end_date + grace_window -> status='stale', close_source='whale_exit'
        (EXCLUDED from realized stats).
      - no match, within grace (or end_date unparseable) -> stay pending (resolution may still land).
    Biases DOWN: a whale exit never books paper P&L; an unparseable end_date is never called stale.
    Runs the C2.3 subset assertion FIRST -- FAILS LOUD before touching any row."""
    now = now_ts if now_ts is not None else int(time.time())
    grace = int(grace_window_sec if grace_window_sec is not None else get_config(conn, "grace_window_sec"))
    subset = assert_pinned_subset_of_refresh(conn)        # FAIL LOUD before adjudicating anything

    pending = conn.execute("SELECT * FROM pm_paper_trade WHERE status='pending_adjudication'").fetchall()
    closed = staled = still_pending = 0
    for row in pending:
        w = (row["wallet"] or "").lower()
        pk = (row["wallet"], row["condition_id"], row["outcome_index"], row["entry_observed_ts"])
        cp = conn.execute(
            "SELECT won, realized_pnl, resolved_ts, pnl_suspect, suspect_reason FROM pm_closed_position "
            "WHERE wallet=? AND condition_id=? AND outcome_index=?",
            (w, row["condition_id"], _i(row["outcome_index"], 0))).fetchone()
        if cp is not None:
            won = _i(cp["won"], 0)
            conn.execute(
                "UPDATE pm_paper_trade SET status='closed', close_source='resolution', resolved_ts=?, "
                "won=?, realized_pnl=?, pnl_suspect=?, suspect_reason=?, updated_ts=? "
                "WHERE wallet=? AND condition_id=? AND outcome_index=? AND entry_observed_ts=?",
                (cp["resolved_ts"], won, _paper_realized(row, won), _i(cp["pnl_suspect"], 0),
                 cp["suspect_reason"], now, *pk))
            closed += 1
        elif _past_grace(row["market_end_date"], now, grace):
            conn.execute(
                "UPDATE pm_paper_trade SET status='stale', close_source='whale_exit', stale_ts=?, "
                "stale_reason='vanished_pre_resolution_grace_elapsed', updated_ts=? "
                "WHERE wallet=? AND condition_id=? AND outcome_index=? AND entry_observed_ts=?",
                (now, now, *pk))
            staled += 1
        else:
            still_pending += 1
    if hasattr(conn, "commit"):
        conn.commit()
    return {"pending_in": len(pending), "closed": closed, "staled": staled,
            "still_pending": still_pending, "grace_window_sec": grace, "subset": subset}


# ---- roster/watchlist seed from pm_category_stats (Jack's ruling 2026-08-24; C2.4 REVERSED) ---------
# The watchlist (paper-traded set) is EVERY (wallet, category) pair in pm_category_stats for the migrated
# legacy whale set -- NOT a curated pin list. NO minimum-resolved floor (n=3 counts); 'unknown'-category
# pairs stay; ALL categories paper-trade (real money still needs a P3 account-category attachment). This
# matches P2_PLAN Ruling B (as amended 2026-08-24). The prior scout-provenance seed (load_pin_provenance +
# config/pm_farm_pin_provenance.yaml) is RETIRED from the seed path -- the yaml stays on disk as the
# historical scout attribution, but nothing reads it here. See CP3A_CONTAMINATION_GATE.md (C2.4 reversal).


def seed_farm_roster(conn, *, wallets, now_ts: int | None = None) -> dict:
    """Seed pm_roster(active=1) + pm_watchlist(status='pinned') for EVERY (wallet, category) row present in
    pm_category_stats for the migrated whale set. Idempotent (INSERT OR IGNORE). Nothing can be
    'unresolved' -- every pair is generated from a row that exists by definition (Ruling B; C2.4 reversed).

    wallets: the migrated legacy whale wallets (from agent_state via rosters.load_seed_roster). Every
    (wallet, category) in pm_category_stats for those wallets becomes a roster + pinned-watchlist pair --
    NO floor (n=3 counts), 'unknown' included, ALL categories. user_name is joined from pm_whale (may be
    empty until sync-names has run). Returns {n_seeded, n_wallets, seeded:[{wallet, user_name, category,
    rows_in_category, status}], wallets}.
    """
    now = now_ts if now_ts is not None else int(time.time())
    wset = sorted({(w or "").lower() for w in (wallets or []) if w})
    if not wset:
        return {"n_seeded": 0, "n_wallets": 0, "seeded": [], "wallets": []}
    ph = ",".join("?" * len(wset))
    rows = conn.execute(
        "SELECT s.wallet, s.category, s.n_resolved, w.user_name "
        "FROM pm_category_stats s LEFT JOIN pm_whale w ON s.wallet = w.wallet "
        "WHERE s.wallet IN (%s) ORDER BY s.wallet, s.category" % ph, wset).fetchall()
    seeded: list[dict] = []
    src = "pm_category_stats (Ruling B; C2.4 reversed 2026-08-24)"
    for r in rows:
        wal = (r["wallet"] or "").lower()
        cat = r["category"]
        name = r["user_name"] or ""
        conn.execute(
            "INSERT OR IGNORE INTO pm_roster (wallet, category, user_name, source, added_ts, active) "
            "VALUES (?, ?, ?, ?, ?, 1)", (wal, cat, name, src, now))
        conn.execute(
            "INSERT OR IGNORE INTO pm_watchlist (wallet, category, added_ts, source, status, pinned_ts, updated_ts) "
            "VALUES (?, ?, ?, ?, 'pinned', ?, ?)", (wal, cat, now, src, now, now))
        seeded.append({"wallet": wal, "user_name": name, "category": cat,
                       "rows_in_category": _i(r["n_resolved"], 0), "status": "pinned"})
    if hasattr(conn, "commit"):
        conn.commit()
    return {"n_seeded": len(seeded), "n_wallets": len(wset), "seeded": seeded, "wallets": wset}


def seeded_pairs_table(conn, wallets=None) -> list[dict]:
    """The eyeball table (repurposed from validate_pairs_have_history, which became meaningless once the
    seed IS 'has rows in this category'). Every PINNED (wallet, category) pair with user_name, category,
    rows_in_category (pm_category_stats.n_resolved) and status -- read from the SEEDED state so it can be
    printed independently of a seed run. Optionally scoped to `wallets`. Read-only. This is the list Jack
    reviews before the poller's first run."""
    q = ("SELECT w.wallet, r.user_name, w.category, w.status, s.n_resolved "
         "FROM pm_watchlist w "
         "LEFT JOIN pm_roster r ON w.wallet = r.wallet AND w.category = r.category "
         "LEFT JOIN pm_category_stats s ON w.wallet = s.wallet AND w.category = s.category "
         "WHERE w.status = 'pinned'")
    params: list = []
    if wallets:
        wl = sorted({(x or "").lower() for x in wallets if x})
        if not wl:
            return []
        q += " AND w.wallet IN (%s)" % ",".join("?" * len(wl))
        params = wl
    q += " ORDER BY w.wallet, w.category"
    return [{"wallet": r["wallet"], "user_name": r["user_name"] or "", "category": r["category"],
             "rows_in_category": _i(r["n_resolved"], 0), "status": r["status"]}
            for r in conn.execute(q, params).fetchall()]
