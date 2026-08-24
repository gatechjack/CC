"""User-name population for Prediction Markets whales (CP2 Phase 3).

WHY THIS EXISTS (false-premise fix, 2026-08-24): the P3 handoff assumed
`pm_whale.user_name` "already exists" as a pure display join. It does NOT --
`ingest._stamp_whale` never writes `user_name`, and `/closed-positions` (the
backfill source) carries no name field, so the column is NULL for every whale.
The display names Jack recognizes (Kickstand7, BetMechanic, SDTrading, ...)
live in the ROSTER (legacy `agent_state` + seed yaml, via
`rosters.load_seed_roster`). This module copies those labels into
`pm_whale.user_name` so the scoreboard/drill pages can show a recognizable name
beside the wallet.

DISCIPLINE (Board rulings, Option A):
- A POPULATION step, NOT a join. Populated names go STALE if a whale renames on
  Polymarket -- the stored label is silently wrong until this is re-run. That is
  why the sync is RE-RUNNABLE + IDEMPOTENT and records WHEN it last ran
  (`pm_meta` key `user_name_sync`), so a stale name is DIAGNOSABLE, not
  mysterious. Re-running refreshes the labels; `n_changed` surfaces renames.
- WALLET IS THE IDENTITY. Names are labels for recognition only -- this keys on
  WALLET; a display-name collision NEVER merges two whales.
- Does NOT edit `ingest.py` (off-limits) and NEVER writes the legacy DB (it only
  reads it read-only through `rosters` for the seed labels).
- NO new migration: `pm_meta` is an ops/provenance table created idempotently
  OUTSIDE the numbered-migration chain, so `schema_version` stays 4 (migration
  005 stays reserved for `pm_paper_trade.size_basis`, e7).

Spec: reports/prediction_markets/P3_KICKOFF_2026-08-24.md.
"""
from __future__ import annotations

import json
import sqlite3
import time

# Ops/provenance KV -- intentionally OUTSIDE db.MIGRATIONS so schema_version stays 4 (Phase 3 is a READ
# feature; the only writer is this name-sync). Created idempotently by sync_user_names(); reads tolerate
# its absence so the web GET path never creates it.
_PM_META_DDL = "CREATE TABLE IF NOT EXISTS pm_meta (key TEXT PRIMARY KEY, value TEXT, updated_ts INTEGER)"
_NAME_SYNC_KEY = "user_name_sync"


def sync_user_names(conn, roster, *, now_ts: int | None = None) -> dict:
    """Populate `pm_whale.user_name` from the roster labels. Idempotent + re-runnable.

    `roster`: iterable of `{wallet, user_name, ...}` (the `rosters.load_seed_roster` shape).
    UPDATES existing `pm_whale` rows ONLY -- a whale with no backfill has no row and no page
    presence, so names are pure annotation of tracked whales (backfill owns row creation).
    Keyed on WALLET; a shared `user_name` across two wallets stays two distinct whales. Only a
    non-empty roster label is written, and an empty label never clobbers an existing name.
    Records the run in `pm_meta('user_name_sync')`. Returns a counts dict.
    """
    now = now_ts if now_ts is not None else int(time.time())
    # roster label per wallet (lowercased; first non-empty label wins so a later empty/dup entry
    # never overwrites a real one)
    labels: dict[str, str] = {}
    n_roster = 0
    for e in roster or []:
        n_roster += 1
        w = str((e.get("wallet") if isinstance(e, dict) else "") or "").lower()
        nm = str((e.get("user_name") if isinstance(e, dict) else "") or "").strip()
        if w and nm and w not in labels:
            labels[w] = nm
    # existing pm_whale wallets + current names -- UPDATE existing only (never INSERT a nameless whale)
    existing: dict[str, str | None] = {
        r["wallet"]: r["user_name"]
        for r in conn.execute("SELECT wallet, user_name FROM pm_whale").fetchall()
    }
    matched = n_set = n_changed = n_unchanged = 0
    for w, nm in labels.items():
        if w not in existing:
            continue                       # not backfilled -> no row to annotate
        matched += 1
        cur = existing[w]
        if cur == nm:
            n_unchanged += 1
            continue
        conn.execute("UPDATE pm_whale SET user_name = ? WHERE wallet = ?", (nm, w))
        n_set += 1
        if cur:                            # a real prior name changed -> a RENAME tell (staleness signal)
            n_changed += 1
    counts = {
        "last_run_ts": now,
        "n_roster": n_roster,                    # roster entries seen
        "n_roster_named": len(labels),           # distinct wallets with a non-empty roster label
        "n_whales": len(existing),               # pm_whale rows present
        "n_matched": matched,                    # roster-named wallets that ARE tracked whales
        "n_set": n_set,                          # rows updated this run (first-set + renamed)
        "n_changed": n_changed,                  # rows whose PRIOR non-empty name changed (rename tell)
        "n_unchanged": n_unchanged,              # already had the current label (idempotent no-op)
        # tracked whales with no name available anywhere after this run -> page shows the WALLET
        "n_whales_unnamed_after": sum(1 for w, cur in existing.items() if not (labels.get(w) or cur)),
        "source": "roster",
    }
    conn.execute(_PM_META_DDL)
    conn.execute("INSERT OR REPLACE INTO pm_meta (key, value, updated_ts) VALUES (?, ?, ?)",
                 (_NAME_SYNC_KEY, json.dumps(counts), now))
    if hasattr(conn, "commit"):
        conn.commit()
    return counts


def last_sync(conn) -> dict | None:
    """The recorded name-sync run (counts + `last_run_ts`), or None if never run. Read helper for the
    CLI `--status` and the whale-detail 'names as of' stamp so a stale name is DIAGNOSABLE. Tolerates a
    missing `pm_meta` (names never synced) WITHOUT creating it -- a web GET read path stays pure."""
    try:
        row = conn.execute(
            "SELECT value, updated_ts FROM pm_meta WHERE key = ?", (_NAME_SYNC_KEY,)).fetchone()
    except sqlite3.OperationalError:
        return None                        # pm_meta absent -> never synced; honest None, no write on read
    if row is None:
        return None
    try:
        d = json.loads(row["value"])
    except (TypeError, ValueError):
        d = {}
    d.setdefault("last_run_ts", row["updated_ts"])
    return d
