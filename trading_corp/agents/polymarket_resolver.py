"""Polymarket round-trip resolver + equity snapshot writer.

Two periodic background tasks that capture the data needed to render a
betmoar.fun-style Polymarket portfolio dashboard:

  - resolve_pending_round_trips: hourly. Walks `would_have_placed`
    audit rows for the polymarket_arbitrage strategy that don't yet
    have a `polymarket_round_trips` row, looks up each market's
    resolution via gamma-api, computes binary-outcome P&L, and INSERTs
    one row. INSERT OR IGNORE keyed on order_id so re-runs are safe.

  - write_equity_snapshot: every 5 min. Calls broker.snapshot() and
    appends one row to `polymarket_equity_history`.

Both are read-only with respect to the trading path — they enrich
historical data, never gate a decision. Failures log + skip; the next
tick retries. Mirrors the pattern in trading_corp.agents.paper_trade_replay.

Phase 3 (live trading) will keep this module unchanged: would_have_placed
rows that became real fills still resolve through gamma-api the same way,
and broker.snapshot() reads real on-chain balances.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from trading_corp.persistence import db as _db

log = logging.getLogger(__name__)


# ── round-trip resolver ────────────────────────────────────────────────


def _fetch_unresolved_orders(db_url: str) -> list[dict]:
    """Return `would_have_placed` audit rows that have no
    polymarket_round_trips entry yet. Each dict is the parsed payload
    plus a `_ts` field carrying the audit-event ts and a `_actor` field
    carrying the producing strategy (so the resolver can stamp the right
    `division` on the resulting round-trip row)."""
    with _db.connect(db_url) as conn:
        # `side` filter: market-settle resolution applies to BUY-side audit
        # rows only. SELL-side rows from copy_trader are handled by
        # `_pair_pending_exits`, which matches them to a prior BUY and
        # computes realized PnL from entry+exit prices. Without this
        # filter, `_compute_round_trip_row` would mistreat a SELL as a
        # fresh bet and emit wrong PnL when the market eventually settled.
        # Also exclude audit rows whose order_id was already linked as an
        # entry to a paired round-trip (entry_order_id), so the entry
        # doesn't keep being scanned after pairing resolves it.
        cur = conn.execute(
            "SELECT a.ts AS ts, a.actor AS actor, a.payload_json "
            "FROM audit_event a "
            "LEFT JOIN polymarket_round_trips r "
            "  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            "WHERE a.actor IN ('polymarket_arbitrage', 'polymarket_copy_trader') "
            "  AND a.kind  = 'would_have_placed' "
            "  AND COALESCE(json_extract(a.payload_json, '$.side'), 'buy') = 'buy' "
            "  AND r.order_id IS NULL "
            "  AND json_extract(a.payload_json, '$.order_id') NOT IN ("
            "        SELECT entry_order_id FROM polymarket_round_trips "
            "        WHERE entry_order_id IS NOT NULL"
            "      ) "
            "ORDER BY a.ts ASC"
        )
        rows: list[dict] = []
        for r in cur.fetchall():
            try:
                p = json.loads(r["payload_json"])
                p["_ts"] = r["ts"]
                p["_actor"] = r["actor"]
                rows.append(p)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
        return rows


def _compute_round_trip_row(row: dict, res: dict) -> dict | None:
    """P&L for both binary YES/NO and multi-leg outcomes.

    Multi-leg path (2026-05-14): when the audit row's outcome string is
    something other than yes/no (e.g., "Cincinnati Reds", "Up", "Real
    Oviedo"), use the row's `outcome_index` against `outcome_prices` to
    determine the win. Polymarket multi-outcome markets resolve with
    exactly one entry == "1" and the rest "0"; the broker's
    `get_market_resolution` now passes through resolved status for
    these (was previously voided as "fractional").

    Returns the INSERT dict, or None if not yet resolved / malformed.
    """
    if res.get("status") != "resolved":
        return None
    outcome = (row.get("outcome") or "yes").strip()
    outcome_lower = outcome.lower()
    qty = float(row.get("qty") or 0.0)
    price = float(row.get("limit_price") or 0.0)
    if qty <= 0 or price <= 0 or price >= 1.0:
        return None

    # Binary YES/NO path (legacy + arb strategies).
    if outcome_lower in ("yes", "no"):
        yes_won = bool(res.get("yes_won"))
        won = yes_won if outcome_lower == "yes" else (not yes_won)
    else:
        # Multi-leg path — use outcome_index against outcome_prices.
        outcome_index_raw = row.get("outcome_index")
        outcome_prices = res.get("outcome_prices") or []
        if outcome_index_raw is None or not outcome_prices:
            return None
        try:
            idx = int(outcome_index_raw)
        except (TypeError, ValueError):
            return None
        if idx < 0 or idx >= len(outcome_prices):
            return None
        try:
            won = float(outcome_prices[idx]) == 1.0
        except (TypeError, ValueError):
            return None
        # `yes_won` only meaningful for binary; leave 0 for multi-leg
        # (consumers that branch on this should also branch on outcome).
        yes_won = False
    notional = qty * price
    pnl = qty * (1.0 - price) if won else -qty * price
    roi = (100.0 * pnl / notional) if notional > 0 else 0.0
    # `division` distinguishes arbitrage vs copy-trader rows in the same
    # table. Prefer the explicit payload field (the copy-trader includes
    # it in its audit base_payload); otherwise infer from the producing
    # actor name (legacy polymarket_arbitrage rows didn't carry it).
    division = row.get("division")
    if not division:
        actor = (row.get("_actor") or "").strip()
        if actor == "polymarket_copy_trader":
            division = "polymarket_copy_trading"
        else:
            division = "polymarket_arbitrage"
    return {
        "order_id": row.get("order_id") or "",
        "condition_id": row.get("condition_id") or "",
        "slug": row.get("market_slug") or row.get("slug"),
        "market_question": row.get("market_question") or row.get("market_title"),
        "category": row.get("category") or "other",
        "series": row.get("series") or "",
        "division": division,
        "outcome_bet": outcome,
        "qty": qty,
        "entry_price": price,
        "notional": notional,
        "entry_ts": row["_ts"],
        "resolved_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "yes_won": 1 if yes_won else 0,
        "won": 1 if won else 0,
        "realized_pnl": pnl,
        "roi_pct": roi,
        "implied_at_entry": row.get("implied_prob_at_entry"),
        "llm_prob": row.get("llm_prob_estimate"),
        "divergence_pct": row.get("divergence_pct"),
        "extra_json": json.dumps(
            {
                "rationale": row.get("rationale"),
                "risk_verdict": row.get("risk_verdict"),
                "llm_confidence": row.get("llm_confidence"),
                # PCT attribution — preserved here so the Whales dashboard
                # tab can attribute multi-leg-resolved RTs to the whale
                # that originated the bet. Pre-2026-05-14 fix these
                # weren't included; post-fix audit rows are correctly
                # attributed. Existing rows backfilled separately.
                "whale_user_name": row.get("whale_user_name"),
                "whale_wallet": row.get("whale_wallet"),
                "outcome_index": row.get("outcome_index"),
            },
            default=str,
        ),
    }


def _insert_round_trip(db_url: str, record: dict) -> bool:
    cols = list(record.keys())
    placeholders = ",".join("?" for _ in cols)
    sql = (
        f"INSERT OR IGNORE INTO polymarket_round_trips "
        f"({','.join(cols)}) VALUES ({placeholders})"
    )
    with _db.connect(db_url) as conn:
        cur = conn.execute(sql, [record[c] for c in cols])
        return (cur.rowcount or 0) > 0


# ── whale-exit pairing (copy-trader rows) ──────────────────────────────


def _pair_pending_exits(db_url: str) -> dict:
    """Match unpaired SELL audit rows from polymarket_copy_trader with
    their prior BUY (same wallet, condition_id, outcome_index) and emit
    one round-trip row per pair, keyed by the exit's order_id with the
    entry's order_id linked via `entry_order_id`.

    Counts: scanned (sells found), paired (round-trips written),
    skipped_no_entry (sell had no matching unpaired buy), skipped_bad_data
    (missing prices/qty).

    Idempotent: a SELL already linked as a round-trip's `order_id` won't
    be re-fetched; same for entries (excluded by entry_order_id).
    """
    counts = {"scanned": 0, "paired": 0, "skipped_no_entry": 0, "skipped_bad_data": 0}
    with _db.connect(db_url) as conn:
        # Unpaired SELLs from copy_trader (chronological — pair oldest first
        # so multi-buy/multi-sell sequences pair correctly).
        sells = conn.execute(
            "SELECT a.ts AS ts, a.payload_json "
            "FROM audit_event a "
            "LEFT JOIN polymarket_round_trips r "
            "  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            "WHERE a.actor = 'polymarket_copy_trader' "
            "  AND a.kind = 'would_have_placed' "
            "  AND json_extract(a.payload_json, '$.side') = 'sell' "
            "  AND r.order_id IS NULL "
            "ORDER BY a.ts ASC"
        ).fetchall()
        counts["scanned"] = len(sells)
        for sell in sells:
            try:
                ep = json.loads(sell["payload_json"])
            except (json.JSONDecodeError, TypeError, ValueError):
                counts["skipped_bad_data"] += 1
                continue
            wallet = ep.get("whale_wallet")
            cid = ep.get("condition_id")
            outcome_index = ep.get("outcome_index")
            exit_price = float(
                ep.get("limit_price") or ep.get("whale_exit_price") or 0.0
            )
            if not wallet or not cid or outcome_index is None or exit_price <= 0:
                counts["skipped_bad_data"] += 1
                continue
            # Most recent unpaired BUY from same whale+market+outcome,
            # strictly before this sell's ts.
            entry_cur = conn.execute(
                "SELECT a.ts AS ts, a.payload_json "
                "FROM audit_event a "
                "LEFT JOIN polymarket_round_trips r "
                "  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
                "WHERE a.actor = 'polymarket_copy_trader' "
                "  AND a.kind = 'would_have_placed' "
                "  AND json_extract(a.payload_json, '$.side') = 'buy' "
                "  AND json_extract(a.payload_json, '$.whale_wallet') = ? "
                "  AND json_extract(a.payload_json, '$.condition_id') = ? "
                "  AND json_extract(a.payload_json, '$.outcome_index') = ? "
                "  AND a.ts < ? "
                "  AND r.order_id IS NULL "
                "  AND json_extract(a.payload_json, '$.order_id') NOT IN ("
                "    SELECT entry_order_id FROM polymarket_round_trips "
                "    WHERE entry_order_id IS NOT NULL"
                "  ) "
                "ORDER BY a.ts DESC LIMIT 1",
                (wallet, cid, outcome_index, sell["ts"]),
            )
            entry_row = entry_cur.fetchone()
            if not entry_row:
                counts["skipped_no_entry"] += 1
                continue
            try:
                bp = json.loads(entry_row["payload_json"])
            except (json.JSONDecodeError, TypeError, ValueError):
                counts["skipped_bad_data"] += 1
                continue
            entry_price = float(
                bp.get("limit_price") or bp.get("whale_entry_price") or 0.0
            )
            qty = float(bp.get("qty") or 0.0)
            if entry_price <= 0 or qty <= 0:
                counts["skipped_bad_data"] += 1
                continue
            # Realized PnL: bought qty contracts at entry_price, sold at exit_price.
            # Polymarket binary contracts settle in [0,1] so this is the
            # per-contract dollar delta × qty.
            pnl = qty * (exit_price - entry_price)
            notional = qty * entry_price
            roi = (100.0 * pnl / notional) if notional > 0 else 0.0
            record = {
                "order_id": ep.get("order_id") or "",
                "entry_order_id": bp.get("order_id") or "",
                "condition_id": cid,
                "slug": ep.get("market_slug") or bp.get("market_slug") or "",
                "market_question": (
                    ep.get("market_title") or bp.get("market_title") or ""
                ),
                "category": ep.get("category") or bp.get("category"),
                "division": ep.get("division") or "polymarket_copy_trading",
                "outcome_bet": (bp.get("outcome") or "").lower(),
                "qty": qty,
                "entry_price": entry_price,
                "notional": notional,
                "entry_ts": str(entry_row["ts"]),
                "resolved_ts": str(sell["ts"]),
                # `yes_won` doesn't apply to whale-closed trades; store 0
                # for schema compatibility. The dashboard's "result"
                # column reads `market_result` instead for these rows.
                "yes_won": 0,
                "won": 1 if pnl > 0 else 0,
                "realized_pnl": pnl,
                "roi_pct": roi,
                "extra_json": json.dumps(
                    {
                        "market_result": "whale_closed",
                        "exit_price": exit_price,
                        "whale_user_name": ep.get("whale_user_name"),
                        "whale_wallet": wallet,
                        "rationale_exit": ep.get("rationale"),
                        "rationale_entry": bp.get("rationale"),
                    },
                    default=str,
                ),
            }
            if _insert_round_trip(db_url, record):
                counts["paired"] += 1
    return counts


async def resolve_pending_round_trips(
    db_url: str,
    broker,
    *,
    max_per_tick: int = 100,
) -> dict:
    """One pass. Returns counts: scanned, resolved, pending, void,
    not_found, errors, plus whale-exit pairing counts.

    `max_per_tick` caps gamma-api calls per tick so a long-running
    backlog can't melt the rate-limit budget on a single sweep. With $1
    fixed sizing × 6h cooldown × ~60 markets/day, the unresolved
    backlog should stay well under 100.

    Two passes per tick:
      1. `_pair_pending_exits`: pair copy-trader SELL audit rows with
         their prior BUY, emit one round-trip per pair. Pure SQL — no
         broker API calls. Runs first because pairing resolves rows that
         the market-settle pass would otherwise repeatedly poll.
      2. Market-settle path: for any remaining unresolved BUY rows, look
         up the market on gamma-api and write a round-trip if settled.
    """
    pair_counts = _pair_pending_exits(db_url)
    rows = _fetch_unresolved_orders(db_url)
    rows = rows[:max_per_tick]
    counts = {
        "scanned": len(rows),
        "resolved": 0,
        "pending": 0,
        "void": 0,
        "not_found": 0,
        "errors": 0,
        "paired": pair_counts["paired"],
        "pair_scanned": pair_counts["scanned"],
        "pair_skipped_no_entry": pair_counts["skipped_no_entry"],
        "pair_skipped_bad_data": pair_counts["skipped_bad_data"],
    }
    if not rows:
        return counts
    for row in rows:
        cid = row.get("condition_id")
        slug = row.get("market_slug")
        if not cid and not slug:
            counts["errors"] += 1
            continue
        try:
            res = await broker.get_market_resolution(condition_id=cid, slug=slug)
        except Exception as e:
            log.warning(
                "polymarket_resolver: lookup failed for %s: %s", cid or slug, e,
            )
            counts["errors"] += 1
            continue
        st = res.get("status", "not_found")
        if st == "resolved":
            rt = _compute_round_trip_row(row, res)
            if rt and _insert_round_trip(db_url, rt):
                counts["resolved"] += 1
        elif st == "pending":
            counts["pending"] += 1
        elif st == "void":
            counts["void"] += 1
        else:
            counts["not_found"] += 1
    return counts


# ── equity snapshot writer ─────────────────────────────────────────────


async def write_equity_snapshot(db_url: str, division: str, broker) -> bool:
    """Single snapshot. Returns True on write, False on broker error.

    `positions_value` is derived as max(0, equity - cash) rather than
    summing per-position market values — robust against position-shape
    drift in the broker (data-api position field names not yet pinned).
    Tightens up later if we want per-position snapshots too (Gap C).
    """
    try:
        snap = await broker.snapshot()
    except Exception as e:
        log.warning("polymarket_resolver: snapshot failed: %s", e)
        return False
    equity = float(getattr(snap, "equity", 0.0) or 0.0)
    cash = float(getattr(snap, "cash", 0.0) or 0.0)
    positions = list(getattr(snap, "positions", []) or [])
    positions_value = max(0.0, equity - cash)
    n_pos = len(positions)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO polymarket_equity_history "
            "(ts, division, equity, cash_usdc, positions_value, n_positions) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, division, equity, cash, positions_value, n_pos),
        )
    return True


# ── periodic loops ─────────────────────────────────────────────────────


async def _resolver_loop(db_url: str, broker, interval_sec: int) -> None:
    log.info(
        f"polymarket round-trip resolver online (interval={interval_sec}s)"
    )
    while True:
        try:
            counts = await resolve_pending_round_trips(db_url, broker)
            if counts["scanned"]:
                # f-string (not %s) — RedactingFilter rewrites dict args
                # into their keys, producing wrong output on % formatting.
                log.info(f"polymarket_resolver tick: {counts}")
        except asyncio.CancelledError:
            log.info("polymarket_resolver cancelled.")
            return
        except Exception:
            log.exception("polymarket_resolver tick error (continuing)")
        await asyncio.sleep(interval_sec)


async def _equity_snapshot_loop(
    db_url: str, division: str, broker, interval_sec: int,
) -> None:
    log.info(
        f"polymarket equity snapshot writer online "
        f"(division={division}, interval={interval_sec}s)"
    )
    while True:
        try:
            await write_equity_snapshot(db_url, division, broker)
        except asyncio.CancelledError:
            log.info("polymarket_equity_snapshot cancelled.")
            return
        except Exception:
            log.exception("polymarket_equity_snapshot tick error (continuing)")
        await asyncio.sleep(interval_sec)


def start_resolver_loop(
    db_url: str, broker, *, interval_sec: int = 3600,
) -> asyncio.Task:
    """Spawn the hourly round-trip resolver."""
    return asyncio.create_task(
        _resolver_loop(db_url, broker, interval_sec),
        name="polymarket_resolver_loop",
    )


def start_equity_snapshot_loop(
    db_url: str, division: str, broker, *, interval_sec: int = 300,
) -> asyncio.Task:
    """Spawn the 5-minute equity snapshot writer."""
    return asyncio.create_task(
        _equity_snapshot_loop(db_url, division, broker, interval_sec),
        name="polymarket_equity_snapshot_loop",
    )
