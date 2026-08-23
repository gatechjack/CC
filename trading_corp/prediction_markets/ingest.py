"""Ingestion for Prediction Markets (P1): /closed-positions -> pm_closed_position.

Pure helpers (invariant, event-group quarantine, row mapping) + injectable async
orchestration (HTTP client, DB connection, clock, tier-2 fetch all injected) so tests
run offline with no network and no real DB.

The §3A quarantine is applied HERE at ingest, in three stages (clause (b) ONLY — see §13A(f)):
  1. row-level clause (b) [zero-cost/nonzero-realized] -> pnl_suspect / suspect_reason='row_invariant'
  2. event-group propagate -> a suspect row taints its whole (wallet, event_slug) group;
     clean siblings get suspect_reason='event_group' (closes the winner-survives gap).
  3. no-cost-basis quarantine (Ruling A, §13A(h)) -> a row with cost_basis<=0 (avg_price<=0/NULL, no
     knowable cost) -> pnl_suspect / suspect_reason='no_cost_basis'. Row-level, NO propagation; applied
     AFTER stage 2 so it never triggers group propagation.
Clause (a) [loss-exceeds-cost] is DEMOTED to a non-excluding, non-propagated anomaly flag
(pnl_anomaly / anomaly_reason='loss_exceeds_cost') 2026-08-22: total_bought understates cost on
scale-in rows, so it false-flags real losses; recorded for investigation, never excluded from stats.

Spec: reports/prediction_markets/P1_PLAN.md §3A, §6, §8.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Iterable

from .category import (
    CATEGORY_UNKNOWN,
    SOURCE_UNKNOWN,
    derive_categories_batch,
    derive_category_from_slug,
)

WON_THRESHOLD = 0.9          # cur_price >= 0.9 -> holder's side won (mirrors legacy _decode_resolution)
EPS_FLOOR = 1.00             # §3A epsilon: max($1, 1% of cost) absorbs API rounding at every scale
EPS_PCT = 0.01


def compute_row_suspect(total_bought: float | None, realized_pnl: float | None) -> tuple[int, str | None]:
    """§3A QUARANTINE trigger = clause (b) ONLY. Returns (pnl_suspect 0/1, suspect_reason).
      (b) total_bought <= 0 AND realized_pnl != 0   -> zero-cost attribution (EITHER sign; negRisk phantom)

    Clause (a) [loss-exceeds-cost] was DEMOTED from quarantine to a non-excluding anomaly flag
    2026-08-22 (§13A(f), `QUARANTINE_RECONCILE_2026-08-22.md`): live data showed it false-positives on
    real single-game losses because `/closed-positions total_bought` understates cost on scale-in rows,
    so quarantining it dropped real losses and biased the scoreboard UP. Clause (a) now lives in
    `compute_row_anomaly` (recorded via `pnl_anomaly`, NOT excluded, NOT event-group propagated).
    Clause (b) is empirically sound (fires only on genuine negRisk; 0 on binary MLB / pako).
    """
    tb = total_bought or 0.0
    rp = realized_pnl or 0.0
    if tb <= 0 and rp != 0:
        return 1, "row_invariant"
    return 0, None


def compute_row_anomaly(total_bought: float | None, realized_pnl: float | None,
                        *, eps_floor: float = EPS_FLOOR, eps_pct: float = EPS_PCT) -> tuple[int, str | None]:
    """§3A clause (a) as a RECORDED anomaly, NOT a quarantine (demoted 2026-08-22, §13A(f)).
    `realized_pnl < -(total_bought + EPS)` with a POSITIVE cost basis -> a realized loss exceeding cost.
    This is a genuine anomaly worth surfacing (`pnl_anomaly`), but it is NOT excluded from stats and NOT
    event-group propagated, because `total_bought` understates true cost on scale-in rows (so it flags real
    losses). Returns (pnl_anomaly 0/1, anomaly_reason). Zero-cost rows are clause (b)'s domain, not here.
    """
    tb = total_bought or 0.0
    rp = realized_pnl or 0.0
    if tb > 0:
        eps = max(eps_floor, eps_pct * tb)
        if rp < -(tb + eps):
            return 1, "loss_exceeds_cost"
    return 0, None


def apply_event_group_quarantine(records: list[dict]) -> None:
    """§3A EVENT-GROUP propagation (mutates records in place). If ANY row in a
    (wallet, event_slug) group is row-suspect, mark EVERY row in that group suspect;
    clean siblings become suspect_reason='event_group'. Rows with empty/NULL event_slug
    cannot be grouped -> judged row-level only (no propagation to/from them)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        es = (r.get("event_slug") or "").strip()
        if es:
            groups[es].append(r)
    for grp in groups.values():
        if any(r["pnl_suspect"] for r in grp):
            for r in grp:
                if not r["pnl_suspect"]:
                    r["pnl_suspect"] = 1
                    r["suspect_reason"] = "event_group"


