"""Kalshi round-trip resolver + equity snapshot writer (Phase K2.4).

Mirrors `trading_corp.agents.polymarket_resolver`. Two periodic background
tasks per Kalshi division:

  - resolve_pending_round_trips: hourly. Walks `would_have_placed` audit
    rows for any of the six Kalshi strategies in `_KALSHI_ACTORS` that
    don't yet have a `kalshi_round_trips` row, looks up each market via
    `KalshiBroker.get_market_resolution`, and INSERTs one row per resolved
    market. INSERT OR IGNORE keyed on order_id so re-runs are safe.
    Fetches are per-actor budgeted (max_per_actor) so a strategy with a
    large stuck-pending backlog cannot starve newer or lower-volume
    strategies — the original ts-ASC-cap had kalshi_weather_arb +
    kalshi_crypto_arb invisible behind kalshi_llm_arbitrage's backlog.

  - write_equity_snapshot: every 5 min, per division. Calls
    `broker.snapshot()` and appends one row to `kalshi_equity_history`.
    Two divisions share the same Kalshi account so both rows reflect the
    same dollar equity today; per-division logical separation persists for
    the dashboard.

Both are read-only with respect to the trading path. Failures log and skip;
the next tick retries.

Side detection across strategies:
  - kalshi_llm_arbitrage      → payload.outcome ∈ {"yes","no"}
  - kalshi_tail_price_arb     → payload.leg ∈ {"yes","no"}
  - kalshi_temporal_bucket_arb→ payload.leg ∈ {"yes_<ticker>","no_<ticker>"}

`_detect_side` handles all three with a single fallback ladder.

Kalshi P&L (binary contracts settle to $1 winner / $0 loser):
  notional = qty × entry_price       (cash committed at entry)
  if won:   realized = qty × (1.0 - entry_price)
  else:     realized = -qty × entry_price
  void:     realized = 0 (refund — paper representation is break-even)

Fees are NOT modeled in this paper-mode P&L. The Kalshi taker fee formula
(`roundup(0.07 × C × P × (1−C))` per fill) is mechanical and small enough
relative to the $1/leg sizes in Phase K6.1 that we surface gross P&L for
expectancy comparison vs. polymarket; fees come in at Phase K5+ live work.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from trading_corp.persistence import db as _db

log = logging.getLogger(__name__)

_KALSHI_ACTORS = (
    "kalshi_tail_price_arb",
    "kalshi_temporal_bucket_arb",
    "kalshi_llm_arbitrage",
    "kalshi_copy_trader",
    "kalshi_weather_arb",
    "kalshi_crypto_arb",
)
_KALSHI_DIVISIONS = ("kalshi_arbitrage", "kalshi_llm_arbitrage", "kalshi_copy_trading", "kalshi_weather", "kalshi_crypto")
_ACTOR_TO_DIVISION = {
    "kalshi_tail_price_arb": "kalshi_arbitrage",
    "kalshi_temporal_bucket_arb": "kalshi_arbitrage",
    "kalshi_llm_arbitrage": "kalshi_llm_arbitrage",
    "kalshi_copy_trader": "kalshi_copy_trading",
    "kalshi_weather_arb": "kalshi_weather",
    "kalshi_crypto_arb": "kalshi_crypto",
}
_ACTOR_TO_ARB_TYPE_DEFAULT = {
    "kalshi_tail_price_arb": "tail",
    "kalshi_llm_arbitrage": "llm_divergence",
    "kalshi_copy_trader": "copy_trade",
    "kalshi_weather_arb": "weather_forecast",
    "kalshi_crypto_arb": "crypto_spot",
    # kalshi_temporal_bucket_arb keeps the payload's own kalshi_arb_type
    # field which is either 'temporal' or 'bucket'.
}


# ── side detection ─────────────────────────────────────────────────────


def _detect_side(row: dict) -> str | None:
    """Return 'yes', 'no', or None. Handles all three Kalshi strategies."""
    outcome = (row.get("outcome") or "").strip().lower()
    if outcome in ("yes", "no"):
        return outcome
    leg = (row.get("leg") or "").strip().lower()
    if leg.startswith("yes"):
        return "yes"
    if leg.startswith("no"):
        return "no"
    return None


# ── round-trip resolver ────────────────────────────────────────────────


def _fetch_unresolved_orders(
    db_url: str, *, max_per_actor: int = 50,
) -> list[dict]:
    """Return Kalshi `would_have_placed` (paper) and
    `kalshi_copy_placed_live` (live copy placement) audit rows without a
    kalshi_round_trips entry. Each dict is the parsed payload plus
    `_ts` and `_actor` carrying audit-event metadata.

    Filtered to BUY-side rows only — SELL rows from kalshi_copy_trader
    are handled by `_pair_pending_exits` (which matches them to a prior
    BUY and computes realized PnL from entry+exit prices). Also excludes
    rows already linked as the entry leg of a paired round-trip
    (entry_order_id), so the entry doesn't keep getting scanned after
    pairing resolves it.

    Per-actor budget: each actor in `_KALSHI_ACTORS` fetches up to
    `max_per_actor` of its unresolved rows. A single
    `WHERE actor IN (...) ORDER BY ts ASC LIMIT N` query starved newer
    strategies — when kalshi_llm_arbitrage had 1700+ stuck-pending rows,
    the global ts-ASC cap meant kalshi_weather_arb + kalshi_crypto_arb
    rows never made the top-N cut.

    Ordering: `expires_at ASC NULLS LAST` (NULLs synthesized via
    `(expires_at IS NULL)` since SQLite NULLS LAST is version-conditional).
    Past-expiration rows scanned first — they're the ones most likely to
    have a final resolution on Kalshi. The original `ts ASC` ordering
    prioritized OLDEST audit rows, but oldest-audit ≠ most-likely-resolved
    — early LLM bets targeted multi-week-out Politics markets that are
    still pending while later, short-horizon bets already settled. The
    old ordering left 600+ past-expiration kalshi_llm rows permanently
    stuck behind the long-horizon backlog.
    """
    rows: list[dict] = []
    with _db.connect(db_url) as conn:
        for actor in _KALSHI_ACTORS:
            cur = conn.execute(
                "SELECT a.ts AS ts, a.actor AS actor, a.payload_json "
                "FROM audit_event a "
                "LEFT JOIN kalshi_round_trips r "
                "  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
                "WHERE a.actor = ? "
                "  AND a.kind IN ('would_have_placed', 'kalshi_copy_placed_live') "
                "  AND COALESCE(json_extract(a.payload_json, '$.side'), 'buy') = 'buy' "
                "  AND r.order_id IS NULL "
                "  AND json_extract(a.payload_json, '$.order_id') NOT IN ("
                "        SELECT entry_order_id FROM kalshi_round_trips "
                "        WHERE entry_order_id IS NOT NULL"
                "      ) "
                "ORDER BY (json_extract(a.payload_json, '$.expires_at') IS NULL), "
                "         json_extract(a.payload_json, '$.expires_at') ASC, "
                "         a.ts ASC "
                "LIMIT ?",
                (actor, max_per_actor),
            )
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
    """Compose the kalshi_round_trips row dict, or None if unresolvable.

    `res` comes from `KalshiBroker.get_market_resolution`; this is only
    called when res.status ∈ {'resolved','void'}. (Pending and not_found
    are filtered out in the caller.)
    """
    side = _detect_side(row)
    if side not in ("yes", "no"):
        return None
    # Live copy placements (kind='kalshi_copy_placed_live') carry `fill_qty`
    # (actual contracts filled) + `fill_price` (the outcome-leg per-contract
    # cost, correct after the kalshi_live FIX-1 YES->leg inversion), while their
    # top-level `qty` is the USD copy size (wrong units for the contract math).
    # Prefer those when present so the round-trip is dimensionally correct — e.g.
    # a NO 166-contract copy @ 0.013 books loss -$2.16 if YES wins / gain
    # 166×0.987 if NO wins, NOT the bogus 166×0.987 = $163.84. Paper rows keep
    # qty=contracts / price=limit_price.
    # NOTE: this books on SETTLEMENT assuming the copy is held to resolution. A
    # live copy whose exit actually FILLED before resolution would be booked
    # slightly off — acceptable v1 (exits almost always no-fill on the fast
    # markets this targets); `_pair_pending_exits` is deliberately left untouched.
    fill_qty = row.get("fill_qty")
    fill_price = row.get("fill_price")
    if fill_qty is not None and fill_price is not None:
        # Live copy placement — book only if flagged `leg_priced` (set by main.py
        # after the kalshi_live FIX-1 broker inversion). Pre-fix live rows carry a
        # YES-centric fill_price that would mis-book (NO 166 @ 0.987 → phantom
        # $163.84 vs the real ~$2.16), so they are SKIPPED, not booked wrong.
        if not row.get("leg_priced"):
            return None
        qty = float(fill_qty)
        price = float(fill_price)
    else:
        qty = float(row.get("qty") or 0.0)
        price = float(row.get("limit_price") or 0.0)
    if qty <= 0 or price <= 0 or price >= 1.0:
        return None

    status = res.get("status")
    market_result = (res.get("result") or "").lower()
    if status == "void":
        won = False
        realized = 0.0
    elif status == "resolved" and market_result in ("yes", "no"):
        won = (side == market_result)
        realized = qty * (1.0 - price) if won else -qty * price
    else:
        return None

    notional = qty * price
    roi = (100.0 * realized / notional) if notional > 0 else 0.0

    actor = row.get("_actor") or row.get("strategy") or ""
    division = (
        row.get("division")
        or _ACTOR_TO_DIVISION.get(actor, "kalshi_arbitrage")
    )
    arb_type = (
        row.get("kalshi_arb_type")
        or _ACTOR_TO_ARB_TYPE_DEFAULT.get(actor)
        or "unknown"
    )

    return {
        "order_id":         row.get("order_id") or "",
        "ticker":           row.get("ticker") or "",
        "event_ticker":     row.get("event_ticker"),
        "event_title":      row.get("event_title"),
        "category":         row.get("category"),
        "strategy":         actor,
        "division":         division,
        "arb_type":         arb_type,
        "arb_set_id":       row.get("kalshi_arb_set_id") or row.get("kalshi_pair_id"),
        "outcome_bet":      side,
        "qty":              qty,
        "entry_price":      price,
        "notional":         notional,
        "entry_ts":         row["_ts"],
        "resolved_ts":      datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market_result":    market_result or "void",
        "won":              1 if won else 0,
        "realized_pnl":     realized,
        "roi_pct":          roi,
        "implied_at_entry": row.get("implied_prob_at_entry"),
        "llm_prob":         row.get("llm_prob_estimate"),
        "divergence_pct":   row.get("divergence_pct"),
        "edge_cents":       row.get("edge_cents"),
        "extra_json":       json.dumps(
            {
                "rationale":      row.get("rationale"),
                "risk_verdict":   row.get("risk_verdict"),
                "risk_reason":    row.get("risk_reason"),
                # LLM analysis fields surfaced by the prediction-markets
                # dashboard's expandable row UI. Pre-2026-05-11 the
                # resolver dropped these on the floor; preserving them
                # keeps History tab row-expansion useful long after the
                # source audit row scrolls out of the visible window.
                "llm_confidence": row.get("llm_confidence"),
                "llm_reasoning":  row.get("llm_reasoning"),
                "key_unknowns":   row.get("key_unknowns"),
                "leg":            row.get("leg"),
                "subtitle":       row.get("subtitle"),
                "expires_at":     row.get("expires_at"),
            },
            default=str,
        ),
    }


def _insert_round_trip(db_url: str, record: dict) -> bool:
    cols = list(record.keys())
    placeholders = ",".join("?" for _ in cols)
    sql = (
        f"INSERT OR IGNORE INTO kalshi_round_trips "
        f"({','.join(cols)}) VALUES ({placeholders})"
    )
    with _db.connect(db_url) as conn:
        cur = conn.execute(sql, [record[c] for c in cols])
        return (cur.rowcount or 0) > 0


# ── whale-exit pairing (K3 copy-trader rows) ──────────────────────────


def _pair_pending_exits(db_url: str) -> dict:
    """Match unpaired SELL audit rows from kalshi_copy_trader with their
    prior BUY (same whale_handle, ticker, outcome) and emit one round-trip
    row per pair, keyed by the exit's order_id with the entry's order_id
    linked via `entry_order_id`. K3 mirror of polymarket_resolver's
    `_pair_pending_exits`.

    Idempotent: a SELL already linked as a round-trip's `order_id` won't
    re-fetch; same for entries (excluded via entry_order_id).
    """
    counts = {"scanned": 0, "paired": 0, "skipped_no_entry": 0, "skipped_bad_data": 0}
    with _db.connect(db_url) as conn:
        sells = conn.execute(
            "SELECT a.ts AS ts, a.payload_json "
            "FROM audit_event a "
            "LEFT JOIN kalshi_round_trips r "
            "  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            "WHERE a.actor = 'kalshi_copy_trader' "
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
            whale = ep.get("whale_handle")
            ticker = ep.get("ticker")
            outcome = (ep.get("outcome") or "").lower()
            # Pre-Fix-A K3 rows (deployed 2026-05-12 with whale_entry_price /
            # whale_exit_price capture) have null limit_price. Pair them
            # anyway so user sees the historical exits in History tab;
            # realized_pnl falls through to 0 when prices are missing.
            exit_price = float(
                ep.get("limit_price") or ep.get("whale_exit_price") or 0.0
            )
            if not whale or not ticker or outcome not in ("yes", "no"):
                counts["skipped_bad_data"] += 1
                continue
            entry_cur = conn.execute(
                "SELECT a.ts AS ts, a.payload_json "
                "FROM audit_event a "
                "LEFT JOIN kalshi_round_trips r "
                "  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
                "WHERE a.actor = 'kalshi_copy_trader' "
                "  AND a.kind = 'would_have_placed' "
                "  AND json_extract(a.payload_json, '$.side') = 'buy' "
                "  AND json_extract(a.payload_json, '$.whale_handle') = ? "
                "  AND json_extract(a.payload_json, '$.ticker') = ? "
                "  AND json_extract(a.payload_json, '$.outcome') = ? "
                "  AND a.ts < ? "
                "  AND r.order_id IS NULL "
                "  AND json_extract(a.payload_json, '$.order_id') NOT IN ("
                "    SELECT entry_order_id FROM kalshi_round_trips "
                "    WHERE entry_order_id IS NOT NULL"
                "  ) "
                "ORDER BY a.ts DESC LIMIT 1",
                (whale, ticker, outcome, sell["ts"]),
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
                # Pre-Fix-A K3 rows have null limit_price; pair them anyway
                # but mark realized_pnl=0 so the row at least shows up in
                # History tab. Skip if qty is also missing — nothing to log.
                if qty <= 0:
                    counts["skipped_bad_data"] += 1
                    continue
                entry_price = 0.0
                pnl = 0.0
                notional = 0.0
                roi = 0.0
            else:
                pnl = qty * (exit_price - entry_price)
                notional = qty * entry_price
                roi = (100.0 * pnl / notional) if notional > 0 else 0.0
            record = {
                "order_id": ep.get("order_id") or "",
                "entry_order_id": bp.get("order_id") or "",
                "ticker": ticker,
                "event_ticker": ep.get("event_ticker") or bp.get("event_ticker"),
                "event_title": ep.get("event_title") or bp.get("event_title"),
                "category": ep.get("category") or bp.get("category"),
                "strategy": "kalshi_copy_trader",
                "division": ep.get("division") or "kalshi_copy_trading",
                "arb_type": "copy_trade",
                "outcome_bet": outcome,
                "qty": qty,
                "entry_price": entry_price,
                "notional": notional,
                "entry_ts": str(entry_row["ts"]),
                "resolved_ts": str(sell["ts"]),
                "market_result": "whale_closed",
                "won": 1 if pnl > 0 else 0,
                "realized_pnl": pnl,
                "roi_pct": roi,
                "extra_json": json.dumps(
                    {
                        "exit_price": exit_price,
                        "whale_handle": whale,
                        "rationale_entry": bp.get("rationale"),
                        "rationale_exit": ep.get("rationale"),
                        "side_detection_confidence": bp.get("side_detection_confidence"),
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
    max_per_tick: int = 300,
    max_per_actor: int = 50,
) -> dict:
    """One pass. Returns counts: scanned, resolved, pending, void,
    not_found, errors, plus whale-exit pairing counts.

    `max_per_actor` is the primary knob: each actor in `_KALSHI_ACTORS`
    gets up to that many of its oldest unresolved rows per tick. With 6
    actors × 50 = 300 max scanned per tick — kept low enough that Kalshi
    API call volume stays trivial, high enough that backlog drains.

    `max_per_tick` is a safety net on the merged-and-truncated total. It
    must be ≥ `max_per_actor × len(_KALSHI_ACTORS)` to avoid re-introducing
    starvation (truncation order is by actor order in `_KALSHI_ACTORS`,
    so actors at the end of the tuple would be cut first).

    Two passes per tick:
      1. `_pair_pending_exits`: pair K3 copy-trader SELL audit rows with
         their prior BUY, emit one round-trip per pair. Pure SQL — no
         Kalshi API calls. Runs first because pairing resolves rows that
         would otherwise repeatedly hit `get_market_resolution`.
      2. Market-settle path: for remaining unresolved BUY rows, look up
         the market on Kalshi and write a round-trip if settled.
    """
    pair_counts = _pair_pending_exits(db_url)
    rows = _fetch_unresolved_orders(db_url, max_per_actor=max_per_actor)
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
        ticker = row.get("ticker")
        if not ticker:
            counts["errors"] += 1
            continue
        try:
            res = await broker.get_market_resolution(ticker)
        except Exception as e:
            log.warning(
                "kalshi_resolver: lookup failed for %s: %s", ticker, e,
            )
            counts["errors"] += 1
            continue
        st = res.get("status", "not_found")
        if st == "resolved":
            rt = _compute_round_trip_row(row, res)
            if rt and _insert_round_trip(db_url, rt):
                counts["resolved"] += 1
        elif st == "void":
            rt = _compute_round_trip_row(row, res)
            if rt and _insert_round_trip(db_url, rt):
                counts["void"] += 1
        elif st == "pending":
            counts["pending"] += 1
        else:
            counts["not_found"] += 1
    return counts


# ── equity snapshot writer ─────────────────────────────────────────────


async def write_equity_snapshot(db_url: str, division: str, broker) -> bool:
    """Single snapshot for one division. Returns True on write, False on
    broker error. Mirrors polymarket_resolver.write_equity_snapshot."""
    try:
        snap = await broker.snapshot()
    except Exception as e:
        log.warning("kalshi_resolver: snapshot failed (%s): %s", division, e)
        return False
    equity = float(getattr(snap, "equity", 0.0) or 0.0)
    cash = float(getattr(snap, "cash", 0.0) or 0.0)
    positions = list(getattr(snap, "positions", []) or [])
    positions_value = max(0.0, equity - cash)
    n_pos = len(positions)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO kalshi_equity_history "
            "(ts, division, equity, cash_usd, positions_value, n_positions) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, division, equity, cash, positions_value, n_pos),
        )
    return True


# ── periodic loops ─────────────────────────────────────────────────────


async def _resolver_loop(db_url: str, broker, interval_sec: int) -> None:
    log.info(
        f"kalshi round-trip resolver online (interval={interval_sec}s)"
    )
    while True:
        try:
            counts = await resolve_pending_round_trips(db_url, broker)
            if counts["scanned"]:
                # f-string (not %s) — RedactingFilter rewrites dict args
                # into their keys, producing wrong output on % formatting.
                log.info(f"kalshi_resolver tick: {counts}")
        except asyncio.CancelledError:
            log.info("kalshi_resolver cancelled.")
            return
        except Exception:
            log.exception("kalshi_resolver tick error (continuing)")
        await asyncio.sleep(interval_sec)


async def _equity_snapshot_loop(
    db_url: str, division: str, broker, interval_sec: int,
) -> None:
    log.info(
        f"kalshi equity snapshot writer online "
        f"(division={division}, interval={interval_sec}s)"
    )
    while True:
        try:
            await write_equity_snapshot(db_url, division, broker)
        except asyncio.CancelledError:
            log.info(f"kalshi_equity_snapshot({division}) cancelled.")
            return
        except Exception:
            log.exception(
                f"kalshi_equity_snapshot({division}) tick error (continuing)"
            )
        await asyncio.sleep(interval_sec)


def start_resolver_loop(
    db_url: str, broker, *, interval_sec: int = 3600,
) -> asyncio.Task:
    """Spawn the hourly Kalshi round-trip resolver."""
    return asyncio.create_task(
        _resolver_loop(db_url, broker, interval_sec),
        name="kalshi_resolver_loop",
    )


def start_equity_snapshot_loop(
    db_url: str, division: str, broker, *, interval_sec: int = 300,
) -> asyncio.Task:
    """Spawn the 5-minute Kalshi equity snapshot writer for one division."""
    return asyncio.create_task(
        _equity_snapshot_loop(db_url, division, broker, interval_sec),
        name=f"kalshi_equity_snapshot_loop[{division}]",
    )
