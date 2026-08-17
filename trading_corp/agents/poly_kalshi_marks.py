"""Poly->Kalshi live mark-to-market poller (Phase 2b CP2).

A server-side loop that, every ~60s, reads the OPEN poly_kalshi_order positions
(broker-free), quotes each open KXMLBGAME ticker via the shared KalshiBroker, computes
unrealized P&L, and writes ephemeral marks to two VOLATILE tables (NEVER audit_event):
  - poly_kalshi_mark_live: one current row per open position (INSERT OR REPLACE on
    order_id); the dashboard reads it broker-free with an "as of mark_ts" stale label.
  - poly_kalshi_mark_history: a bounded rolling yes-mid series per position (cap
    `_HISTORY_CAP`) that powers the live price sparkline (the "game story").

Mirrors the `mace_rung_live` volatile-cache idiom (db.py) + the kalshi equity-snapshot
loop pattern (kalshi_resolver.py). A missed quote leaves the last row in place so the
view judges staleness off `mark_ts` — a miss never fabricates a value. Rows for positions
that have resolved/closed are pruned each cycle so the tables track OPEN positions only.

Unrealized uses the always-YES contract math: `(yes_mid - fill_price) * fill_count`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from trading_corp.persistence import db as _db

log = logging.getLogger(__name__)

_DIVISION = "poly_kalshi_mlb"
_HISTORY_CAP = 60          # bounded rolling yes-mid points per position (sparkline)


def _fetch_open_positions(db_url: str) -> list[dict]:
    """OPEN poly_kalshi positions (broker-free): placed ENTRY poly_kalshi_order rows
    with a persisted order_id, not yet resolved (no kalshi_round_trips row). Same gate
    as the CP3 dashboard OPEN query. Returns {order_id, ticker, fill_price, fill_count}.
    Rows without real fill data (pre-CP3) are skipped — they carry no basis to mark."""
    out: list[dict] = []
    with _db.connect(db_url) as conn:
        cur = conn.execute(
            "SELECT a.payload_json FROM audit_event a "
            "LEFT JOIN kalshi_round_trips r "
            "  ON r.order_id = json_extract(a.payload_json, '$.order_id') "
            "WHERE a.actor = ? AND a.kind = 'poly_kalshi_order' "
            "  AND json_extract(a.payload_json, '$.status') = 'placed' "
            "  AND COALESCE(json_extract(a.payload_json, '$.action'), 'entry') = 'entry' "
            "  AND COALESCE(json_extract(a.payload_json, '$.order_id'), '') != '' "
            "  AND r.order_id IS NULL",
            (_DIVISION,),
        )
        for row in cur.fetchall():
            try:
                p = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            oid = str(p.get("order_id") or "")
            tkr = str(p.get("ticker") or "")
            fp, fc = p.get("fill_price"), p.get("fill_count")
            if not oid or not tkr or fp is None or fc is None:
                continue
            out.append({"order_id": oid, "ticker": tkr,
                        "fill_price": float(fp), "fill_count": float(fc)})
    return out


def _prune_closed(db_url: str, open_ids: set[str]) -> None:
    """Drop volatile marks for positions no longer open (resolved/closed), so the
    tables track OPEN positions only. Empty open set -> clear both tables."""
    with _db.connect(db_url) as conn:
        if open_ids:
            ph = ",".join("?" for _ in open_ids)
            args = tuple(open_ids)
            conn.execute(f"DELETE FROM poly_kalshi_mark_live WHERE order_id NOT IN ({ph})", args)
            conn.execute(f"DELETE FROM poly_kalshi_mark_history WHERE order_id NOT IN ({ph})", args)
        else:
            conn.execute("DELETE FROM poly_kalshi_mark_live")
            conn.execute("DELETE FROM poly_kalshi_mark_history")


def _write_mark(db_url: str, *, order_id: str, ticker: str, yes_mid: float,
                unrealized: float, unrealized_pct: float, ts: str) -> None:
    """Overwrite the live row + append one bounded-history point (pruned to cap)."""
    with _db.connect(db_url) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO poly_kalshi_mark_live "
            "(order_id, ticker, yes_mid, unrealized, unrealized_pct, mark_ts) "
            "VALUES (?,?,?,?,?,?)",
            (order_id, ticker, yes_mid, unrealized, unrealized_pct, ts),
        )
        conn.execute(
            "INSERT INTO poly_kalshi_mark_history (order_id, ticker, yes_mid, ts) "
            "VALUES (?,?,?,?)",
            (order_id, ticker, yes_mid, ts),
        )
        conn.execute(
            "DELETE FROM poly_kalshi_mark_history WHERE order_id=? AND id NOT IN ("
            "  SELECT id FROM poly_kalshi_mark_history WHERE order_id=? ORDER BY id DESC LIMIT ?)",
            (order_id, order_id, _HISTORY_CAP),
        )


async def run_mark_cycle(db_url: str, broker) -> dict:
    """One pass: prune closed, then quote each open position and write its live mark +
    history. Broker-free EXCEPT the per-ticker `quote()`. NEVER writes audit_event. A
    quote miss (0.0/error, per KalshiBroker.quote's contract) leaves the prior row in
    place (staleness judged off mark_ts)."""
    positions = _fetch_open_positions(db_url)
    _prune_closed(db_url, {p["order_id"] for p in positions})
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    counts = {"open": len(positions), "marked": 0, "quote_miss": 0}
    for pos in positions:
        try:
            yes_mid = await broker.quote(pos["ticker"])
        except Exception as e:  # noqa: BLE001 — a quote miss must never kill the loop
            log.warning("poly_kalshi mark: quote failed for %s: %s", pos["ticker"], e)
            yes_mid = None
        if not yes_mid or yes_mid <= 0:      # 0.0 == stub/miss (KalshiBroker.quote contract)
            counts["quote_miss"] += 1
            continue                          # leave the prior row; view judges stale by mark_ts
        fp, fc = pos["fill_price"], pos["fill_count"]
        unrealized = (float(yes_mid) - fp) * fc                  # always-YES contract math
        unrealized_pct = (100.0 * (float(yes_mid) - fp) / fp) if fp else 0.0
        _write_mark(db_url, order_id=pos["order_id"], ticker=pos["ticker"],
                    yes_mid=float(yes_mid), unrealized=float(unrealized),
                    unrealized_pct=float(unrealized_pct), ts=ts)
        counts["marked"] += 1
    return counts


def _log_tick(counts: dict) -> None:
    """Emit the per-cycle tick line with pre-formatted scalar %-args (NOT the raw dict).
    A lone Mapping arg is collapsed by `logging` into `record.args`, which the shared
    `RedactingFilter` (secrets.py) iterates into a keys-tuple -> `getMessage()` then raises
    `TypeError: not all arguments converted` every cycle. Scalar args sidestep it (mirrors
    the polymarket_resolver tick log that renders cleanly on the same handler)."""
    log.info(
        "poly_kalshi mark tick: open=%s marked=%s quote_miss=%s",
        counts["open"], counts["marked"], counts["quote_miss"],
    )


async def _mark_loop(db_url: str, broker, interval_sec: int) -> None:
    log.info("poly_kalshi mark poller online (interval=%ss)", interval_sec)
    while True:
        try:
            counts = await run_mark_cycle(db_url, broker)
            if counts["open"]:
                _log_tick(counts)
        except asyncio.CancelledError:
            log.info("poly_kalshi mark poller cancelled.")
            return
        except Exception:
            log.exception("poly_kalshi mark tick error (continuing)")
        await asyncio.sleep(interval_sec)


def start_poly_kalshi_mark_loop(db_url: str, broker, *, interval_sec: int = 60) -> asyncio.Task:
    """Spawn the ~60s Poly->Kalshi live mark-to-market poller."""
    return asyncio.create_task(
        _mark_loop(db_url, broker, interval_sec),
        name="poly_kalshi_mark_loop",
    )