def apply_no_cost_basis_quarantine(records: list[dict]) -> None:
    """Ruling A (Jack, 2026-08-22): a scoreable row with `cost_basis <= 0` (avg_price<=0/NULL, so no
    KNOWABLE USDC cost) cannot yield a meaningful return-on-cost -> quarantine it (`pnl_suspect=1`,
    `suspect_reason='no_cost_basis'`). Row-level ONLY: a missing cost basis is a per-row data artifact,
    not a negRisk-event phenomenon, so it does NOT propagate to event siblings (they may have valid cost).
    Applied AFTER `apply_event_group_quarantine`, and only on rows not already suspect, so it never
    triggers propagation and never overwrites a 'row_invariant'/'event_group' reason."""
    for r in records:
        if not r["pnl_suspect"] and (r.get("cost_basis") or 0.0) <= 0:
            r["pnl_suspect"] = 1
            r["suspect_reason"] = "no_cost_basis"


def _f(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def cp_to_record(cp: Any, category: str, category_source: str, now_ts: int) -> dict:
    """Map a ClosedPositionRow (or attr-compatible object) to a pm_closed_position record
    dict, computing won / shares_derived / row-level pnl_suspect. Event-group propagation
    runs afterward over the full batch."""
    tb = _f(getattr(cp, "total_bought", 0.0))
    avg = _f(getattr(cp, "avg_price", 0.0))
    rp = _f(getattr(cp, "realized_pnl", 0.0))
    cur = _f(getattr(cp, "cur_price", 0.0))
    suspect, reason = compute_row_suspect(tb, rp)     # clause (b) -> quarantine (+ event-group propagation)
    anomaly, areason = compute_row_anomaly(tb, rp)    # clause (a) -> RECORD only, no exclusion/propagation
    return {
        "wallet": str(getattr(cp, "proxy_wallet", "") or "").lower(),
        "condition_id": str(getattr(cp, "condition_id", "") or ""),
        "slug": getattr(cp, "slug", "") or "",
        "event_slug": getattr(cp, "event_slug", "") or "",
        "title": getattr(cp, "title", "") or "",
        "category": category,
        "category_source": category_source,
        "outcome": getattr(cp, "outcome", "") or "",
        "outcome_index": int(getattr(cp, "outcome_index", 0) or 0),
        "avg_price": avg,
        "total_bought": tb,                            # NOTIONAL (shares/payout@$1), NOT cost (§13A(g))
        "cost_basis": tb * avg,                         # real USDC cost = shares * avg_price (ROI denominator, §13 dec 11)
        "realized_pnl": rp,
        "cur_price": cur,
        "won": 1 if cur >= WON_THRESHOLD else 0,
        "pnl_suspect": suspect,
        "suspect_reason": reason,
        "pnl_anomaly": anomaly,
        "anomaly_reason": areason,
        "shares_derived": (tb / avg) if avg > 0 else None,
        "end_date": getattr(cp, "end_date", "") or "",
        "resolved_ts": int(getattr(cp, "timestamp", 0) or 0),
        "ingested_ts": now_ts,
        "updated_ts": now_ts,
    }


_CP_COLS = [
    "wallet", "condition_id", "slug", "event_slug", "title", "category", "category_source",
    "outcome", "outcome_index", "avg_price", "total_bought", "cost_basis", "realized_pnl", "cur_price",
    "won", "pnl_suspect", "suspect_reason", "pnl_anomaly", "anomaly_reason", "shares_derived",
    "end_date", "resolved_ts", "ingested_ts", "updated_ts",
]


class IntegrityError(RuntimeError):
    """Raised when pulled rows would COLLAPSE under the storage PK — the silent-data-loss guard
    (§13A(i)). INSERT OR REPLACE fails silently, which is why the (wallet, condition_id) collapse of
    489 two-sided Kickstand7 rows was invisible for a full backfill. This makes it LOUD."""


def _pk_of(r: dict) -> tuple:
    return (r.get("wallet"), r.get("condition_id"), int(r.get("outcome_index") or 0))


def _assert_no_pk_collision(wallet: str, records: list[dict], *, pk=_pk_of) -> None:
    """§13A(i) integrity guard: every pulled row must key UNIQUELY on the storage PK. An in-batch
    collision means INSERT OR REPLACE would SILENTLY drop real rows. HARD-FAIL the wallet (per-wallet
    isolation in backfill_wallets surfaces it loudly in the CLI summary) rather than store wrong stats —
    bias toward NO entry over a WRONG one (§13 dec 10). Justification for hard error over warning:
    silent collapse is the exact defect this exists to prevent; a collision means the stored net/roi/win
    for that wallet would be wrong, so the wallet must not enter the scoreboard at all."""
    counts: dict[tuple, int] = {}
    for r in records:
        k = pk(r)
        counts[k] = counts.get(k, 0) + 1
    collisions = {k: n for k, n in counts.items() if n > 1}
    if collisions:
        sample = [{"key": list(k), "rows": n} for k, n in list(collisions.items())[:5]]
        raise IntegrityError(
            "PK COLLISION for wallet %s: %d pulled rows -> %d distinct PKs; %d key(s) would be "
            "SILENTLY collapsed by INSERT OR REPLACE. sample=%s"
            % (wallet, len(records), len(counts), len(collisions), sample))


def upsert_closed_positions(conn, records: Iterable[dict]) -> int:
    """INSERT OR REPLACE on PK (wallet, condition_id). Idempotent."""
    recs = list(records)
    if not recs:
        return 0
    placeholders = ", ".join(["?"] * len(_CP_COLS))
    sql = "INSERT OR REPLACE INTO pm_closed_position (%s) VALUES (%s)" % (", ".join(_CP_COLS), placeholders)
    conn.executemany(sql, [tuple(r.get(c) for c in _CP_COLS) for r in recs])
    return len(recs)


def _stamp_whale(conn, wallet: str, now_ts: int, *, backfill: bool,
                 complete: bool, pulled: int, stored: int) -> None:
    cflag = 1 if complete else 0
    row = conn.execute("SELECT wallet FROM pm_whale WHERE wallet = ?", (wallet,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO pm_whale (wallet, first_seen_ts, last_backfill_ts, last_refresh_ts, "
            "backfill_complete, last_pulled, last_stored) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (wallet, now_ts, now_ts if backfill else None, now_ts, cflag, pulled, stored),
        )
    else:
        col = "last_backfill_ts" if backfill else "last_refresh_ts"
        conn.execute(
            "UPDATE pm_whale SET %s = ?, last_refresh_ts = ?, backfill_complete = ?, "
            "last_pulled = ?, last_stored = ? WHERE wallet = ?" % col,
            (now_ts, now_ts, cflag, pulled, stored, wallet))


async def _pull_closed(client, wallet: str, *, limit: int, cap: int,
                       retries: int = 4, base_backoff: float = 2.0, sleep=None) -> tuple[list, bool]:
    """Paginate /closed-positions with 429/error BACKOFF+RETRY. Returns (rows, complete).
    `complete` is True IFF pagination ended on a genuinely empty/short page -- NOT on hitting `cap`
    and NOT via an exhausted-retry error. On repeated failure for a page the exception PROPAGATES, so
    the wallet is isolated as FAILED by backfill_wallets (never silently PARTIAL). A cap-hit returns
    complete=False (PARTIAL: more history exists). Step-4 429 safety, §13A(k)."""
    _sleep = sleep or asyncio.sleep
    rows: list = []
    complete = False
    for off in range(0, cap, limit):
        page = None
        for attempt in range(retries + 1):
            try:
                page = await client.fetch_closed_positions(wallet, limit=limit, offset=off)
                break
            except Exception:
                if attempt >= retries:
                    raise                                   # exhausted -> wallet FAILS (isolated), not PARTIAL
                await _sleep(base_backoff * (2 ** attempt))  # 2s,4s,8s,16s backoff on 429/error
        if not page:
            complete = True
            break
        rows.extend(page)
        if len(page) < limit:
            complete = True
            break
    return rows, complete                                    # loop exhausting `cap` w/o a short page -> complete=False (PARTIAL)


async def _categorize(records: list[dict], *, fetch_events) -> None:
    """Tier-2 fill for records tier-1 left 'unknown' (by event_slug). Mutates in place."""
    unknown_slugs = sorted({r["event_slug"] for r in records
                            if r["category"] == CATEGORY_UNKNOWN and r["event_slug"]})
    if not unknown_slugs:
        return
    tier2 = await derive_categories_batch(unknown_slugs, fetch_events=fetch_events)
    for r in records:
        if r["category"] == CATEGORY_UNKNOWN:
            c2, src2 = tier2.get(r["event_slug"], (CATEGORY_UNKNOWN, SOURCE_UNKNOWN))
            if c2 != CATEGORY_UNKNOWN:
                r["category"], r["category_source"] = c2, src2


async def backfill_wallet(conn, wallet: str, *, client, now_ts: int, fetch_events=None,
                          limit: int = 50, cap: int = 8000, backfill: bool = True, sleep=None) -> dict:
    """Backfill/refresh one wallet: pull /closed-positions (429 backoff+retry) -> tier-1 categorize ->
    tier-2 for unknowns -> row invariant -> event-group quarantine -> no-cost-basis quarantine ->
    PK-collision guard -> upsert -> stamp pm_whale with the COMPLETENESS verdict. Idempotent.
    verdict='complete' IFF pagination ran to a short/empty page AND pulled==stored; else 'partial'
    (Step-4 429 safety, §13A(k)) -> backfill_complete flag drives ranking exclusion downstream."""
    wallet = wallet.lower()
    cps, complete = await _pull_closed(client, wallet, limit=limit, cap=cap, sleep=sleep)
    records = []
    for cp in cps:
        cat, src = derive_category_from_slug(getattr(cp, "event_slug", ""), getattr(cp, "slug", ""))
        records.append(cp_to_record(cp, cat, src, now_ts))
    await _categorize(records, fetch_events=fetch_events)
    apply_event_group_quarantine(records)
    apply_no_cost_basis_quarantine(records)   # Ruling A: exclude cost_basis<=0 rows (row-level, no propagation)
    _assert_no_pk_collision(wallet, records)  # §13A(i): LOUD hard-fail if any pulled row would collapse
    n = upsert_closed_positions(conn, records)
    db_rows = conn.execute("SELECT COUNT(1) FROM pm_closed_position WHERE wallet = ?", (wallet,)).fetchone()[0]
    verdict = "complete" if (complete and len(records) == db_rows) else "partial"
    _stamp_whale(conn, wallet, now_ts, backfill=backfill, complete=(verdict == "complete"),
                 pulled=len(records), stored=db_rows)
    conn.commit() if hasattr(conn, "commit") else None
    n_suspect = sum(r["pnl_suspect"] for r in records)
    n_anomaly = sum(r["pnl_anomaly"] for r in records)
    # pulled == stored is the integrity headline; verdict gates whether this wallet may be RANKED.
    return {"wallet": wallet, "rows": n, "pulled": len(records), "stored": db_rows, "verdict": verdict,
            "suspect": n_suspect, "anomaly": n_anomaly}


async def refresh_wallet(conn, wallet: str, *, client, now_ts: int, fetch_events=None,
                         limit: int = 50, cap: int = 8000, sleep=None) -> dict:
    """v1 refresh == full idempotent re-pull (upserts make ordering irrelevant)."""
    return await backfill_wallet(conn, wallet, client=client, now_ts=now_ts,
                                 fetch_events=fetch_events, limit=limit, cap=cap, backfill=False, sleep=sleep)


_OP_COLS = ["wallet", "condition_id", "slug", "event_slug", "title", "category", "outcome", "outcome_index",
            "size", "avg_price", "initial_value", "current_value", "cash_pnl", "refreshed_ts"]


async def refresh_open_positions(conn, wallet: str, *, client, now_ts: int) -> int:
    """/positions -> pm_open_position (delete-then-insert per wallet; the open set shrinks)."""
    wallet = wallet.lower()
    positions = await client.fetch_positions(wallet)
    conn.execute("DELETE FROM pm_open_position WHERE wallet = ?", (wallet,))
    recs = []
    for p in positions:
        cat, _ = derive_category_from_slug(getattr(p, "event_slug", ""), getattr(p, "slug", ""))
        recs.append({
            "wallet": wallet,
            "condition_id": str(getattr(p, "condition_id", "") or ""),
            "slug": getattr(p, "slug", "") or "",
            "event_slug": getattr(p, "event_slug", "") or "",
            "title": getattr(p, "title", "") or "",
            "category": cat,
            "outcome": getattr(p, "outcome", "") or "",
            "outcome_index": int(getattr(p, "outcome_index", 0) or 0),   # part of the PK (two-sided holdings, migration 002)
            "size": _f(getattr(p, "size", 0.0)),
            "avg_price": _f(getattr(p, "avg_price", 0.0)),
            "initial_value": _f(getattr(p, "initial_value", 0.0)),
            "current_value": _f(getattr(p, "current_value", 0.0)),
            "cash_pnl": _f(getattr(p, "pnl", 0.0)),
            "refreshed_ts": now_ts,
        })
    _assert_no_pk_collision(wallet, recs)   # §13A(i): open positions can also hold both sides -> same guard
    if recs:
        ph = ", ".join(["?"] * len(_OP_COLS))
        conn.executemany(
            "INSERT OR REPLACE INTO pm_open_position (%s) VALUES (%s)" % (", ".join(_OP_COLS), ph),
            [tuple(r.get(c) for c in _OP_COLS) for r in recs],
        )
    conn.commit() if hasattr(conn, "commit") else None
    return len(recs)


async def g0_validate(client, losers: Iterable[Any], *, limit: int = 50, cap: int = 8000) -> dict:
    """G0 gate as a callable: pull /closed-positions for known net-losers and assert NEGATIVE
    realized_pnl rows exist (disproves the positives-only survivorship claim). READ-ONLY (no DB
    writes). Returns {passed, per_wallet:[{wallet, user_name, n, negative, positive, net, passed}]}.
    """
    per = []
    overall = True
    for entry in losers:
        w = str((entry.get("wallet") if isinstance(entry, dict) else entry) or "").lower()
        cps, _complete = await _pull_closed(client, w, limit=limit, cap=cap)
        neg = sum(1 for r in cps if _f(getattr(r, "realized_pnl", 0.0)) < 0)
        pos = sum(1 for r in cps if _f(getattr(r, "realized_pnl", 0.0)) > 0)
        net = sum(_f(getattr(r, "realized_pnl", 0.0)) for r in cps)
        ok = neg > 0
        overall = overall and ok
        per.append({"wallet": w, "user_name": (entry.get("user_name") if isinstance(entry, dict) else ""),
                    "n": len(cps), "negative": neg, "positive": pos, "net": net, "passed": ok})
    return {"passed": overall, "per_wallet": per}


async def backfill_wallets(conn, wallets: Iterable[str], *, client, now_ts: int, fetch_events=None,
                           limit: int = 50, cap: int = 8000, backfill: bool = True, sleep=None) -> dict:
    """Batch driver with PER-WALLET isolation: one wallet raising never aborts the batch. Each wallet's
    pull uses 429 backoff+retry; a wallet that exhausts retries lands in `failed` (never silently PARTIAL),
    and a wallet that completes-but-truncated (cap) lands in `ok` with verdict='partial'. `complete`/`partial`
    counts summarize the batch so a throttled wallet is impossible to miss (§13A(k))."""
    summary = {"ok": [], "failed": [], "complete": 0, "partial": 0}
    seen = set()
    for w in wallets:
        wl = (w or "").lower()
        if not wl or wl in seen:
            continue
        seen.add(wl)
        try:
            res = await backfill_wallet(conn, wl, client=client, now_ts=now_ts, fetch_events=fetch_events,
                                        limit=limit, cap=cap, backfill=backfill, sleep=sleep)
            summary["ok"].append(res)
            summary["complete" if res.get("verdict") == "complete" else "partial"] += 1
        except Exception as e:  # isolation: log-and-continue
            summary["failed"].append({"wallet": wl, "error": repr(e)[:200]})
    return summary
